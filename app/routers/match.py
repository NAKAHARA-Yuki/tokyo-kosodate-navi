"""Phase2: ユーザープロフィールと制度マッチング。

- /api/user/profile   : ユーザー属性の検証・正規化
- /api/benefits/match : 属性から対象制度を一括取得（マッチ理由付き）
"""

from datetime import date

import dependencies
from config import DATASET_ID, PROJECT_ID
from fastapi import APIRouter, HTTPException, Query
from google.cloud import bigquery
from pydantic import BaseModel, Field
from queries import AGE_SOURCE_ORDER_BY, ages_filter_sql

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


def months_between(birth: date, today: date | None = None) -> int:
    """生年月日から満月齢を求める。誕生日が来ていない月は繰り上げない。"""
    today = today or date.today()
    months = (today.year - birth.year) * 12 + (today.month - birth.month)
    if today.day < birth.day:
        months -= 1
    return max(months, 0)


class ChildProfile(BaseModel):
    """子ども1人分。**生年月日を正とする。**

    月齢は時間が経てば変わるので、保存すると古くなる。マイナンバー由来のデータでは
    生年月日が取れる想定なので、そちらを持ち、月齢は都度計算する（issue #75）。
    生年月日が分からない場合のために月齢も受け取れるようにしてある。
    """

    birth_date: date | None = Field(default=None, description="生年月日")
    age_months: int | None = Field(default=None, ge=0, le=300, description="月齢（生年月日が不明なとき）")

    def resolved_age_months(self) -> int | None:
        if self.birth_date is not None:
            return months_between(self.birth_date)
        return self.age_months


class UserProfile(BaseModel):
    """設定画面で登録するユーザー属性。チャットではなく選択式フォームからの入力を想定。"""

    area_code: str | None = Field(default=None, description="居住地の市区町村コード")
    # きょうだいがいるのが普通なので配列で持つ。実データには第2子以降を条件に含む制度が
    # 306件（3.9%）ある。単数で持つと利用履歴（#31）も子ごとに持てない。
    children: list[ChildProfile] = Field(default_factory=list, description="子どもの一覧")
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

    children = []
    for child in profile.children:
        months = child.resolved_age_months()
        label = None
        if months is not None:
            years, rest = divmod(months, 12)
            label = f"{years}歳{rest}か月" if rest else f"{years}歳"
        children.append({"age_months": months, "age_label": label})

    return {
        "profile": profile.model_dump(mode="json"),
        "resolved": {"area_name": area_name, "children": children},
    }


@router.get("/api/benefits/match")
def match_benefits(
    area_code: str | None = Query(default=None, description="居住地の市区町村コード"),
    child_age_months: list[int] = Query(
        default=[], description="子どもの月齢。きょうだいの分だけ繰り返し指定できる"
    ),
    child_birth_date: list[date] = Query(
        default=[], description="子どもの生年月日。指定するとサーバ側で月齢に換算する"
    ),
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

    # 生年月日と月齢の両方を受け取れる。生年月日の方が正（月齢は時間で変わるため）。
    ages = [months_between(b) for b in child_birth_date] + list(child_age_months)
    if ages:
        # 妊娠中なら「妊娠期の制度」も対象に加える。
        # きょうだいのいずれかが当たれば結果に含める。
        conditions.append(ages_filter_sql("ages", include_prenatal=is_pregnant))
        params.append(bigquery.ArrayQueryParameter("ages", "INT64", ages))
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
        lo = r["effective_min_age_months"]
        hi = r["effective_max_age_months"]
        # どの子が当たったかを返す。きょうだいがいると「上の子だけ対象」が普通にあるため、
        # 「対象です」とだけ言われても誰のことか分からない。
        matched = [a for a in ages if (lo is None or lo <= a) and (hi is None or hi >= a)]
        if ages and (lo is not None or hi is not None) and matched:
            span = f"{lo if lo is not None else '-'}〜{hi if hi is not None else '-'}ヶ月"
            suffix = "（対象年齢は本文からの推定）" if r["age_source"] == "inferred" else ""
            who = "・".join(f"月齢{a}" for a in matched)
            reasons.append(f"お子さん（{who}）が対象範囲{span}に該当{suffix}")
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
                "matched_child_age_months": matched,
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
