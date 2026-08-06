"""API の結合テスト（BigQuery はモック）。

重点は2つ:
1. 絞り込み条件が正しいか（特に年齢は effective_* を使っているか）
2. レスポンスの形が壊れていないか
"""

import pytest
from conftest import FakeQueryJob, FakeRow


def benefit_row(**overrides):
    """検索系エンドポイントが返す行の雛形。"""
    row = {
        "benefit_id": "psid-1",
        "title": "3歳児健康診査",
        "category": "3歳児健康診査",
        "summary": "概要テキスト",
        "min_age_months": 36,
        "max_age_months": 47,
        "age_source": "explicit",
        "area_name": "台東区",
        "area_code": "131067",
        "has_free_text_conditions": False,
        "is_free": True,
        "monetary_support_text": None,
        "cost_text": None,
        "electronic_submission": False,
    }
    row.update(overrides)
    return row


class TestHealth:
    def test_healthz(self, client):
        res = client.get("/api/healthz")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        # どの環境・データセットを見ているかはデプロイ事故の切り分けに要る
        assert body["env"] in ("dev", "staging", "prod")
        assert body["dataset"]

    def test_healthz_reports_test_env(self, client):
        """テストは dev 設定で動く（本番データセットを指していないこと）。"""
        assert res_dataset(client) != "gov_knowledge_db"


def res_dataset(client) -> str:
    return client.get("/api/healthz").json()["dataset"]


class TestSearchBenefits:
    def test_returns_expected_shape(self, client, bq):
        bq.set_rows([benefit_row()])
        res = client.get("/api/benefits")
        assert res.status_code == 200
        item = res.json()[0]
        assert item["benefit_id"] == "psid-1"
        assert item["title"] == "3歳児健康診査"
        assert item["age_source"] == "explicit"

    def test_age_filter_uses_effective_columns(self, client, bq):
        """素の min/max_age_months は6割超が NULL のため、
        それで絞ると「10歳なのに新生児向け制度が出る」状態になる（過去のバグ）。"""
        bq.set_rows([])
        client.get("/api/benefits?age_months=120")

        query = bq.last_query
        assert "effective_min_age_months" in query
        assert "effective_max_age_months" in query
        # 素のカラムを条件に使っていないこと（SELECT のエイリアスとしては登場する）
        where_part = query.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
        assert "effective_min_age_months" in where_part
        assert bq.last_params()["age_months"] == 120

    def test_age_filter_orders_by_confidence(self, client, bq):
        """年齢が明示された制度を先に、不明なものを後ろに出す。"""
        bq.set_rows([])
        client.get("/api/benefits?age_months=36")
        assert "CASE age_source" in bq.last_query

    def test_no_age_filter_orders_by_title(self, client, bq):
        bq.set_rows([])
        client.get("/api/benefits")
        assert "CASE age_source" not in bq.last_query

    def test_area_filter_is_parameterized(self, client, bq):
        """SQL インジェクションを防ぐため、値は必ずクエリパラメータで渡す。"""
        bq.set_rows([])
        client.get("/api/benefits?area_code=131067")
        assert bq.last_params()["area_code"] == "131067"
        assert "131067" not in bq.last_query

    def test_limit_is_capped(self, client, bq):
        bq.set_rows([])
        assert client.get("/api/benefits?limit=1000").status_code == 422

    def test_negative_age_rejected(self, client, bq):
        bq.set_rows([])
        assert client.get("/api/benefits?age_months=-1").status_code == 422


