"""年齢推定ルールのテスト。

このロジックが壊れるとマッチ精度が静かに劣化し、
「対象なのに出ない」「対象外なのに出る」が起きるため、重点的にテストする。
背景は docs/adr/0002-age-inference.md を参照。
"""

import pytest

from age_rules import extract_age_range, is_prenatal


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
