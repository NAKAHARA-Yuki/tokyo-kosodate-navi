"""主要なユーザーフローの E2E テスト。

ここで守りたいのは「ブラウザで実際に操作したときに壊れていないこと」。
過去にレイアウト崩れ・ラベルのはみ出し・タブ切り替えの不具合が
ユニットテストをすり抜けて本番に出たため、画面操作で検証する。
"""

import contextlib
import os
import re

import pytest
from fake_data import FAILING_BENEFIT_ID
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
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
    """属性による絞り込みが**実際に絞れている**こと（issue #64）。

    以前はどちらのテストも `count() > 0` しか見ておらず、スタブが `area_code` も
    `age_months` も無視して常に全件返していたため、**絞り込みを完全に壊しても通りました。**
    スタブがパラメータを見るようになったので、結果の中身で検証します。
    """

    @pytest.fixture
    def _stub_only(self):
        """スタブの制度構成に依存するテストにだけ付ける。

        **クラス全体には付けない。** `test_inferred_age_is_labeled_as_estimate` は
        実データでも通る書き方なので、実環境でも回したい。

        下の3つは、スタブが「制度名に区名を入れている」ことと、
        `3歳児健康診査` などが一覧の先頭に出ることを前提にしている。
        実データでは制度名に区名が入らず、既定の一覧（先頭40件・タイトル順）は
        `0歳…` `1歳6か月児健康診査` で埋まるため、前提そのものが成立しない。
        """
        if os.environ.get("E2E_BASE_URL"):
            pytest.skip("スタブの制度構成（制度名に区名が入る等）に依存するため、実データでは実行しない")

    def titles(self, page) -> list[str]:
        return page.locator(".result-item .title").all_inner_texts()

    def settle(self, page, predicate: str) -> list[str]:
        """絞り込みの反映を待ってから、そのときの一覧を返す。

        **待てなかったことをテストの失敗にしない。** `wait_for_function` を直接使うと
        落ちたときのメッセージが `Timeout` だけになり、何が出ていたのかが分かりません。
        待つのはあくまで安定化のためで、判定は呼び出し側の assert に任せます。
        """
        with contextlib.suppress(PlaywrightTimeoutError):
            page.wait_for_function(
                f"() => {{ const t = [...document.querySelectorAll('.result-item .title')]"
                f".map(e => e.textContent); return {predicate}; }}",
                timeout=5_000,
            )
        return self.titles(page)

    def test_area_filter_narrows_to_that_area(self, app_page, _stub_only):
        """千代田区を選んだら千代田区の制度だけになること。"""
        app_page.select_option("#area-select", "131016")
        titles = self.settle(app_page, "t.length > 0 && t.every(x => x.includes('千代田区'))")
        assert titles, "千代田区の制度が1件も出ていません"
        assert all("千代田区" in t for t in titles), f"他の区の制度が混ざっています: {titles}"

    def test_area_filter_excludes_other_areas(self, app_page, _stub_only):
        """台東区を選んだら千代田区の制度が消えること。

        「選んだ区のものが出る」だけでは、絞り込まず全件返していても通ります。
        **出てはいけないものが消えている**ことを見ます。
        """
        before = self.titles(app_page)
        assert any("千代田区" in t for t in before), (
            f"前提が崩れています。絞り込み前に千代田区の制度が出ているはず: {before}"
        )
        app_page.select_option("#area-select", "131067")
        titles = self.settle(app_page, "t.length > 0 && !t.some(x => x.includes('千代田区'))")
        assert titles, "台東区の制度が1件も出ていません"
        assert not any("千代田区" in t for t in titles), f"千代田区が残っています: {titles}"

    def test_age_filter_drops_out_of_range_benefits(self, app_page, _stub_only):
        """5歳では、対象年齢を外れる制度が消えること。

        スタブは `app/queries.py` の `age_filter_sql()` と同じ判定をします
        （**年齢が NULL の制度は素通り**。実データの 34.2% がこれで、
        ここで落とすと「対象なのに出ない」を作るため）。

        5歳（60ヶ月）だと:
          3歳児健康診査（36〜47ヶ月）  → 範囲外なので消える
          あそびひろば（0〜36ヶ月）    → 範囲外なので消える
          児童手当（0〜227ヶ月）       → 残る
          子育て相談窓口（年齢NULL）   → 残る
        """
        before = self.titles(app_page)
        assert any("3歳児健康診査" in t for t in before), f"前提が崩れています: {before}"

        app_page.fill("#age-years", "5")
        titles = self.settle(app_page, "t.length > 0 && !t.some(x => x.includes('3歳児健康診査'))")
        assert any("児童手当" in t for t in titles), f"範囲内の制度が消えています: {titles}"
        assert any("子育て相談窓口" in t for t in titles), (
            f"年齢が NULL の制度まで落ちています（「対象なのに出ない」を作ります）: {titles}"
        )
        assert not any("あそびひろば" in t for t in titles), f"範囲外の制度が残っています: {titles}"

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


