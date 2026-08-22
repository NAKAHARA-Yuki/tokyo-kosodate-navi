"""ロード前の退避（issue #160）。

ETL は8テーブルを全置換する。**戻す手段が用意されていなかった。**
いま戻せるのはタイムトラベルだけで、それも7日で切れる。

BigQuery は使わない（クライアントを差し替えて、発行される SQL と
飛ばす判断だけを見る）。
"""

from datetime import UTC, datetime

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

        class _Job:
            def result(self_inner):
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
