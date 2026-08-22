"""BigQuery への書き込み経路（issue #152）。

`transform()` までは `tests/test_etl_transform.py` が厚く見ているが、
**そこから先は誰も見ていなかった**（`etl_load` / `etl_schema` / `create_graph` が
カバレッジ 0%）。この経路は `WRITE_TRUNCATE` で対象データセットを全置換するので、
壊れたときの影響は判定経路より大きい。

見たいのは **BigQuery そのものの挙動ではなく、こちらが出した指示が正しいか**。
そのためクライアントは差し替え、発行された SQL と job_config だけを見る。
GCP 認証は要らない。
"""

import pytest
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

import create_graph
from etl_graph import transform
from etl_load import ensure_dataset, load_tables
from etl_schema import TABLE_SCHEMAS, build_benefits_schema

# transform() が返す8テーブル。ここが増減したらロード側も追随が要る。
EXPECTED_TABLES = {
    "benefits",
    "schemes",
    "statuses",
    "documents",
    "benefit_requires_status",
    "benefit_requires_doc",
    "benefit_in_scheme",
    "benefit_leads_to",
}


def _record(psid: str, title: str, min_months: int, max_months: int) -> dict:
    return {
        "basicInformation": {"psid": psid, "canonicalName": title},
        # **area は必須。** 無いとエッジ生成が自治体で束ねられず benefit_leads_to が空になる。
        "area": {"areaCode": "131024;中央区"},
        "target": {
            "greaterThanOrEqualTo": {"targetAgeOfMonths": min_months},
            "lessThanOrEqualTo": {"targetAgeOfMonths": max_months},
        },
        "必要書類": "母子健康手帳",
    }


@pytest.fixture
def tables():
    """**8テーブルすべてが空にならない**最小の入力。

    空の DataFrame は列を持たないので、空のまま検査すると
    「スキーマと列が一致している」を確かめたことにならない。
    年齢帯を地続きにして NEXT_STEP のエッジまで作らせている。
    """
    return transform(
        [
            _record("psid-1", "乳児期の制度", 0, 11),
            _record("psid-2", "幼児期の制度", 12, 23),
        ]
    )


class FakeLoadJob:
    def result(self):
        return None


class FakeClient:
    """ロード指示と SQL を記録するだけのクライアント。"""

    def __init__(self, dataset_exists=True):
        self.dataset_exists = dataset_exists
        self.created_datasets = []
        self.loads = []  # (table_id, job_config)
        self.queries = []

    def get_dataset(self, ref):
        if not self.dataset_exists:
            raise NotFound(str(ref))
        return ref

    def create_dataset(self, dataset):
        self.created_datasets.append(dataset.dataset_id)
        return dataset

    def load_table_from_dataframe(self, df, table_id, job_config=None):
        self.loads.append((table_id, job_config, df))
        return FakeLoadJob()

    def query(self, sql):
        self.queries.append(sql)
        return FakeLoadJob()


class TestEnsureDataset:
    def test_creates_when_missing(self):
        client = FakeClient(dataset_exists=False)
        ensure_dataset(client, "proj")
        assert client.created_datasets

    def test_does_not_recreate_when_present(self):
        client = FakeClient(dataset_exists=True)
        ensure_dataset(client, "proj")
        assert client.created_datasets == []


class TestLoadTables:
    def test_loads_every_table(self, tables):
        client = FakeClient()
        load_tables(client, "proj", tables)
        loaded = {table_id.split(".")[-1] for table_id, _, _ in client.loads}
        assert loaded == EXPECTED_TABLES

    def test_every_load_is_write_truncate(self, tables):
        """**追記になると件数が倍々に増える。** 全置換であることを固定する。"""
        client = FakeClient()
        load_tables(client, "proj", tables)
        for table_id, job_config, _ in client.loads:
            assert job_config.write_disposition == bigquery.WriteDisposition.WRITE_TRUNCATE, (
                f"{table_id} が全置換になっていない"
            )

    def test_every_load_has_an_explicit_schema(self, tables):
        """自動検出に任せると DATE が STRING に、空配列の STRUCT が推論できずに落ちる。"""
        client = FakeClient()
        load_tables(client, "proj", tables)
        for table_id, job_config, _ in client.loads:
            assert job_config.schema, f"{table_id} にスキーマが渡っていない"

    def test_writes_to_the_configured_dataset(self, tables):
        from config import DATASET_ID

        client = FakeClient()
        load_tables(client, "proj", tables)
        for table_id, _, _ in client.loads:
            assert table_id.startswith(f"proj.{DATASET_ID}."), table_id


