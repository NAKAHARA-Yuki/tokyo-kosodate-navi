"""年齢推定ルールのテスト。

このロジックが壊れるとマッチ精度が静かに劣化し、
「対象なのに出ない」「対象外なのに出る」が起きるため、重点的にテストする。
背景は docs/adr/0002-age-inference.md を参照。
"""

import pytest

from age_rules import extract_age_range, extract_disability_max_age, is_prenatal


def months(years: int, mons: int = 0) -> int:
    return years * 12 + mons


class TestExplicitRanges:
    """数値で明示されたレンジ。最も確度が高く、優先して評価される。"""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("3歳から5歳まで", (months(3), months(5, 11))),
            ("2歳〜17歳", (months(2), months(17, 11))),
            ("6歳から7歳未満", (months(6), months(7) - 1)),
            ("生後2か月から7か月", (2, 7)),
        ],
    )
    def test_ranges(self, text, expected):
        lo, hi, _rule = extract_age_range(text)
        assert (lo, hi) == expected


class TestSingleBounds:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("3歳未満のお子さん", (0, months(3) - 1)),
            ("18歳以下", (0, months(18, 11))),
            ("12歳以上の方", (months(12), None)),
            ("生後4か月まで", (0, 3)),
        ],
    )
    def test_bounds(self, text, expected):
        lo, hi, _rule = extract_age_range(text)
        assert (lo, hi) == expected


class TestPreschoolBeatsSchoolAge:
    """「小学校入学前」は文字列 '小学' を含むため、
    就学前判定を学齢判定より先に評価しないと小学生と誤判定する（実装当初に踏んだバグ）。"""

    @pytest.mark.parametrize(
        "text",
        [
            "小学校就学前まで",
            "小学校入学前のお子さん",
            "就学前の児童",
            "小学校就学の始期に達するまで",
        ],
    )
    def test_preschool_not_elementary(self, text):
        lo, hi, rule = extract_age_range(text)
        assert rule == "stage_preschool"
        assert hi == months(5, 11), "就学前は5歳11か月までであるべき"
        assert lo <= months(5, 11)

    def test_preschool_with_lower_bound(self):
        """「満3歳になった後の4月1日から小学校入学前まで」は下限も拾う。"""
        lo, hi, rule = extract_age_range("満3歳になった後の4月1日から小学校入学前まで")
        assert rule == "stage_preschool"
        assert lo == months(3)
        assert hi == months(5, 11)

    @pytest.mark.parametrize(
        "text",
        [
            "小学校に入学するまで",
            "小学校入学するまで",
            "小学校入学まで",
            "就学するまで",
            "小学校就学まで",
        ],
    )
    def test_until_entering_school_is_also_preschool(self, text):
        """**「〜まで」の形も就学前**（issue #107）。

        パターンが「前」で終わる形しか見ておらず、実データにある
        「小学校に入学するまで」を取りこぼして学齢（72〜143ヶ月）と誤判定していた。

            母子健康手帳は、妊娠からお子さんが小学校に入学するまでの、母と子の健康の記録です。

        この一文が summary に入っている**妊娠期の制度62件**に 6歳〜11歳11か月 が付いていた。
        """
        lo, hi, rule = extract_age_range(text)
        assert rule == "stage_preschool", f"{text!r} が就学前と判定されていない"
        assert hi == months(5, 11)

    @pytest.mark.parametrize(
        "text",
        [
            "区内在住の未就学児とその保護者",
            "未就学のお子さんと保護者",
            "翌年度小学校へ入学する未就学児の保護者",
        ],
    )
    def test_not_yet_enrolled_is_also_preschool(self, text):
        """**「未就学」も就学前**（issue #117）。

        このパターンが無いために、「未就学児」と書いてあるのに
        「小学校」を含む文と同じ扱い（72〜143か月）になっていた（dev の inferred 37件のうち 7件）。
        #107 とまったく同じ型の取りこぼし。
        """
        lo, hi, rule = extract_age_range(text)
        assert rule == "stage_preschool", f"{text!r} が就学前と判定されていない"
        assert hi == months(5, 11)

    def test_compulsory_education_is_not_preschool(self):
        """「義務教育就学期」は '就学' を含むが**就学後**。就学前より先に判定する。"""
        _lo, _hi, rule = extract_age_range("義務教育就学期にある児童")
        assert rule == "stage_compulsory_education"

    def test_the_real_sentence_from_the_registry(self):
        """実データの原文そのもの。#107 で見つけた 62件はこの形。"""
        lo, hi, rule = extract_age_range(
            "母子健康手帳を交付します。母子健康手帳は、妊娠からお子さんが"
            "小学校に入学するまでの、母と子の健康の記録です。"
        )
        assert rule == "stage_preschool"
        assert (lo, hi) == (0, months(5, 11))


