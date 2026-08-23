"""ロード前のデータ品質チェック（issue #62）。

ここで守りたいのは「壊れたデータが黙って本番に入らないこと」。
`transform()` は例外を投げずに空のテーブルや全部 NULL の列を作れてしまうため、
その状態を検知できることをテストで担保する。

BigQuery は使わない（`check_tables()` は DataFrame と前回件数だけを見る純粋関数）。
"""

import pandas as pd
import pytest

from etl_quality import (
    MAX_CONTRADICTIONS_SHOWN,
    MAX_ROW_DECREASE_RATIO,
    QualityCheckError,
    age_contradictions,
    check_tables,
    run_quality_checks,
    write_step_summary,
)


def healthy_tables(**overrides) -> dict:
    """実データの縮尺を保った、基準を満たすテーブル一式。"""
    benefits = pd.DataFrame(
        {
            "benefit_id": [f"psid-{i}" for i in range(100)],
            "title": [f"制度{i}" for i in range(100)],
            "scheme_id": [f"scheme-{i % 10}" for i in range(100)],
            # 実データは63自治体。下限 55 を満たす数にする
            "area_code": [f"1310{i % 60:02d}" for i in range(100)],
            "description": [f"詳細{i}" for i in range(100)],
            "summary": [f"概要{i}" for i in range(100)],
            # 実測の unknown 比率は34%。上限45%を下回る
            "age_source": ["explicit"] * 40 + ["inferred"] * 30 + ["unknown"] * 30,
        }
    )
    tables = {
        "benefits": benefits,
        "schemes": pd.DataFrame({"scheme_id": [f"scheme-{i}" for i in range(10)]}),
        "statuses": pd.DataFrame(
            {
                "status_id": [f"st-{i}" for i in range(10)],
                "name": [f"条件{i}" for i in range(10)],
                "type": ["AGE"] * 10,
            }
        ),
        "documents": pd.DataFrame(
            {"doc_id": [f"doc-{i}" for i in range(10)], "doc_name": [f"書類{i}" for i in range(10)]}
        ),
        "benefit_requires_status": pd.DataFrame({"benefit_id": ["psid-0"], "status_id": ["st-0"]}),
        "benefit_requires_doc": pd.DataFrame({"benefit_id": ["psid-0"], "doc_id": ["doc-0"]}),
        "benefit_in_scheme": pd.DataFrame({"benefit_id": ["psid-0"], "scheme_id": ["scheme-0"]}),
        "benefit_leads_to": pd.DataFrame({"from_benefit_id": ["psid-0"], "to_benefit_id": ["psid-1"]}),
    }
    tables.update(overrides)
    return tables


class TestHealthyData:
    def test_passes(self):
        assert check_tables(healthy_tables()) == []

    def test_passes_without_previous_counts(self):
        """初回実行（既存テーブルが無い）でも通ること。"""
        assert check_tables(healthy_tables(), previous_counts={}) == []

    def test_row_increase_is_allowed(self):
        """自治体の追加で件数が増えるのは正常なので止めない。"""
        assert check_tables(healthy_tables(), {"benefits": 50}) == []


class TestEmptyTables:
    def test_empty_table_is_rejected(self):
        tables = healthy_tables(benefit_leads_to=pd.DataFrame(columns=["from_benefit_id"]))
        problems = check_tables(tables)
        assert any("benefit_leads_to" in p and "0件" in p for p in problems), problems

    def test_missing_table_is_rejected(self):
        tables = healthy_tables()
        del tables["documents"]
        problems = check_tables(tables)
        assert any("documents" in p and "生成されていない" in p for p in problems), problems


class TestRowCountDrop:
    def test_large_decrease_is_rejected(self):
        """7,812件が3,000件になるような急減を止める。"""
        problems = check_tables(healthy_tables(), {"benefits": 1000})
        assert any("benefits" in p and "件数" in p for p in problems), problems

    def test_small_decrease_is_allowed(self):
        """しきい値内の減少は通す（制度の終了で普通に減る）。"""
        before = int(100 / (1 - MAX_ROW_DECREASE_RATIO)) - 1
        assert check_tables(healthy_tables(), {"benefits": before}) == []


