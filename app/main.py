"""
子育て支援制度ナレッジグラフ 可視化アプリ (FastAPI + BigQuery Graph)

制度の適用判定は BigQuery Graph への定型クエリのみで行い（LLM不使用・ミリ秒・誤判定ゼロ）、
Gemini は制度のやさしい解説や書類添削といった伴走サポートにのみ使う。

- /api/categories        : カテゴリ一覧（件数付き）
- /api/areas             : 市区町村一覧
- /api/benefits          : キーワード／属性で制度を検索
- /api/subgraph          : 指定した制度を中心にしたサブグラフ
- /api/user/profile      : ユーザー属性の検証・正規化（Phase2）
- /api/benefits/match    : 属性から対象制度を一括取得（マッチ理由付き・Phase2）
- /api/timeline          : 年齢軸に制度を並べたタイムライン（Phase3）
- /api/support/draft-review : Gemini によるやさしい解説・書類添削（Phase2）
"""

import os

from config import APP_ENV, DATASET_ID, GRAPH_NAME, LOCATION, PROJECT_ID
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from google.cloud import bigquery
from pydantic import BaseModel, Field

GEMINI_MODEL = "gemini-3.5-flash-lite"
# 行政制度の言い換えは誤りが許されないため、軽量モデルでも thinking を厚めに確保する。
# thinking_level と thinking_budget は併用不可（400になる）。実測で thinking_level=HIGH の方が
# thinking_budget 指定より多く思考する（509 vs 206 tokens）ため HIGH を採用。
GEMINI_THINKING_LEVEL = "HIGH"

app = FastAPI(title="子育て支援制度ナレッジグラフ")

BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# BigQuery クライアントは初回利用時に作る。
# import 時に生成すると GCP 認証のない環境（CI・テスト）でモジュールを読み込めなくなるため。
_bq_client: bigquery.Client | None = None


def get_client() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT_ID, location=LOCATION)
    return _bq_client


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(BASE_DIR, "templates", "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/api/categories")
def get_categories():
    query = f"""
        SELECT category, COUNT(*) AS count
        FROM `{PROJECT_ID}.{DATASET_ID}.benefits`
        GROUP BY category
        ORDER BY count DESC
        LIMIT 50
    """
    rows = get_client().query(query).result()
    return [{"category": r["category"], "count": r["count"]} for r in rows]


@app.get("/api/areas")
def get_areas():
    query = f"""
        SELECT area_code, area_name, COUNT(*) AS count
        FROM `{PROJECT_ID}.{DATASET_ID}.benefits`
        WHERE area_code IS NOT NULL
        GROUP BY area_code, area_name
        ORDER BY area_code
    """
    rows = get_client().query(query).result()
    return [{"area_code": r["area_code"], "area_name": r["area_name"], "count": r["count"]} for r in rows]


@app.get("/api/benefits")
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
    # 年齢での絞り込みは effective_*（明示値がなければ推定値）を使う。
    # 素の min/max_age_months は6割超が NULL のため、それだけで絞ると
    # 「10歳なのに新生児向けの制度が出る」といった取りこぼしが起きる。
    order_by = "title"
    if age_months is not None:
        conditions.append("(effective_min_age_months IS NULL OR effective_min_age_months <= @age_months)")
        conditions.append("(effective_max_age_months IS NULL OR effective_max_age_months >= @age_months)")
        params.append(bigquery.ScalarQueryParameter("age_months", "INT64", age_months))
        # 年齢が明示されている制度（確度が高い）を先に、年齢不明のものを最後に出す
        order_by = "CASE age_source WHEN 'explicit' THEN 0 WHEN 'inferred' THEN 1 ELSE 2 END, title"

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
    rows = get_client().query(query, job_config=job_config).result()
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


