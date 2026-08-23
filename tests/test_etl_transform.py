"""ETL の整形ロジックのテスト（GCP 不要な純粋関数のみ）。

元データの癖（本文への埋め込みリンク、書類欄に混ざる注意書き、表記ゆれ）に
対応するための処理が壊れていないことを担保する。
"""

import pytest

from age_rules import has_multiple_age_stages
from etl_documents import (
    canonical_document_name,
    looks_like_document,
    split_belongings,
    strip_decorations,
)
from etl_graph import build_benefit_edges, build_benefit_row, transform
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

    def test_length_is_checked_after_stripping_decorations(self):
        """**長さは飾りを外してから見る。**

        元の文字列で測ると `（PDF：98KB）` `新しいウィンドウで開きます` が字数を押し上げ、
        40字の足切りに正当な申請書が引っかかる（実データで50件。うち47件は申請書等）。
        余計に落とせる非書類は3件だけで、代償に合わない。
        """
        name = "北区ベビーシッター利用支援事業（一時預かり利用支援）補助金交付申請書兼交付請求書（PDF：98KB）"
        assert len(name) > 40, "飾り込みでは40字を超える前提のテスト"
        assert looks_like_document(name) is True

    def test_link_intro_is_not_a_document(self):
        """長さの足切りを緩めた分、リンクの導入句はここで落とす。

        実データでの巻き添えは0件（この規則で落ちるのはこの1件だけ）。
        """
        name = "ダウンロードは江東区ベビーシッター利用内訳表（PDF：674KB）（別ウィンドウで開きます）"
        assert looks_like_document(name) is False

    def test_sentence_in_parentheses_is_accepted_knowingly(self):
        """**「。）」で終わるものは落とさない。承知のうえで通している。**

        落とせば `（質問票英語版…。）` のような文を1件消せるが、実データでは
        `住民票（申請日前3か月以内に発行された、マイナンバーの記載がないもの。）`
        `診断書（申請日前3か月以内に発行されたもの。）` など**書類15件が巻き添えになる**。
        ちょうど ac6272f で拾えるようにしたものが再び落ちる形になるため採用しない。
        """
        assert (
            looks_like_document("住民票（申請日前3か月以内に発行された、マイナンバーの記載がないもの。）")
            is True
        )
        assert looks_like_document("診断書（申請日前3か月以内に発行されたもの。）") is True

    def test_punctuation_is_checked_before_stripping(self):
        """句読点は外す前に見る。strip_decorations が末尾の読点を落とすため。"""
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
    def _benefit(self, area, lo, hi, title=None, conditions=None):
        return {
            "area_code": area,
            "effective_min_age_months": lo,
            "effective_max_age_months": hi,
            "title": title,
            "conditions_text": conditions,
            "target_persons_text": None,
        }

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


