"""src/create_graph.sql のプレースホルダを実値に置換して BigQuery 上で実行する。

対象データセットは APP_ENV（dev / staging / prod）で切り替わる。
"""

import os
import sys
from pathlib import Path

from google.cloud import bigquery

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from config import APP_ENV, DATASET_ID, LOCATION  # noqa: E402

SQL_PATH = Path(__file__).parent / "create_graph.sql"


def main():
    project_id = os.environ.get("GCP_PROJECT_ID", "opendatahackathon-503500")
    sql = (
        SQL_PATH.read_text(encoding="utf-8")
        .replace("{{PROJECT_ID}}", project_id)
        .replace("{{DATASET}}", DATASET_ID)
    )

    print(f"[main] env={APP_ENV} project={project_id} dataset={DATASET_ID}", flush=True)
    client = bigquery.Client(project=project_id, location=LOCATION)
    client.query(sql).result()
    print("[main] property graph kosodate_graph created successfully", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"[error] graph creation failed: {exc}", file=sys.stderr, flush=True)
        raise
