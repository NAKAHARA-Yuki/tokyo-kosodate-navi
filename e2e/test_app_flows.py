"""主要なユーザーフローの E2E テスト。

ここで守りたいのは「ブラウザで実際に操作したときに壊れていないこと」。
過去にレイアウト崩れ・ラベルのはみ出し・タブ切り替えの不具合が
ユニットテストをすり抜けて本番に出たため、画面操作で検証する。
"""

import re

import pytest
from playwright.sync_api import expect


class TestInitialRender:
    def test_title_and_sidebar(self, app_page):
        expect(app_page).to_have_title(re.compile("子育て支援制度"))
        expect(app_page.locator("h1")).to_contain_text("子育て支援制度ナレッジグラフ")

    def test_search_results_listed(self, app_page):
        assert app_page.locator(".result-item").count() > 0

    def test_me_centered_graph_is_drawn(self, app_page):
        """「自分」を中心に制度が並ぶグラフが描画されていること。"""
        node_types = app_page.evaluate("cy.nodes().map(n => n.data('type'))")
        assert "Me" in node_types
        assert "Benefit" in node_types

    def test_no_console_errors(self, page, base_url):
        errors = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(base_url)
        page.wait_for_selector(".result-item")
        assert errors == [], f"コンソールエラーが出ています: {errors}"


class TestAttributeFilter:
    def test_area_filter_triggers_search(self, app_page):
        app_page.select_option("#area-select", "131067")
        app_page.wait_for_function("() => cy.nodes().length > 0")
        assert app_page.locator(".result-item").count() > 0

    def test_age_input_accepts_value(self, app_page):
        app_page.fill("#age-years", "3")
        app_page.wait_for_timeout(600)  # デバウンス待ち
        assert app_page.locator(".result-item").count() > 0

    def test_inferred_age_is_labeled_as_estimate(self, app_page):
        """推定値は「推定」と明示し、断定的に見せない。"""
        texts = app_page.locator(".result-item .cat").all_inner_texts()
        assert any("推定" in t for t in texts), "推定ラベルが表示されていない"


class TestBenefitFocus:
    def test_click_narrows_to_single_benefit(self, app_page):
        """制度をクリックすると、その制度と直結ノードだけに絞り込まれる。"""
        app_page.locator(".result-item").first.click()
        app_page.wait_for_function("() => cy.nodes().filter(n => n.data('type')==='Status').length > 0")

        types = app_page.evaluate("cy.nodes().map(n => n.data('type'))")
        assert types.count("Benefit") == 1, "フォーカス時は制度1件だけになるはず"
        assert "Status" in types
        assert "Document" in types

    def test_detail_panel_shows_contact_and_procedure(self, app_page):
        app_page.locator(".result-item").first.click()
        detail = app_page.locator("#detail")
        expect(detail).to_be_visible()
        expect(detail).to_contain_text("手続き")
        expect(detail).to_contain_text("問い合わせ")

    def test_labels_fit_inside_nodes(self, app_page):
        """ラベルがノードからはみ出していないこと（実際に起きた不具合の回帰防止）。

        cytoscape の text-wrap は空白でしか折り返さないため、
        日本語ラベルは自前で改行しないと箱をはみ出す。
        """
        app_page.locator(".result-item").first.click()
        app_page.wait_for_function("() => cy.nodes().filter(n => n.data('type')==='Document').length > 0")

        overflowing = app_page.evaluate("""
            () => cy.nodes()
              .filter(n => ['Status', 'Document'].includes(n.data('type')))
              .map(n => {
                const lines = String(n.data('label')).split('\\n');
                return { label: n.data('label'), lines: lines.length, height: n.height() };
              })
              // 1行あたり17px + 余白16px を超えて文字が入っていたら箱に収まっていない
              .filter(x => x.height < x.lines * 17 + 10);
        """)
        assert overflowing == [], f"ノードからラベルがはみ出しています: {overflowing}"

    def test_back_button_returns_to_results(self, app_page):
        app_page.locator(".result-item").first.click()
        back = app_page.locator("#back-button")
        expect(back).to_be_visible()

        before = app_page.evaluate("cy.nodes().filter(n => n.data('type')==='Benefit').length")
        back.click()
        app_page.wait_for_function(
            f"() => cy.nodes().filter(n => n.data('type')==='Benefit').length > {before}"
        )
        expect(back).to_be_hidden()