class TestRequiresBenefitEdges:
    """条件文が前提として挙げている制度をつなぐ（issue #121 / #124）。

    `NEXT_STEP` は年齢が地続きなだけで、制度としての関係が無い。
    こちらは**条件文にそう書いてある**ので、利用者に見せられる根拠になる。
    """

    def _b(self, title, conditions=None):
        return {
            "area_code": "131067",
            "area_name": "台東区",
            "effective_min_age_months": None,
            "effective_max_age_months": None,
            "title": title,
            "conditions_text": conditions,
            "target_persons_text": None,
        }

    def _edges(self, benefits):
        return [e for e in build_benefit_edges(benefits, {}) if e["relation"] == "REQUIRES_BENEFIT"]

    def test_前提の制度からエッジを引く(self):
        benefits = {
            "a": self._b("児童扶養手当"),
            "b": self._b("ひとり親家庭高等職業訓練促進給付金", "児童扶養手当の支給を受けている方"),
        }
        edges = self._edges(benefits)
        assert len(edges) == 1
        # **向きは「前提 → その制度」。** 認定されたら次にこれが申請できる、という導線
        assert (edges[0]["from_benefit_id"], edges[0]["to_benefit_id"]) == ("a", "b")
        assert "児童扶養手当を受けている" in edges[0]["reason"]

    def test_受けていないことが条件の文からは引かない(self):
        """**肯定とほとんど同じ形をしている。** 逆に取ると意味が反転する。"""
        benefits = {
            "a": self._b("児童扶養手当"),
            "b": self._b("給付金", "過去にこの給付金を受けたことがない方。児童扶養手当を受給していない方"),
        }
        assert self._edges(benefits) == []

    def test_同じ制度文に肯定と否定が同居していても肯定だけ拾う(self):
        """高等職業訓練促進給付金は実データでこの形（#139）。文単位で判定する。"""
        benefits = {
            "a": self._b("児童扶養手当"),
            "b": self._b(
                "高等職業訓練促進給付金",
                "児童扶養手当の支給を受けている方。過去にこの給付金を受けたことがない方",
            ),
        }
        assert len(self._edges(benefits)) == 1

    def test_特別児童扶養手当を児童扶養手当と混同しない(self):
        """**部分一致で引いてはいけない。** 両者は別の制度で、dev では誤りが126本できた。"""
        benefits = {
            "a": self._b("特別児童扶養手当"),
            "b": self._b("給付金", "児童扶養手当の支給を受けている方"),
        }
        assert self._edges(benefits) == []

    def test_他の自治体の制度にはつながない(self):
        benefits = {
            "a": {**self._b("児童扶養手当"), "area_code": "131016"},
            "b": self._b("給付金", "児童扶養手当の支給を受けている方"),
        }
        assert self._edges(benefits) == []

    def test_前置きごと名前として取らない(self):
        """**左端を固定する。** 末尾だけ決めて最左一致に任せると前置きを飲み込む。

        「ひとり親家庭の父または母が児童扶養手当」を名前として扱っていたため、
        同じ自治体に児童扶養手当があるのにエッジが引けていなかった（レビューでの指摘）。
        """
        benefits = {
            "a": self._b("児童扶養手当"),
            "b": self._b(
                "ひとり親家庭高等学校卒業程度認定試験合格支援事業",
                "ひとり親家庭の父または母が児童扶養手当の支給を受けているか、同等の所得水準にある方",
            ),
        }
        edges = self._edges(benefits)
        assert len(edges) == 1
        assert (edges[0]["from_benefit_id"], edges[0]["to_benefit_id"]) == ("a", "b")

    def test_並んだ前提を全部取る(self):
        """「AおよびB」は動詞が最後にしか付かないので、名前だけ見ると先頭側を落とす。"""
        benefits = {
            "a": self._b("児童扶養手当"),
            "b": self._b("特別児童扶養手当"),
            "c": self._b("給付金", "児童扶養手当および特別児童扶養手当を受給している方"),
        }
        assert {e["from_benefit_id"] for e in self._edges(benefits)} == {"a", "b"}

    def test_自治体名が頭に付く形も引く(self):
        """`title` 側には自治体名が付いていない。"""
        benefits = {
            "a": {**self._b("児童育成手当"), "area_name": "中央区"},
            "b": {**self._b("給付金", "中央区児童育成手当を受けている方"), "area_name": "中央区"},
        }
        assert len(self._edges(benefits)) == 1

    def test_バックスラッシュ区切りでも文に割れる(self):
        """元データにはこれを区切りに使うものがある。割れないと肯定が否定に巻き込まれる。"""
        benefits = {
            "a": self._b("児童扶養手当"),
            "b": self._b(
                "ひとり親家庭高等職業訓練促進給付金等",
                "児童扶養手当の支給を受けているか、同等の所得水準にある方"
                "\\訓練促進給付金と趣旨を同じくする給付を受給していない方",
            ),
        }
        assert len(self._edges(benefits)) == 1

    def test_前提が同じ自治体に無ければ引かない(self):
        """引けない分は条件原文の提示に倒す（#63）。誤ったエッジを作らない。"""
        benefits = {"b": self._b("給付金", "心身障害者福祉手当を受けている方")}
        assert self._edges(benefits) == []


