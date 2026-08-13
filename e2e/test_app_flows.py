"""主要なユーザーフローの E2E テスト。

ここで守りたいのは「ブラウザで実際に操作したときに壊れていないこと」。
過去にレイアウト崩れ・ラベルのはみ出し・タブ切り替えの不具合が
ユニットテストをすり抜けて本番に出たため、画面操作で検証する。
"""

import os
import re

import pytest
from fake_data import FAILING_BENEFIT_ID
from playwright.sync_api import expect


class TestTopPage:
    """新しいトップページ（一覧ビュー）。グラフは出さず、項目＋サマリーのカードを並べる。"""

    @pytest.fixture
    def top_page(self, page, base_url):
        page.set_default_timeout(15_000)
        page.goto(base_url)
        page.wait_for_selector("main ul li")
        return page

    def test_benefits_are_listed(self, top_page):
        assert top_page.locator("main ul li").count() > 0

    def test_no_graph_on_top_page(self, top_page):
        """トップページはグラフ表示をしない（UX方針）。"""
        assert top_page.locator("#cy").count() == 0

    def test_detail_link_opens_detail_page(self, top_page):
        """一覧から詳細へ遷移できること。

        benefit_id は `+` を含むため、URL のエンコードを誤ると backend が 404 を返す。
        実際に遷移させて回帰を検出する。
        """
        top_page.locator("main ul li a").first.click()
        top_page.wait_for_url(re.compile(r"/benefits/"))
        # 404 ページではなく詳細が出ていること
        expect(top_page.locator("main")).to_contain_text("一覧に戻る")


