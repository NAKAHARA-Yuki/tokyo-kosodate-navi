"""ロード前の退避（issue #160）。

ETL は8テーブルを全置換する。**戻す手段が用意されていなかった。**
いま戻せるのはタイムトラベルだけで、それも7日で切れる。

BigQuery は使わない（クライアントを差し替えて、発行される SQL と
飛ばす判断だけを見る）。
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from google.cloud.exceptions import NotFound

from etl_snapshot import (
    SNAPSHOT_EXPIRATION_DAYS,
    SNAPSHOT_PREFIX,
    snapshot_suffix,
    snapshot_tables,
)

FIXED_NOW = datetime(2026, 8, 22, 8, 15, 0, tzinfo=UTC)


class FakeClient:
    """存在するテーブルだけを持つクライアント。発行された SQL を記録する。"""

    def __init__(self, existing=(), existing_datasets=()):
        self.existing = set(existing)
        self.datasets = set(existing_datasets)
        self.queries: list[str] = []
        self.created_datasets: list[str] = []
        # 退避が権限不足で落ちる状況を再現する（実際に起きた）
        self.raise_on_query: Exception | None = None

    def get_table(self, table_id):
        name = table_id.split(".")[-1]
        if name not in self.existing:
            raise NotFound(table_id)
        return object()

    def get_dataset(self, ref):
        if ref.dataset_id not in self.datasets:
            raise NotFound(ref.dataset_id)
        return object()

    def create_dataset(self, dataset):
        self.created_datasets.append(dataset.dataset_id)
        self.datasets.add(dataset.dataset_id)
        return dataset

    def query(self, sql):
        self.queries.append(sql)
        raise_on_query = self.raise_on_query

        class _Job:
            def result(self_inner):
                if raise_on_query is not None:
                    raise raise_on_query
                return None

        return _Job()


class TestSnapshotTables:
    def test_snapshots_existing_tables(self):
        client = FakeClient(existing={"benefits", "documents"})
        created = snapshot_tables(client, "proj", ["benefits", "documents"], now=FIXED_NOW)
        assert len(created) == 2
        assert all("CREATE SNAPSHOT TABLE" in q for q in client.queries)
        assert any("benefits_20260822T081500Z" in q for q in client.queries)

    def test_skips_tables_that_do_not_exist_yet(self):
        """**初回実行を止めない。** ここで落ちると最初の ETL が永久に通らない。"""
        client = FakeClient(existing=set())
        assert snapshot_tables(client, "proj", ["benefits"], now=FIXED_NOW) == []
        assert client.queries == []

    def test_partial_existence(self):
        client = FakeClient(existing={"benefits"})
        created = snapshot_tables(client, "proj", ["benefits", "documents"], now=FIXED_NOW)
        assert len(created) == 1
        assert "benefits" in created[0]

    def test_uses_the_snapshot_prefix(self):
        """**接頭辞で見分ける。** `make cleanup` はこれを見て残す。"""
        client = FakeClient(existing={"benefits"})
        created = snapshot_tables(client, "proj", ["benefits"], now=FIXED_NOW)
        assert created[0].split(".")[-1].startswith(SNAPSHOT_PREFIX)

    def test_does_not_need_dataset_creation(self):
        """**同じデータセットに置く。**

        別データセットにすると `bigquery.datasets.create` が要り、手元の
        `claude-dev` では 403 になる（実測）。壊れたときに試せない復旧手順は
        無いのと同じなので、権限を増やさずに済む形にしている。
        """
        client = FakeClient(existing={"benefits"})
        snapshot_tables(client, "proj", ["benefits"], now=FIXED_NOW)
        assert client.created_datasets == []

    def test_sets_an_expiration(self):
        """**放置すると溜まり続ける。** 保持期間は SQL に必ず入れる。"""
        client = FakeClient(existing={"benefits"})
        snapshot_tables(client, "proj", ["benefits"], now=FIXED_NOW)
        assert "expiration_timestamp" in client.queries[0]
        # 2026-08-22 + 30日 = 2026-09-21
        assert "2026-09-21" in client.queries[0]
        assert SNAPSHOT_EXPIRATION_DAYS == 30

    def test_all_tables_share_one_suffix(self):
        """**同じ時点として戻せること。** テーブルごとに時刻がずれると揃わない。"""
        client = FakeClient(existing={"benefits", "documents"})
        created = snapshot_tables(client, "proj", ["benefits", "documents"], now=FIXED_NOW)
        suffixes = {name.rsplit("_", 1)[-1] for name in created}
        assert len(suffixes) == 1


class TestSnapshotSuffix:
    def test_is_utc(self):
        """実行は GitHub Actions（UTC）なので、名前も UTC に揃える。"""
        assert snapshot_suffix(FIXED_NOW) == "20260822T081500Z"


class TestSnapshotHappensBeforeLoad:
    """**退避はロードの前**でなければ意味が無い（PR #162 のレビュー）。

    順序が入れ替わると、退避される内容が「壊す直前の状態」ではなく
    「壊した後の状態」になる。**仕組み全体が黙って無意味になり、
    気づくのは実際に戻そうとした最悪のタイミング**。
    """

    def test_order_is_quality_then_snapshot_then_load(self):
        import etl_to_bq

        order: list[str] = []
        with (
            patch.object(etl_to_bq, "fetch_json", return_value=[{"id": "1"}]),
            patch.object(etl_to_bq, "bigquery") as mock_bq,
            patch.object(etl_to_bq, "transform", return_value={"benefits": "df"}),
            patch.object(etl_to_bq, "ensure_dataset"),
            patch.object(etl_to_bq, "run_quality_checks", side_effect=lambda *a: order.append("quality")),
            patch.object(etl_to_bq, "snapshot_tables", side_effect=lambda *a: order.append("snapshot")),
            patch.object(etl_to_bq, "load_tables", side_effect=lambda *a: order.append("load")),
        ):
            mock_bq.Client.return_value = MagicMock()
            etl_to_bq.main()

        assert order == ["quality", "snapshot", "load"], order


class TestSnapshotFailureDoesNotStopEtl:
    """**退避に失敗しても ETL は止めない**（実際に本番相当で落ちた）。

    守るための仕組みが、守る対象を止めてしまっては本末転倒。
    ETL 用の SA には `bigquery.tables.deleteSnapshot` が無く、
    有効期限付きのスナップショット作成が 403 になって ETL 全体が落ちた。

    ただし**黙って続けない**。退避が無いまま上書きされるので、
    戻せない状態に入ったことは必ず出す。
    """

    def test_失敗しても例外を投げない(self, capsys):
        client = FakeClient(existing={"benefits"})
        client.raise_on_query = RuntimeError("403 deleteSnapshot denied")
        assert snapshot_tables(client, "proj", ["benefits"], now=FIXED_NOW) == []

    def test_失敗したことを必ず出す(self, capsys):
        client = FakeClient(existing={"benefits"})
        client.raise_on_query = RuntimeError("403 deleteSnapshot denied")
        snapshot_tables(client, "proj", ["benefits"], now=FIXED_NOW)
        out = capsys.readouterr().out
        assert "失敗" in out and "戻せない" in out
        assert "deleteSnapshot" in out

    def test_初回実行と取り違えない(self, capsys):
        """**「退避するものが無い」と「退避できなかった」は別。**"""
        client = FakeClient(existing={"benefits"})
        client.raise_on_query = RuntimeError("403")
        snapshot_tables(client, "proj", ["benefits"], now=FIXED_NOW)
        assert "初回実行" not in capsys.readouterr().out