@app.get("/api/subgraph")
def get_subgraph(benefit_id: str = Query(..., description="中心にする制度のID")):
    query = f"""
        GRAPH `{GRAPH_NAME}`
        MATCH (b:Benefit {{benefit_id: @benefit_id}})
        OPTIONAL MATCH (b)-[:REQUIRES]->(s:Status)
        -- 必要書類欄には注意書きの文章も混ざるため、書類らしいものだけをノードとして出す
        OPTIONAL MATCH (b)-[:REQUIRES_DOC]->(d:Document WHERE d.is_probable_document)
        RETURN
          b.benefit_id AS benefit_id, b.title AS title, b.category AS category, b.summary AS summary,
          b.area_name AS area_name, b.min_age_months AS min_age_months, b.max_age_months AS max_age_months,
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
          d.doc_id AS doc_id, d.doc_name AS doc_name
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("benefit_id", "STRING", benefit_id)]
    )
    rows = list(get_client().query(query, job_config=job_config).result())
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
            "area_name": first["area_name"],
            "min_age_months": first["min_age_months"],
            "max_age_months": first["max_age_months"],
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
                {"data": {"id": doc_node_id, "label": r["doc_name"], "type": "Document"}},
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


# ============================================================ Phase2: ユーザープロフィール


class UserProfile(BaseModel):
    """設定画面で登録するユーザー属性。チャットではなく選択式フォームからの入力を想定。"""

    area_code: str | None = Field(default=None, description="居住地の市区町村コード")
    child_age_months: int | None = Field(default=None, ge=0, le=300, description="子どもの月齢")
    is_pregnant: bool = Field(default=False, description="妊娠中かどうか")
    is_single_parent: bool = Field(default=False, description="ひとり親世帯かどうか")
    has_disability: bool = Field(default=False, description="障がいのあるお子さんがいるか")


@app.post("/api/user/profile")
def save_user_profile(profile: UserProfile):
    """属性を検証・正規化して返す。

    永続化はクライアント側（localStorage）で行う前提のため、ここでは値の妥当性検証と
    人間可読なラベル付けのみを担当する。将来マイナポータル等と連携する際の差し替え点。
    """
    area_name = None
    if profile.area_code:
        query = f"""
            SELECT area_name FROM `{PROJECT_ID}.{DATASET_ID}.benefits`
            WHERE area_code = @area_code LIMIT 1
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("area_code", "STRING", profile.area_code)]
        )
        rows = list(get_client().query(query, job_config=job_config).result())
        if not rows:
            raise HTTPException(status_code=400, detail="unknown area_code")
        area_name = rows[0]["area_name"]

    age_label = None
    if profile.child_age_months is not None:
        years, months = divmod(profile.child_age_months, 12)
        age_label = f"{years}歳{months}か月" if months else f"{years}歳"

    return {
        "profile": profile.model_dump(),
        "resolved": {"area_name": area_name, "child_age_label": age_label},
    }


# ============================================================ Phase2: 制度マッチング


