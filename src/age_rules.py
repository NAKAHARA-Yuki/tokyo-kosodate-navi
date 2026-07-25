"""制度の対象年齢をテキストから推定するルール群（正規表現のみ。LLMは使わない）。

レジストリの target.greaterThan/lessThan 等に年齢が入っているのは全7,812件中2,794件だけで、
残り64%は「小学校就学前まで」「3歳児」のように文章にしか年齢が書かれていない。
属性マッチングを成立させるため、制度名・対象者テキストから機械的に年齢帯を推定する。

誤検出は「対象外の制度を出す/対象の制度を隠す」に直結するため、
具体的な記述（数値レンジ→学年→就学前→単一境界→段階語）の順に評価し、
推定値は必ず explicit な値と別カラムに保持して age_source で区別できるようにする。
"""

import re

KANJI_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
N = r"([0-9０-９]{1,3}|[一二三四五六七八九十])"


def _num(s):
    if s is None:
        return None
    s = s.strip().translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if s.isdigit():
        return int(s)
    return KANJI_NUM.get(s)


# 学年 -> 月齢下限（4月時点の学年開始年齢）
GRADE_BASE = {"小学": 72, "中学": 144, "高校": 180, "高等学校": 180}


def _norm(text):
    return (text or "").replace("ヵ", "か").replace("ヶ", "か").replace("カ月", "か月")


def _grade_months(school, grade):
    base = GRADE_BASE.get(school)
    if base is None or grade is None:
        return None
    return base + (grade - 1) * 12


def extract_age_range(text):
    """テキストから年齢範囲(月, 閉区間)を推定。(min, max, ルール名) or None。
    誤検出を避けるため、具体的な記述から順に評価する。"""
    t = _norm(text)
    if not t:
        return None

    # ---- 1. 数値による明示レンジ（最も確度が高い）----
    m = re.search(N + r"\s*歳\s*(?:から|〜|～|-|―)\s*" + N + r"\s*歳\s*(?:未満|の誕生日の前日)", t)
    if m:
        a, b = _num(m.group(1)), _num(m.group(2))
        if a is not None and b is not None and a < b:
            return a * 12, b * 12 - 1, "range_years_exclusive"
    m = re.search(N + r"\s*歳\s*(?:から|〜|～|-|―)\s*" + N + r"\s*歳", t)
    if m:
        a, b = _num(m.group(1)), _num(m.group(2))
        if a is not None and b is not None and a <= b:
            return a * 12, b * 12 + 11, "range_years"
    m = re.search(r"(?:生後)?\s*" + N + r"\s*か月\s*(?:から|〜|～|-|―)\s*" + N + r"\s*か月", t)
    if m:
        a, b = _num(m.group(1)), _num(m.group(2))
        if a is not None and b is not None and a <= b:
            return a, b, "range_months"

    # ---- 2. 学年レンジ（「小学校6年生から高校1年生」など）----
    grade_pat = r"(小学|中学|高校|高等学校)(?:校|生)?\s*" + N + r"\s*年"
    grades = [(mm.group(1), _num(mm.group(2))) for mm in re.finditer(grade_pat, t)]
    if len(grades) >= 2:
        lo = _grade_months(*grades[0])
        hi = _grade_months(*grades[-1])
        if lo is not None and hi is not None and lo <= hi:
            return lo, hi + 11, "range_grades"
    if len(grades) == 1:
        lo = _grade_months(*grades[0])
        if lo is not None:
            return lo, lo + 11, "single_grade"

    # ---- 3. 就学前（「小学校入学前」等。'小学'を含むので学齢判定より必ず先に見る）----
    if re.search(r"就学前|入学前|就学の始期|小学校に入学する前|小学校就学前", t):
        # 「満3歳になった後の4月1日から小学校入学前まで」のように下限が書かれていれば拾う
        m = re.search(r"満?" + N + r"\s*歳[^。]{0,20}?(?:から|以降|以後)", t)
        lo = _num(m.group(1)) * 12 if m and _num(m.group(1)) is not None else 0
        if lo > 71:
            lo = 0
        return lo, 71, "stage_preschool"

    # ---- 3b. 「小・中学校」「中学・高校」のような複合学齢 ----
    if re.search(r"小[・･、]?中学", t):
        return 72, 179, "stage_elementary_juniorhigh"
    if re.search(r"中[・･、]?高(?:校|等学校)", t):
        return 144, 215, "stage_juniorhigh_highschool"

    # ---- 4. 児童手当・児童扶養手当の定型文 ----
    if re.search(r"18\s*歳(?:に達する日|の誕生日)?以後の最初の3月31日", t):
        return 0, 227, "stage_until18fy"

    # ---- 5. 単一境界 ----
    m = re.search(N + r"\s*歳\s*(?:未満|に満たない|の誕生日の前日まで)", t)
    if m and _num(m.group(1)) is not None:
        return 0, _num(m.group(1)) * 12 - 1, "upper_years_exclusive"
    m = re.search(N + r"\s*歳\s*(?:以下|まで)", t)
    if m and _num(m.group(1)) is not None:
        return 0, _num(m.group(1)) * 12 + 11, "upper_years"
    m = re.search(N + r"\s*歳\s*以上", t)
    if m and _num(m.group(1)) is not None:
        return _num(m.group(1)) * 12, None, "lower_years"
    m = re.search(r"(?:生後)?" + N + r"\s*か月\s*(?:未満|まで|以内)", t)
    if m and _num(m.group(1)) is not None:
        return 0, max(_num(m.group(1)) - 1, 0), "upper_months"

    # ---- 6. 「X歳児」「Xか月児」健診など ----
    m = re.search(N + r"\s*歳\s*" + N + r"\s*か月児", t)
    if m:
        y, mo = _num(m.group(1)), _num(m.group(2))
        if y is not None and mo is not None:
            base = y * 12 + mo
            return base, base + 5, "child_age_ym"
    m = re.search(N + r"\s*か月児", t)
    if m and _num(m.group(1)) is not None:
        base = _num(m.group(1))
        return base, base + 2, "child_age_months"
    m = re.search(N + r"\s*歳児", t)
    if m and _num(m.group(1)) is not None:
        y = _num(m.group(1))
        return y * 12, y * 12 + 11, "child_age_years"

    # ---- 7. 学齢（就学前判定を通過した場合のみ）----
    if re.search(r"高校生|高等学校", t):
        return 180, 215, "stage_highschool"
    if re.search(r"中学生|中学校", t):
        return 144, 179, "stage_juniorhigh"
    if re.search(r"小学生|小学校|児童・生徒", t):
        return 72, 143, "stage_elementary"

    # ---- 8. 発達段階語（範囲が広く確度は低め）----
    if re.search(r"新生児", t):
        return 0, 1, "stage_newborn"
    if re.search(r"乳幼児", t):
        return 0, 71, "stage_infant_toddler"
    if re.search(r"乳児", t):
        return 0, 11, "stage_infant"
    if re.search(r"幼児", t):
        return 12, 71, "stage_toddler"
    return None


PRENATAL_RE = re.compile(r"妊婦|妊娠|プレママ|出産予定|産前|マタニティ")


def is_prenatal(text):
    return bool(_norm(text) and PRENATAL_RE.search(_norm(text)))
