#!/usr/bin/env python
"""世帯要件がどう書かれているかを数える（docs/household-conditions.md の計測）。

issue #125 は「世帯・同居・扶養・生計を含む制度が 1,334件（17.1%）あり、
所得（833件）より大きい塊」としている。**その 1,334件が本当に世帯要件なのかを測る。**

規則で抽出できる範囲を見積もるための調査用スクリプト。ETL の一部ではない。

    python scripts/survey_household_conditions.py          # 既定は dev
    APP_ENV=staging python scripts/survey_household_conditions.py
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter
from pathlib import Path

from google.cloud import bigquery

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import config  # noqa: E402

# 世帯要件を含むかどうかの一次判定。issue #125 が使った条件と同じ
MENTIONS_HOUSEHOLD = r"世帯|同居|扶養|生計"

# **一次判定に掛かるが世帯要件ではないもの。**
# 「児童扶養手当」は制度名に「扶養」が入っているだけで、世帯構成の条件ではない。
# 「住民税非課税世帯」は所得の条件で、#76 の担当範囲。
BENEFIT_NAME_RE = re.compile(r"(?:特別)?児童扶養手当|扶養手当")
INCOME_WORLD_RE = re.compile(
    r"(?:住民税|市民税|区民税|町民税|村民税|市町村民税|特別区民税|地方税)[^。]{0,6}(?:非)?課税世帯"
    r"|非課税世帯|課税世帯|世帯所得|世帯の所得"
)

CATEGORIES: list[tuple[str, str]] = [
    ("A 同居・別居", r"同居|別居|同一の世帯|世帯を同じく|同一世帯"),
    ("B 生計同一", r"生計を(?:同じく|一に)|生計同一|生計を維持"),
    ("C 扶養・養育している", r"扶養して|扶養する|養育して|監護して"),
    ("D ひとり親・父母の状況", r"ひとり親|母子家庭|父子家庭|父又は母|父または母|配偶者のない"),
    ("E 世帯人数・多子", r"第[2-9２-９]子|多子|世帯に.{0,6}人以上|児童が.{0,4}人以上|多胎"),
    ("F 世帯全員が〜", r"世帯(?:員)?(?:全員|の全員|のすべて)"),
    ("G 扶養義務者・親族", r"扶養義務者|扶養親族|直系親族|同居の親族"),
]

# すでにプロフィールが持っている属性で表せるもの
ALREADY_SINGLE_PARENT_RE = re.compile(r"ひとり親|母子家庭|父子家庭|配偶者のない")  # #77
ALREADY_CHILD_COUNT_RE = re.compile(r"第[2-9２-９]子|多子|多胎|児童が.{0,4}人以上")  # #75
OTHER_HOUSEHOLD_RE = re.compile(
    r"同居|別居|生計を(?:同じく|一に)|扶養して|養育して|監護して"
    r"|世帯(?:員)?(?:全員|の全員)|扶養義務者|扶養親族"
)


def denoise(text: str) -> str:
    """制度名と課税世帯（＝所得の話）を伏せる。伏せてもまだ世帯の語が残るかを見るため。"""
    return INCOME_WORLD_RE.sub("〇", BENEFIT_NAME_RE.sub("〇", text))


def fetch_rows(client: bigquery.Client, dataset: str) -> list[dict]:
    query = f"""
        SELECT
          title,
          scheme_id,
          CONCAT(IFNULL(conditions_text, ''), ' ', IFNULL(target_persons_text, '')) AS txt
        FROM `{dataset}.benefits`
        WHERE REGEXP_CONTAINS(
          CONCAT(IFNULL(conditions_text, ''), ' ', IFNULL(target_persons_text, '')),
          r'{MENTIONS_HOUSEHOLD}')
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

    print(f"{config.DATASET_ID}: 世帯の語を含む {total} 件\n")

    # --- 1. そもそも世帯要件なのか ---
    real = [r for r in rows if re.search(MENTIONS_HOUSEHOLD, denoise(r["txt"]))]
    noise = total - len(real)
    print("── 一次判定の内訳 ─────────────────")
    print(
        f"  制度名・課税世帯を伏せると消える  {noise:5} 件 ({noise / total * 100:4.1f}%)  ← 世帯要件ではない"
    )
    print(f"  伏せても世帯の語が残る          {len(real):5} 件 ({len(real) / total * 100:4.1f}%)")

    # --- 2. 同じ条件文の使い回し ---
    schemes = len({r["scheme_id"] for r in rows if r["scheme_id"]})
    sigs = Counter(hashlib.md5(re.sub(r"\s+", "", r["txt"]).encode()).hexdigest() for r in rows)
    titles = Counter(r["title"] for r in rows)
    print("\n── 実際にはいくつの制度なのか ───────")
    print(f"  異なる scheme_id                {schemes:5}")
    print(f"  異なる条件文（空白を潰して一致）   {len(sigs):5}")
    print("  制度名の上位:")
    for name, count in titles.most_common(5):
        print(f"      {count:4} 件  {name[:40]}")

    # --- 3. 書き方の分類 ---
    hits: Counter[str] = Counter()
    unmatched: list[dict] = []
    for row in real:
        text = denoise(row["txt"])
        matched = [name for name, pattern in CATEGORIES if re.search(pattern, text)]
        if matched:
            hits.update(matched)
        else:
            unmatched.append(row)

    print(f"\n── 書き方の分類（{len(real)}件・重複あり）──")
    for name, _ in CATEGORIES:
        print(f"  {name:24} {hits[name]:5} 件 ({hits[name] / len(real) * 100:4.1f}%)")
    print(f"  {'H どれにも当たらない':24} {len(unmatched):5} 件 ({len(unmatched) / len(real) * 100:4.1f}%)")

    # --- 4. いま持っている属性で足りるか ---
    single = child_count = other_only = 0
    for row in real:
        text = denoise(row["txt"])
        is_single = bool(ALREADY_SINGLE_PARENT_RE.search(text))
        is_multi = bool(ALREADY_CHILD_COUNT_RE.search(text))
        single += is_single
        child_count += is_multi
        if OTHER_HOUSEHOLD_RE.search(text) and not is_single and not is_multi:
            other_only += 1

    print("\n── すでに持っている属性で表せるか ───")
    print(f"  ひとり親（#77 で判定済み）        {single:5} 件")
    print(f"  子どもの人数（#75 で複数対応済み）  {child_count:5} 件")
    print(f"  それ以外の世帯要件だけを持つもの    {other_only:5} 件  ← 新しい属性が要るのはここ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
