"""制度の検索・閲覧系エンドポイント（判定層。LLM不使用）。

- /api/categories : カテゴリ一覧（件数付き）
- /api/areas      : 市区町村一覧
- /api/benefits   : キーワード／属性で制度を検索
- /api/subgraph   : 指定した制度を中心にしたサブグラフ
"""

import dependencies
from config import DATASET_ID, PROJECT_ID
from fastapi import APIRouter, HTTPException, Query
from google.cloud import bigquery
from queries import AGE_SOURCE_ORDER_BY, age_filter_sql

router = APIRouter()


@router.get("/api/categories")
def get_categories():
    query = f"""
        SELECT category, COUNT(*) AS count
        FROM `{PROJECT_ID}.{DATASET_ID}.benefits`
        GROUP BY category
        ORDER BY count DESC
        LIMIT 50
    """
    rows = dependencies.get_client().query(query).result()
    return [{"category": r["category"], "count": r["count"]} for r in rows]


@router.get("/api/areas")
def get_areas():
    query = f"""
        SELECT area_code, area_name, COUNT(*) AS count
        FROM `{PROJECT_ID}.{DATASET_ID}.benefits`
        WHERE area_code IS NOT NULL
        GROUP BY area_code, area_name
        ORDER BY area_code
    """
    rows = dependencies.get_client().query(query).result()
    return [{"area_code": r["area_code"], "area_name": r["area_name"], "count": r["count"]} for r in rows]