class TestSettingsAndModelUsers:
    """設定画面から属性を入れて絞り込む（issue #53 / #35）。

    **トップページには入力欄を置かない。** 一覧を見るたびにフォームが挟まるのを避け、
    `/settings` に集約している。入力結果は URL のクエリとして渡り、
    判定はサーバ側の確定クエリが行う（ADR 0001。LLM は挟まない）。

    ここで見るのは「絞り込みが実際に効いているか」。
    **「1件以上出る」では、絞り込みを丸ごと外しても通る**（#110 で同じ穴を潰したばかり）。
    出てはいけないものが消えていることを見る。
    """

    @pytest.fixture(autouse=True)
    def _stub_only(self):
        if os.environ.get("E2E_BASE_URL"):
            pytest.skip("スタブの制度構成に依存するため、実データに対しては実行しない")

    def titles(self, page):
        page.wait_for_selector("main ul li")
        return page.locator("main ul li a").all_inner_texts()

    def test_top_page_has_no_input_form(self, page, base_url):
        """トップに入力欄を置かない、という判断を固定する。"""
        page.set_default_timeout(15_000)
        page.goto(base_url)
        page.wait_for_selector("main ul li")
        assert page.locator("main select").count() == 0
        assert page.locator('main input[type="date"]').count() == 0

    def test_header_links_to_settings(self, page, base_url):
        page.set_default_timeout(15_000)
        page.goto(base_url)
        page.locator('header a[href="/settings"]').click()
        page.wait_for_url(re.compile(r"/settings"))
        expect(page.locator("main h1")).to_contain_text("条件を設定")

    def test_model_user_fills_the_form_and_filters(self, page, base_url):
        """モデルユーザーを選ぶと、その条件で絞り込まれること（issue #35）。"""
        page.set_default_timeout(15_000)
        page.goto(f"{base_url}/settings")
        page.wait_for_selector('[data-testid="model-user-baby"]')
        page.locator('[data-testid="model-user-baby"]').click()
        page.locator('[data-testid="apply-profile"]').click()
        page.wait_for_url(re.compile(r"area_code="))

        titles = self.titles(page)
        # 台東区・生後4か月。3歳児健診（36〜47ヶ月）は対象外なので消えていること
        assert not any("3歳児健康診査" in t for t in titles), f"対象外の制度が残っています: {titles}"
        assert any("児童手当" in t for t in titles), f"対象の制度が消えています: {titles}"

    def test_area_filter_excludes_other_areas(self, page, base_url):
        """台東区を選んだら千代田区の制度が消えること。

        「選んだ区のものが出る」だけでは、絞り込まず全件返していても通る。
        """
        page.set_default_timeout(15_000)
        page.goto(f"{base_url}/?area_code=131067&child_age_months=40")
        titles = self.titles(page)
        assert not any("千代田区" in t for t in titles), f"他の自治体が残っています: {titles}"

    def test_match_reasons_are_shown(self, page, base_url):
        """なぜ対象なのかを必ず添える（判定を LLM に任せない設計の要）。"""
        page.set_default_timeout(15_000)
        page.goto(f"{base_url}/?area_code=131067&child_age_months=40")
        page.wait_for_selector("main ul li")
        text = page.locator("main ul li").first.inner_text()
        assert "台東区" in text or "対象" in text, f"マッチ理由が出ていません: {text}"

    def test_url_reproduces_the_same_result(self, page, base_url):
        """URL を共有すると同じ結果が出ること（#53 の完了条件）。"""
        page.set_default_timeout(15_000)
        url = f"{base_url}/?area_code=131067&child_age_months=40"
        page.goto(url)
        first = self.titles(page)
        page.goto(url)
        assert self.titles(page) == first

    def test_settings_restores_the_previous_input(self, page, base_url):
        """クエリ無しで設定画面を開き直すと、前回の入力が復元されること。

        **この経路が一度も動いていなかった**（レビューで指摘）。
        `initial === EMPTY_PROFILE` で「URL にクエリが無い」を判定していたが、
        Server Component から Client Component へ渡る props は RSC の
        シリアライズを経由するため、**参照比較は常に false** になる。
        中身が同じでもサーバ側とクライアント側で別インスタンスだから。

        既存のテストは「URL で渡した場合」しか見ておらず、すり抜けていた。
        """
        page.set_default_timeout(15_000)
        page.goto(f"{base_url}/settings")
        page.wait_for_selector('[data-testid="apply-profile"]')
        page.evaluate(
            """() => window.localStorage.setItem('kosodate.profile', JSON.stringify({
                areaCode: '131067', children: [], isPregnant: false,
                isSingleParent: false, hasDisability: false }))"""
        )

        page.goto(f"{base_url}/settings")  # クエリ無しで開き直す
        page.wait_for_selector('[data-testid="apply-profile"]')
        assert page.locator("#area").input_value() == "131067", "前回の入力が復元されていません"

    def test_url_wins_over_saved_input(self, page, base_url):
        """URL にクエリがあれば、保存された値より URL を優先すること（URL が正）。"""
        page.set_default_timeout(15_000)
        page.goto(f"{base_url}/settings")
        page.wait_for_selector('[data-testid="apply-profile"]')
        page.evaluate(
            """() => window.localStorage.setItem('kosodate.profile', JSON.stringify({
                areaCode: '131067', children: [], isPregnant: false,
                isSingleParent: false, hasDisability: false }))"""
        )

        page.goto(f"{base_url}/settings?area_code=131016")
        page.wait_for_selector('[data-testid="apply-profile"]')
        assert page.locator("#area").input_value() == "131016", "URL より保存値が優先されています"

    def test_groups_by_attribute(self, page, base_url):
        """自分の属性に該当する制度が、見出しでまとまること（issue #53）。

        **絞り込みではなく見出し。** 分類コードと本文の一致率は90%で、
        該当しない制度を隠すと「対象なのに出ない」を作る（CLAUDE.md）。
        """
        page.set_default_timeout(15_000)
        page.goto(f"{base_url}/?area_code=131067&child_age_months=40&is_single_parent=true")
        page.wait_for_selector("main ul li")
        headings = page.locator("main h2").all_inner_texts()
        assert any("ひとり親家庭" in h for h in headings), f"属性の見出しが出ていません: {headings}"
        assert any("そのほか" in h for h in headings), f"残りの束が出ていません: {headings}"

    def test_attribute_grouping_does_not_hide_anything(self, page, base_url):
        """見出しを付けても件数が減らないこと。

        **束ねるのであって、絞り込むのではない。** 属性を足したときに
        該当しない制度が消えていたら、それは「対象なのに出ない」を作っている。
        """
        page.set_default_timeout(15_000)
        base = f"{base_url}/?area_code=131067&child_age_months=40"
        # マッチ理由も <ul><li> で描画されるので、カードだけを数える
        page.goto(base)
        page.wait_for_selector('[data-testid="benefit-card"]')
        without = page.locator('[data-testid="benefit-card"]').count()

        page.goto(f"{base}&is_single_parent=true")
        page.wait_for_selector('[data-testid="benefit-card"]')
        assert page.locator('[data-testid="benefit-card"]').count() == without, (
            "属性を指定したら件数が減りました"
        )

    def test_attribute_not_declared_has_no_heading(self, page, base_url):
        """指定していない属性の見出しは出さない（勝手に決めつけない）。"""
        page.set_default_timeout(15_000)
        page.goto(f"{base_url}/?area_code=131067&child_age_months=40")
        page.wait_for_selector("main ul li")
        headings = page.locator("main h2").all_inner_texts()
        assert not any("ひとり親家庭" in h for h in headings), f"指定していない見出しが出ています: {headings}"

    def test_no_attributes_shows_everything(self, page, base_url):
        """属性が無ければ絞り込まない（入力を強制しない）。"""
        page.set_default_timeout(15_000)
        page.goto(base_url)
        page.wait_for_selector('[data-testid="filter-status"]')
        expect(page.locator('[data-testid="filter-status"]')).to_contain_text("すべての制度")
        page.goto(f"{base_url}/?area_code=131067&child_age_months=40")
        page.wait_for_selector('[data-testid="filter-status"]')
        expect(page.locator('[data-testid="filter-status"]')).to_contain_text("絞り込んでいます")
