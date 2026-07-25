"""kosodate_graph に対して GQL クエリを発行し、動作検証を行う。

1. リレーション検証: 制度→条件→書類がたどれるか（従来通り）
2. 属性マッチ検証: ユーザープロフィール（居住地・子どもの月齢）を模した固定値で、
   Benefit ノードの構造化プロパティ（area_code / min_age_months / max_age_months）を
   直接 WHERE 句で絞り込めるか（データの持ち方見直しの本題）
"""

import os
import sys

from google.cloud import bigquery

LOCATION = "asia-northeast1"

RELATION_QUERY_TEMPLATE = """
GRAPH `{project_id}.gov_knowledge_db.kosodate_graph`
MATCH (b:Benefit)-[:REQUIRES]->(s:Status), (b)-[:REQUIRES_DOC]->(d:Document)
RETURN b.title AS benefit_title, s.name AS requirement, d.doc_name AS required_document
LIMIT 5
"""

# ユーザープロフィール想定: 台東区(131067)在住、子ども 3歳(36ヶ月)
ATTRIBUTE_MATCH_QUERY_TEMPLATE = """
GRAPH `{project_id}.gov_knowledge_db.kosodate_graph`
MATCH (b:Benefit)
WHERE b.area_code = @area_code
  AND (b.min_age_months IS NULL OR b.min_age_months <= @age_months)
  AND (b.max_age_months IS NULL OR b.max_age_months >= @age_months)
RETURN
  b.title AS title,
  b.category AS category,
  b.min_age_months AS min_age_months,
  b.max_age_months AS max_age_months,
  b.has_free_text_conditions AS has_free_text_conditions
ORDER BY title
LIMIT 10
"""


def run_relation_check(client: bigquery.Client, project_id: str):
    query = RELATION_QUERY_TEMPLATE.format(project_id=project_id)
    rows = list(client.query(query).result())
    print(f"[relation] {len(rows)} rows returned", flush=True)
    for row in rows:
        print(
            f"  benefit={row['benefit_title']!r} | requirement={row['requirement']!r} | "
            f"document={row['required_document']!r}"
        )


def run_attribute_match_check(client: bigquery.Client, project_id: str):
    query = ATTRIBUTE_MATCH_QUERY_TEMPLATE.format(project_id=project_id)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("area_code", "STRING", "131067"),  # 台東区
            bigquery.ScalarQueryParameter("age_months", "INT64", 36),  # 3歳
        ]
    )
    rows = list(client.query(query, job_config=job_config).result())
    print(
        f"[attribute_match] area=台東区(131067) age=3歳(36ヶ月) -> {len(rows)} 件(上位10件表示)", flush=True
    )
    for row in rows:
        flag = "※自由記述条件あり" if row["has_free_text_conditions"] else ""
        print(
            f"  {row['title']} [{row['category']}] "
            f"age={row['min_age_months']}〜{row['max_age_months']}ヶ月 {flag}"
        )


def main():
    project_id = os.environ.get("GCP_PROJECT_ID", "opendatahackathon-503500")
    print(f"[main] running verification queries against project {project_id}", flush=True)
    client = bigquery.Client(project=project_id, location=LOCATION)

    run_relation_check(client, project_id)
    run_attribute_match_check(client, project_id)

    print("[main] verification completed", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"[error] verification failed: {exc}", file=sys.stderr, flush=True)
        raise