class TestSourceAndDisclaimer:
    """出典・免責・データの鮮度（#57）。

    行政情報を扱う以上ここは機能ではなく責任にあたるので、
    「たまたま出ている」ではなく全ページで出ていることを検証する。
    実データでも通るよう、件数や日付の具体的な値は前提にしない。
    """

    @pytest.fixture
    def top_page(self, page, base_url):
        page.set_default_timeout(15_000)
        page.goto(base_url)
        page.wait_for_selector("footer")
        return page

    def test_source_is_shown_with_link(self, top_page):
        footer = top_page.locator("footer")
        expect(footer).to_contain_text("出典")
        expect(footer).to_contain_text("子育て支援制度レジストリ")
        link = footer.locator('a[href*="portal.data.metro.tokyo.lg.jp"]')
        expect(link.first).to_be_visible()

    def test_disclaimer_is_shown(self, top_page):
        expect(top_page.locator("footer")).to_contain_text("最終的な判断は各自治体の公式情報")

    def test_states_it_is_not_an_official_service(self, top_page):
        """公式サービスだと誤解させない。"""
        expect(top_page.locator("footer")).to_contain_text("公式サービスではありません")

    def test_data_freshness_is_shown(self, top_page):
        """いつ時点の、どれだけのデータかが分かる。"""
        text = top_page.locator("footer").inner_text()
        assert re.search(r"[\d,]+件", text), f"収録件数が出ていません: {text}"
        assert "最終更新" in text, f"データの最終更新が出ていません: {text}"

    def test_footer_is_on_the_detail_page_too(self, top_page):
        """一覧だけでなく詳細ページにも出ること。"""
        top_page.locator("main ul li a").first.click()
        top_page.wait_for_url(re.compile(r"/benefits/"))
        expect(top_page.locator("footer")).to_contain_text("最終的な判断は各自治体の公式情報")

    def test_header_links_back_to_top(self, top_page):
        top_page.locator("main ul li a").first.click()
        top_page.wait_for_url(re.compile(r"/benefits/"))
        top_page.locator("header a").first.click()
        top_page.wait_for_url(re.compile(r"/$"))
        expect(top_page.locator("main ul li").first).to_be_visible()


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _rgb(css_color: str) -> tuple[int, int, int]:
    """`rgb(51, 51, 51)` / `rgba(...)` を (r, g, b) にする。"""
    values = css_color[css_color.index("(") + 1 : css_color.index(")")].split(",")
    return tuple(int(float(v)) for v in values[:3])


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG のコントラスト比。AA は本文で 4.5:1 以上。"""
    a, b = _relative_luminance(_rgb(foreground)), _relative_luminance(_rgb(background))
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


class TestReadableInDarkMode:
    """OS の設定がダークモードでも文字が読めること（issue #101）。

    デジタル庁デザインシステムのトークンはライト前提の**固定色**なので、
    `body` の背景だけを反転させると黒地に濃いグレーの文字が乗る。
    staging の実機で測ったときは本文 1.57:1 / リンク 1.39:1 で、事実上読めなかった。

    **コントラスト比で見ているのは、対処の仕方を縛らないため。** いまは
    `globals.css` の `prefers-color-scheme` を外して固定しているが、
    将来ダークモードにきちんと対応した場合もこのテストは通ってよい。
    """

    @pytest.fixture
    def dark_page(self, browser, base_url):
        context = browser.new_context(color_scheme="dark")
        page = context.new_page()
        page.set_default_timeout(15_000)
        page.goto(base_url)
        page.wait_for_selector("main ul li")
        yield page
        context.close()

    @pytest.mark.parametrize("selector,label", [("main li p", "制度の概要"), ("main li a", "制度名のリンク")])
    def test_text_has_enough_contrast(self, dark_page, selector, label):
        background = dark_page.evaluate("getComputedStyle(document.body).backgroundColor")
        color = dark_page.evaluate(f"getComputedStyle(document.querySelector({selector!r})).color")
        ratio = contrast_ratio(color, background)
        assert ratio >= 4.5, f"{label}が読めません: {color} on {background} = {ratio:.2f}:1"


class TestAgeSourceIsShown:
    """対象年齢を、**どれだけ信用してよいかが分かる形**で出すこと（issue #61）。

    `age_source` は `explicit`（元データに記載）/ `inferred`（本文から推定）/
    `unknown`（読み取れず）の3種類。実データでは inferred が 30.0%、unknown が 34.2% で、
    **6割超が「元データに年齢が書かれていない」制度**にあたる。

    ここを黙って explicit と同じ見た目で出すと、こちらの推定を自治体が定めた条件のように
    見せることになる（CLAUDE.md「推定値をユーザーに見せるときは『推定』と明示する」）。

    旧 `/debug` にはこの表示があったが（`TestBenefitFocus` 参照）、
    **新しいトップページには検証が無かった**（issue #61 で指摘されていた）。
    """

    @pytest.fixture(autouse=True)
    def _stub_only(self):
        if os.environ.get("E2E_BASE_URL"):
            pytest.skip("3種類すべてが一覧の先頭に来る保証が無いため、実データでは実行しない")

    @pytest.fixture
    def chips(self, page, base_url):
        page.set_default_timeout(15_000)
        page.goto(base_url)
        page.wait_for_selector("main ul li")
        return page.locator('[data-testid="age-chip"]')

    def test_explicit_age_has_no_estimate_marker(self, chips):
        texts = chips.all_inner_texts()
        assert any(t == "対象 3歳〜3歳11か月" for t in texts), f"明示された年齢が出ていない: {texts}"

    def test_inferred_age_is_marked_as_estimate(self, chips):
        """**推定であることが文言で分かること。** 色だけに頼らない（WCAG 1.4.1）。"""
        texts = chips.all_inner_texts()
        assert any("（推定）" in t for t in texts), f"推定である旨が出ていない: {texts}"

    def test_unknown_age_does_not_show_a_range(self, chips):
        """読み取れなかったものに範囲を出さない。

        範囲は NULL なので「0か月〜」のような既定値を当てると、
        **読み取れなかったという事実が消える。**
        """
        texts = chips.all_inner_texts()
        assert any(t == "対象年齢の記載なし" for t in texts), f"記載なしの表示が無い: {texts}"

    def test_detail_page_shows_it_too(self, page, base_url):
        page.set_default_timeout(15_000)
        page.goto(base_url)
        page.wait_for_selector("main ul li")
        page.locator("main ul li a").first.click()
        page.wait_for_url(re.compile(r"/benefits/"))
        expect(page.locator('[data-testid="age-chip"]')).to_be_visible()


class TestBenefitDetail:
    """詳細ページに制度の本文・条件の原文・申請リンクが出ること（issue #63）。

    以前は `/api/subgraph` が summary しか返しておらず、詳細ページに出ている説明文が
    一覧のカードと同じだった。条件の原文に至っては一切出ておらず、
    構造化された条件チップだけを見て「自分は対象だ」と誤解させる状態だった。

    ここで見る項目（条件の原文・申請書式・要確認の注意書き）は、**制度によって有無が違う**。
    実データでは target_persons_text は84%、form_links に至っては14%しか持っていない。
    一覧の先頭に来る制度がそれらを持っている保証はないため、スタブ限定で実行する
    （デプロイ先に対しては `TestSmoke` が最低限の確認を担う）。
    """

    @pytest.fixture(autouse=True)
    def _stub_only(self):
        if os.environ.get("E2E_BASE_URL"):
            pytest.skip("項目の有無が制度ごとに違うため、実データに対しては実行しない")

    @pytest.fixture
    def detail_page(self, page, base_url):
        page.set_default_timeout(15_000)
        page.goto(base_url)
        page.wait_for_selector("main ul li")
        page.locator("main ul li a").first.click()
        page.wait_for_url(re.compile(r"/benefits/"))
        page.wait_for_selector("main")
        return page

    def test_shows_the_full_description(self, detail_page):
        """一覧の要約ではなく、制度の本文が出ること。"""
        expect(detail_page.locator("main")).to_contain_text("制度の内容")

    def test_shows_raw_condition_text(self, detail_page):
        """条件の原文（対象になる方）が出ること。この issue の本題。"""
        expect(detail_page.locator("main")).to_contain_text("対象になる方")

    def test_warns_when_conditions_are_not_machine_checkable(self, detail_page):
        """has_free_text_conditions=true のとき、チップだけで判断させない注意書きを出す。"""
        expect(detail_page.locator("main")).to_contain_text("機械的に判定しきれない条件")

    def test_application_form_link_is_shown(self, detail_page):
        expect(detail_page.locator("main")).to_contain_text("申請書式")

    def test_links_are_not_duplicated(self, detail_page):
        """リンク節の中で同じ URI を二度出さない。

        元データは同じ URI を related_links と embedded_links の両方に持つことがある。

        必要書類の `doc_url` と申請書式が同じ URI になる制度も実データに10件あるが、
        そちらは「必要な書類」と「申請の導線」で役割が違うため重複を許す。
        ここで見るのはリンク節（申請書式・関連リンク）の中だけ。
        """
        hrefs = detail_page.locator('[data-testid="link-list"] a').evaluate_all(
            "els => els.map(e => e.getAttribute('href'))"
        )
        assert len(hrefs) == len(set(hrefs)), f"同じリンクが重複しています: {hrefs}"


class TestNotFoundPages:
    """存在しない URL / 制度に 404 の画面を出すこと（issue #59）。

    存在しない ID は実データでも同じく 404 になるので、デプロイ先に対しても実行する。
    """

    @pytest.mark.smoke
    def test_unknown_url_returns_404_page(self, page, base_url):
        """どのルートにも一致しない URL。ステータスも 404 であること。"""
        res = page.goto(f"{base_url}/no-such-page")
        assert res is not None and res.status == 404
        expect(page.locator("main")).to_contain_text("見つかりませんでした")

    def test_unknown_benefit_returns_404_page(self, page, base_url):
        """存在しない benefit_id（backend が 404 を返すケース）。"""
        page.goto(f"{base_url}/benefits/does-not-exist")
        expect(page.locator("main")).to_contain_text("見つかりませんでした")

    def test_404_page_links_back_to_the_list(self, page, base_url):
        page.goto(f"{base_url}/benefits/does-not-exist")
        page.locator("main a").first.click()
        page.wait_for_selector("main ul li")


class TestBackendFailurePage:
    """backend が 500 を返したときのエラー画面（issue #59）。

    以前は失敗時に `backend が 500 を返しました` がそのまま画面に出ていた。
    利用者にとって意味が無いうえ、backend の状態を外に晒すため、出さないことを担保する。

    **500 を意図的に起こせるのはスタブだけ**なので、デプロイ先に対しては実行しない。
    `FAILING_BENEFIT_ID` を 500 に変換するのは `e2e/fake_data.py` の仕掛けで、
    実環境では「ただの存在しない ID」＝ 404 になり、`TestNotFoundPages` と同じ画面が出る。
    それでも `test_backend_failure_does_not_leak_internals` の方は 404 画面にも
    "backend" の文字が無いため**偶然通ってしまい**、壊れていることに気づけない。
    """

    @pytest.fixture(autouse=True)
    def _stub_only(self):
        if os.environ.get("E2E_BASE_URL"):
            pytest.skip("500 を意図的に起こせるのはスタブだけなので、実環境では実行しない")

    def test_backend_failure_shows_a_user_facing_page(self, page, base_url):
        """backend が 500 を返したとき、利用者向けの文言と復帰導線が出ること。"""
        page.goto(f"{base_url}/benefits/{FAILING_BENEFIT_ID}")
        main = page.locator("main")
        expect(main).to_contain_text("ページを表示できませんでした")
        expect(main.get_by_role("button", name="もう一度読み込む")).to_be_visible()
        expect(main.get_by_role("link", name="制度の一覧に戻る")).to_be_visible()

    def test_backend_failure_does_not_leak_internals(self, page, base_url):
        """内部のエラーメッセージが画面に出ないこと（この issue の本題）。

        このテストが守れるのは「Server Component が握りつぶして自分で描画する」形の漏れ
        （実際に起きていた形）。バグを再投入して落ちることを確認済み。
        逆に app/error.tsx 側で `error.message` を出しても**このテストは通ってしまう**。
        本番ビルドでは Next.js が Server Component の例外メッセージを匿名化するため。
        """
        page.goto(f"{base_url}/benefits/{FAILING_BENEFIT_ID}")
        page.wait_for_selector("main")
        text = page.locator("main").inner_text()
        assert "backend" not in text.lower(), f"backend の内部事情が出ています: {text!r}"
        assert "を返しました" not in text, f"内部のエラーメッセージが出ています: {text!r}"


class TestJapaneseFont:
    """日本語がデザインシステムの想定フォント（Noto Sans JP）で描画されること（issue #59）。

    以前は create-next-app の雛形のまま Geist（latin のみ）を読み込み、`body` を
    `font-family: Arial` で固定していたため、日本語は OS 依存のフォントに落ちていた。
    Docker の standalone ビルドでフォントファイルが同梱され損ねると同じ状態に戻るため、
    デプロイ先に対しても確認する。
    """

    @pytest.mark.smoke
    def test_japanese_is_rendered_with_noto_sans_jp(self, page, base_url):
        page.goto(base_url)
        family = page.evaluate("getComputedStyle(document.body).fontFamily")
        assert "Noto Sans JP" in family, f"Noto Sans JP が指定されていません: {family!r}"

        # 指定されているだけでなく、日本語のグリフを持つフォントが実際に読み込めていること。
        # （next/font の subsets に "japanese" は無く、latin 指定でも日本語チャンクが
        #   入るという前提が崩れていないかの確認でもある）
        page.evaluate("() => document.fonts.ready")
        assert page.evaluate("() => document.fonts.check('16px \"Noto Sans JP\"', '子育て支援')"), (
            "Noto Sans JP で日本語を描画できていません（フォントが配信されていない可能性）"
        )


class TestDebugPageIsMarkedAsDevOnly:
    """`/debug` は開発・デモ用として残す画面（docs/adr/0014）。

    正式な画面と取り違えられないことと、検索結果に出ないことを担保する。
    """

    def test_dev_banner_is_shown(self, app_page):
        expect(app_page.locator("#dev-banner")).to_be_visible()
        expect(app_page.locator("#dev-banner")).to_contain_text("開発用")

    def test_banner_links_to_the_real_app(self, app_page):
        """正式な画面への戻り道があること。"""
        app_page.locator("#dev-banner a").click()
        app_page.wait_for_url(re.compile(r"/$"))
        expect(app_page.locator("main ul li").first).to_be_visible()

    def test_disclaimer_is_shown(self, app_page):
        """実データの制度情報を出す以上、この画面にも免責が要る。

        `to_contain_text` は要素が非表示でも通ってしまうため、可視性も併せて確認する。
        """
        disclaimer = app_page.locator("#dev-banner .disclaimer")
        expect(disclaimer).to_be_visible()
        expect(disclaimer).to_contain_text("最終的な判断")

    def test_not_indexed_by_search_engines(self, app_page):
        content = app_page.locator('meta[name="robots"]').get_attribute("content")
        assert content is not None and "noindex" in content, (
            f"robots メタが noindex になっていません: {content!r}"
        )

    def test_new_pages_do_not_link_to_debug(self, page, base_url):
        """新画面から `/debug` への導線は張らない。"""
        page.goto(base_url)
        page.wait_for_selector("main ul li")
        assert page.locator('a[href*="/debug"]').count() == 0


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
        page.goto(f"{base_url}/debug")
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
        app_page.wait_for_function("() => cy.nodes().filter(n => n.data('type')==='Benefit').length === 1")

        types = app_page.evaluate("cy.nodes().map(n => n.data('type'))")
        assert types.count("Benefit") == 1, "フォーカス時は制度1件だけになるはず"
        assert "Me" in types, "自分ノードは残る"
        # 書類を持たない制度は実データに普通に存在するため、種別ごとの件数では判定しない。
        # 「自分と制度のほかは条件か書類しか出ない」という構造だけを担保する。
        assert set(types) <= {"Me", "Benefit", "Status", "Document"}
        assert any(t in ("Status", "Document") for t in types), "制度に紐づくノードが1つも出ないのはおかしい"

    def test_detail_panel_shows_benefit_sections(self, app_page):
        """詳細パネルが制度名と、データのある節を表示する。

        「手続き」「問い合わせ」などの節は、元データに該当項目が無ければ描画されない。
        実データでは欠けている制度が普通にあるため、特定の節の存在は前提にしない。
        """
        app_page.locator(".result-item").first.click()
        detail = app_page.locator("#detail")
        expect(detail).to_be_visible()

        # 制度名は必ず出る
        assert detail.locator("h3").inner_text().strip(), "制度名が空になっている"
        # 節は最低1つ出る（費用・手続き・問い合わせのいずれか）
        headings = detail.inner_text()
        assert any(k in headings for k in ("費用", "手続き", "問い合わせ")), (
            f"節が1つも描画されていない: {headings[:200]}"
        )

    def test_labels_fit_inside_nodes(self, app_page):
        """ラベルがノードからはみ出していないこと（実際に起きた不具合の回帰防止）。

        cytoscape の text-wrap は空白でしか折り返さないため、
        日本語ラベルは自前で改行しないと箱をはみ出す。
        """
        app_page.locator(".result-item").first.click()
        # 書類を持たない制度もあるので、条件か書類のどちらかが出るのを待つ
        app_page.wait_for_function(
            "() => cy.nodes().filter(n => ['Status','Document'].includes(n.data('type'))).length > 0"
        )

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
    # 実際の Gemini は thinking_level=HIGH で 9〜13 秒かかる（staging 実測）。
    # Playwright の expect() は page.set_default_timeout の影響を受けず既定 5 秒で判定するため、
    # AI の待ち時間だけ明示的に伸ばす。全体を伸ばすと他の箇所の遅延を見逃すのでここだけにする。
    AI_TIMEOUT_MS = 45_000

    def test_explain_button_renders_result(self, app_page):
        app_page.locator(".result-item").first.click()
        app_page.click("#ai-explain")
        expect(app_page.locator("#ai-result")).to_contain_text(
            "AIによるやさしい解説", timeout=self.AI_TIMEOUT_MS
        )

    def test_disclaimer_is_shown(self, app_page):
        """AI 生成である旨の注記を必ず出す。"""
        app_page.locator(".result-item").first.click()
        app_page.click("#ai-explain")
        expect(app_page.locator("#ai-result")).to_contain_text("AI", timeout=self.AI_TIMEOUT_MS)
        expect(app_page.locator("#ai-result")).to_contain_text("公式情報", timeout=self.AI_TIMEOUT_MS)


class TestMobileLayout:
    @pytest.fixture
    def mobile_page(self, page, base_url):
        page.set_viewport_size({"width": 390, "height": 844})
        page.set_default_timeout(15_000)
        page.goto(f"{base_url}/debug")
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
        res = page.request.get(f"{base_url}/api/healthz")
        assert res.ok
        body = res.json()
        assert body["status"] == "ok"
        assert body["env"] in ("dev", "staging", "prod")

    def test_top_page_renders(self, app_page):
        expect(app_page.locator("h1")).to_be_visible()
        assert app_page.locator(".result-item").count() > 0
