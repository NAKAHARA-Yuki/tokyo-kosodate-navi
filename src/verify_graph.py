"""kosodate_graph のノード/エッジテーブルに SQL クエリを発行し、動作検証を行う。

1. リレーション検証: 制度→条件→書類がたどれるか
2. 属性マッチ検証: ユーザープロフィール（居住地・子どもの月齢）を模した固定値で、
   Benefit ノードの構造化プロパティを直接 WHERE 句で絞り込めるか
3. スキルツリー検証: 制度から制度への LEADS_TO エッジがたどれるか

対象データセットは APP_ENV（dev / staging / prod）で切り替わる。

BigQuery Graph (GQL) は Enterprise 予約が必須になったため、ここでは通常 SQL で
ノード/エッジテーブルを直接 JOIN している（経緯は docs/adr/0003）。
PROPERTY GRAPH の定義自体は create_graph.sql に残しており、データモデルは変わらない。
"""

import os
import sys
from pathlib import Path

from google.cloud import bigquery

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from config import APP_ENV, DATASET_ID, GRAPH_NAME, LOCATION, PROJECT_ID  # noqa: E402

RELATION_QUERY = f"""
SELECT b.title AS benefit_title, s.name AS requirement, d.doc_name AS required_document
FROM `{PROJECT_ID}.{DATASET_ID}.benefit_requires_status` brs
JOIN `{PROJECT_ID}.{DATASET_ID}.benefits` b ON b.benefit_id = brs.benefit_id
JOIN `{PROJECT_ID}.{DATASET_ID}.statuses` s ON s.status_id = brs.status_id
JOIN `{PROJECT_ID}.{DATASET_ID}.benefit_requires_doc` brd ON brd.benefit_id = b.benefit_id
JOIN `{PROJECT_ID}.{DATASET_ID}.documents` d ON d.doc_id = brd.doc_id
LIMIT 5
"""

# ユーザープロフィール想定: 台東区(131067)在住、子ども 3歳(36ヶ月)
# 年齢は effective_*（明示値がなければ推定値）で絞る。素の min/max だと6割超が NULL で素通りする。
ATTRIBUTE_MATCH_QUERY = f"""
SELECT
  title,
  category,
  effective_min_age_months AS min_age_months,
  effective_max_age_months AS max_age_months,
  age_source,
  has_free_text_conditions
FROM `{PROJECT_ID}.{DATASET_ID}.benefits`
WHERE area_code = @area_code
  AND (effective_min_age_months IS NULL OR effective_min_age_months <= @age_months)
  AND (effective_max_age_months IS NULL OR effective_max_age_months >= @age_months)
ORDER BY title
LIMIT 10
"""

SKILL_TREE_QUERY = f"""
SELECT a.title AS src, b.title AS dst, e.relation AS relation, e.reason AS reason
FROM `{PROJECT_ID}.{DATASET_ID}.benefit_leads_to` e
JOIN `{PROJECT_ID}.{DATASET_ID}.benefits` a ON a.benefit_id = e.from_benefit_id
JOIN `{PROJECT_ID}.{DATASET_ID}.benefits` b ON b.benefit_id = e.to_benefit_id
WHERE a.area_name = "台東区"
ORDER BY relation, src
LIMIT 5
"""


def run_relation_check(client: bigquery.Client):
    rows = list(client.query(RELATION_QUERY).result())
    print(f"[relation] {len(rows)} rows returned", flush=True)
    for row in rows:
        print(
            f"  benefit={row['benefit_title']!r} | requirement={row['requirement']!r} | "
            f"document={row['required_document']!r}"
        )


def run_attribute_match_check(client: bigquery.Client):
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("area_code", "STRING", "131067"),  # 台東区
            bigquery.ScalarQueryParameter("age_months", "INT64", 36),  # 3歳
        ]
    )
    rows = list(client.query(ATTRIBUTE_MATCH_QUERY, job_config=job_config).result())
    print(f"[attribute_match] area=台東区(131067) age=3歳(36ヶ月) -> {len(rows)} 件", flush=True)
    for row in rows:
        flag = "※自由記述条件あり" if row["has_free_text_conditions"] else ""
        est = "（推定）" if row["age_source"] == "inferred" else ""
        print(
            f"  {row['title']} [{row['category']}] "
            f"age={row['min_age_months']}〜{row['max_age_months']}ヶ月{est} {flag}"
        )


def run_skill_tree_check(client: bigquery.Client):
    rows = list(client.query(SKILL_TREE_QUERY).result())
    print(f"[skill_tree] {len(rows)} rows returned", flush=True)
    for row in rows:
        print(f"  [{row['relation']}] {row['src']} → {row['dst']}（{row['reason']}）")


def main():
    project_id = os.environ.get("GCP_PROJECT_ID", "opendatahackathon-503500")
    print(f"[main] env={APP_ENV} graph={GRAPH_NAME}", flush=True)
    client = bigquery.Client(project=project_id, location=LOCATION)

    run_relation_check(client)
    run_attribute_match_check(client)
    run_skill_tree_check(client)

    print("[main] verification completed", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"[error] verification failed: {exc}", file=sys.stderr, flush=True)
        raise