class TestIncomeConditionsInRow:
    """所得条件が benefits 行に入ることを、呼び出し口（build_benefit_row）で確かめる。

    ルール単体が正しくても呼び出し側が別のテキストを渡していれば意味が無い
    （issue #119 でそれをやった）。ここでは実際に組み立てた行を見る。
    """

    @staticmethod
    def _row(conditions=None, target_persons=None):
        rec = {
            "basicInformation": {},
            "institutionName": {"canonicalName": "テスト制度"},
            "target": {"conditions": conditions, "targetPersons": target_persons},
        }
        return build_benefit_row(rec, "test+1")

    def test_金額のしきい値が列に入る(self):
        row = self._row(conditions="市町村民税（所得割）が23万5千円未満であること")
        assert row["income_max_yen"] == 235_000
        assert row["income_basis"] == "tax_levy"
        assert row["income_rule"] == "threshold_upper"

    def test_対象者テキストからも拾う(self):
        row = self._row(target_persons="生活保護を受給している世帯")
        assert row["requires_welfare"] is True
        assert row["excludes_welfare"] is False

    def test_所得条件が無ければ空のまま(self):
        row = self._row(conditions="18歳未満の児童を養育している方")
        assert row["income_max_yen"] is None
        assert row["income_basis"] is None
        assert row["requires_non_taxable"] is False
        assert row["requires_welfare"] is False
        assert row["excludes_welfare"] is False
        assert row["income_rule"] is None

    def test_根拠の文が残る(self):
        row = self._row(conditions="住民税非課税世帯の方が対象です")
        assert row["requires_non_taxable"] is True
        assert "非課税" in row["income_evidence"]


class TestMarkdownTablesInDocuments:
    """必要書類欄に埋まっている Markdown の表と `<br>`（issue #120）。

    元データは表と改行タグを本文に直接埋めている。行単位でしか切っていなかったため、
    **表の1行が丸ごと1つの「書類」**になっていた。
    dev 実測で `|` を含む書類ノードが 579件、区切り行だけのものが 5件あった。
    """

    def test_書類の表はセルに割る(self):
        items = split_belongings("|健康保険証|住民票の写し|国民年金手帳|社員証|")
        assert items == ["健康保険証", "住民票の写し", "国民年金手帳", "社員証"]

    def test_区切り行は落とす(self):
        assert split_belongings("|:----|:----|") == []
        assert split_belongings("|---|---|---|") == []

    def test_brタグで切る(self):
        assert split_belongings("母子健康手帳<br>健康保険証<br>印鑑") == [
            "母子健康手帳",
            "健康保険証",
            "印鑑",
        ]

    def test_書類名が無い表は割らない(self):
        """**日程表や料金表まで割ると、時刻や金額が書類として並ぶ。**

        割らなければ `|` が残るので、従来どおり書類ではないと判定される。
        """
        line = "|12時30分～13時45分|1,920,000円 未満|"
        assert split_belongings(line) == [line]
        assert looks_like_document(line) is False


class TestHeadingLinesAreNotDocuments:
    """欄の**見出し**を書類にしない（issue #120）。

    「必要書類」「申請に必要なもの」はどの制度にも同じ文字列で現れるため、
    ノードにすると「同じ書類が要る制度」が大量に生える
    （dev 実測: 「申請に必要なもの」で 70制度、「必要書類」で 57本のエッジ）。
    """

    @pytest.mark.parametrize(
        "heading",
        [
            "必要書類",
            "申請に必要なもの",
            "持ち物",
            "申請に必要な書類",
            "手続きに必要なもの",
            "接種当日の持ち物",
        ],
    )
    def test_見出しは書類ではない(self, heading):
        assert looks_like_document(heading) is False

    @pytest.mark.parametrize("name", ["本人確認書類", "世帯調書", "母子健康手帳", "就労証明書"])
    def test_正当な書類名を巻き込まない(self, name):
        assert looks_like_document(name) is True