class TestMatchBenefits:
    def match_row(self, **overrides):
        row = benefit_row(
            is_prenatal=False,
            conditions_text=None,
            official_url="https://example.com",
            scheme_id="SCHEME_x",
        )
        row["effective_min_age_months"] = row.pop("min_age_months")
        row["effective_max_age_months"] = row.pop("max_age_months")
        # 属性による並び替え・理由付けで参照する（app/routers/match.py）
        row["is_single_parent_target"] = False
        row["is_disability_target"] = False
        row.update(overrides)
        return row

    def test_returns_match_reasons(self, client, bq):
        """なぜ当たったかを説明できることが、判定をLLMに任せない設計の要。"""
        bq.set_rows([self.match_row()])
        res = client.get("/api/benefits/match?area_code=131067&child_age_months=40&include_skill_tree=false")
        assert res.status_code == 200
        body = res.json()
        assert body["count"] == 1

        reasons = body["benefits"][0]["match_reasons"]
        assert any("台東区" in r for r in reasons)
        assert any("40" in r for r in reasons)

    def test_single_parent_reason_is_added(self, client, bq):
        """ひとり親向けに分類されている制度に理由が付くこと（issue #77）。"""
        bq.set_rows([self.match_row(is_single_parent_target=True)])
        res = client.get(
            "/api/benefits/match?area_code=131067&is_single_parent=true&include_skill_tree=false"
        )
        reasons = res.json()["benefits"][0]["match_reasons"]
        assert any("ひとり親" in r for r in reasons), reasons

    def test_disability_reason_is_added(self, client, bq):
        bq.set_rows([self.match_row(is_disability_target=True)])
        res = client.get("/api/benefits/match?area_code=131067&has_disability=true&include_skill_tree=false")
        reasons = res.json()["benefits"][0]["match_reasons"]
        assert any("障がい" in r for r in reasons), reasons

    def test_attribute_reason_is_not_added_when_not_requested(self, client, bq):
        """属性を指定していない人に、その属性の理由を出さない。"""
        bq.set_rows([self.match_row(is_single_parent_target=True)])
        res = client.get("/api/benefits/match?area_code=131067&include_skill_tree=false")
        reasons = res.json()["benefits"][0]["match_reasons"]
        assert not any("ひとり親" in r for r in reasons), reasons

    def test_attributes_do_not_filter_out_benefits(self, client, bq):
        """属性は並び順と理由にだけ使い、絞り込みには使わない。

        該当しない人からこれらの制度を隠すと「対象なのに出ない」を作る。
        分類コードと本文の一致率は90%で、残り10%の取りこぼしの方が害が大きい。
        """
        bq.set_rows([self.match_row(is_single_parent_target=False)])
        res = client.get(
            "/api/benefits/match?area_code=131067&is_single_parent=true&include_skill_tree=false"
        )
        assert res.json()["count"] == 1, "該当しない制度も結果から消してはいけない"
        assert "target_codes" not in bq.last_query.split("WHERE")[-1], (
            f"分類コードが WHERE 句に入っています: {bq.last_query}"
        )

    def test_orders_matching_benefits_first(self, client, bq):
        """属性を指定したときだけ並び替えに使う。"""
        bq.set_rows([self.match_row()])
        client.get("/api/benefits/match?area_code=131067&is_single_parent=true&include_skill_tree=false")
        assert "is_single_parent_target DESC" in bq.last_query

        bq.set_rows([self.match_row()])
        client.get("/api/benefits/match?area_code=131067&include_skill_tree=false")
        assert "is_single_parent_target DESC" not in bq.last_query

    def test_inferred_age_is_flagged_in_reason(self, client, bq):
        """推定値で当たった場合はユーザーに断定的に見せない。"""
        bq.set_rows([self.match_row(age_source="inferred")])
        res = client.get("/api/benefits/match?child_age_months=40&include_skill_tree=false")
        reasons = res.json()["benefits"][0]["match_reasons"]
        assert any("推定" in r for r in reasons)

    def test_includes_tokyo_wide_benefits(self, client, bq):
        """東京都全域の制度(130001)も対象に含める。"""
        bq.set_rows([])
        client.get("/api/benefits/match?area_code=131067&include_skill_tree=false")
        assert "130001" in bq.last_query

    def test_prenatal_included_when_pregnant(self, client, bq):
        bq.set_rows([])
        client.get("/api/benefits/match?child_age_months=0&is_pregnant=true&include_skill_tree=false")
        assert "is_prenatal" in bq.last_query

    def test_needs_confirmation_surfaced(self, client, bq):
        """自由記述条件が残る制度は、機械判定だけで確定させない。"""
        bq.set_rows([self.match_row(has_free_text_conditions=True, conditions_text="所得制限あり")])
        res = client.get("/api/benefits/match?child_age_months=40&include_skill_tree=false")
        item = res.json()["benefits"][0]
        assert item["needs_confirmation"] is True
        assert item["conditions_text"] == "所得制限あり"