class TestSchoolStages:
    @pytest.mark.parametrize(
        ("text", "expected_rule", "expected"),
        [
            ("小学生の児童", "stage_elementary", (months(6), months(11, 11))),
            ("中学生", "stage_juniorhigh", (months(12), months(14, 11))),
            ("高校生", "stage_highschool", (months(15), months(17, 11))),
            ("小・中学校に在籍する児童・生徒", "stage_elementary_juniorhigh", (months(6), months(14, 11))),
        ],
    )
    def test_stages(self, text, expected_rule, expected):
        lo, hi, rule = extract_age_range(text)
        assert rule == expected_rule
        assert (lo, hi) == expected

    def test_grade_range(self):
        """「小学校6年生から高校1年生相当」のような学年レンジ。"""
        lo, hi, rule = extract_age_range("小学校6年生から高校1年生相当")
        assert rule == "range_grades"
        assert lo == months(11)  # 小学6年 = 6歳 + 5学年
        assert hi == months(15, 11)  # 高校1年 = 15歳 + 11か月


class TestChildAgeExpressions:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("3歳児健康診査", (months(3), months(3, 11))),
            ("1歳6か月児健康診査", (months(1, 6), months(1, 11))),
            ("4か月児健診", (4, 6)),
        ],
    )
    def test_child_age(self, text, expected):
        lo, hi, _rule = extract_age_range(text)
        assert (lo, hi) == expected


class TestDevelopmentStages:
    @pytest.mark.parametrize(
        ("text", "expected_rule"),
        [
            ("新生児聴覚検査", "stage_newborn"),
            ("乳幼児健康診査", "stage_infant_toddler"),
            ("乳児家庭訪問", "stage_infant"),
            ("幼児教育の無償化", "stage_toddler"),
        ],
    )
    def test_stages(self, text, expected_rule):
        _lo, _hi, rule = extract_age_range(text)
        assert rule == expected_rule

    def test_infant_toddler_checked_before_infant(self):
        """『乳幼児』は『乳児』を部分文字列に含むため、順序を間違えると乳児と誤判定する。"""
        _lo, hi, rule = extract_age_range("乳幼児のための制度")
        assert rule == "stage_infant_toddler"
        assert hi == months(5, 11)


class TestChildAllowance:
    def test_until_end_of_fiscal_year_18(self):
        """児童手当・児童扶養手当の定型文。"""
        lo, hi, rule = extract_age_range("18歳に達する日以後の最初の3月31日までの児童")
        assert rule == "stage_until18fy"
        assert lo == 0
        assert hi == 227

    @pytest.mark.parametrize(
        "text",
        [
            "18歳に到達した年度末までの児童を養育している父または母",
            "18歳に到達した年度の末日以前の児童",
            "18歳に達した年度末までの児童",
            "18歳到達の年度末までの児童を養育している方",
            "18歳に達する年度末まで",
            "18歳に達した日の属する年度の末日以前",
        ],
    )
    def test_fiscal_year_end_variants(self, text):
        """同じことを「年度末」と書く自治体がある（issue #117）。

        **この表記を拾えず、児童扶養手当・児童育成手当・ひとり親家庭等医療費助成が
        対象年齢を一つも持てていなかった。** dev で 12件。
        """
        lo, hi, rule = extract_age_range(text)
        assert rule == "stage_until_fiscal_year_end"
        assert (lo, hi) == (0, months(18, 11))

    def test_fiscal_year_end_beats_disability_exception(self):
        """「20歳未満」は障害がある場合の例外。**本則の18歳を採る。**

        例外の方を採ると 0〜239か月になり、対象でない19歳が対象として出る
        （dev で 32件がこの状態だった）。
        """
        lo, hi, rule = extract_age_range(
            "18歳に達する年度末まで（政令で定める程度の障害がある場合は20歳未満）の児童"
        )
        assert rule == "stage_until_fiscal_year_end"
        assert (lo, hi) == (0, months(18, 11))


