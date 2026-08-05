"""ETL の整形ロジックのテスト（GCP 不要な純粋関数のみ）。

元データの癖（本文への埋め込みリンク、書類欄に混ざる注意書き、表記ゆれ）に
対応するための処理が壊れていないことを担保する。
"""

import pytest

from etl_documents import canonical_document_name, looks_like_document, split_belongings
from etl_graph import build_benefit_edges
from etl_normalize import extract_links, normalize_date, normalize_time, normalize_zip
from etl_statuses import _clean_codes, compute_age_bounds


class TestNormalizeDate:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2024-04-01", "2024-04-01"),
            ("2024/04/01", "2024-04-01"),  # 実データに両形式が混在する
            ("2024/4/1", "2024-04-01"),
            ("随時", None),  # 自由記述は DATE にせず原文列に残す
            ("随時\nご自宅に伺います。", None),
            (None, None),
            ("", None),
            ("2024-13-01", None),  # 月が不正
        ],
    )
    def test_normalize(self, value, expected):
        assert normalize_date(value) == expected


class TestNormalizeTime:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("09:00", "09:00"), ("9:00", "09:00"), ("18:45", "18:45"), ("随時", None), (None, None)],
    )
    def test_normalize(self, value, expected):
        assert normalize_time(value) == expected


class TestNormalizeZip:
    def test_adds_hyphen(self):
        assert normalize_zip("1020073") == "102-0073"

    def test_keeps_existing_format(self):
        assert normalize_zip("102-0073") == "102-0073"

    def test_passthrough_unexpected(self):
        assert normalize_zip("郵便番号なし") == "郵便番号なし"


class TestExtractLinks:
    """元データは本文に `タイトル;https://...` 形式でリンクを直接埋め込んでいる。"""

    def test_separates_link_from_text(self):
        links, plain = extract_links("予防接種の案内;https://example.com/a.html をご覧ください")
        assert links == [{"title": "予防接種の案内", "uri": "https://example.com/a.html"}]
        assert "https://" not in plain
        assert "予防接種の案内" in plain

    def test_title_can_contain_spaces(self):
        """タイトルに空白が含まれていても切り詰めない（issue #80）。

        以前は空白を跨げず、`;` の直前のトークンだけを拾っていた。
        実データの 4.1%（1,516件）が `30.5KB)` のようなサイズ表記の断片になっていた。
        """
        links, _ = extract_links(
            "ひとり親家庭医療費助成制度に係る第三者行為による傷病届 "
            "(PDFファイル: 30.5KB);https://example.com/a.pdf"
        )
        assert links[0]["title"] == (
            "ひとり親家庭医療費助成制度に係る第三者行為による傷病届 (PDFファイル: 30.5KB)"
        )

    def test_does_not_swallow_the_preceding_sentence(self):
        """句点をタイトルに含めない。

        空白を跨げるようにした副作用で、直前の文まで飲み込みうる。
        日本語の本文は空白で区切られないため、句点で止める必要がある。
        実データでは 1,495件がこの状態だった。
        """
        links, _ = extract_links("給付先は保護者となります。申請方法はこちら;https://example.com/b.html")
        assert links[0]["title"] == "申請方法はこちら"

    def test_does_not_cross_newline(self):
        links, _ = extract_links("前の行の文章\n申請書のダウンロード;https://example.com/c.pdf")
        assert links[0]["title"] == "申請書のダウンロード"

    def test_leading_prose_is_included_when_no_delimiter(self):
        """区切りが無ければ、直前の語もタイトルに入る（許容している挙動）。

        「詳しくは」のような導入句まで含まれてしまうが、
        タイトルがサイズ表記の断片になるより害が小さいという判断（issue #80）。
        """
        links, _ = extract_links("詳しくは 予防接種の案内;https://example.com/a.html")
        assert links[0]["title"] == "詳しくは 予防接種の案内"

    def test_stops_at_fullwidth_paren(self):
        """URL の後に全角括弧が続く場合、括弧を URL に含めない。"""
        links, _plain = extract_links("小児用肺炎球菌;https://example.com/i.html#haien（初回）")
        assert links[0]["uri"] == "https://example.com/i.html#haien"

    def test_multiple_links(self):
        links, _ = extract_links("A;https://a.example.com\nB;https://b.example.com")
        assert [link["uri"] for link in links] == ["https://a.example.com", "https://b.example.com"]

    def test_no_link(self):
        links, plain = extract_links("リンクのない本文")
        assert links == []
        assert plain == "リンクのない本文"

    def test_none(self):
        assert extract_links(None) == ([], None)