class TestUserProfile:
    def test_normalizes_age_label(self, client, bq):
        bq.set_rows([{"area_name": "台東区"}])
        res = client.post(
            "/api/user/profile",
            json={"area_code": "131067", "child_age_months": 18},
        )
        assert res.status_code == 200
        assert res.json()["resolved"] == {"area_name": "台東区", "child_age_label": "1歳6か月"}

    def test_exact_years_label(self, client, bq):
        bq.set_rows([{"area_name": "台東区"}])
        res = client.post("/api/user/profile", json={"area_code": "131067", "child_age_months": 24})
        assert res.json()["resolved"]["child_age_label"] == "2歳"

    def test_unknown_area_rejected(self, client, bq):
        bq.set_rows([])
        res = client.post("/api/user/profile", json={"area_code": "999999"})
        assert res.status_code == 400

    def test_empty_profile_is_allowed(self, client, bq):
        res = client.post("/api/user/profile", json={})
        assert res.status_code == 200
        assert res.json()["resolved"]["area_name"] is None


def subgraph_row(**overrides):
    """/api/subgraph が返す行の雛形（制度1件 × 条件 × 書類の直積の1行）。"""
    row = {
        "benefit_id": "psid-1",
        "title": "3歳児健康診査",
        "category": "健診",
        "summary": "概要",
        "description": "身体計測と内科診察を行います。",
        "utilization": None,
        "conditions_text": "前年の所得が一定額を下回る世帯に限ります。",
        "target_persons_text": "台東区に住民登録のある3歳のお子さん",
        "has_free_text_conditions": True,
        "related_links": [{"title": "案内", "uri": "https://example.com/info"}],
        "form_links": [{"title": "問診票", "uri": "https://example.com/form.pdf"}],
        # uri が無い要素は捨てられること、title が空なら uri で補われることも見る
        "embedded_links": [
            {"title": None, "uri": "https://example.com/embedded"},
            {"title": "壊れたリンク", "uri": None},
        ],
        "area_name": "台東区",
        "min_age_months": 36,
        "max_age_months": 47,
        "cost_text": None,
        "cost_conditions_text": None,
        "monetary_support_text": None,
        "materially_support_text": None,
        "is_free": True,
        "department": "保健所",
        "contact_name": "健康課",
        "contact_phone": "03-0000-0000",
        "contact_email": None,
        "contact_address": None,
        "official_url": "https://example.com",
        "official_title": "公式",
        "procedure_method": None,
        "procedure_counter": None,
        "electronic_submission": False,
        "regulation_name": "母子保健法",
        "update_date": None,
        "status_id": "AGE_1",
        "status_name": "3歳〜3歳11か月",
        "status_type": "AGE",
        "doc_id": "DOC_1",
        "doc_name": "母子健康手帳",
        "doc_url": "https://example.com/boshi.pdf",
    }
    row.update(overrides)
    return row


