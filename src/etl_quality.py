"""ロード前のデータ品質チェック。

元データは東京都が更新するもので、こちらの都合とは無関係に構造が変わりうる。
`transform()` は例外を投げずに「空のテーブル」や「全部 NULL の列」を作れてしまうため、
そのまま流すと**壊れたデータが黙って本番に入る**。実際に検知したいのは次のようなもの。

- 件数の急変（7,812件が急に3,000件になる、0件になる）
- 必須列の NULL 率の上昇（`title` が空、`area_code` が取れない）
- `age_source='unknown'` の比率が跳ね上がる
  （`src/age_rules.py` の正規表現が元データの表現変更に追従できていないサイン）
- エッジの消失（`benefit_leads_to` が 6,192 → 0 など）

**チェックはロードの前に行う。** `load_tables()` はテーブルごとに WRITE_TRUNCATE するので、
途中で落ちると「benefits だけ新しく statuses は古い」という中途半端な状態が残る。
書く前に落とせば、その状態自体を作らずに済む（issue #62）。

前回値は**いま BigQuery に入っているデータそのもの**から読む。専用のメタテーブルを
持たなくても、直前の ETL 結果がそこにあるため。初回（テーブルが無い）は比較を飛ばす。
"""

import pandas as pd
from config import DATASET_ID
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

# 件数がこの割合を超えて減ったら止める。
# 元データの制度が一度に3割も消えることは運用上考えにくく、
# 消えたとすれば取得か整形の失敗を疑うべき、という基準。
# 増加は止めない（自治体の追加で普通に増えるため）。
MAX_ROW_DECREASE_RATIO = 0.3

# 空になってはいけないテーブル。1件でも入っていなければ落とす。
REQUIRED_NON_EMPTY = (
    "benefits",
    "statuses",
    "documents",
    "schemes",
    "benefit_requires_status",
    "benefit_requires_doc",
    "benefit_in_scheme",
    "benefit_leads_to",
)

# 「この列がこの割合を超えて NULL/空なら止める」。
# 0.0 は「1件でも欠けたら止める」。実測値（dev）を基準に、少し余裕を持たせている。
MAX_NULL_RATIO = {
    "benefits": {
        "benefit_id": 0.0,  # 主キー。PROPERTY GRAPH の前提でもある
        "title": 0.0,  # 実測 0%。画面の見出しに使うので欠けたら困る
        "scheme_id": 0.0,  # 実測 0%
        "area_code": 0.05,  # 実測 0%。地域で絞れなくなるため厳しめ
        "description": 0.10,  # 実測 2.7%
        "summary": 0.10,  # 実測 0%
    },
    "statuses": {"status_id": 0.0, "name": 0.0, "type": 0.0},
    "documents": {"doc_id": 0.0, "doc_name": 0.0},
}

# age_source='unknown' の比率の上限。実測 34.2%（2,672/7,812）。
# ここが跳ね上がったら age_rules.py が元データの表現変更に追従できていない。
MAX_UNKNOWN_AGE_RATIO = 0.45

# 自治体数の下限。実測 63。区市町村の統廃合があっても急には減らない。
MIN_AREA_COUNT = 55


class QualityCheckError(Exception):
    """品質チェックに落ちたことを表す。ロードを行わずに ETL を止める。"""


def _null_ratio(df, column: str) -> float:
    """NULL と空文字の割合。BigQuery 側で両者を区別していない列があるためまとめて見る。"""
    if len(df) == 0:
        return 1.0
    series = df[column]
    missing = series.isna()
    # dtype で分岐するとき `== object` だけを見てはいけない。pandas 3 では文字列列の
    # dtype が `str` になり、`object` との比較が False になるため空文字を取りこぼす。
    if pd.api.types.is_string_dtype(series) or pd.api.types.is_object_dtype(series):
        missing = missing | (series.fillna("").astype(str).str.strip() == "")
    return float(missing.sum()) / len(df)


def previous_row_counts(client: bigquery.Client, project_id: str, table_names) -> dict:
    """いま BigQuery に入っている件数を返す。テーブルが無いものは含めない。

    初回実行やデータセットを作り直した直後は空の dict が返り、件数比較は行われない。
    """
    counts = {}
    for name in table_names:
        try:
            table = client.get_table(f"{project_id}.{DATASET_ID}.{name}")
        except NotFound:
            continue
        counts[name] = table.num_rows
    return counts


def check_tables(tables: dict, previous_counts: dict | None = None) -> list[str]:
    """問題点を文字列のリストで返す。空なら合格。

    例外ではなくリストで返すのは、**1つ目で止めずに全部出す**ため。
    元データの構造が変わったときは複数の指標が同時に壊れるので、
    1件ずつ潰しては再実行するより一覧で見えた方が原因にたどり着きやすい。
    """
    problems: list[str] = []
    previous_counts = previous_counts or {}

    for name in REQUIRED_NON_EMPTY:
        if name not in tables:
            problems.append(f"{name}: テーブルが生成されていない")
        elif len(tables[name]) == 0:
            problems.append(f"{name}: 0件（空のテーブルはロードしない）")

    for name, df in tables.items():
        before = previous_counts.get(name)
        if before is None or before == 0 or len(df) == 0:
            continue
        decrease = (before - len(df)) / before
        if decrease > MAX_ROW_DECREASE_RATIO:
            problems.append(
                f"{name}: 件数が {before:,} → {len(df):,} と {decrease:.1%} 減った"
                f"（上限 {MAX_ROW_DECREASE_RATIO:.0%}）"
            )

    for name, columns in MAX_NULL_RATIO.items():
        df = tables.get(name)
        if df is None or len(df) == 0:
            continue
        for column, limit in columns.items():
            if column not in df.columns:
                problems.append(f"{name}.{column}: 列が無くなっている")
                continue
            ratio = _null_ratio(df, column)
            if ratio > limit:
                problems.append(f"{name}.{column}: 欠損 {ratio:.1%}（上限 {limit:.0%}）")

    benefits = tables.get("benefits")
    if benefits is not None and len(benefits) > 0:
        if "age_source" in benefits.columns:
            unknown = float((benefits["age_source"] == "unknown").sum()) / len(benefits)
            if unknown > MAX_UNKNOWN_AGE_RATIO:
                problems.append(
                    f"benefits.age_source: unknown が {unknown:.1%}"
                    f"（上限 {MAX_UNKNOWN_AGE_RATIO:.0%}）。"
                    "age_rules.py が元データの表現変更に追従できていない可能性がある"
                )
        if "area_code" in benefits.columns:
            areas = benefits["area_code"].dropna().nunique()
            if areas < MIN_AREA_COUNT:
                problems.append(f"benefits.area_code: 自治体が {areas} 件（下限 {MIN_AREA_COUNT}）")

    return problems


def run_quality_checks(client: bigquery.Client, project_id: str, tables: dict) -> None:
    """チェックして、問題があれば QualityCheckError を投げる（ロードは行われない）。"""
    previous = previous_row_counts(client, project_id, tables.keys())
    if previous:
        print(f"[quality] 前回の件数と比較する: {previous}", flush=True)
    else:
        print("[quality] 既存テーブルが無いため件数比較は行わない（初回実行）", flush=True)

    problems = check_tables(tables, previous)
    if problems:
        detail = "\n".join(f"  - {p}" for p in problems)
        raise QualityCheckError(
            f"データ品質チェックに失敗したため、ロードを中止した（{len(problems)}件）:\n{detail}"
        )

    print(f"[quality] {len(tables)} テーブルすべてが基準を満たした", flush=True)