class TestTimeline:
    def test_switches_to_timeline(self, app_page):
        app_page.click("#view-timeline")
        app_page.wait_for_selector(".stage-card")
        assert app_page.locator(".stage").count() == 8, "ライフステージは8つ"

    def test_stage_card_opens_graph(self, app_page):
        app_page.click("#view-timeline")
        app_page.wait_for_selector(".stage-card")
        app_page.locator(".stage-card").first.click()

        expect(app_page.locator("#cy")).to_be_visible()
        app_page.wait_for_function("() => cy.nodes().length > 0")

    def test_switch_back_to_graph(self, app_page):
        app_page.click("#view-timeline")
        app_page.wait_for_selector(".stage-card")
        app_page.click("#view-graph")
        expect(app_page.locator("#timeline")).to_be_hidden()
        expect(app_page.locator("#cy")).to_be_visible()


class TestAiSupport:
    def test_explain_button_renders_result(self, app_page):
        app_page.locator(".result-item").first.click()
        app_page.click("#ai-explain")
        expect(app_page.locator("#ai-result")).to_contain_text("AIによるやさしい解説")

    def test_disclaimer_is_shown(self, app_page):
        """AI 生成である旨の注記を必ず出す。"""
        app_page.locator(".result-item").first.click()
        app_page.click("#ai-explain")
        expect(app_page.locator("#ai-result")).to_contain_text("AI")
        expect(app_page.locator("#ai-result")).to_contain_text("公式情報")


class TestMobileLayout:
    @pytest.fixture
    def mobile_page(self, page, base_url):
        page.set_viewport_size({"width": 390, "height": 844})
        page.set_default_timeout(15_000)
        page.goto(base_url)
        page.wait_for_selector(".result-item")
        return page

    def test_tabs_are_visible(self, mobile_page):
        expect(mobile_page.locator("#mobile-tabs")).to_be_visible()

    def test_tapping_result_switches_to_graph(self, mobile_page):
        """スマホでは制度をタップしたらグラフタブへ自動遷移する。"""
        mobile_page.locator(".result-item").first.click()
        expect(mobile_page.locator("#tab-graph")).to_have_class(re.compile("active"))

    def test_graph_fits_within_viewport(self, mobile_page):
        """グラフが画面外にはみ出していないこと（実際に起きた不具合の回帰防止）。"""
        mobile_page.locator(".result-item").first.click()
        mobile_page.wait_for_function("() => cy.nodes().filter(n => n.data('type')==='Status').length > 0")
        mobile_page.wait_for_timeout(900)  # レイアウトのアニメーション完了待ち

        box = mobile_page.evaluate("""
            () => {
              const b = cy.elements().renderedBoundingBox();
              return { x1: b.x1, x2: b.x2, width: cy.width() };
            }
        """)
        assert box["x1"] >= -5, f"グラフが左にはみ出しています: {box}"
        assert box["x2"] <= box["width"] + 5, f"グラフが右にはみ出しています: {box}"

    def test_detail_sheet_has_close_button(self, mobile_page):
        mobile_page.locator(".result-item").first.click()
        expect(mobile_page.locator("#detail-close")).to_be_visible()
        mobile_page.click("#detail-close")
        expect(mobile_page.locator("#detail")).to_be_hidden()


@pytest.mark.smoke
class TestSmoke:
    """デプロイ先に対しても実行できる最小限の確認。

    `make e2e-smoke ENV=staging` のように E2E_BASE_URL を指定して使う。
    """

    def test_health(self, page, base_url):
        res = page.request.get(f"{base_url}/healthz")
        assert res.ok
        body = res.json()
        assert body["status"] == "ok"
        assert body["env"] in ("dev", "staging", "prod")

    def test_top_page_renders(self, app_page):
        expect(app_page.locator("h1")).to_be_visible()
        assert app_page.locator(".result-item").count() > 0
