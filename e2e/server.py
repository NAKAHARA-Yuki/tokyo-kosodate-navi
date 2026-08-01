"""E2E 用のアプリ起動スクリプト。

BigQuery と Gemini をスタブに差し替えた状態で FastAPI を起動する。
GCP 認証なしで CI からブラウザテストを回せるようにするためのもの。

    python e2e/server.py [port]
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "e2e"))

os.environ.setdefault("APP_ENV", "dev")

import fake_data  # noqa: E402


class _FakeJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return iter(self._rows)


class FakeBigQueryClient:
    """クエリ内容とパラメータに応じてスタブ行を返すだけのクライアント。"""

    def query(self, query, job_config=None):
        params = {}
        if job_config is not None:
            for p in getattr(job_config, "query_parameters", None) or []:
                params[p.name] = p.value
        return _FakeJob(fake_data.rows_for(query, params))


class _FakeResponse:
    text = (
        "【どんな制度か】\n3歳のお子さんの発育・発達を確認する健診です。\n\n"
        "【誰が対象か】\n3歳になったお子さん\n\n"
        "詳細は自治体窓口にご確認ください。"
    )


class _FakeModels:
    def generate_content(self, model, contents, config=None):
        return _FakeResponse()


class FakeGenaiClient:
    def __init__(self, *args, **kwargs):
        self.models = _FakeModels()


def build_app():
    import dependencies
    import main

    dependencies.get_client = lambda: FakeBigQueryClient()
    dependencies._build_genai_client = lambda: FakeGenaiClient()
    return main.app


if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    uvicorn.run(build_app(), host="127.0.0.1", port=port, log_level="warning")