class TestNullRatio:
    def test_missing_title_is_rejected(self):
        benefits = healthy_tables()["benefits"].copy()
        benefits.loc[0, "title"] = None
        problems = check_tables(healthy_tables(benefits=benefits))
        assert any("benefits.title" in p for p in problems), problems

    def test_empty_string_counts_as_missing(self):
        """空文字も欠損として扱う（BigQuery 側で NULL と区別していない列があるため）。"""
        benefits = healthy_tables()["benefits"].copy()
        benefits.loc[0, "title"] = "   "
        problems = check_tables(healthy_tables(benefits=benefits))
        assert any("benefits.title" in p for p in problems), problems

    def test_dropped_column_is_rejected(self):
        """元データの構造変更で列ごと消えるケース。"""
        benefits = healthy_tables()["benefits"].drop(columns=["area_code"])
        problems = check_tables(healthy_tables(benefits=benefits))
        assert any("area_code" in p and "列が無くなっている" in p for p in problems), problems

    def test_ratio_within_limit_is_allowed(self):
        """description は10%まで許容している（実測2.7%）。"""
        benefits = healthy_tables()["benefits"].copy()
        benefits.loc[0:4, "description"] = None  # 5/100 = 5%
        assert check_tables(healthy_tables(benefits=benefits)) == []


class TestAgeSourceDistribution:
    def test_unknown_spike_is_rejected(self):
        """age_rules.py が元データの表現変更に追従できていないサインを拾う。"""
        benefits = healthy_tables()["benefits"].copy()
        benefits["age_source"] = ["unknown"] * 100
        problems = check_tables(healthy_tables(benefits=benefits))
        assert any("age_source" in p and "age_rules.py" in p for p in problems), problems


class TestAreaCount:
    def test_too_few_areas_is_rejected(self):
        benefits = healthy_tables()["benefits"].copy()
        benefits["area_code"] = ["131067"] * 100
        problems = check_tables(healthy_tables(benefits=benefits))
        assert any("自治体" in p for p in problems), problems


class TestReportsAllProblems:
    def test_multiple_problems_are_reported_together(self):
        """1件目で止めない。構造が変わると複数の指標が同時に壊れるため。"""
        benefits = healthy_tables()["benefits"].copy()
        benefits["age_source"] = ["unknown"] * 100
        benefits["area_code"] = ["131067"] * 100
        tables = healthy_tables(benefits=benefits, documents=pd.DataFrame(columns=["doc_id"]))
        assert len(check_tables(tables)) >= 3


class TestRunQualityChecks:
    class _FakeClient:
        """get_table が必ず NotFound を投げる（＝初回実行相当）クライアント。"""

        def get_table(self, table_id):
            from google.cloud.exceptions import NotFound

            raise NotFound(table_id)

    def test_raises_and_does_not_load(self):
        """問題があれば例外。呼び出し側はここで止まるのでロードは走らない。"""
        tables = healthy_tables(documents=pd.DataFrame(columns=["doc_id"]))
        with pytest.raises(QualityCheckError) as exc:
            run_quality_checks(self._FakeClient(), "proj", tables)
        assert "ロードを中止" in str(exc.value)
        assert "documents" in str(exc.value)

    def test_passes_silently_when_healthy(self):
        run_quality_checks(self._FakeClient(), "proj", healthy_tables())


