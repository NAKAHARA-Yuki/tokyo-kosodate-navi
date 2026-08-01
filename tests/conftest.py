"""API テスト用の共通フィクスチャ。

BigQuery をモックして、GCP 認証なしで CI からエンドポイントを検証できるようにする。
検証したいのは「発行されるクエリの条件」と「レスポンスの形」であり、
BigQuery そのものの挙動ではない。
"""

import os

import pytest

# main / config を import する前に環境を固定する。
# テストが誤って本番データセットを指す設定で走らないようにするため。
os.environ.setdefault("APP_ENV", "dev")

from fastapi.testclient import TestClient  # noqa: E402


class FakeRow(dict):
    """BigQuery の Row は dict ライクにアクセスできるので dict で代用する。"""


class FakeQueryJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return iter(self._rows)


class FakeBigQueryClient:
    """発行されたクエリを記録し、あらかじめ用意した行を返すだけのスタブ。"""

    def __init__(self):
        self.rows_to_return: list[FakeRow] = []
        self.queries: list[str] = []
        self.job_configs: list[object] = []

    def query(self, query, job_config=None):
        self.queries.append(query)
        self.job_configs.append(job_config)
        return FakeQueryJob(self.rows_to_return)

    # ---- テストから使うヘルパー ----

    def set_rows(self, rows):
        self.rows_to_return = [FakeRow(r) for r in rows]

    @property
    def last_query(self) -> str:
        assert self.queries, "クエリが1件も発行されていません"
        return self.queries[-1]

    def last_params(self) -> dict:
        """最後のクエリに渡されたパラメータを {name: value} で返す。"""
        config = self.job_configs[-1]
        if config is None or not getattr(config, "query_parameters", None):
            return {}
        return {p.name: p.value for p in config.query_parameters}


@pytest.fixture
def bq(monkeypatch):
    """dependencies.get_client() が返す BigQuery クライアントを差し替える。

    各ルーターは `dependencies.get_client()` とモジュール経由で呼ぶ約束になっているため、
    ここも dependencies モジュールの属性を差し替える（app/dependencies.py 参照）。
    """
    import dependencies

    fake = FakeBigQueryClient()
    monkeypatch.setattr(dependencies, "get_client", lambda: fake)
    return fake


@pytest.fixture
def client():
    import main

    return TestClient(main.app)
