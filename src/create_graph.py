"""src/create_graph.sql の {{PROJECT_ID}} を実プロジェクトIDに置換して BigQuery 上で実行する。"""

import os
import sys
from pathlib import Path

from google.cloud import bigquery

SQL_PATH = Path(__file__).parent / "create_graph.sql"
LOCATION = "asia-northeast1"


def main():
    project_id = os.environ.get("GCP_PROJECT_ID", "opendatahackathon-503500")
    sql = SQL_PATH.read_text(encoding="utf-8").replace("{{PROJECT_ID}}", project_id)

    print(f"[main] creating property graph in project {project_id}", flush=True)
    client = bigquery.Client(project=project_id, location=LOCATION)
    job = client.query(sql)
    job.result()
    print("[main] property graph kosodate_graph created successfully", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"[error] graph creation failed: {exc}", file=sys.stderr, flush=True)
        raise