@router.get("/api/benefits")
def search_benefits(
    q: str | None = Query(default=None, description="制度名の部分一致キーワード"),
    category: str | None = Query(default=None, description="カテゴリの完全一致"),
    area_code: str | None = Query(default=None, description="居住地の市区町村コード（ユーザー属性）"),
    age_months: int | None = Query(default=None, ge=0, description="子どもの月齢（ユーザー属性）"),
    limit: int = Query(default=30, le=100),
):
    """属性（居住地・子どもの月齢）とキーワードで制度を検索する。
    area_code / age_months は benefits ノードの構造化列を直接比較するだけで、LLMを介さない確定的な絞り込み。
    """
    conditions = []
    params = []
    if q:
        conditions.append("LOWER(title) LIKE LOWER(@q)")
        params.append(bigquery.ScalarQueryParameter("q", "STRING", f"%{q}%"))
    if category:
        conditions.append("category = @category")
        params.append(bigquery.ScalarQueryParameter("category", "STRING", category))
    if area_code:
        conditions.append("area_code = @area_code")
        params.append(bigquery.ScalarQueryParameter("area_code", "STRING", area_code))
    order_by = "title"
    if age_months is not None:
        conditions.append(age_filter_sql("age_months"))
        params.append(bigquery.ScalarQueryParameter("age_months", "INT64", age_months))
        # 年齢が明示されている制度（確度が高い）を先に、年齢不明のものを最後に出す
        order_by = f"{AGE_SOURCE_ORDER_BY}, title"

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT benefit_id, title, category, summary,
               effective_min_age_months AS min_age_months,
               effective_max_age_months AS max_age_months,
               age_source, area_name, has_free_text_conditions,
               is_free, monetary_support_text, cost_text, electronic_submission
        FROM `{PROJECT_ID}.{DATASET_ID}.benefits`
        {where_clause}
        ORDER BY {order_by}
        LIMIT @limit
    """
    params.append(bigquery.ScalarQueryParameter("limit", "INT64", limit))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = dependencies.get_client().query(query, job_config=job_config).result()
    return [
        {
            "benefit_id": r["benefit_id"],
            "title": r["title"],
            "category": r["category"],
            "summary": (r["summary"] or "")[:120],
            "min_age_months": r["min_age_months"],
            "max_age_months": r["max_age_months"],
            "age_source": r["age_source"],
            "area_name": r["area_name"],
            "has_free_text_conditions": r["has_free_text_conditions"],
            "is_free": r["is_free"],
            "has_amount_info": bool(r["monetary_support_text"] or r["cost_text"]),
            "electronic_submission": r["electronic_submission"],
        }
        for r in rows
    ]


def _links(rows) -> list[dict]:
    """ARRAY<STRUCT<title, uri>> を JSON に載る形に整える。

    uri の無い要素は捨てる（リンクとして使えないため）。title が空なら uri を表示名にする。
    同じ uri が related / embedded の両方に出ることがあるので、ここでは重複を残し、
    まとめるかどうかは表示側に任せる。
    """
    result = []
    for row in rows or []:
        uri = row.get("uri")
        if not uri:
            continue
        result.append({"title": row.get("title") or uri, "uri": uri})
    return result


@router.get("/api/subgraph")
def get_subgraph(benefit_id: str = Query(..., description="中心にする制度のID")):
    # BigQuery Graph (GQL) は Enterprise 予約が必須になったため通常 SQL で書いている
    # （経緯は docs/adr/0003）。REQUIRES / REQUIRES_DOC の2つの LEFT JOIN を独立に
    # 行うことで、GQL の逐次 OPTIONAL MATCH と同じ「条件×書類のクロス積」を再現する。
    query = f"""
        SELECT
          b.benefit_id AS benefit_id, b.title AS title, b.category AS category, b.summary AS summary,
          -- 詳細ページの本体。summary は一覧と同じ要約なので、これが無いと
          -- 「詳細ページなのに一覧と同じことしか書いていない」状態になる（実データの97%が description を持つ）
          b.description AS description, b.utilization AS utilization,
          -- 機械判定しきれない条件の原文。has_free_text_conditions=true は約半数（3,808件）あり、
          -- これを出さないと所得制限などを知らないまま「自分は対象だ」と思い込ませる（CLAUDE.md）
          b.conditions_text AS conditions_text, b.target_persons_text AS target_persons_text,
          b.has_free_text_conditions AS has_free_text_conditions,
          -- 本文に埋め込まれていたリンクを ETL が分離したもの。申請書式への導線になる
          b.related_links AS related_links, b.form_links AS form_links,
          b.embedded_links AS embedded_links,
          b.area_name AS area_name,
          -- **素の min/max_age_months を返してはいけない。** 6割超が NULL で、
          -- 詳細ページに出すと「対象年齢の記載なし」ばかりになる（CLAUDE.md / issue #61）。
          -- 一覧（search_benefits）は最初から effective_* を返しており、詳細だけがずれていた。
          b.effective_min_age_months AS min_age_months,
          b.effective_max_age_months AS max_age_months,
          -- 推定値を断定的に見せないために、どこから来た値かも返す
          b.age_source AS age_source,
          b.cost_text AS cost_text, b.cost_conditions_text AS cost_conditions_text,
          b.monetary_support_text AS monetary_support_text, b.materially_support_text AS materially_support_text,
          b.is_free AS is_free,
          b.department AS department, b.contact_name AS contact_name, b.contact_phone AS contact_phone,
          b.contact_email AS contact_email, b.contact_address AS contact_address,
          b.official_url AS official_url, b.official_title AS official_title,
          b.procedure_method AS procedure_method, b.procedure_counter AS procedure_counter,
          b.electronic_submission AS electronic_submission,
          b.regulation_name AS regulation_name, b.update_date AS update_date,
          s.status_id AS status_id, s.name AS status_name, s.type AS status_type,
          d.doc_id AS doc_id, d.doc_name AS doc_name, d.doc_url AS doc_url
        FROM `{PROJECT_ID}.{DATASET_ID}.benefits` b
        LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.benefit_requires_status` brs
          ON brs.benefit_id = b.benefit_id
        LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.statuses` s
          ON s.status_id = brs.status_id
        -- 必要書類欄には注意書きの文章も混ざるため、書類らしいものだけをノードとして出す
        -- （ON句に置くことで、書類が無い制度でも制度自体の行は残す＝OPTIONAL MATCH相当）
        LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.benefit_requires_doc` brd
          ON brd.benefit_id = b.benefit_id
        LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.documents` d
          ON d.doc_id = brd.doc_id AND d.is_probable_document
        WHERE b.benefit_id = @benefit_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("benefit_id", "STRING", benefit_id)]
    )
    rows = list(dependencies.get_client().query(query, job_config=job_config).result())
    if not rows:
        raise HTTPException(status_code=404, detail="benefit not found")

    nodes = {}
    edges = []
    edge_ids = set()

    first = rows[0]
    benefit_node_id = f"benefit:{first['benefit_id']}"
    nodes[benefit_node_id] = {
        "data": {
            "id": benefit_node_id,
            "benefit_id": first["benefit_id"],
            "label": first["title"],
            "type": "Benefit",
            "category": first["category"],
            "summary": first["summary"],
            "description": first["description"],
            "utilization": first["utilization"],
            "conditions_text": first["conditions_text"],
            "target_persons_text": first["target_persons_text"],
            "has_free_text_conditions": first["has_free_text_conditions"],
            "related_links": _links(first["related_links"]),
            "form_links": _links(first["form_links"]),
            "embedded_links": _links(first["embedded_links"]),
            "area_name": first["area_name"],
            "min_age_months": first["min_age_months"],
            "max_age_months": first["max_age_months"],
            "age_source": first["age_source"],
            "cost_text": first["cost_text"],
            "cost_conditions_text": first["cost_conditions_text"],
            "monetary_support_text": first["monetary_support_text"],
            "materially_support_text": first["materially_support_text"],
            "is_free": first["is_free"],
            "department": first["department"],
            "contact_name": first["contact_name"],
            "contact_phone": first["contact_phone"],
            "contact_email": first["contact_email"],
            "contact_address": first["contact_address"],
            "official_url": first["official_url"],
            "official_title": first["official_title"],
            "procedure_method": first["procedure_method"],
            "procedure_counter": first["procedure_counter"],
            "electronic_submission": first["electronic_submission"],
            "regulation_name": first["regulation_name"],
            "update_date": str(first["update_date"]) if first["update_date"] else None,
        }
    }

    for r in rows:
        if r["status_id"]:
            status_node_id = f"status:{r['status_id']}"
            nodes.setdefault(
                status_node_id,
                {
                    "data": {
                        "id": status_node_id,
                        "label": r["status_name"],
                        "type": "Status",
                        "status_type": r["status_type"],
                    }
                },
            )
            edge_id = f"{benefit_node_id}->{status_node_id}"
            if edge_id not in edge_ids:
                edge_ids.add(edge_id)
                edges.append(
                    {
                        "data": {
                            "id": edge_id,
                            "source": benefit_node_id,
                            "target": status_node_id,
                            "label": "REQUIRES",
                        }
                    }
                )
        if r["doc_id"]:
            doc_node_id = f"doc:{r['doc_id']}"
            nodes.setdefault(
                doc_node_id,
                {
                    "data": {
                        "id": doc_node_id,
                        "label": r["doc_name"],
                        "type": "Document",
                        # 書類名に紐づく様式のURL。持っているのは 462/4,919 件だけ
                        "doc_url": r["doc_url"],
                    }
                },
            )
            edge_id = f"{benefit_node_id}->{doc_node_id}"
            if edge_id not in edge_ids:
                edge_ids.add(edge_id)
                edges.append(
                    {
                        "data": {
                            "id": edge_id,
                            "source": benefit_node_id,
                            "target": doc_node_id,
                            "label": "REQUIRES_DOC",
                        }
                    }
                )

    return {"nodes": list(nodes.values()), "edges": edges}