@app.get("/api/benefits/match")
def match_benefits(
    area_code: str | None = Query(default=None, description="居住地の市区町村コード"),
    child_age_months: int | None = Query(default=None, ge=0, le=300, description="子どもの月齢"),
    is_pregnant: bool = Query(default=False),
    include_skill_tree: bool = Query(default=True, description="次に繋がる制度も返すか"),
    limit: int = Query(default=60, le=200),
):
    """ユーザー属性から対象制度を一括取得する（LLM不使用の確定ロジック）。

    どの条件で当たったかを match_reasons として返し、推定年齢で当たった場合は
    age_source='inferred' を添えて「推定」であることを利用側が示せるようにする。
    """
    conditions = []
    params = []

    if area_code:
        # 東京都全域の制度（area_code=130001）も対象に含める
        conditions.append("(area_code = @area_code OR area_code = '130001')")
        params.append(bigquery.ScalarQueryParameter("area_code", "STRING", area_code))

    if child_age_months is not None:
        age_clause = (
            "((effective_min_age_months IS NULL OR effective_min_age_months <= @age) "
            "AND (effective_max_age_months IS NULL OR effective_max_age_months >= @age))"
        )
        # 妊娠中なら「妊娠期の制度」も対象に加える
        if is_pregnant:
            age_clause = f"({age_clause} OR is_prenatal)"
        conditions.append(age_clause)
        params.append(bigquery.ScalarQueryParameter("age", "INT64", child_age_months))
    elif is_pregnant:
        conditions.append("is_prenatal")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT benefit_id, title, category, summary, area_name, area_code,
               effective_min_age_months, effective_max_age_months, age_source,
               is_prenatal, is_free, monetary_support_text, electronic_submission,
               has_free_text_conditions, conditions_text, official_url, scheme_id
        FROM `{PROJECT_ID}.{DATASET_ID}.benefits`
        {where_clause}
        ORDER BY
          -- 年齢が明示されている制度（確度が高い）を先に出す
          CASE age_source WHEN 'explicit' THEN 0 WHEN 'inferred' THEN 1 ELSE 2 END,
          effective_min_age_months NULLS LAST,
          title
        LIMIT @limit
    """
    params.append(bigquery.ScalarQueryParameter("limit", "INT64", limit))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = list(get_client().query(query, job_config=job_config).result())

    results = []
    for r in rows:
        reasons = []
        if area_code:
            reasons.append(
                "東京都全域の制度" if r["area_code"] == "130001" else f"お住まいの{r['area_name']}が対象"
            )
        if child_age_months is not None and (
            r["effective_min_age_months"] is not None or r["effective_max_age_months"] is not None
        ):
            lo = r["effective_min_age_months"]
            hi = r["effective_max_age_months"]
            span = f"{lo if lo is not None else '-'}〜{hi if hi is not None else '-'}ヶ月"
            suffix = "（対象年齢は本文からの推定）" if r["age_source"] == "inferred" else ""
            reasons.append(f"お子さんの月齢{child_age_months}が対象範囲{span}に該当{suffix}")
        if is_pregnant and r["is_prenatal"]:
            reasons.append("妊娠中の方が対象")

        results.append(
            {
                "benefit_id": r["benefit_id"],
                "title": r["title"],
                "category": r["category"],
                "summary": (r["summary"] or "")[:140],
                "area_name": r["area_name"],
                "min_age_months": r["effective_min_age_months"],
                "max_age_months": r["effective_max_age_months"],
                "age_source": r["age_source"],
                "is_free": r["is_free"],
                "has_amount_info": bool(r["monetary_support_text"]),
                "electronic_submission": r["electronic_submission"],
                "needs_confirmation": r["has_free_text_conditions"],
                "conditions_text": (r["conditions_text"] or "")[:200] or None,
                "official_url": r["official_url"],
                "match_reasons": reasons,
            }
        )

    payload = {"count": len(results), "benefits": results}

    if include_skill_tree and results:
        payload["next_steps"] = _fetch_next_steps([r["benefit_id"] for r in results[:30]])
    return payload


def _fetch_next_steps(benefit_ids: list[str]):
    """マッチした制度から「次に繋がる制度」をスキルツリーのエッジで取得する。"""
    query = f"""
        GRAPH `{GRAPH_NAME}`
        MATCH (a:Benefit)-[e:LEADS_TO]->(b:Benefit)
        WHERE a.benefit_id IN UNNEST(@ids)
        RETURN a.benefit_id AS from_id, a.title AS from_title,
               b.benefit_id AS to_id, b.title AS to_title,
               e.relation AS relation, e.reason AS reason
        LIMIT 80
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", benefit_ids)]
    )
    return [
        {
            "from_benefit_id": r["from_id"],
            "from_title": r["from_title"],
            "to_benefit_id": r["to_id"],
            "to_title": r["to_title"],
            "relation": r["relation"],
            "reason": r["reason"],
        }
        for r in get_client().query(query, job_config=job_config).result()
    ]


# ============================================================ Phase3: タイムライン


