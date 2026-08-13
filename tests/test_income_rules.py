"""所得条件の抽出ルールのテスト。

実データ（dev）に実在する書き方をそのまま使っている。
とくに「向きを取り違えないこと」と「所得でない金額を拾わないこと」を厚く見る。
"""

import pytest

from income_rules import IncomeCondition, extract_income_condition, parse_yen


class TestParseYen:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("46万円", 460_000),
            ("23万5千円", 235_000),
            ("600万円", 6_000_000),
            ("1,000円", 1_000),
            ("960万円", 9_600_000),
            ("１０万円", 100_000),  # 全角
            ("5千円", 5_000),
        ],
    )
    def test_読める表記(self, text, expected):
        assert parse_yen(text) == expected

    @pytest.mark.parametrize("text", ["円", "数万円", "約円"])
    def test_読めない表記は取らない(self, text):
        assert parse_yen(text) is None


class TestThreshold:
    def test_未満は上限として取る(self):
        got = extract_income_condition("市町村民税（所得割）が23万5千円未満であること")
        assert got.max_yen == 235_000
        assert got.basis == "tax_levy"
        assert got.rule == "threshold_upper"

    def test_以上プラス対象外も上限として取る(self):
        text = "住民税所得割額が46万円以上の場合は対象外になります"
        got = extract_income_condition(text)
        assert got.max_yen == 460_000
        assert got.rule == "threshold_lower_excluded"

    def test_以上だけでは向きが決まらないので取らない(self):
        # 「所得が100万円以上の方」は対象なのか対象外なのか本文からは決まらない
        got = extract_income_condition("世帯の所得が100万円以上の方")
        assert got is None or got.max_yen is None

    def test_所得税額を所得と取り違えない(self):
        # 入院助産の 8,400円 は所得税額。利用者の所得と比べると桁が違う
        got = extract_income_condition("前年度の所得税の額が8,400円以下の世帯")
        assert got.max_yen == 8_400
        assert got.basis == "income_tax"

    def test_所得割額は所得と区別する(self):
        got = extract_income_condition("市民税所得割額が年額77,100円以下の世帯")
        assert got.basis == "tax_levy"

    def test_所得と無関係な金額は取らない(self):
        # 助成額を所得のしきい値と取り違えると「対象外なのに対象」を作る
        assert extract_income_condition("助成額は月額5,000円です") is None

    def test_資産額は所得として取らない(self):
        text = "世帯員の預貯金等資産の保有額が600万円以下であること"
        got = extract_income_condition(text)
        assert got is None or got.max_yen is None

    def test_所得の語から離れた金額は取らない(self):
        text = "所得を証明する書類を添えて申請してください。なお交付手数料は300円です"
        got = extract_income_condition(text)
        assert got is None or got.max_yen is None


class TestTaxStatus:
    def test_非課税世帯は要件として取る(self):
        got = extract_income_condition("住民税非課税世帯の方が対象です")
        assert got.requires_non_taxable is True
        assert got.requires_taxable is False

    def test_課税世帯も要件として取る(self):
        got = extract_income_condition("住民税課税世帯で対象児童が0～2歳児クラスに該当する")
        assert got.requires_taxable is True
        assert got.requires_non_taxable is False

    def test_自治体ごとの呼び方を拾う(self):
        for subject in ("特別区民税", "市町村民税", "区民税"):
            got = extract_income_condition(f"{subject}が非課税の世帯")
            assert got is not None, subject
            assert got.requires_non_taxable is True, subject

    def test_除外の文脈は要件にしない(self):
        got = extract_income_condition("住民税非課税世帯は対象外です")
        assert got is None or got.requires_non_taxable is False


class TestWelfare:
    def test_受給が要件(self):
        got = extract_income_condition("生活保護を受給している世帯")
        assert got.requires_welfare is True
        assert got.excludes_welfare is False

    def test_除外は要件にしない(self):
        got = extract_income_condition("生活保護を受けている方は対象外です")
        assert got.excludes_welfare is True
        assert got.requires_welfare is False

    def test_受けていないが要件の場合も除外として扱う(self):
        got = extract_income_condition("生活保護を受けていないこと")
        assert got.excludes_welfare is True
        assert got.requires_welfare is False


class TestNoCondition:
    @pytest.mark.parametrize(
        "text",
        [
            None,
            "",
            "18歳未満の児童を養育している方",
            # 額が別ページにあるもの。本文からは抽出できない（49.0% がこの形）
            "保護者の所得が限度額を超えないこと（所得限度額表については下記リンクをご覧ください）",
            # 他制度との関係。数値ではないので #124 で別に扱う
            "児童扶養手当を受給しているか、それと同等の所得水準の方",
            # 条件ではなく自己負担の説明
            "（所得に応じて費用負担があります。）",
        ],
    )
    def test_抽出しない(self, text):
        got = extract_income_condition(text)
        assert got is None or got.is_empty()


class TestPriority:
    def test_金額が課税区分より優先される(self):
        text = "住民税非課税世帯。ただし所得割額が23万5千円未満であること"
        got = extract_income_condition(text)
        assert got.max_yen == 235_000

    def test_根拠の文が返る(self):
        got = extract_income_condition("生活保護を受給している世帯")
        assert "生活保護" in got.evidence


class TestIsEmpty:
    def test_空の条件(self):
        assert IncomeCondition().is_empty() is True

    def test_金額があれば空でない(self):
        assert IncomeCondition(max_yen=1).is_empty() is False