class TestCompulsoryEducation:
    """「義務教育就学期」は就学**後**（小1〜中3）。'就学' を含むので就学前より先に見る。"""

    @pytest.mark.parametrize(
        "text",
        [
            "義務教育就学期（6歳に達する日の翌日以後の最初の4月1日から15歳に達する日以後の最初の3月31日まで）の児童",
            "市内に住所のある義務教育就学期の児童を養育している方",
        ],
    )
    def test_compulsory_education(self, text):
        lo, hi, rule = extract_age_range(text)
        assert rule == "stage_compulsory_education"
        assert (lo, hi) == (months(6), months(15, 11))

    @pytest.mark.parametrize(
        "text",
        [
            "市内に住所を有する義務教育就学前（6歳に達した日以降最初の3月31日）までの乳幼児",
            "義務教育就学前（6歳に達した日以後最初の3月31日までの間）の児童",
        ],
    )
    def test_before_compulsory_education_is_preschool(self, text):
        """**「義務教育就学前」は就学前。** 前方一致で就学期の規則に食われてはいけない。

        `義務教育就学` だけで判定していたため、乳幼児医療費助成 5件が
        0〜71か月 → 72〜191か月になっていた。**0歳児にこの制度が出なくなる**（PR #128 のレビュー）。
        """
        lo, hi, rule = extract_age_range(text)
        assert rule == "stage_preschool"
        assert (lo, hi) == (0, months(5, 11))

    def test_other_benefit_name_is_not_taken(self):
        """**他制度の名前に反応してはいけない。** しかも当たっていたのは対象外の節。

        ひとり親家庭等医療費助成制度の対象者欄。本則は「18歳に達した日以後の最初の3月31日まで」で、
        `義務教育就学児医療費助成制度` は**対象にならない人**の説明に出てくるだけ。
        72〜191か月と読むと、0〜5歳の子を持つひとり親にこの制度が出なくなる。
        """
        lo, hi, rule = extract_age_range(
            "対象となる方\n"
            "村内に住所があり、次のいずれかに該当する児童（児童が18歳に達した日以後の"
            "最初の3月31日まで・中度以上の障害を持つ児童は20歳未満）とその児童を養育している"
            "父、母または養育者\n"
            "・父母が離婚した児童\n"
            "対象外となる方\n"
            "・乳幼児医療費助成制度医療証、義務教育就学児医療費助成制度医療証、"
            "心身障害者医療費助成制度医療証を交付されている方"
        )
        assert rule != "stage_compulsory_education"
        # main と同じ読み方に戻ること（この PR で変えるつもりが無かった行）
        assert (lo, hi, rule) == (0, months(19, 11), "upper_years_exclusive")


