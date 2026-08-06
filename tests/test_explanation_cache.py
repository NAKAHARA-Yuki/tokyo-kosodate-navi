"""やさしい解説のキャッシュ（issue #68 / ADR 0015）。

見たいのは2つ:
1. 2回目に Gemini を呼ばないこと。かつ**内容がキャッシュの有無で変わらない**こと
2. 保存してはいけないもの（review モードの下書き）を保存していないこと
"""

import pytest
from conftest import FakeRow

BENEFIT_ROW = {
    "title": "児童手当",
    "category": "児童手当",
    "area_name": "台東区",
    "summary": "概要",
    "description": "詳細",
    "target_persons_text": "高校生年代まで",
    "conditions_text": None,
    "monetary_support_text": "月額15,000円",
    "procedure_method": "郵送",
    "official_url": "https://example.com",
}

CACHED_ROW = {"result": "保存済みのやさしい解説", "generated_at": "2026-07-01T00:00:00+00:00"}


@pytest.fixture(autouse=True)
def _reset_table_flag(monkeypatch):
    """テーブル作成済みフラグはプロセス内グローバルなのでテスト間で持ち越さない。"""
    import explanation_cache

    monkeypatch.setattr(explanation_cache, "_table_ready", False)


@pytest.fixture
def gemini(monkeypatch):
    """Gemini を差し替え、呼ばれた回数を数える。"""
    import dependencies

    calls = []

    class FakeModels:
        def generate_content(self, model, contents, config=None):
            calls.append(contents)

            class R:
                text = "いま生成したやさしい解説"

            return R()

    class FakeGenaiClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(dependencies, "_build_genai_client", lambda: FakeGenaiClient())
    return calls


class TestCacheKey:
    """キーに含まれているものが1つでも変われば作り直しになること。"""

    def key(self, **overrides):
        import explanation_cache

        args = {"benefit_id": "psid-1", "facts": "制度名: 児童手当", "model": "gemini-x", "prompt_version": 1}
        args.update(overrides)
        return explanation_cache.cache_key(**args)

    def test_same_input_gives_same_key(self):
        assert self.key() == self.key()

    @pytest.mark.parametrize(
        "field,value",
        [
            ("benefit_id", "psid-2"),
            ("facts", "制度名: 児童手当（改定）"),
            ("model", "gemini-y"),
            ("prompt_version", 2),
        ],
    )
    def test_key_changes_when_any_input_changes(self, field, value):
        assert self.key(**{field: value}) != self.key(), f"{field} を変えてもキーが同じ"

    def test_key_survives_reordered_but_different_facts(self):
        """事実文字列が1文字でも違えば別キーになること（部分一致で済ませていない）。"""
        assert self.key(facts="制度名: 児童手当") != self.key(facts="制度名: 児童手当 ")