class TestSubgraph:
    def test_404_when_not_found(self, client, bq):
        bq.set_rows([])
        assert client.get("/api/subgraph?benefit_id=missing").status_code == 404

    def test_filters_out_prose_documents(self, client, bq):
        """必要書類欄に混ざる注意書きをノードにしない。"""
        bq.set_rows([])
        client.get("/api/subgraph?benefit_id=x")
        assert "is_probable_document" in bq.last_query

    def test_builds_nodes_and_edges(self, client, bq):
        bq.set_rows([subgraph_row()])
        body = client.get("/api/subgraph?benefit_id=psid-1").json()

        types = [n["data"]["type"] for n in body["nodes"]]
        assert types.count("Benefit") == 1
        assert "Status" in types
        assert "Document" in types
        assert {e["data"]["label"] for e in body["edges"]} == {"REQUIRES", "REQUIRES_DOC"}

    def test_returns_body_conditions_and_links(self, client, bq):
        """詳細ページに必要な本文・条件の原文・リンクを返すこと（issue #63）。

        特に条件の原文は、無いと「チップだけ見て対象だと思い込む」状態を作る。
        """
        bq.set_rows([subgraph_row()])
        body = client.get("/api/subgraph?benefit_id=psid-1").json()
        benefit = next(n["data"] for n in body["nodes"] if n["data"]["type"] == "Benefit")

        assert benefit["description"] == "身体計測と内科診察を行います。"
        assert benefit["conditions_text"]
        assert benefit["target_persons_text"]
        assert benefit["has_free_text_conditions"] is True
        assert benefit["form_links"] == [{"title": "問診票", "uri": "https://example.com/form.pdf"}]

    def test_drops_links_without_uri_and_falls_back_to_uri_as_title(self, client, bq):
        """uri の無いリンクは捨て、title が空なら uri を表示名にする。"""
        bq.set_rows([subgraph_row()])
        body = client.get("/api/subgraph?benefit_id=psid-1").json()
        benefit = next(n["data"] for n in body["nodes"] if n["data"]["type"] == "Benefit")

        assert benefit["embedded_links"] == [
            {"title": "https://example.com/embedded", "uri": "https://example.com/embedded"}
        ]

    def test_document_node_carries_its_url(self, client, bq):
        bq.set_rows([subgraph_row()])
        body = client.get("/api/subgraph?benefit_id=psid-1").json()
        doc = next(n["data"] for n in body["nodes"] if n["data"]["type"] == "Document")
        assert doc["doc_url"] == "https://example.com/boshi.pdf"


class TestTimeline:
    def test_returns_all_life_stages(self, client, bq):
        bq.set_rows([])
        body = client.get("/api/timeline").json()
        keys = [s["key"] for s in body["stages"]]
        assert keys[0] == "prenatal"
        assert "15-18y" in keys
        assert len(keys) == 8

    def test_groups_benefits_by_stage(self, client, bq):
        bq.set_rows(
            [
                {
                    "stage_key": "0y",
                    "benefit_id": "b1",
                    "title": "乳児健診",
                    "category": "健診",
                    "area_name": "台東区",
                    "is_free": True,
                    "electronic_submission": False,
                    "age_source": "explicit",
                }
            ]
        )
        body = client.get("/api/timeline").json()
        stage = next(s for s in body["stages"] if s["key"] == "0y")
        assert stage["benefits"][0]["title"] == "乳児健診"

    def test_single_query_for_all_stages(self, client, bq):
        """ステージごとにクエリを投げると往復が増えて体感が遅くなるため1クエリにまとめている。"""
        bq.set_rows([])
        client.get("/api/timeline")
        assert len(bq.queries) == 1