class TestAgeContradictions:
    """**元データの年齢欄が制度名と食い違う**ものを見つける（issue #114）。

    ADR 0002 は `explicit` を最優先すると決めており、その前提は
    「元データの年齢欄は正しい」こと。実データではこれが成り立たない。

    `effective_*` は `explicit` をそのまま使うので、誤った年齢欄が判定に入る。
    三鷹市の「3～4カ月児健康診査」は年齢欄が 36〜71（＝3〜5歳）で、
    **0歳の子に出ず、3〜5歳の子に出る。**
    """

    def frame(self, title: str, lo, hi) -> pd.DataFrame:
        return pd.DataFrame(
            {"title": [title], "min_age_months": [lo], "max_age_months": [hi], "area_name": ["某市"]}
        )

    def test_月と歳を取り違えた年齢欄を見つける(self):
        """実データ（三鷹市）。「カ月」を「歳」として登録している。"""
        found = age_contradictions(self.frame("3～4カ月児健康診査", 36, 71))
        assert len(found) == 1
        assert "3～4カ月児健康診査" in found[0]
        assert "年齢欄=36〜71" in found[0]

    def test_歳を月として登録したものも見つける(self):
        """実データ（青梅市）。「5歳児」の年齢欄が 5〜6 か月になっている。"""
        assert len(age_contradictions(self.frame("5歳児虫歯予防教室", 5, 6))) == 1

    def test_正しいものは挙げない(self):
        assert age_contradictions(self.frame("3～4か月児健康診査", 3, 4)) == []
        assert age_contradictions(self.frame("3歳児健康診査", 36, 47)) == []

    def test_一部でも重なっていれば挙げない(self):
        """**完全一致は求めない。** 境界のずれ（18→19 など）まで挙げると数が多すぎる。"""
        assert age_contradictions(self.frame("1歳6か月児健康診査", 19, 23)) == []

    def test_年齢欄が無いものは見ない(self):
        """年齢欄が無ければ推定に回るので、ここでの矛盾は起きない。"""
        assert age_contradictions(self.frame("3歳児健康診査", None, None)) == []

    def test_制度名から年齢が読めなければ見ない(self):
        assert age_contradictions(self.frame("子育て相談窓口", 36, 47)) == []

    def test_片側だけの範囲も扱える(self):
        """上限だけ・下限だけの年齢欄でも落ちないこと。"""
        assert len(age_contradictions(self.frame("新生児誕生祝金", 12, None))) == 1

    def test_ロードは止めない(self):
        """**報告であって停止条件ではない。** 元データの誤りで ETL を止める理由はない。"""
        tables = healthy_tables()
        tables["benefits"].loc[0, "title"] = "3～4カ月児健康診査"
        tables["benefits"]["min_age_months"] = 36
        tables["benefits"]["max_age_months"] = 71
        assert check_tables(tables) == []


class TestStepSummary:
    """検出結果が**実行画面に出る**ことを守る（issue #159）。

    #141 の検出はログにしか出ておらず、grep した人しか気づけなかった。
    件数が増えたときこそ見たいのに、増えても静かなままだった。
    """

    def test_writes_nothing_without_github_env(self, monkeypatch, tmp_path):
        """手元やテストでは何もしない。環境変数が無いときに落ちないこと。"""
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        assert write_step_summary(healthy_tables(), None, []) is False

    def test_zero_contradictions_is_stated_explicitly(self, monkeypatch, tmp_path):
        """**0件のときも「0件」と書く。**

        「検出されなかった」と「そもそも検査していない」は、
        黙っていると区別できない。
        """
        path = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
        assert write_step_summary(healthy_tables(), None, []) is True
        assert "0件" in path.read_text(encoding="utf-8")

    def test_lists_contradictions_and_caps_them(self, monkeypatch, tmp_path):
        path = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
        n = MAX_CONTRADICTIONS_SHOWN + 5
        many = [f"三鷹市 「健診{i}」 制度名=4〜6か月 / 年齢欄=36〜71か月" for i in range(n)]
        write_step_summary(healthy_tables(), None, many)
        text = path.read_text(encoding="utf-8")
        assert f"{n}件" in text
        assert "三鷹市 「健診0」" in text
        # 全部は貼らない。上限を超えた分は件数だけ示す
        assert f"健診{MAX_CONTRADICTIONS_SHOWN}" not in text
        assert "他 5 件" in text

    def test_shows_row_count_delta(self, monkeypatch, tmp_path):
        """件数は「何と比べていくつか」が分かる形で出す。"""
        path = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
        tables = healthy_tables()
        previous = {name: len(df) - 5 for name, df in tables.items()}
        write_step_summary(tables, previous, [])
        text = path.read_text(encoding="utf-8")
        assert "+5" in text

    def test_appends_instead_of_overwriting(self, monkeypatch, tmp_path):
        """GITHUB_STEP_SUMMARY は他のステップも書く。上書きしないこと。"""
        path = tmp_path / "summary.md"
        path.write_text("先に書かれていたもの\n", encoding="utf-8")
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
        write_step_summary(healthy_tables(), None, [])
        assert "先に書かれていたもの" in path.read_text(encoding="utf-8")