class TestSplitBelongings:
    def test_does_not_split_on_ideographic_comma(self):
        """読点で切ると一文が途中でぶつ切りになる（実際に起きたバグ）。"""
        text = "上記以外にも資格審査上、別途書類等をご用意いただく場合があります。"
        assert split_belongings(text) == [text]

    def test_splits_on_newline(self):
        assert split_belongings("母子健康手帳\n予防接種予診票") == ["母子健康手帳", "予防接種予診票"]

    @pytest.mark.parametrize(
        "line",
        ["・母子健康手帳", "(1)母子健康手帳", "（1）母子健康手帳", "1.母子健康手帳", "注釈1）母子健康手帳"],
    )
    def test_strips_list_markers(self, line):
        assert split_belongings(line) == ["母子健康手帳"]

    def test_ignores_blank_lines(self):
        assert split_belongings("A\n\n　\nB") == ["A", "B"]


class TestLooksLikeDocument:
    @pytest.mark.parametrize("name", ["母子健康手帳", "健康保険証", "申請者及び児童の戸籍謄本"])
    def test_accepts_documents(self, name):
        assert looks_like_document(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "別途書類等をご用意いただく場合があります。",  # 文で終わる
            "児童が無戸籍となる場合には、ご相談ください。",
            "必要な書類がそろっていない場合、申請を受け付けることができません。ご注意ください。",
            "",
        ],
    )
    def test_rejects_prose(self, name):
        assert looks_like_document(name) is False


class TestCanonicalDocumentName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("母子手帳", "母子健康手帳"),
            ("親子健康手帳", "母子健康手帳"),
            ("母子健康手帳", "母子健康手帳"),
            ("健康保険被保険者証", "健康保険証"),
            ("個人番号カード", "マイナンバーカード"),
            ("子ども医療証", "こども医療証"),
        ],
    )
    def test_aliases(self, raw, expected):
        assert canonical_document_name(raw) == expected

    def test_unknown_passes_through(self):
        assert canonical_document_name("特殊な申請書") == "特殊な申請書"


class TestComputeAgeBounds:
    def test_inclusive_bounds(self):
        target = {
            "greaterThanOrEqualTo": {"targetAge": 1, "targetAgeOfMonths": 6},
            "lessThanOrEqualTo": {"targetAge": 2, "targetAgeOfMonths": None},
        }
        assert compute_age_bounds(target) == (18, 24)

    def test_exclusive_bounds_are_shifted(self):
        """greaterThan / lessThan は ±1か月して閉区間に寄せる。"""
        target = {
            "greaterThan": {"targetAge": 1, "targetAgeOfMonths": None},
            "lessThan": {"targetAge": 3, "targetAgeOfMonths": None},
        }
        assert compute_age_bounds(target) == (13, 35)

    def test_empty(self):
        assert compute_age_bounds({}) == (None, None)


class TestCleanCodes:
    def test_splits_and_trims(self):
        assert _clean_codes(["027 ", "002，003"]) == ["027", "002", "003"]

    def test_empty(self):
        assert _clean_codes(None) == []


class TestBuildBenefitEdges:
    def _benefit(self, area, lo, hi):
        return {"area_code": area, "effective_min_age_months": lo, "effective_max_age_months": hi}

    def test_next_step_links_contiguous_ages(self):
        benefits = {
            "a": self._benefit("131067", 0, 11),
            "b": self._benefit("131067", 12, 23),
        }
        edges = build_benefit_edges(benefits, {})
        next_steps = [e for e in edges if e["relation"] == "NEXT_STEP"]
        assert any(e["from_benefit_id"] == "a" and e["to_benefit_id"] == "b" for e in next_steps)

    def test_next_step_skips_distant_ages(self):
        """1年以上空いていたら「次の一歩」とは言えない。"""
        benefits = {
            "a": self._benefit("131067", 0, 11),
            "b": self._benefit("131067", 60, 71),
        }
        edges = build_benefit_edges(benefits, {})
        assert [e for e in edges if e["relation"] == "NEXT_STEP"] == []

    def test_does_not_link_across_municipalities(self):
        """他区の制度に繋がっても申請できないため、同一自治体内に限定する。"""
        benefits = {
            "a": self._benefit("131067", 0, 11),
            "b": self._benefit("131016", 12, 23),
        }
        edges = build_benefit_edges(benefits, {})
        assert edges == []

    def test_shared_doc_links_benefits(self):
        benefits = {
            "a": self._benefit("131067", None, None),
            "b": self._benefit("131067", None, None),
        }
        edges = build_benefit_edges(benefits, {"a": {"DOC_x"}, "b": {"DOC_x"}})
        assert any(e["relation"] == "SHARED_DOC" for e in edges)

    def test_no_self_loop(self):
        benefits = {"a": self._benefit("131067", 0, 11)}
        edges = build_benefit_edges(benefits, {"a": {"DOC_x"}})
        assert all(e["from_benefit_id"] != e["to_benefit_id"] for e in edges)