class TestDraftReview:
    def test_404_for_unknown_benefit(self, client, bq):
        bq.set_rows([])
        res = client.post("/api/support/draft-review", json={"benefit_id": "missing"})
        assert res.status_code == 404

    def test_review_requires_draft(self, client, bq):
        bq.set_rows(
            [
                {
                    k: None
                    for k in (
                        "title",
                        "category",
                        "area_name",
                        "summary",
                        "description",
                        "target_persons_text",
                        "conditions_text",
                        "monetary_support_text",
                        "procedure_method",
                        "official_url",
                    )
                }
            ]
        )
        res = client.post("/api/support/draft-review", json={"benefit_id": "x", "mode": "review"})
        assert res.status_code == 400

    @pytest.mark.parametrize("mode", ["explain", "review"])
    def test_prompt_forbids_inventing_facts(self, client, bq, monkeypatch, mode):
        """LLM に制度情報にないことを補わせない指示が必ず入っていること。"""
        import dependencies

        captured = {}

        class FakeModels:
            def generate_content(self, model, contents, config=None):
                captured["prompt"] = contents

                class R:
                    text = "生成結果"

                return R()

        class FakeGenaiClient:
            def __init__(self, **kwargs):
                self.models = FakeModels()

        monkeypatch.setattr(dependencies, "_build_genai_client", lambda: FakeGenaiClient())

        bq.set_rows(
            [
                {
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
            ]
        )
        payload = {"benefit_id": "x", "mode": mode}
        if mode == "review":
            payload["draft"] = "申請したいです"

        res = client.post("/api/support/draft-review", json=payload)
        assert res.status_code == 200
        assert res.json()["disclaimer"], "AI生成である旨の注記が必要"
        assert "書かれていないこと" in captured["prompt"]


class TestDataSource:
    """出典とデータの鮮度（#57）。

    出典・免責は「BigQuery が落ちていても出る」ことが要件なので、そこを重点的に見る。
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self, monkeypatch):
        """プロセス内キャッシュはテスト間で持ち越さない。"""
        import routers.meta as meta

        monkeypatch.setattr(meta, "_cache", None)
        monkeypatch.setattr(meta, "_cache_expires_at", 0.0)

    def test_returns_source_and_freshness(self, client, bq):
        bq.set_rows([{"benefit_count": 7812, "area_count": 63, "latest_update_date": "2026-03-31"}])
        body = client.get("/api/data-source").json()

        assert body["source"]["publisher"] == "東京都"
        assert "子育て支援制度レジストリ" in body["source"]["name"]
        assert body["source"]["url"].startswith("https://portal.data.metro.tokyo.lg.jp/")
        assert body["benefit_count"] == 7812
        assert body["area_count"] == 63
        assert body["latest_update_date"] == "2026-03-31"

    def test_source_is_returned_even_if_bigquery_fails(self, client, monkeypatch):
        """鮮度が取れなくても、出典だけは必ず返す（フッターの免責を消さないため）。"""
        import dependencies

        class Boom:
            def query(self, *args, **kwargs):
                raise RuntimeError("BigQuery が落ちている")

        monkeypatch.setattr(dependencies, "get_client", lambda: Boom())

        res = client.get("/api/data-source")
        assert res.status_code == 200
        body = res.json()
        assert body["source"]["name"]
        assert body["benefit_count"] is None
        assert body["latest_update_date"] is None

    def test_failure_is_not_cached(self, client, monkeypatch):
        """失敗を1時間キャッシュしない（BigQuery が復旧したら次のリクエストで出る）。

        レビュー指摘（#69）。成功時と同じ期限を設定していたため、コールドスタート直後に
        BigQuery が一瞬詰まっただけで、そのインスタンスは復旧後も1時間ずっと
        件数と更新日を出さない状態になっていた。
        """
        import dependencies

        calls = {"n": 0}

        class Flaky:
            def query(self, *args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("BigQuery が一時的に落ちている")
                return FakeQueryJob(
                    [
                        FakeRow(
                            {
                                "benefit_count": 7812,
                                "area_count": 63,
                                "latest_update_date": "2026-03-31",
                            }
                        )
                    ]
                )

        monkeypatch.setattr(dependencies, "get_client", lambda: Flaky())

        first = client.get("/api/data-source").json()
        assert first["benefit_count"] is None, "1回目は失敗するので鮮度は返らない"
        assert first["source"]["name"], "出典は失敗しても返る"

        second = client.get("/api/data-source").json()
        assert second["benefit_count"] == 7812, (
            f"BigQuery が復旧しても鮮度が返りません（失敗がキャッシュされている）: {second}"
        )

    def test_result_is_cached(self, client, bq):
        """全ページのフッターで使うので、毎回 BigQuery を叩かない。"""
        bq.set_rows([{"benefit_count": 1, "area_count": 1, "latest_update_date": "2026-03-31"}])
        client.get("/api/data-source")
        client.get("/api/data-source")
        assert len(bq.queries) == 1, f"2回クエリが飛んでいます: {bq.queries}"

    def test_counts_distinct_areas(self, client, bq):
        bq.set_rows([{"benefit_count": 1, "area_count": 1, "latest_update_date": None}])
        client.get("/api/data-source")
        assert "COUNT(DISTINCT area_code)" in bq.last_query
        assert "MAX(update_date)" in bq.last_query