class TestSplitArtifacts:
    """分割の副産物を書類にしない（issue #120）。"""

    def test_括弧が閉じていないものは落とす(self):
        assert looks_like_document("A：個人番号カード（写真のあるマイナンバーカード") is False

    @pytest.mark.parametrize(
        "name",
        [
            "所得関係書類(父・母）",
            "受給者、対象児童の戸籍謄本(請求日の1か月以内に発行されたもの）",
            "身元確認書類(マイナンバーカード、運転免許証等）",
        ],
    )
    def test_全角と半角が混ざっていても閉じていれば残す(self, name):
        """**元データは片方だけ全角で書くことが多い**（PR #166 のレビュー）。

        種類ごとに数えると、閉じているものまで「閉じていない」と判定して落とす。
        実データで 18件が巻き添えになっていた。
        """
        assert looks_like_document(name) is True

    @pytest.mark.parametrize("cell", ["03(3831)2181", "0人", "1", "（注1）", "(外勤者)"])
    def test_表の値や注記は書類ではない(self, cell):
        assert looks_like_document(cell) is False


class TestSharedDocIgnoresNonDocuments:
    """**書類でないものでエッジを張らない**（issue #120）。

    dev 実測で SHARED_DOC 4,901本のうち 2,656本が、見出し行や表の区切り行で
    張られていた。「同じ書類が要る」という根拠が無く、
    「ついで申請できる」という提示の意味が失われる。
    """

    def _records(self, doc_text: str):
        return [
            {
                "basicInformation": {"psid": f"psid-{i}", "canonicalName": f"制度{i}"},
                "area": {"areaCode": "131024;中央区"},
                "必要書類": doc_text,
            }
            for i in range(2)
        ]

    def _shared_docs(self, doc_text: str):
        result = transform(self._records(doc_text))
        edges = result["benefit_leads_to"]
        if len(edges) == 0:
            return []
        return [e for e in edges.to_dict("records") if e["relation"] == "SHARED_DOC"]

    def test_見出しではエッジを張らない(self):
        assert self._shared_docs("必要書類") == []

    def test_区切り行ではエッジを張らない(self):
        assert self._shared_docs("|:----|:----|") == []

    def test_本物の書類ならエッジを張る(self):
        """**塞ぎすぎていないこと。** ここが空になると、この検査群は無意味になる。"""
        assert len(self._shared_docs("母子健康手帳")) >= 1


class TestDisabilityLimitDoesNotCreateNewCeiling:
    """**上限が無い制度に、新しい上限を付けない**（PR #169 のレビュー）。

    `effective_max_age_months` が NULL の制度は、元々どの年齢にも出ていた。
    そこへ `disability_max_age_months` だけ入ると、
    **障害があると答えた人にだけ**制度が見えなくなる。
    正直に申告した人が不利になる、いちばん避けたい壊れ方。
    """

    def row(self, target: dict):
        return build_benefit_row({"institutionName": {"canonicalName": "児童手当"}, "target": target}, "p1")

    def test_上限が無い制度には入れない(self):
        """条件文にだけ障害と年齢が出てくる形（実データの誤検出はこの形だった）。"""
        r = self.row({"conditions": "障がいの状態にある20歳未満の児童を含む"})
        assert r["effective_max_age_months"] is None
        assert r["disability_max_age_months"] is None

    def test_広げるときだけ入る(self):
        r = self.row(
            {
                "targetPersons": "18歳に到達した年度末まで（障がいの状態にある20歳未満の児童を含む）",
                "lessThanOrEqualTo": {"targetAgeOfMonths": 227},
            }
        )
        assert r["effective_max_age_months"] == 227
        assert r["disability_max_age_months"] == 239


