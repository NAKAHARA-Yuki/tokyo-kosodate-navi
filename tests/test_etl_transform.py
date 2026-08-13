"""ETL の整形ロジックのテスト（GCP 不要な純粋関数のみ）。

元データの癖（本文への埋め込みリンク、書類欄に混ざる注意書き、表記ゆれ）に
対応するための処理が壊れていないことを担保する。
"""

import pytest

from etl_documents import (
    canonical_document_name,
    looks_like_document,
    split_belongings,
    strip_decorations,
)
from etl_graph import build_benefit_edges, transform
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

    def test_url_only_title_is_dropped(self):
        """タイトルが裸のURLそのものなら表示名として使わない（issue #80）。

        本文にURLが並記されている箇所で起きる。実データで19件。
        title=None にすると、API 側（_links）が uri を表示名に使うフォールバックに乗る。
        """
        links, plain = extract_links("https://example.com/old.html;https://example.com/new.html")
        assert links[0]["title"] is None
        assert links[0]["uri"] == "https://example.com/new.html"
        # 本文はタイトルの取り方に影響されない
        assert "https://example.com/old.html" in plain

    def test_title_with_text_and_url_is_kept(self):
        """URLを含んでいても、意味のある文字列があるタイトルは捨てない。

        「…「医療情報ネット」https://…（外部サイト）」のような形が実データにある。
        URLを一律に剥がすと、この手前の文字列まで失われる。
        """
        links, _ = extract_links(
            "「医療情報ネット」https://example.com/net/（外部サイト）;https://example.com/a.html"
        )
        assert links[0]["title"] == "「医療情報ネット」https://example.com/net/（外部サイト）"

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


class TestUriDoesNotSwallowTheText:
    """URL 側が後ろの本文や次のリンクを飲み込まないこと（issue #86）。

    元データは `タイトル;URL` を本文中に**空白なしで**埋め込んでおり、URL の直後に
    本文がそのまま続く。空白だけを終端にしていた頃は、実データ 35,122件のうち
    5,236件（14.9%）の URI に本文が混ざっていた。この URI は詳細ページの
    関連リンクとして画面に出るため（#63）、放置すると利用者が 404 を踏む。
    """

    def test_prose_after_a_file_link_is_dropped(self):
        """`…/R6riyouyakkan.pdfを必ずご確認ください` の形。issue の実例1。"""
        links, plain = extract_links(
            "利用約款;https://example.lg.jp/babysitter.files/R6riyouyakkan.pdfを必ずご確認ください"
        )
        assert links[0]["uri"] == "https://example.lg.jp/babysitter.files/R6riyouyakkan.pdf"
        # 飲み込んでいた本文は捨てずに本文側へ戻す
        assert "を必ずご確認ください" in plain

    def test_consecutive_links_are_split(self):
        """`A;url・B;url・C;url` が3件に分かれること。issue の実例2。

        `;` を URL の終端に含めていなかったため、3本分が1本の URI に潰れていた。
        """
        links, _plain = extract_links(
            "中部第一福祉課;https://example.lg.jp/a.html"
            "・中部第二福祉課;https://example.lg.jp/b.html"
            "・千住福祉課;https://example.lg.jp/c.html"
        )
        assert [link["uri"] for link in links] == [
            "https://example.lg.jp/a.html",
            "https://example.lg.jp/b.html",
            "https://example.lg.jp/c.html",
        ]

    def test_prose_starting_with_hiragana_is_dropped(self):
        """ひらがなは本文の始まりの目印。URL のパスには助詞が現れない。"""
        links, _plain = extract_links("案内;https://example.jp/apply/guide/60からの申し込みが可能です")
        assert links[0]["uri"] == "https://example.jp/apply/guide/60"

    def test_japanese_path_is_kept(self):
        """**日本語を含む正当な URL は壊さない。** 非ASCII を一律に切ってはいけない。"""
        links, _plain = extract_links("産後ケア;https://example.jimdo.com/産後ケアよりお申し込みください")
        assert links[0]["uri"] == "https://example.jimdo.com/産後ケア"

    def test_all_japanese_path_is_kept_whole(self):
        """パス全体が日本語の URL も実在する（漢字・カタカナだけなので本文と区別できる）。"""
        links, _plain = extract_links("案内;https://example-hp.com/診療科-部門紹介/妊婦健診産後ケア/")
        assert links[0]["uri"] == "https://example-hp.com/診療科-部門紹介/妊婦健診産後ケア/"

    def test_fragment_is_kept(self):
        """`#anchor` は URL の一部。拡張子まで戻して切ってはいけない。"""
        links, _plain = extract_links("無償化;https://example.lg.jp/musyoka.html#ninnkagaiについては")
        assert links[0]["uri"] == "https://example.lg.jp/musyoka.html#ninnkagai"

    def test_percent_encoded_fragment_is_kept(self):
        links, _plain = extract_links("案内;https://example.lg.jp/p001472.html#%E4%BF%9D%E8%82%B2")
        assert links[0]["uri"] == "https://example.lg.jp/p001472.html#%E4%BF%9D%E8%82%B2"

    def test_url_in_a_query_parameter_is_kept(self):
        """読み上げサービスのように URL をパラメータに持つ URL がある。"""
        raw = "音声読み上げ;http://example.com/rsent?customerid=7767&url=https://example.lg.jp/a.html"
        links, _plain = extract_links(raw)
        assert links[0]["uri"] == raw.split(";", 1)[1]

    @pytest.mark.parametrize(
        "suffix,expected_tail",
        [("」", "」"), ("¥1,000", "¥1,000"), ("＜対象＞", "＜対象＞"), ("【施設】", "【施設】")],
    )
    def test_fullwidth_symbols_are_not_part_of_the_url(self, suffix, expected_tail):
        """非ASCII の記号・約物は URL に含めない（実データで `」` 456件など）。"""
        links, plain = extract_links(f"案内;https://example.lg.jp/a.html{suffix}")
        assert links[0]["uri"] == "https://example.lg.jp/a.html"
        assert expected_tail in plain

    def test_kanji_after_an_extension_is_prose(self):
        """`…/jikan.html等` の形。漢字だけなのでひらがな規則では拾えない。"""
        links, _plain = extract_links("保育時間;https://example.lg.jp/hoikuen/jikan.html等")
        assert links[0]["uri"] == "https://example.lg.jp/hoikuen/jikan.html"


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


