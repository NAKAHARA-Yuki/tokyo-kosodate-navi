"""BigQuery / Gemini クライアントの生成。

各ルーターはこのモジュールを `import dependencies` した上で
`dependencies.get_client()` のように呼び出すこと。`from dependencies import get_client`
で名前を束縛すると、テストの `monkeypatch.setattr(dependencies, "get_client", ...)`
（tests/conftest.py）や e2e/server.py の差し替えが効かなくなる。
"""

from config import LOCATION, PROJECT_ID
from google.cloud import bigquery

# BigQuery クライアントは初回利用時に作る。
# import 時に生成すると GCP 認証のない環境（CI・テスト）でモジュールを読み込めなくなるため。
_bq_client: bigquery.Client | None = None


def get_client() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT_ID, location=LOCATION)
    return _bq_client


def _build_genai_client():
    """Gemini クライアントを生成する。テストから差し替えられるよう関数に切り出している。"""
    from google import genai

    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")
