#!/usr/bin/env python
"""所得条件がどう書かれているかを数える（docs/income-conditions.md の計測）。

規則で抽出できる範囲を見積もるための調査用スクリプト。ETL の一部ではない。

    python scripts/survey_income_conditions.py          # 既定は dev
    APP_ENV=staging python scripts/survey_income_conditions.py
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from google.cloud import bigquery

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import config  # noqa: E402

# 所得条件を含むかどうかの判定。ここに掛からないものは対象外
MENTIONS_INCOME = r"所得|課税|収入|年収"

# 金額の表記。「46万円」「23万5千円」「600万円」「1,000円」を拾う
MONEY = r"[0-9０-９][0-9０-９,，]*\s*(?:億|万|千)?\s*[0-9０-９]*\s*(?:万|千)?円"

PATTERNS: list[tuple[str, str]] = [
    ("① 金額が本文に書いてある", MONEY),
    (
        "② 住民税の課税/非課税・所得割",
        r"(住民税|市民税|区民税|町民税|村民税|市区町村民税|特別区民税|市町村民税|地方税)"
        r".{0,8}(非課税|課税|所得割)",
    ),
    (
        "③ 他制度の受給が条件",
        r"(手当|給付金|助成|医療証|受給者証).{0,12}(受給|受けている|認定を受け)"
        r"|同等の所得水準|同様の所得水準",
    ),
    ("④ 生活保護の受給", r"生活保護"),
    ("⑤ 所得制限・限度額（額は別表）", r"所得制限|所得限度額|限度額表|限度額を(超|こえ)"),
    ("⑥ 扶養人数で変わる", r"扶養(親族)?の?(人数|数)|扶養人数|扶養している人数"),
    ("⑦ 所得区分・階層", r"所得(区分|階層|段階)|階層区分|世帯の?区分"),
]


def fetch_rows(client: bigquery.Client, dataset: str) -> list[dict]:
    query = f"""
        SELECT
          title,
          CONCAT(IFNULL(conditions_text, ''), ' ', IFNULL(target_persons_text, '')) AS txt
        FROM `{dataset}.benefits`
        WHERE REGEXP_CONTAINS(
          CONCAT(IFNULL(conditions_text, ''), ' ', IFNULL(target_persons_text, '')),
          r'{MENTIONS_INCOME}')
    """
    return [dict(row) for row in client.query(query).result()]


def main() -> int:
    client = bigquery.Client(project=config.PROJECT_ID)
    dataset = f"{config.PROJECT_ID}.{config.DATASET_ID}"
    rows = fetch_rows(client, dataset)
    total = len(rows)
    if total == 0:
        print("対象が0件。データセットを確認すること。")
        return 1

    hits: Counter[str] = Counter()
    unmatched: list[dict] = []
    for row in rows:
        matched = [name for name, pattern in PATTERNS if re.search(pattern, row["txt"])]
        if matched:
            hits.update(matched)
        else:
            unmatched.append(row)

    print(f"{config.DATASET_ID}: 所得条件に言及する {total} 件（1件が複数分類に該当しうる）\n")
    for name, _ in PATTERNS:
        print(f"  {name:24} {hits[name]:4} 件 ({hits[name] / total * 100:4.1f}%)")
    print(f"  {'⑧ どれにも当たらない':24} {len(unmatched):4} 件 ({len(unmatched) / total * 100:4.1f}%)")

    covered = total - len(unmatched)
    print(f"\n何らかの型に掛かった: {covered} / {total} ({covered / total * 100:.1f}%)")

    # しきい値が本文に無いもの。抽出方法によらず数値を取り出せない
    money_re, tax_re = MONEY, PATTERNS[1][1]
    no_threshold = [r for r in rows if not re.search(money_re, r["txt"]) and not re.search(tax_re, r["txt"])]
    print(
        f"しきい値が本文に無い: {len(no_threshold)} 件 "
        f"({len(no_threshold) / total * 100:.1f}%) ← 規則でも LLM でも取り出せない"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