class TestRejectsNonDocuments:
    """書類名でないものをノードにしない（issue #112）。

    必要書類欄には書類名以外が大量に混ざる。実データ 9,369件のうち
    **47.9% が `is_probable_document=false`** で、残った 4,880件の中にも
    表組みの断片やリンクの文言が 260件あった。

    これらがノードになると、詳細ページの「必要な書類」に
    `|求職中|求職活動に関する申立書（PDF 310KB）|` のようなものが並ぶ。
    """

    @pytest.mark.parametrize(
        "name",
        [
            "|求職中|求職活動に関する申立書（PDF 310KB）|",  # 表組みの断片
            "|1|申請書（PDF形式：52KB）|||",
            "委任状はこちら",  # リンクの文言
            "各種様式はこちら",
            "本人確認ができる書類（外部サイトへリンク）",
            "新宿区で住民税が課税されている場合には、公簿により確認をしますので、",  # 文の断片
            "ただし、受診者の健康保険証で被保険者本人が確認できれば、",  # 前の文の続き
        ],
    )
    def test_rejects(self, name):
        assert looks_like_document(name) is False, f"{name!r} を書類として扱っている"

    @pytest.mark.parametrize(
        "name",
        ["母子健康手帳", "委任状（PDF：30KB）", "申請者及び児童の戸籍謄本", "マイナンバーカード"],
    )
    def test_keeps_real_documents(self, name):
        """飾りが付いていても書類名は落とさない。"""
        assert looks_like_document(name) is True, f"{name!r} を落としている"

    def test_length_is_checked_before_stripping_decorations(self):
        """**長さは飾りを外す前に見る。**

        外してから測ると、リンクの文言が40字以内に収まって通ってしまう
        （実データで27件。実装中に実際に踏んだ）。
        """
        name = "ダウンロードは江東区ベビーシッター利用支援事業補助金交付申請書兼口座振替依頼書（PDF：450KB）"
        assert looks_like_document(name) is False

    def test_punctuation_is_checked_before_stripping(self):
        """句読点も外す前に見る。strip_decorations が末尾の読点を落とすため。"""
        assert looks_like_document("入園申込みに必要な書類一式については、子ども育成課、") is False


class TestDocumentJudgementInThePipeline:
    """**ETL を通した結果**で判定を確かめる（issue #112 / #120）。

    `looks_like_document()` を直接呼ぶテストだけでは足りない。実際の ETL は
    `canonical_document_name()` を経由するため、**関数単体では正しいのに
    実際の経路では効かない**ということが起きる。

    実際に起きた: `looks_like_document(canonical_doc)` を渡していたため、
    `canonical_document_name` の中の `strip_decorations` が飾りと末尾の読点を
    先に外してしまい、「長さ・句読点は元の文字列で見る」が無効になっていた
    （レビューで指摘されるまで気づけなかった）。
    """

    def build(self, doc_text: str):
        result = transform(
            [
                {
                    "basicInformation": {"psid": "psid-1", "canonicalName": "テスト制度"},
                    "必要書類": doc_text,
                }
            ]
        )
        return result["documents"]

    @pytest.mark.parametrize(
        "doc_text",
        [
            # 飾りを外すと40字以内に収まるが、生では超える
            "ダウンロードは江東区ベビーシッター利用支援事業補助金交付申請書兼口座振替依頼書（PDF：450KB）",
            # strip_decorations が末尾の読点を落とすので、外した後だと文に見えない
            "入園申込みに必要な書類一式については、子ども育成課、",
        ],
    )
    def test_rejected_through_the_real_path(self, doc_text):
        docs = self.build(doc_text)
        assert len(docs) == 1
        assert bool(docs.iloc[0]["is_probable_document"]) is False, (
            f"ETL 経路では書類として通ってしまっている: {docs.iloc[0]['doc_name']!r}"
        )

    def test_real_documents_survive_the_real_path(self):
        """落としてはいけない側も ETL 経路で確認する。"""
        docs = self.build("母子健康手帳\n・委任状（PDF：30KB）")
        names = list(docs["doc_name"])
        assert all(bool(x) for x in docs["is_probable_document"]), f"書類を落としている: {names}"


class TestStripDecorations:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("医療証交付申請書 （PDF 117.1KB）新しいウィンドウで開きます", "医療証交付申請書"),
            ("委任状（PDF：30KB）", "委任状"),
            ("母子健康手帳", "母子健康手帳"),
        ],
    )
    def test_strips(self, raw, expected):
        """ファイルサイズ違いで同じ書類が別ノードに割れるのを防ぐ（実データで11種類）。"""
        assert strip_decorations(raw) == expected


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
