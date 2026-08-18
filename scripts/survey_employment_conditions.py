#!/usr/bin/env python
"""就労要件がどう書かれているかを数える（docs/employment-conditions.md の計測）。

issue #126 は「就労・勤務・就職・求職・仕事を含む制度が 345件」としている。
**その中身が「就労しているか」なのかを測る。**

規則で抽出できる範囲を見積もるための調査用スクリプト。ETL の一部ではない。

    python scripts/survey_employment_conditions.py          # 既定は dev
    APP_ENV=staging python scripts/survey_employment_conditions.py
"""

from __future__ import annotations

import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from google.cloud import bigquery

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import config  # noqa: E402

# 就労要件を含むかどうかの一次判定。issue #126 が使った条件と同じ
MENTIONS_WORK = r"就労|勤務|就職|求職|仕事"

# 就労時間・日数のしきい値。「月48時間以上」「週3日以上」「1日4時間以上」
WORK_THRESHOLD_RE = re.compile(r"(月|週|1日|一日)\s*([0-9０-９]{1,3})\s*(時間|日)\s*以上")

CHILDCARE_RE = re.compile(r"保育|学童|預かり|クラブ|こども園|幼稚園")

# 事由の主体を切り分けるための補助。**「保護者の疾病」と「障害のある児童」は別のもの。**
SICKNESS_RE = re.compile(r"疾病|病気|障害")
GUARDIAN_SUBJECT_RE = re.compile(r"(?:保護者|父母|父|母|申請者|養育者)[^。]{0,20}?(?:疾病|病気|障害)")
CHILD_SUBJECT_RE = re.compile(
    r"(?:児童|子ども|お子|乳幼児)[^。]{0,15}?(?:疾病|病気|障害)"
    r"|(?:疾病|病気|障害)[^。]{0,10}?(?:のある|の)?(?:児童|子ども|お子)"
)

CATEGORIES: list[tuple[str, str]] = [
    ("① 就労時間のしきい値", WORK_THRESHOLD_RE.pattern),
    (
        "② 保育の必要性（就労等の事由）",
        r"就労(?:等|など)?(?:により|によって|のため|の理由)|保育を必要と|保育が困難|家庭(?:で|において)保育",
    ),
    ("③ 求職活動中", r"求職"),
    ("④ 就学・技能習得", r"就学|技能を?習得|職業訓練|養成機関"),
    ("⑤ 就労証明書（書類であって条件ではない）", r"就労証明書?|勤務証明書?|就労状況(?:届|申告)"),
    ("⑥ 育児休業", r"育児休業|育休"),
    ("⑦ ひとり親の就業支援", r"高等職業訓練|自立支援教育訓練|就業支援|母子家庭等就業"),
]

# 「保育の必要性」の標準事由（子ども・子育て支援法施行規則）。
# **就労はこのうちの1つでしかない。**
CARE_NEED_REASONS: list[tuple[str, str]] = [
    ("就労", r"就労"),
    ("疾病・障害", r"疾病|病気|障害"),
    ("就学", r"就学|技能"),
    ("介護・看護", r"介護|看護"),
    ("妊娠・出産", r"妊娠|出産"),
    ("求職活動", r"求職"),
    ("災害復旧", r"災害"),
    ("育児休業", r"育児休業|育休"),
    ("虐待・DV", r"虐待|DV"),
]


def fetch_rows(client: bigquery.Client, dataset: str) -> list[dict]:
    query = f"""
        SELECT
          title,
          scheme_id,
          CONCAT(IFNULL(conditions_text, ''), ' ', IFNULL(target_persons_text, '')) AS txt
        FROM `{dataset}.benefits`
        WHERE REGEXP_CONTAINS(
          CONCAT(IFNULL(conditions_text, ''), ' ', IFNULL(target_persons_text, '')),
          r'{MENTIONS_WORK}')
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

    schemes = len({r["scheme_id"] for r in rows if r["scheme_id"]})
    childcare = [r for r in rows if CHILDCARE_RE.search(r["title"] + r["txt"])]
    print(f"{config.DATASET_ID}: 就労の語を含む {total} 件（{schemes} スキーム）")
    print(f"  うち保育・学童系  {len(childcare):4} 件 ({len(childcare) / total * 100:4.1f}%)")
    print(f"  それ以外        {total - len(childcare):4} 件  ← ひとり親の就業支援が中心\n")

    hits: Counter[str] = Counter()
    unmatched: list[dict] = []
    for row in rows:
        matched = [name for name, pattern in CATEGORIES if re.search(pattern, row["txt"])]
        if matched:
            hits.update(matched)
        else:
            unmatched.append(row)

    print("── 書き方の分類（重複あり）────────")
    for name, _ in CATEGORIES:
        print(f"  {name:32} {hits[name]:4} 件 ({hits[name] / total * 100:4.1f}%)")
    print(f"  {'⑧ どれにも当たらない':32} {len(unmatched):4} 件 ({len(unmatched) / total * 100:4.1f}%)")

    print("\n── 就労時間のしきい値の実際 ──────")
    thresholds: Counter[str] = Counter()
    for row in rows:
        for m in WORK_THRESHOLD_RE.finditer(row["txt"]):
            # **NFKC で正規化してから数える。** 正規表現は全角も拾うが、
            # ラベルをそのまま使うと「月48時間以上」と「月４８時間以上」が別項目に割れ、
            # 国の下限である月48時間が過小に出る（レビューでの指摘）。
            label = unicodedata.normalize("NFKC", f"{m.group(1)}{m.group(2)}{m.group(3)}以上")
            thresholds[label] += 1
    with_threshold = sum(1 for r in rows if WORK_THRESHOLD_RE.search(r["txt"]))
    print(f"  （しきい値を持つ制度 {with_threshold} 件 / 出現 {sum(thresholds.values())} 回。単位が違う）")
    for label, count in thresholds.most_common(10):
        print(f"  {count:4}  {label}")

    print("\n── 保育の必要性の標準事由が本文に出る数 ──")
    print("  （就労はこのうちの1つでしかない）")
    reasons = Counter()
    for row in rows:
        for name, pattern in CARE_NEED_REASONS:
            if re.search(pattern, row["txt"]):
                reasons[name] += 1
    for name, count in reasons.most_common():
        print(f"  {count:4}  {name}")

    # **この数え方は主体を見ていない。** 「保護者の事由」以外（子どもが主語のもの、
    # 除外条件、融資制度の要件）も混ざるので、属性の規模を見積もるのには使えない。
    # 代表として疾病・障害を主語で切り分けて、どのくらいずれるかを出す（レビューでの指摘）。
    guardian = child = ambiguous = 0
    for row in rows:
        if not SICKNESS_RE.search(row["txt"]):
            continue
        if GUARDIAN_SUBJECT_RE.search(row["txt"]):
            guardian += 1
        elif CHILD_SUBJECT_RE.search(row["txt"]):
            child += 1
        else:
            ambiguous += 1
    print("\n── 主体を見るとどれだけ減るか（疾病・障害で確認）──")
    print(f"  一致 {guardian + child + ambiguous} 件")
    print(f"    保護者が主語   {guardian:4} 件  ← 属性の規模を見積もるならこちら")
    print(f"    子どもが主語   {child:4} 件  （除外条件を含む）")
    print(f"    どちらとも取れない {ambiguous:4} 件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
