"""アクセシビリティの自動チェック（issue #58 / docs/adr/0016）。

行政サービスは「みんなの公共サイト運用ガイドライン」で JIS X 8341-3:2016 の
AA 準拠が求められる領域にある。デジタル庁デザインシステムを採用した以上（#33）、
見た目だけ借りて中身を担保しないのは筋が通らない。

**このファイルは「今の状態を守る」ためのもの。** 導入時点で違反ゼロにしてあるので、
落ちたら「新しく壊した」ことを意味する。

axe-core は `axe-playwright-python` に同梱されたものを注入する（外部から取らない）。
ブラウザが実行時に読むものを CDN から取らない方針（ADR 0010）とは別の話だが、
結果的に同じ形になっている。
"""

import re

import pytest
from playwright.sync_api import expect

try:
    from axe_playwright_python.sync_playwright import Axe
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "axe-playwright-python が入っていません。開発用コンテナならイメージが古いので、"
        "ホスト側で 'make agent-up' を実行し直してください（requirements-dev.txt に追加済み）。"
    ) from exc

# WCAG 2.1 AA 相当だけを必須にする。axe の既定は best-practice（AA の要件ではない助言）も
# 含むため、そのままだと「守るべき基準」と「あった方が良い助言」が混ざって判断できない。
WCAG_AA = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
BEST_PRACTICE = ["best-practice"]


def violations(page, tags):
    return Axe().run(page, options={"runOnly": {"type": "tag", "values": tags}}).response["violations"]


def describe(found) -> str:
    lines = []
    for v in found:
        targets = [t for node in v["nodes"] for t in node["target"]]
        lines.append(f"  [{v['impact']}] {v['id']}: {v['help']} — {len(v['nodes'])}件 {targets[:3]}")
    return "\n".join(lines)


def assert_clean(page, tags, label):
    found = violations(page, tags)
    assert found == [], f"{label} に違反があります:\n{describe(found)}"


@pytest.fixture
def top(page, base_url):
    page.set_default_timeout(15_000)
    page.goto(base_url)
    page.wait_for_selector("main ul li")
    return page


@pytest.fixture
def detail(top):
    top.locator("main ul li a").first.click()
    top.wait_for_url(re.compile(r"/benefits/"))
    top.wait_for_selector("main")
    return top


class TestNewPagesHaveNoViolations:
    """自分たちで書いた Next.js の画面。**AA と best-practice の両方**を必須にする。

    導入時点でどちらもゼロだったので、緩める理由が無い。
    """

    def test_top_page(self, top):
        assert_clean(top, WCAG_AA, "トップページ（AA）")
        assert_clean(top, BEST_PRACTICE, "トップページ（best-practice）")

    def test_detail_page(self, detail):
        assert_clean(detail, WCAG_AA, "詳細ページ（AA）")
        assert_clean(detail, BEST_PRACTICE, "詳細ページ（best-practice）")

    def test_not_found_page(self, page, base_url):
        """404 も同じ基準で守る。

        このPRを出した時点では **Next.js 組み込みの 404**（`404 This page could not be
        found.`）が出ており、`<main>` を持たないため best-practice が2件出ていた。
        そのため AA だけを見る形にしていた（レビューで実装と記載の食い違いを指摘された）。

        **#71 で `not-found.tsx` が入って両方とも消えたので、予告どおり引き上げた。**
        `<main>` 1つ・`<h1>` 1つを持つ画面になっている。
        """
        page.set_default_timeout(15_000)
        page.goto(f"{base_url}/no-such-page")
        page.wait_for_selector("main")
        assert_clean(page, WCAG_AA, "404ページ（AA）")
        assert_clean(page, BEST_PRACTICE, "404ページ（best-practice）")


class TestDebugPageMeetsAA:
    """`/debug` は開発・デモ用として残している画面（ADR 0014）。

    **AA だけを必須にし、best-practice は見ない。** 素の JS で書かれた移行期間中の画面で、
    landmark（`main` が無い等）の指摘が残っているが、正式な画面ではないため
    そこを直す投資はしない。一方 AA は「実データの制度情報を出す画面」である以上譲らない。
    """

    def test_debug_page(self, app_page):
        assert_clean(app_page, WCAG_AA, "/debug（AA）")


class TestKeyboardOnly:
    """キーボードだけで一覧 → 詳細 → 公式サイトまで辿れること。

    axe は静的な検査なので、**操作できるかは分からない。** マウスが使えない人にとって
    「対象制度が出ているのに申し込みページへ行けない」のは、出ていないのと同じ。
    """

    def test_reaches_a_benefit_and_opens_it(self, top):
        # 最初の制度リンクに到達するまで Tab を送る（ヘッダーのリンク等を通過する）
        for _ in range(20):
            top.keyboard.press("Tab")
            focused = top.evaluate(
                "() => { const a = document.activeElement;"
                " return a && a.closest('main ul li') ? a.getAttribute('href') : null; }"
            )
            if focused:
                break
        else:
            pytest.fail("Tab を20回送っても一覧の制度リンクに到達できません")

        top.keyboard.press("Enter")
        top.wait_for_url(re.compile(r"/benefits/"))
        expect(top.locator("main h1")).to_be_visible()

    def test_focus_is_visible(self, top):
        """フォーカスが見えること。見えないとキーボード操作は成立しない。

        **`outline-width` で判定してはいけない。** `outline-style: none` にしても
        計算値は残る（描画されないだけでプロパティの値は消えない）。実測すると

            フォーカス表示あり        outlineStyle=auto  outlineWidth=1px
            outline:none で全部消す   outlineStyle=none  outlineWidth=3px

        となり、幅を条件に入れると**何をしても通るテスト**になる（レビューで指摘）。
        いまのフォーカスリングはブラウザ既定の `outline: auto` で出ており、
        デザイン上の理由で `outline: none` を当てるのが最も起きやすい壊し方なので、
        そこを捕まえられる形にしておく。
        """
        top.keyboard.press("Tab")
        style = top.evaluate(
            "() => { const s = getComputedStyle(document.activeElement);"
            " return {outlineStyle: s.outlineStyle, boxShadow: s.boxShadow}; }"
        )
        invisible = style["outlineStyle"] == "none" and style["boxShadow"] == "none"
        assert not invisible, f"フォーカス位置が視覚的に分かりません: {style}"


class TestHeadingStructure:
    """見出しが**要素として**出ていること（issue #58 / #71 のレビュー指摘）。

    デザインシステムの `Heading` はスタイル用の `<div>` を描画するだけで、
    セマンティックな見出しは `HeadingTitle` が担う。両者を取り違えると
    **見た目は見出しなのにアクセシビリティツリーには何も無い**状態になる。
    実際にそうなっており、全ページで `h1` が1つも出ていなかった。
    """

    def test_top_page_has_one_h1(self, top):
        assert top.locator("h1").count() == 1

    def test_detail_page_has_one_h1(self, detail):
        assert detail.locator("h1").count() == 1

    def test_detail_sections_are_h2(self, detail):
        """節見出しは h1 の下の階層にする（飛び級しない）。"""
        levels = detail.evaluate(
            "() => [...document.querySelectorAll('main h1, main h2, main h3')].map(e => Number(e.tagName[1]))"
        )
        assert levels and levels[0] == 1, f"最初の見出しが h1 ではありません: {levels}"
        for previous, current in zip(levels, levels[1:], strict=False):
            assert current <= previous + 1, f"見出しの階層が飛んでいます: {levels}"