# 妊娠期〜18歳までのライフステージ。年齢軸に制度を並べるために使う。
LIFE_STAGES = [
    {"key": "prenatal", "label": "妊娠中", "min": None, "max": None},
    {"key": "0y", "label": "0歳", "min": 0, "max": 11},
    {"key": "1y", "label": "1歳", "min": 12, "max": 23},
    {"key": "2y", "label": "2歳", "min": 24, "max": 35},
    {"key": "3-5y", "label": "3〜5歳（未就学）", "min": 36, "max": 71},
    {"key": "6-11y", "label": "6〜11歳（小学生）", "min": 72, "max": 143},
    {"key": "12-14y", "label": "12〜14歳（中学生）", "min": 144, "max": 179},
    {"key": "15-18y", "label": "15〜18歳（高校生）", "min": 180, "max": 227},
]


@app.get("/api/timeline")
def get_timeline(
    area_code: str | None = Query(default=None, description="居住地の市区町村コード"),
    per_stage: int = Query(default=8, le=20, description="各ステージで返す制度数"),
):
    """ライフステージごとに制度を並べたタイムラインを返す。

    子育ては本質的に時系列のため、グラフより「次に何が来るか」を掴みやすい。
    """
    params = [bigquery.ScalarQueryParameter("per_stage", "INT64", per_stage)]
    area_filter = ""
    if area_code:
        area_filter = "AND (area_code = @area_code OR area_code = '130001')"
        params.append(bigquery.ScalarQueryParameter("area_code", "STRING", area_code))

    # ステージ定義をクエリ内の UNNEST に展開し、1回のクエリで全ステージ分を取得する
    # （ステージごとにクエリを投げると往復が増えて体感が遅くなるため）
    stage_rows = ", ".join(
        f"STRUCT('{s['key']}' AS key, "
        f"{'CAST(NULL AS INT64)' if s['min'] is None else s['min']} AS lo, "
        f"{'CAST(NULL AS INT64)' if s['max'] is None else s['max']} AS hi)"
        for s in LIFE_STAGES
    )

    query = f"""
        WITH stages AS (
          SELECT * FROM UNNEST([{stage_rows}])
        ),
        matched AS (
          SELECT
            s.key AS stage_key,
            b.benefit_id, b.title, b.category, b.area_name, b.is_free,
            b.electronic_submission, b.age_source, b.effective_min_age_months,
            ROW_NUMBER() OVER (
              PARTITION BY s.key
              ORDER BY CASE b.age_source WHEN 'explicit' THEN 0 ELSE 1 END,
                       b.effective_min_age_months NULLS LAST, b.title
            ) AS rn
          FROM `{PROJECT_ID}.{DATASET_ID}.benefits` b
          JOIN stages s
            ON (s.key = 'prenatal' AND b.is_prenatal)
            OR (s.key != 'prenatal'
                AND NOT b.is_prenatal
                AND b.effective_min_age_months IS NOT NULL
                AND b.effective_max_age_months IS NOT NULL
                AND b.effective_min_age_months <= s.hi
                AND b.effective_max_age_months >= s.lo)
          WHERE TRUE {area_filter}
        )
        SELECT stage_key, benefit_id, title, category, area_name,
               is_free, electronic_submission, age_source
        FROM matched
        WHERE rn <= @per_stage
        ORDER BY stage_key, rn
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = list(get_client().query(query, job_config=job_config).result())

    by_stage = {}
    for r in rows:
        by_stage.setdefault(r["stage_key"], []).append(
            {
                "benefit_id": r["benefit_id"],
                "title": r["title"],
                "category": r["category"],
                "area_name": r["area_name"],
                "is_free": r["is_free"],
                "electronic_submission": r["electronic_submission"],
                "age_source": r["age_source"],
            }
        )

    return {
        "stages": [
            {"key": s["key"], "label": s["label"], "benefits": by_stage.get(s["key"], [])}
            for s in LIFE_STAGES
        ]
    }


# ============================================================ Phase2: Gemini 伴走サポート


def _build_genai_client():
    """Gemini クライアントを生成する。テストから差し替えられるよう関数に切り出している。"""
    from google import genai

    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


class DraftReviewRequest(BaseModel):
    benefit_id: str = Field(description="対象の制度ID")
    mode: str = Field(default="explain", description="explain（やさしい解説）/ review（下書き添削）")
    draft: str | None = Field(default=None, description="review モードで添削したい下書き本文")


@app.post("/api/support/draft-review")
def draft_review(req: DraftReviewRequest):
    """Gemini で制度のやさしい解説、または申請書下書きの添削を行う。

    制度の適用判定には一切使わない（判定は BigQuery Graph の確定クエリのみ）。
    ここで扱うのは「取得済みの制度情報を分かりやすく言い換える」ことだけ。
    """
    query = f"""
        SELECT title, category, area_name, summary, description,
               target_persons_text, conditions_text, monetary_support_text,
               procedure_method, official_url
        FROM `{PROJECT_ID}.{DATASET_ID}.benefits`
        WHERE benefit_id = @benefit_id LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("benefit_id", "STRING", req.benefit_id)]
    )
    rows = list(get_client().query(query, job_config=job_config).result())
    if not rows:
        raise HTTPException(status_code=404, detail="benefit not found")
    b = rows[0]

    facts = "\n".join(
        f"{label}: {value}"
        for label, value in [
            ("制度名", b["title"]),
            ("カテゴリ", b["category"]),
            ("自治体", b["area_name"]),
            ("概要", b["summary"]),
            ("詳細", (b["description"] or "")[:2000]),
            ("対象者", b["target_persons_text"]),
            ("その他の条件", b["conditions_text"]),
            ("金銭的支援", b["monetary_support_text"]),
            ("申請方法", (b["procedure_method"] or "")[:1000]),
        ]
        if value
    )

    if req.mode == "review":
        if not req.draft:
            raise HTTPException(status_code=400, detail="draft is required for review mode")
        prompt = (
            "あなたは行政手続きに詳しい相談員です。以下の制度情報を踏まえ、"
            "利用者が書いた申請書の下書きを添削してください。\n"
            "・不足している情報、誤解を招く表現を具体的に指摘する\n"
            "・修正後の文例を提示する\n"
            "・制度情報に書かれていないことは推測せず「窓口に確認」と案内する\n\n"
            f"【制度情報】\n{facts}\n\n【利用者の下書き】\n{req.draft}"
        )
    else:
        prompt = (
            "あなたは子育て中の保護者を支援する相談員です。以下の制度情報をもとに、"
            "はじめての人にも分かるようやさしく説明してください。\n"
            "・「どんな制度か」「誰が対象か」「いくらもらえる/かかるか」「何をすればいいか」の順\n"
            "・専門用語は言い換える。箇条書きを使う。400字程度\n"
            "・制度情報に書かれていないことは絶対に補わない。"
            "条件が曖昧な場合は「詳細は自治体窓口にご確認ください」と明記する\n\n"
            f"【制度情報】\n{facts}"
        )

    try:
        from google.genai import types

        response = _build_genai_client().models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_level=GEMINI_THINKING_LEVEL),
            ),
        )
        text = response.text
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Gemini呼び出しに失敗しました: {exc}") from exc

    return {
        "benefit_id": req.benefit_id,
        "title": b["title"],
        "mode": req.mode,
        "result": text,
        "official_url": b["official_url"],
        "disclaimer": "この文章はAIが制度情報をもとに生成したものです。最終的な判断は自治体の公式情報をご確認ください。",
    }


# パスが `/api/` 配下なのは意図的。`/healthz` は Google Frontend が手前で横取りし、
# コンテナまでリクエストが届かない（Cloud Run のログにも一切残らず、Google の 404 HTML が返る）。
# デプロイ後の確認が常に失敗して気づいた。
@app.get("/api/healthz")
def healthz():
    # どの環境・どのデータセットを見ているかは、デプロイ事故の切り分けで必ず要る
    return {"status": "ok", "env": APP_ENV, "dataset": DATASET_ID}