class TestSchemaMatchesTransform:
    """**スキーマと `transform()` の出力がずれたら落ちる**ようにする。

    片方だけ列を足したとき、ロード時に初めて分かるのでは遅い。
    """

    def test_benefits_schema_covers_every_column(self, tables):
        df = tables["benefits"]
        schema = build_benefits_schema(df)
        assert [f.name for f in schema] == list(df.columns)

    def test_other_tables_schema_matches_columns(self, tables):
        for name in EXPECTED_TABLES - {"benefits"}:
            fields = {f.name for f in TABLE_SCHEMAS[name]}
            columns = set(tables[name].columns)
            assert fields == columns, f"{name}: スキーマ {fields} と列 {columns} が食い違う"

    def test_date_columns_are_not_strings(self, tables):
        """**日付を STRING で入れると、期間の比較ができなくなる。**"""
        schema = {f.name: f.field_type for f in build_benefits_schema(tables["benefits"])}
        for column in ("update_date", "implementation_period_from_date"):
            assert schema[column] == "DATE", f"{column} が {schema[column]}"

    def test_age_columns_are_integers(self, tables):
        """年齢が STRING になると絞り込みが黙って壊れる（判定に直結する）。"""
        schema = {f.name: f.field_type for f in build_benefits_schema(tables["benefits"])}
        for column in (
            "min_age_months",
            "max_age_months",
            "effective_min_age_months",
            "effective_max_age_months",
        ):
            assert schema[column] == "INT64", f"{column} が {schema[column]}"


class TestCreateGraphSql:
    """PROPERTY GRAPH は元テーブルに PRIMARY KEY (NOT ENFORCED) を要求する。

    **再実行できることが要点。** `DROP PRIMARY KEY IF EXISTS` を先に挟まないと
    2回目が "Already Exists" で落ちる（一度踏んでいる）。
    """

    @pytest.fixture
    def sql(self):
        return create_graph.SQL_PATH.read_text(encoding="utf-8")

    def test_every_add_is_preceded_by_a_drop(self, sql):
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        adds = [s for s in statements if "ADD PRIMARY KEY" in s]
        drops = [s for s in statements if "DROP PRIMARY KEY IF EXISTS" in s]
        assert len(adds) == len(drops) == len(EXPECTED_TABLES)
        for add in adds:
            table = add.split("`")[1] if "`" in add else add
            assert any(table in drop for drop in drops), f"{table} に DROP が無い"

    def test_covers_every_table(self, sql):
        for name in EXPECTED_TABLES:
            assert f"{{{{DATASET}}}}.{name}`" in sql, f"{name} の PRIMARY KEY が無い"

    def test_main_substitutes_placeholders(self, monkeypatch):
        client = FakeClient()
        monkeypatch.setattr(create_graph.bigquery, "Client", lambda **kw: client)
        monkeypatch.setenv("GCP_PROJECT_ID", "proj-x")
        create_graph.main()
        assert len(client.queries) == 1
        sent = client.queries[0]
        assert "{{PROJECT_ID}}" not in sent and "{{DATASET}}" not in sent
        assert "proj-x" in sent


class TestLoadedFrameIsTheOneWeBuilt:
    def test_row_counts_match_transform(self, tables):
        client = FakeClient()
        load_tables(client, "proj", tables)
        by_name = {table_id.split(".")[-1]: df for table_id, _, df in client.loads}
        for name, df in tables.items():
            assert len(by_name[name]) == len(df)


def test_every_table_is_populated(tables):
    """**この土台自体を守る。** どれかが空に戻ると、上のスキーマ検査が
    「列が無いテーブル同士を比べて通る」に退化する。"""
    for name in EXPECTED_TABLES:
        assert len(tables[name]) > 0, f"{name} が空。検査が素通りする"
