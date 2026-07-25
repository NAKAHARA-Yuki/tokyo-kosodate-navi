"""環境（dev / staging / prod）ごとの設定。

GCP プロジェクトは1つのまま、**BigQuery データセットと Cloud Run サービスを環境ごとに分ける**方針。
プロジェクトを分けるのが理想だが、請求先や IAM の管理コストが跳ね上がるため、
まずはデータセット分離で「本番を壊さずに試せる」状態を作る。判断の背景は
docs/adr/0004-environments.md を参照。

ETL 側（src/）からも同じ設定を使うため、アプリ固有の依存は持たせない。
"""

import os

VALID_ENVS = ("dev", "staging", "prod")

# データセットとサービス名の対応。dev/staging は接尾辞で区別する。
_DATASETS = {
    "dev": "gov_knowledge_db_dev",
    "staging": "gov_knowledge_db_staging",
    "prod": "gov_knowledge_db",
}
_SERVICES = {
    "dev": "kosodate-graph-viewer-dev",
    "staging": "kosodate-graph-viewer-staging",
    "prod": "kosodate-graph-viewer",
}


def _resolve_env() -> str:
    """APP_ENV を検証して返す。未指定なら dev（誤って本番を触らないため）。"""
    env = os.environ.get("APP_ENV", "dev").strip().lower()
    if env not in VALID_ENVS:
        raise ValueError(f"APP_ENV は {VALID_ENVS} のいずれかにしてください（指定値: {env!r}）")
    return env


APP_ENV = _resolve_env()
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "opendatahackathon-503500")
LOCATION = "asia-northeast1"

DATASET_ID = os.environ.get("BQ_DATASET_ID") or _DATASETS[APP_ENV]
SERVICE_NAME = _SERVICES[APP_ENV]

GRAPH_NAME = f"{PROJECT_ID}.{DATASET_ID}.kosodate_graph"
IS_PROD = APP_ENV == "prod"


def table(name: str) -> str:
    """`project.dataset.table` の完全修飾名を返す。"""
    return f"{PROJECT_ID}.{DATASET_ID}.{name}"


def describe() -> str:
    return f"env={APP_ENV} project={PROJECT_ID} dataset={DATASET_ID}"