class TestAgeRangesAttachedToChild:
    """「3～4か月児」「0～2歳児クラス」のように、レンジが子どもを指す語に直接付く形。"""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("3～4か月児健康診査", (3, 4)),
            ("6から7か月児健康診査", (6, 7)),
            ("3・4か月児健診", (3, 4)),
            ("おおむね4～6か月のお子さんと保護者", (4, 6)),
            ("生後6～8か月頃の児の保護者", (6, 8)),
        ],
    )
    def test_month_ranges(self, text, expected):
        lo, hi, rule = extract_age_range(text)
        assert rule == "range_months_child"
        assert (lo, hi) == expected

    def test_single_month_form_still_works(self):
        """レンジでない「4か月児」は従来どおり 4〜6か月。"""
        lo, hi, rule = extract_age_range("4か月児健診")
        assert rule == "child_age_months"
        assert (lo, hi) == (4, 6)

    def test_month_without_child_noun_is_not_taken(self):
        """**子どもを指す語が無いものは拾わない。**

        「妊娠8か月」を子の月齢として拾わないための制約。dev の unknown 2,672件のうち
        17件が「妊娠Nか月」を含んでおり、拾うと妊娠中の方に 8か月児向けの制度が出る。
        """
        assert extract_age_range("＜妊娠8か月ころ＞妊娠8か月を迎える妊婦さん") is None

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("3～5歳児クラス", (months(3), months(5, 11))),
            ("0～2歳児クラスの住民税非課税世帯", (0, months(2, 11))),
            ("０～５歳児の保護者", (0, months(5, 11))),
        ],
    )
    def test_year_ranges(self, text, expected):
        lo, hi, rule = extract_age_range(text)
        assert rule == "range_years_child"
        assert (lo, hi) == expected

    def test_multiple_ranges_are_unioned(self):
        """**複数のクラスが並ぶときは全部の和を取る。**

        最初の1つだけを採ると 3〜5歳（36〜71か月）になり、同じ制度の対象である
        0〜2歳児が漏れる。実データでは幼保無償化がこの書き方（dev で 37件）。
        """
        lo, hi, rule = extract_age_range("3～5歳児クラス及び住民税非課税世帯の0～2歳児クラス")
        assert rule == "range_years_child"
        assert (lo, hi) == (0, months(5, 11))

    def test_year_range_without_class_or_child_is_not_taken(self):
        """「0～18歳までの児童の保護者」は従来どおり単一境界で読む（挙動を変えない）。"""
        _lo, _hi, rule = extract_age_range("0～18歳までの児童の保護者")
        assert rule == "upper_years"


class TestDaysAfterBirth:
    """「生後N日」。月齢では表せないが、0か月として持てば判定に使える。"""

    def test_day_range(self):
        lo, hi, rule = extract_age_range("生後5日から7日の赤ちゃん")
        assert rule == "range_days"
        assert (lo, hi) == (0, 0)

    def test_upper_days(self):
        lo, hi, rule = extract_age_range("区内在住で、生後28日未満の赤ちゃんのいる家庭")
        assert rule == "upper_days"
        assert (lo, hi) == (0, 0)

    def test_newborn_wording_wins_over_days(self):
        """**「新生児」と書いてあれば段階語を優先する。**

        「原則として生後28日以内の新生児です。ただし、里帰り出産などで訪問を
        受けられなかった場合には、生後4か月になる前日まで」のように、
        日数は原則で例外はもっと広い。日数に寄せると 1か月の子に新生児訪問が出なくなる。
        """
        lo, hi, rule = extract_age_range("原則として生後28日以内の新生児です。")
        assert rule == "stage_newborn"
        assert (lo, hi) == (0, 1)


class TestNoMatch:
    @pytest.mark.parametrize("text", [None, "", "区内在住の方", "所得制限があります"])
    def test_returns_none(self, text):
        assert extract_age_range(text) is None


class TestPrenatal:
    @pytest.mark.parametrize("text", ["妊婦健康診査", "妊娠中の方", "プレママ教室", "産前産後ヘルパー"])
    def test_detects(self, text):
        assert is_prenatal(text) is True

    @pytest.mark.parametrize("text", [None, "", "3歳児健康診査", "小学生向けの教室"])
    def test_rejects(self, text):
        assert is_prenatal(text) is False


class TestNormalization:
    def test_fullwidth_digits(self):
        """全角数字でも同じ結果になること。"""
        assert extract_age_range("３歳未満") == extract_age_range("3歳未満")

    def test_katakana_ka_variants(self):
        """「ヶ月」「ヵ月」「か月」の表記ゆれを吸収すること。"""
        base = extract_age_range("生後4か月まで")
        assert extract_age_range("生後4ヶ月まで") == base
        assert extract_age_range("生後4ヵ月まで") == base


