"""BigQuery へのデータセット作成・テーブルロード。"""

from config import DATASET_ID, LOCATION
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from etl_schema import TABLE_SCHEMAS, build_benefits_schema


def ensure_dataset(client: bigquery.Client, project_id: str):
    dataset_ref = bigquery.DatasetReference(project_id, DATASET_ID)
    try:
        client.get_dataset(dataset_ref)
        print(f"[bq] dataset {DATASET_ID} already exists", flush=True)
    except NotFound:
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = LOCATION
        client.create_dataset(dataset)
        print(f"[bq] created dataset {DATASET_ID} in {LOCATION}", flush=True)


def load_tables(client: bigquery.Client, project_id: str, tables: dict):
    for table_name, df in tables.items():
        table_id = f"{project_id}.{DATASET_ID}.{table_name}"
        schema = build_benefits_schema(df) if table_name == "benefits" else TABLE_SCHEMAS[table_name]
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=schema,
        )
        job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()
        print(f"[bq] loaded {len(df)} rows into {table_id}", flush=True)
