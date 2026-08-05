"""
東京都「子育て支援制度レジストリ」JSON -> BigQuery ETL

ソースURLからJSONを直接ダウンロードし（ローカルファイルは一切使わない）、
3ノードテーブル（benefits, statuses, documents）と
2エッジテーブル（benefit_requires_status, benefit_requires_doc）に整形して
BigQuery にロードする。

処理は責務ごとに分割している:
- etl_util.py      : 汎用ヘルパー（ハッシュ、辞書アクセスなど）
- etl_normalize.py : 日付・時刻・郵便番号・埋め込みリンクの正規化
- etl_documents.py : 必要書類欄の分解・表記ゆれ統合
- etl_statuses.py  : AGE / LOCATION / TAG_* の status ノード生成
- etl_graph.py     : benefits 行の構築・スキルツリー生成・全体変換（transform）
- etl_schema.py    : BigQuery のテーブルスキーマ定義
- etl_quality.py   : ロード前のデータ品質チェック
- etl_load.py      : BigQuery へのロード

このファイルはエントリポイント（`python src/etl_to_bq.py`）として、
取得（fetch_json/extract_records）と全体の実行順序（main）のみを担う。
docs/data-model.md に整形仕様の詳細がある。
"""

import os
import sys

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# 環境（dev/staging/prod）ごとのデータセット定義はアプリと共通のものを使う
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "app"))
from config import APP_ENV, DATASET_ID, LOCATION  # noqa: E402
from google.cloud import bigquery  # noqa: E402

from etl_graph import transform  # noqa: E402
from etl_load import ensure_dataset, load_tables  # noqa: E402
from etl_quality import run_quality_checks  # noqa: E402

SOURCE_URL = "https://data.storage.data.metro.tokyo.lg.jp/digitalservice/130001_kosodateshienseido_tokyo.json"


def fetch_json(url: str):
    print(f"[fetch] downloading JSON from {url}", flush=True)
    resp = requests.get(url, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    print(f"[fetch] downloaded {len(resp.content):,} bytes", flush=True)
    return data


def extract_records(payload):
    """トップレベルが配列、または {'data': [...]} 等の場合を自動判別してレコード配列を返す。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "results", "items", "records"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    raise ValueError("Unsupported JSON top-level structure")


def main():
    project_id = os.environ.get("GCP_PROJECT_ID", "opendatahackathon-503500")
    # どの環境に書き込むかは事故防止のため必ず出す
    print(f"[main] env={APP_ENV} project={project_id} dataset={DATASET_ID}", flush=True)

    payload = fetch_json(SOURCE_URL)
    records = extract_records(payload)
    print(f"[main] extracted {len(records)} records", flush=True)

    tables = transform(records)

    client = bigquery.Client(project=project_id, location=LOCATION)
    ensure_dataset(client, project_id)

    # ロードの**前**に品質を見る。load_tables() はテーブルごとに WRITE_TRUNCATE するため、
    # 途中で落ちると「benefits だけ新しく statuses は古い」状態が残る。
    # 書く前に落とせばその状態自体を作らずに済む（issue #62）。
    run_quality_checks(client, project_id, tables)

    load_tables(client, project_id, tables)

    print("[main] ETL completed successfully", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"[error] ETL failed: {exc}", file=sys.stderr, flush=True)
        raise