class TestMultipleTargetGroups:
    """**1つの対象者欄に複数の対象区分が並ぶとき、最初の1つだけを採らない**（PR #128 のレビュー）。

    規則は最初に一致したもので打ち切るため、区分が並んでいると残りを捨てる。
    3件とも「main より狭くなる」＝「対象なのに出ない」向きだった。
    """

    def test_箇条書きで並ぶ区分は全部の和を取る(self):
        """MR接種もれ。(2) だけを採ると 13〜19歳が丸ごと落ちる。"""
        lo, hi, rule = extract_age_range(
            "以下のいずれかに該当する方 "
            "（2）MR2期未接種で、12歳となる日の属する年度の末日までの方 "
            "（3）MR2期未接種で、13歳となる日の属する年度の初日から20歳の誕生日の前日までの方"
        )
        assert rule == "union_of_clauses"
        assert (lo, hi) == (0, months(20) - 1)

    def test_括弧書きの例外では和を取らない(self):
        """**「（障害がある場合は20歳未満）」は同じ区分の中の例外。**

        ここで和を取ると本則（18歳年度末）が例外に引きずられて広がる。
        箇条書きのマーカーがあるものだけを対象にしているのはこのため。
        """
        lo, hi, rule = extract_age_range(
            "18歳に達する年度末まで（政令で定める程度の障害がある場合は20歳未満）の児童"
        )
        assert rule == "stage_until_fiscal_year_end"
        assert (lo, hi) == (0, months(18, 11))

    def test_歳の後ろのか月を子の月齢として拾わない(self):
        """「11か月～1歳4か月頃のお子さん」が 4〜6か月になると、**対象が一人も入らない。**"""
        assert extract_age_range("区内在住で11か月～1歳4か月頃のお子さんの保護者") is None

    def test_年度末が下限として使われている文からは取らない(self):
        """**年度末は上限とは限らない。**

        「1歳になった年度末**から**1か月まで」は補助**期間**の話で、対象年齢ではない。
        0〜23か月と読むと、満3歳児向けの制度が 0〜1歳になる。
        """
        assert (
            extract_age_range(
                "育休対象の子が1歳になった年度末から1か月（4月30日）までが補助対象期間となります"
            )
            is None
        )

    def test_年度末が上限のときは取る(self):
        """「まで」「以前」が付いていれば上限。間に日付が挟まっても拾う。"""
        for text in [
            "18歳に到達した年度末までの児童",
            "18歳に達した年度末（3月31日）までの児童",
            "18歳に到達した年度の末日以前の児童",
        ]:
            lo, hi, rule = extract_age_range(text)
            assert rule == "stage_until_fiscal_year_end", text
            assert (lo, hi) == (0, months(18, 11))


class TestDisabilityMaxAge:
    """**障害のある子だけに適用される上限**（issue #157）。

    「原則18年度末まで。ただし障害のある児童は20歳未満」という二段構えの制度がある。
    前段だけを読むと、18〜19歳で障害のあるお子さんを持つひとり親に
    児童扶養手当・ひとり親家庭等医療費助成が出ない（実データで43件）。
    """

    @pytest.mark.parametrize(
        "text",
        [
            # すべて実データ（足立区・昭島区ほか）
            "18歳に到達した年度末までの児童（政令で定める程度以上の障がいの状態にある20歳未満の児童を含む）",
            "児童が18歳になった年の年度末まで、規則で定める程度の障がいをもつ児童は20歳未満",
            "身体障害者手帳1から3級、愛の手帳1から3度程度の障害児は20歳未満",
        ],
    )
    def test_二段構えの後段を拾う(self, text):
        assert extract_disability_max_age(text) == 239  # 19歳11か月

    def test_障害の記述が無ければ拾わない(self):
        assert extract_disability_max_age("18歳に達する日以後の最初の3月31日まで") is None
        assert extract_disability_max_age("20歳未満の方が対象です") is None

    def test_文が違えば拾わない(self):
        """**同じ文の中にあるものだけ。** 離れた記述で上限が伸びると対象外の人に出る。"""
        assert extract_disability_max_age("障害のある方の相談窓口です。20歳未満の方が対象。") is None

    def test_まで_は誕生年を含む(self):
        assert extract_disability_max_age("障害のある児童は20歳まで") == 251
