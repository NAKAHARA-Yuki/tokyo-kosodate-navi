"""複数のルーターで重複していたSQL断片。

年齢での絞り込みは `effective_min/max_age_months`（明示値がなければ推定値）を使うこと。
素の `min/max_age_months` は6割超が NULL で、それだけで絞ると
「10歳なのに新生児向けの制度が出る」といった取りこぼしが起きる（CLAUDE.md参照）。

`age_filter_sql()` は routers/benefits.py の search_benefits で使う
「単一の年齢が範囲内か」の判定。routers/match.py の match_benefits は
きょうだいに対応したため `ages_filter_sql()`（配列版）を使う。
routers/timeline.py の get_timeline は
ライフステージという「範囲」との重複判定で、NULLの扱いも逆（NULLを許容ではなく除外）のため
構造的に別物であり、ここでは共通化していない。
"""

# 年齢が明示されている制度（確度が高い）を先に、推定・不明なものを後に出す。
# `corrected` は元データの年齢欄が制度名と食い違ったため制度名を採ったもの（issue #114）。
# 確度は推定と同じ扱いにする（こちらの読み取りに依っているため）。
AGE_SOURCE_ORDER_BY = (
    "CASE age_source WHEN 'explicit' THEN 0 WHEN 'inferred' THEN 1 WHEN 'corrected' THEN 1 ELSE 2 END"
)


def max_age_expr(has_disability: bool = False) -> str:
    """年齢の上限に使う式。

    **障害があると答えた人にだけ広い方を使う**（issue #157）。制度本文が
    「原則18年度末まで。ただし障害のある児童は20歳未満」と二段構えで書いている場合、
    前段だけを見ると 18〜19歳で障害のあるお子さんを持つ人に制度が出ない。

    `disability_max_age_months` は広い側にしか入らない（ETL 側で担保）。
    NULL の制度では従来どおり `effective_max_age_months` を見る。
    """
    if has_disability:
        return "IFNULL(disability_max_age_months, effective_max_age_months)"
    return "effective_max_age_months"


def age_filter_sql(param_name: str, include_prenatal: bool = False, has_disability: bool = False) -> str:
    """`@{param_name}` の年齢が effective_min/max_age_months の範囲内かを判定するSQL断片。

    include_prenatal=True の場合、妊娠期の制度（is_prenatal）も対象に加える。
    """
    upper = max_age_expr(has_disability)
    clause = (
        f"(effective_min_age_months IS NULL OR effective_min_age_months <= @{param_name}) "
        f"AND ({upper} IS NULL OR {upper} >= @{param_name})"
    )
    if include_prenatal:
        return f"(({clause}) OR is_prenatal)"
    return f"({clause})"


def ages_filter_sql(param_name: str, include_prenatal: bool = False, has_disability: bool = False) -> str:
    """`@{param_name}`（月齢の配列）の **いずれかの子** が範囲内かを判定するSQL断片。

    きょうだいがいる場合、上の子でも下の子でも当たる制度は結果に含める。
    どの子が当たったかは Python 側で突き合わせて返す（`matched_children`）。
    """
    upper = max_age_expr(has_disability)
    clause = (
        f"EXISTS(SELECT 1 FROM UNNEST(@{param_name}) AS a "
        "WHERE (effective_min_age_months IS NULL OR effective_min_age_months <= a) "
        f"AND ({upper} IS NULL OR {upper} >= a))"
    )
    if include_prenatal:
        return f"(({clause}) OR is_prenatal)"
    return f"({clause})"