class TestContradictingAgeColumnIsCorrected:
    """**元データの年齢欄が制度名と食い違うとき、制度名を採る**（issue #114）。

    ADR 0002 は explicit を最優先すると決めているが、その前提
    （元データの年齢欄は正しい）が実データでは成り立たない。
    三鷹市「3～4カ月児健康診査」の年齢欄は 36〜71（＝3〜5歳）で、
    **0歳の子に出ず、3〜5歳の子に出る**。
    """

    def row(self, title: str, lo, hi):
        return build_benefit_row(
            {
                "institutionName": {"canonicalName": title},
                "target": {
                    "greaterThanOrEqualTo": {"targetAgeOfMonths": lo},
                    "lessThanOrEqualTo": {"targetAgeOfMonths": hi},
                },
            },
            "psid-1",
        )

    def test_月と歳の取り違えを補正する(self):
        r = self.row("3～4カ月児健康診査", 36, 71)
        assert (r["effective_min_age_months"], r["effective_max_age_months"]) == (3, 4)
        assert r["age_source"] == "corrected"

    def test_元の欄は残す(self):
        """**上書きしない。** 元データに何が書かれていたかは追えるようにする。"""
        r = self.row("3～4カ月児健康診査", 36, 71)
        assert (r["min_age_months"], r["max_age_months"]) == (36, 71)

    def test_重なっていれば元データを尊重する(self):
        """**少しのずれで上書きしない。** 元データのほうが正確なこともある。"""
        r = self.row("1歳6か月児健康診査", 18, 23)
        assert r["age_source"] == "explicit"
        assert (r["effective_min_age_months"], r["effective_max_age_months"]) == (18, 23)

    def test_制度名から読めなければ何もしない(self):
        r = self.row("子育て支援センターのご案内", 36, 71)
        assert r["age_source"] == "explicit"
        assert (r["effective_min_age_months"], r["effective_max_age_months"]) == (36, 71)

    def test_年齢欄が無いものは従来どおり推定(self):
        r = build_benefit_row({"institutionName": {"canonicalName": "3歳児健康診査"}}, "psid-1")
        assert r["age_source"] == "inferred"


class TestCompositeTitlesAreNotCorrected:
    """**複数の段階が並んだ制度名では補正しない**（PR #170 のレビュー）。

    「3から4か月児・1歳6か月児・3歳児健康診査」は同じ制度名で複数行あり、
    行ごとに違う段階の年齢が入っている。制度名からは「この行がどの段階か」を
    決められないので、最初に出てきた年齢を全行に当てると
    **正しい元データを壊す**。何もしないより悪い結果になる。
    """

    def row(self, title: str, lo, hi):
        return build_benefit_row(
            {
                "institutionName": {"canonicalName": title},
                "target": {
                    "greaterThanOrEqualTo": {"targetAgeOfMonths": lo},
                    "lessThanOrEqualTo": {"targetAgeOfMonths": hi},
                },
            },
            "psid-1",
        )

    @pytest.mark.parametrize(
        ("title", "lo", "hi"),
        [
            # 小金井市。同じ制度名で2行あり、それぞれ別の段階の正しい値を持っている
            ("「3から4か月児・1歳6か月児・3歳児健康診査」", 18, 18),
            ("「3から4か月児・1歳6か月児・3歳児健康診査」", 36, 36),
            # 檜原村
            ("1 歳 6 ヶ月児・3 歳児健康診査", 36, 48),
        ],
    )
    def test_複合タイトルは元データを尊重する(self, title, lo, hi):
        r = self.row(title, lo, hi)
        assert r["age_source"] == "explicit"
        assert (r["effective_min_age_months"], r["effective_max_age_months"]) == (lo, hi)

    def test_単一の段階なら従来どおり補正する(self):
        """**塞ぎすぎていないこと。** ここが止まると #114 が直らない。"""
        r = self.row("3～4カ月児健康診査", 36, 71)
        assert r["age_source"] == "corrected"
        assert (r["effective_min_age_months"], r["effective_max_age_months"]) == (3, 4)

    def test_歳と月で1つの年齢を表すものは複合ではない(self):
        assert has_multiple_age_stages("1歳6か月児健康診査") is False
        assert has_multiple_age_stages("1 歳 6 ヶ月児・3 歳児健康診査") is True

    def test_単位を共有した列挙も複合とみなす(self):
        """「2・3・4か月」は数え方によっては1件にしか見えない。"""
        assert has_multiple_age_stages("2・3・4か月の赤ちゃんとママの会") is True
