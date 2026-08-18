#!/usr/bin/env python
"""「他制度を受けていること」が条件になっている関係を数える（docs/requires-benefit.md の計測）。

issue #124（REQUIRES_BENEFIT エッジ）を実装する前に、
**エッジにできるのか・何本引けるのか**を測る。

規則で抽出できる範囲を見積もるための調査用スクリプト。ETL の一部ではない。

    python scripts/survey_requires_benefit.py          # 既定は dev
    APP_ENV=staging python scripts/survey_requires_benefit.py
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from google.cloud import bigquery

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import config  # noqa: E402

# 制度名らしい語。「〜手当」「〜給付金」などで終わるまとまり
BENEFIT_NAME = r"(?P<name>[一-龥ぁ-んァ-ヶ]{2,18}?(?:手当|給付金|助成|医療証|受給者証))"

# **肯定**: その制度を受けていることが条件
REQUIRES_RE = re.compile(
    BENEFIT_NAME + r"(?:の支給)?を?(?:受けている|受給している|受給し[てい]|支給を受けて|認定を受けて)"
)

# **否定**: その制度を受けて「いない」ことが条件。**肯定とほぼ同じ形をしている。**
# これを肯定として拾うとエッジの意味が逆になる。
EXCLUDES_RE = re.compile(
    r"(?:過去に|既に|すでに)[^。]{0,30}?(?:給付金|手当|訓練給付金)[^。]{0,20}?"
    r"(?:受けたことがない|受給していない|受けていない)"
    r"|"
    + BENEFIT_NAME
    + r"[^。]{0,12}?(?:受給していない|受けていない|受けることができません|対象となりません)"
)

# 「過去にこの給付金」のような、制度名ではない捕捉を落とす
NOT_A_NAME_RE = re.compile(r"^(?:過去に|既に|すでに|同じ|本事業|この|当該)|過去")

HUB = "児童扶養手当"


def fetch_rows(client: bigquery.Client, dataset: str) -> list[dict]:
    query = f"""
        SELECT
          benefit_id, title, area_code, area_name,
          CONCAT(IFNULL(conditions_text, ''), ' ', IFNULL(target_persons_text, '')) AS txt
        FROM `{dataset}.benefits`
    """
    return [dict(row) for row in client.query(query).result()]


def main() -> int:
    client = bigquery.Client(project=config.PROJECT_ID)
    dataset = f"{config.PROJECT_ID}.{config.DATASET_ID}"
    rows = fetch_rows(client, dataset)
    if not rows:
        print("対象が0件。データセットを確認すること。")
        return 1

    excludes = [r for r in rows if EXCLUDES_RE.search(r["txt"])]

    by_area: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_area[row["area_code"]].append(row)

    sources: set[str] = set()
    refs: Counter[str] = Counter()
    resolved = unresolved = 0
    unresolved_names: Counter[str] = Counter()
    downstream: Counter[str] = Counter()
    per_area: Counter[str] = Counter()

    for row in rows:
        for m in REQUIRES_RE.finditer(row["txt"]):
            name = m.group("name")
            if NOT_A_NAME_RE.search(name):
                continue
            sources.add(row["benefit_id"])
            refs[name] += 1
            if [x for x in by_area[row["area_code"]] if name in x["title"]]:
                resolved += 1
                if HUB in name:
                    downstream[row["title"][:40]] += 1
                    per_area[row["area_name"]] += 1
            else:
                unresolved += 1
                unresolved_names[name] += 1

    print(f"{config.DATASET_ID}\n")
    print("── 肯定と否定 ──────────────────")
    print(f"  他制度を受けて**いる**ことが条件  {len(sources):4} 件")
    print(f"  他制度を受けて**いない**ことが条件 {len(excludes):4} 件  ← 形がほぼ同じ。逆に取ると誤判定")

    print("\n── エッジを引けるか ───────────────")
    print(f"  参照の総数                    {resolved + unresolved:4}")
    print(f"  同じ自治体の制度に解決できた      {resolved:4}")
    print(f"  解決できなかった               {unresolved:4}")
    for name, count in unresolved_names.most_common(5):
        print(f"      {count:3}  {name}")

    print("\n── 前提として挙げられる制度 ──────")
    for name, count in refs.most_common(8):
        print(f"  {count:4}  {name}")

    print(f"\n── {HUB} の下流（芋づるの中身）──")
    for title, count in downstream.most_common(10):
        print(f"  {count:4}  {title}")

    if per_area:
        values = sorted(per_area.values())
        print(f"\n── {HUB} を前提にする制度の自治体あたり件数 ──")
        print(f"  自治体数 {len(per_area)} / 中央値 {values[len(values) // 2]} / 最大 {max(values)}")
        print(f"  平均 {sum(values) / len(values):.1f} 件が芋づる式に申請できるようになる")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
