"""Phase2: ユーザープロフィールと制度マッチング。

- /api/user/profile   : ユーザー属性の検証・正規化
- /api/benefits/match : 属性から対象制度を一括取得（マッチ理由付き）
"""

import dependencies
from config import DATASET_ID, PROJECT_ID
from fastapi import APIRouter, HTTPException, Query
from google.cloud import bigquery
from pydantic import BaseModel, Field
from queries import AGE_SOURCE_ORDER_BY, age_filter_sql

router = APIRouter()

# 東京都の標準分類コード（benefits.target_codes）。
# `*_code_labels` は統計的推定（公式マスタが非公開のため。docs/data-model.md）だが、
# **コードそのものは元データの値**なので、コードで絞る分には推定を挟まない。
#
# コードの意味づけが妥当かは dev の実データで検証した（本文との一致率）:
#   088（ひとり親家庭）541件 … 本文に「ひとり親/母子/父子/寡婦/遺児」を含む 90%
#   090（障がい児）  495件 … 本文に「障害/障がい」を含む 90%
TARGET_CODE_SINGLE_PARENT = "088"
TARGET_CODE_DISABILITY = "090"


class UserProfile(BaseModel):
    """設定画面で登録するユーザー属性。チャットではなく選択式フォームからの入力を想定。"""

    area_code: str | None = Field(default=None, description="居住地の市区町村コード")
    child_age_months: int | None = Field(default=None, ge=0, le=300, description="子どもの月齢")
    is_pregnant: bool = Field(default=False, description="妊娠中かどうか")
    is_single_parent: bool = Field(default=False, description="ひとり親世帯かどうか")
    has_disability: bool = Field(default=False, description="障がいのあるお子さんがいるか")


@router.post("/api/user/profile")
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
        rows = list(dependencies.get_client().query(query, job_config=job_config).result())
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


@router.get("/api/benefits/match")
def match_benefits(
    area_code: str | None = Query(default=None, description="居住地の市区町村コード"),
    child_age_months: int | None = Query(default=None, ge=0, le=300, description="子どもの月齢"),
    is_pregnant: bool = Query(default=False),
    is_single_parent: bool = Query(default=False, description="ひとり親世帯かどうか"),
    has_disability: bool = Query(default=False, description="障がいのあるお子さんがいるか"),
    include_skill_tree: bool = Query(default=True, description="次に繋がる制度も返すか"),
    limit: int = Query(default=60, le=200),
):
    """ユーザー属性から対象制度を一括取得する（LLM不使用の確定ロジック）。

    どの条件で当たったかを match_reasons として返し、推定年齢で当たった場合は
    age_source='inferred' を添えて「推定」であることを利用側が示せるようにする。

    ひとり親・障がいの属性は **並び順と理由付けにだけ使い、絞り込みには使わない**。
    該当しない人からこれらの制度を隠すこともできるが、それは
    「対象なのに出ない」を作りうる。分類コードと本文の一致率は90%で、
    残り10%を取りこぼす方が、関係ない制度が数件混ざるより害が大きい（CLAUDE.md）。
    """
    conditions = []
    params = []

    if area_code:
        # 東京都全域の制度（area_code=130001）も対象に含める
        conditions.append("(area_code = @area_code OR area_code = '130001')")
        params.append(bigquery.ScalarQueryParameter("area_code", "STRING", area_code))

    if child_age_months is not None:
        # 妊娠中なら「妊娠期の制度」も対象に加える
        conditions.append(age_filter_sql("age", include_prenatal=is_pregnant))
        params.append(bigquery.ScalarQueryParameter("age", "INT64", child_age_months))
    elif is_pregnant:
        conditions.append("is_prenatal")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # 属性に該当する制度を先頭に出す。指定が無ければ全部 false になり並びは変わらない。
    params.append(bigquery.ScalarQueryParameter("sp_code", "STRING", TARGET_CODE_SINGLE_PARENT))
    params.append(bigquery.ScalarQueryParameter("dis_code", "STRING", TARGET_CODE_DISABILITY))
    priority_order = []
    if is_single_parent:
        priority_order.append("is_single_parent_target DESC")
    if has_disability:
        priority_order.append("is_disability_target DESC")
    priority_clause = ("".join(f"{o},\n          " for o in priority_order)) if priority_order else ""

    query = f"""
        SELECT benefit_id, title, category, summary, area_name, area_code,
               effective_min_age_months, effective_max_age_months, age_source,
               is_prenatal, is_free, monetary_support_text, electronic_submission,
               has_free_text_conditions, conditions_text, official_url, scheme_id,
               @sp_code IN UNNEST(target_codes) AS is_single_parent_target,
               @dis_code IN UNNEST(target_codes) AS is_disability_target
        FROM `{PROJECT_ID}.{DATASET_ID}.benefits`
        {where_clause}
        ORDER BY
          {priority_clause}{AGE_SOURCE_ORDER_BY},
          effective_min_age_months NULLS LAST,
          title
        LIMIT @limit
    """
    params.append(bigquery.ScalarQueryParameter("limit", "INT64", limit))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = list(dependencies.get_client().query(query, job_config=job_config).result())

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
        # 分類コードは元データの値だが、コードが何を指すかの対応づけはこちらの推定
        # （公式マスタが非公開）。断定を避けた言い回しにする。
        if is_single_parent and r["is_single_parent_target"]:
            reasons.append("ひとり親家庭向けに分類されている制度")
        if has_disability and r["is_disability_target"]:
            reasons.append("障がいのあるお子さん向けに分類されている制度")

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
    # BigQuery Graph (GQL) は Enterprise 予約が必須になったため通常 SQL で書いている（docs/adr/0003）。
    query = f"""
        SELECT
          a.benefit_id AS from_id, a.title AS from_title,
          b.benefit_id AS to_id, b.title AS to_title,
          e.relation AS relation, e.reason AS reason
        FROM `{PROJECT_ID}.{DATASET_ID}.benefit_leads_to` e
        JOIN `{PROJECT_ID}.{DATASET_ID}.benefits` a ON a.benefit_id = e.from_benefit_id
        JOIN `{PROJECT_ID}.{DATASET_ID}.benefits` b ON b.benefit_id = e.to_benefit_id
        WHERE e.from_benefit_id IN UNNEST(@ids)
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
        for r in dependencies.get_client().query(query, job_config=job_config).result()
    ]