class TestCacheBehaviour:
    def test_second_call_does_not_call_gemini(self, client, bq, gemini):
        """キャッシュがあれば Gemini を呼ばない（issue #68 の主目的）。"""
        bq.set_rows_sequence([BENEFIT_ROW], [CACHED_ROW])
        res = client.post("/api/support/draft-review", json={"benefit_id": "psid-1"})

        assert res.status_code == 200
        assert gemini == [], "キャッシュがあるのに Gemini を呼んでいる"
        body = res.json()
        assert body["result"] == "保存済みのやさしい解説"
        assert body["cached"] is True

    def test_miss_generates_and_stores(self, client, bq, gemini):
        bq.set_rows_sequence([BENEFIT_ROW], [])
        res = client.post("/api/support/draft-review", json={"benefit_id": "psid-1"})

        assert res.status_code == 200
        assert len(gemini) == 1, "キャッシュが無いのに生成していない"
        body = res.json()
        assert body["result"] == "いま生成したやさしい解説"
        assert body["cached"] is False

        assert len(bq.inserted) == 1, "生成結果が保存されていない"
        table, rows = bq.inserted[0]
        assert table.endswith(".benefit_explanations")
        assert rows[0]["result"] == "いま生成したやさしい解説"
        assert rows[0]["benefit_id"] == "psid-1"
        assert rows[0]["generated_at"] == body["generated_at"]

    def test_result_is_identical_whether_cached_or_not(self, client, bq, gemini):
        """キャッシュの有無で利用者に見せる中身が変わらないこと。"""
        bq.set_rows_sequence([BENEFIT_ROW], [])
        fresh = client.post("/api/support/draft-review", json={"benefit_id": "psid-1"}).json()

        stored = bq.inserted[0][1][0]
        bq.set_rows_sequence(
            [BENEFIT_ROW], [{"result": stored["result"], "generated_at": stored["generated_at"]}]
        )
        cached = client.post("/api/support/draft-review", json={"benefit_id": "psid-1"}).json()

        ignore = {"cached"}
        assert {k: v for k, v in fresh.items() if k not in ignore} == {
            k: v for k, v in cached.items() if k not in ignore
        }

    def test_cache_hit_still_has_disclaimer_and_generated_at(self, client, bq, gemini):
        """キャッシュから返しても AI 生成である旨と、いつ作ったかは必ず付ける。"""
        bq.set_rows_sequence([BENEFIT_ROW], [CACHED_ROW])
        body = client.post("/api/support/draft-review", json={"benefit_id": "psid-1"}).json()

        assert body["disclaimer"], "AI生成である旨の注記が必要"
        assert body["generated_at"] == "2026-07-01T00:00:00+00:00"

    def test_table_is_created_on_first_store(self, client, bq, gemini):
        bq.set_rows_sequence([BENEFIT_ROW], [])
        client.post("/api/support/draft-review", json={"benefit_id": "psid-1"})
        assert len(bq.created_tables) == 1
        assert bq.created_tables[0].clustering_fields == ["cache_key"]


class TestReviewModeIsNotCached:
    """下書きには氏名や世帯の事情が入りうる。保存も参照もしない。"""

    def payload(self):
        return {"benefit_id": "psid-1", "mode": "review", "draft": "私は山田花子です。申請したいです。"}

    def test_review_is_not_stored(self, client, bq, gemini):
        bq.set_rows([FakeRow(BENEFIT_ROW)])
        res = client.post("/api/support/draft-review", json=self.payload())

        assert res.status_code == 200
        assert bq.inserted == [], "利用者が書いた下書きを保存している"
        assert res.json()["cached"] is False

    def test_review_does_not_read_the_cache(self, client, bq, gemini):
        """参照もしない。制度が同じでも下書きが違えば別の結果になるべきなので、
        キャッシュを引くこと自体が誤り。"""
        bq.set_rows([FakeRow(BENEFIT_ROW)])
        client.post("/api/support/draft-review", json=self.payload())

        assert not any("benefit_explanations" in q for q in bq.queries), (
            "review モードでキャッシュを参照している"
        )
        assert len(gemini) == 1


class TestCacheFailuresDoNotBreakTheEndpoint:
    """キャッシュは高速化の仕組みで、落ちても解説そのものは出せなければならない。"""

    def test_store_failure_still_returns_200(self, client, bq, gemini):
        bq.set_rows_sequence([BENEFIT_ROW], [])
        bq.insert_should_fail = True

        res = client.post("/api/support/draft-review", json={"benefit_id": "psid-1"})
        assert res.status_code == 200
        assert res.json()["result"] == "いま生成したやさしい解説"

    def test_lookup_failure_falls_back_to_generation(self, client, bq, gemini, monkeypatch):
        """キャッシュ参照が落ちても生成に進む。テーブルがまだ無い初回もここを通る。"""
        import dependencies

        monkeypatch.setattr(dependencies, "get_client", lambda: _RaisingOnCacheQuery(bq))
        bq.set_rows_sequence([BENEFIT_ROW])

        res = client.post("/api/support/draft-review", json={"benefit_id": "psid-1"})
        assert res.status_code == 200
        assert res.json()["result"] == "いま生成したやさしい解説"
        assert len(gemini) == 1


class _RaisingOnCacheQuery:
    """キャッシュ参照のクエリだけ落ちる BigQuery クライアント。"""

    def __init__(self, inner):
        self._inner = inner

    def query(self, query, job_config=None):
        if "benefit_explanations" in query:
            raise RuntimeError("table not found")
        return self._inner.query(query, job_config=job_config)

    def __getattr__(self, name):
        return getattr(self._inner, name)
