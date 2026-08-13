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

    # 生後N日。月齢では表せないほど短い期間だが、**0か月として扱えば判定に使える**
    # （「生後5日から7日の赤ちゃん」＝先天性代謝異常等検査）。日を月に丸めて持つ。
    m = re.search(r"生後\s*" + N + r"\s*日\s*(?:から|〜|～|-|―)\s*" + N + r"\s*日", t)
    if m:
        a, b = _num(m.group(1)), _num(m.group(2))
        if a is not None and b is not None and a <= b:
            return a // 30, b // 30, "range_days"

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
    #
    # **「〜まで」の形も就学前として扱う。** ADR 0002 は「就学前を学齢より先に評価する」と
    # 定めているが、パターンが「前」で終わる形しか見ておらず、実データにある
    # 「小学校に入学するまで」を取りこぼして学齢（72〜143ヶ月）と誤判定していた。
    # dev で 70件が該当し、うち 62件が誤判定（すべて妊娠期の制度。issue #107）。
    #
    #   母子健康手帳は、妊娠からお子さんが小学校に入学するまでの、母と子の健康の記録です。
    #
    # 「妊娠の届出」に 6歳〜11歳11か月 が付いていた。
    # 「義務教育就学期」は就学**後**（小1〜中3）を指す。「就学」を含むので
    # 就学前の判定より先に見る。6歳の誕生日の翌日以後の最初の4月1日〜15歳以後の最初の3月31日。
    if re.search(r"義務教育就学", t):
        return 72, 191, "stage_compulsory_education"

    # **「未就学」も就学前として扱う。** 「未就学児」は 0〜71か月のことだが、
    # このパターンが無いために「小学校」を含む文と同じ扱い（72〜143か月）になっていた
    # （dev の inferred 37件のうち 7件。#107 と同じ型の取りこぼし）。
    if re.search(
        r"就学前|入学前|就学の始期|小学校に入学する前|小学校就学前|未就学"
        r"|小学校に?入学するまで|小学校入学まで|就学するまで|小学校就学まで",
        t,
    ):
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
    # 同じことを「年度末」と書く自治体がある。**児童扶養手当・児童育成手当・
    # ひとり親家庭等医療費助成がこちらの表記で、対象年齢を一つも持てていなかった**
    # （dev で 12件）。ひとり親の中心的な制度なので効く。
    m = re.search(N + r"\s*歳[^。]{0,12}?(?:年度末|年度の末日|年度末日|年度の3月31日)", t)
    if m and _num(m.group(1)) is not None:
        return 0, _num(m.group(1)) * 12 + 11, "stage_until_fiscal_year_end"

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
    #
    # **レンジの形（「3～4か月児」「3～5歳児クラス」）を先に見る。** 単発の
    # 「Nか月児」だけを見ていたため、「3～4か月児健康診査」が 4〜6か月（4か月児の扱い）に
    # なっていた。正しくは 3〜4か月。
    #
    # **見つかったものは全部の和を取る。** 「3～5歳児クラス及び0～2歳児クラス」のように
    # 1つの制度が複数のクラスを並べる書き方が多く、最初の1つだけ拾うと
    # 3〜5歳（36〜71か月）となって 0〜2歳児が漏れる。
    #
    # 単位を後ろにしか書かない形（「4～6か月のお子さん」）も同じ扱いにするが、
    # **必ず子どもを指す語が直後に付くものだけ**を見る。これが無いと
    # 「妊娠8か月」を子の月齢として拾う（dev の unknown 2,672件のうち 17件が該当）。
    child_noun = r"(?:児|(?:頃|ころ|位|くらい|程度)?\s*の\s*(?:お子さん|子ども|こども|児|乳児|赤ちゃん))"
    sep = r"(?:から|〜|～|-|―|・|､|、)"
    spans = [
        (_num(mm.group(1)), _num(mm.group(2)))
        for mm in re.finditer(N + r"\s*" + sep + r"\s*" + N + r"\s*か月\s*" + child_noun, t)
    ]
    spans = [(a, b) for a, b in spans if a is not None and b is not None and a <= b]
    if spans:
        return min(a for a, _ in spans), max(b for _, b in spans), "range_months_child"
    spans = [
        (_num(mm.group(1)), _num(mm.group(2)))
        for mm in re.finditer(N + r"\s*" + sep + r"\s*" + N + r"\s*歳\s*(?:" + child_noun + r"|クラス)", t)
    ]
    spans = [(a, b) for a, b in spans if a is not None and b is not None and a <= b]
    if spans:
        return min(a for a, _ in spans) * 12, max(b for _, b in spans) * 12 + 11, "range_years_child"

    m = re.search(N + r"\s*歳\s*" + N + r"\s*か月児", t)
    if m:
        y, mo = _num(m.group(1)), _num(m.group(2))
        if y is not None and mo is not None:
            base = y * 12 + mo
            return base, base + 5, "child_age_ym"
    # 「Nか月児」だけでなく「おおむね5か月のお子さん」も同じ意味（離乳食講習会・育児学級）。
    # **「の＋子どもを指す語」を必須にする。** これが無いと「妊娠8か月」を
    # 子の月齢として拾う（dev の unknown 2,672件のうち 17件が「妊娠Nか月」を含む）。
    m = re.search(
        N
        + r"\s*か月\s*(?:児|(?:頃|ころ|位|くらい|程度)?\s*の\s*(?:お子さん|子ども|こども|児|乳児|赤ちゃん))",
        t,
    )
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

    # 「生後N日未満」。**段階語（新生児・乳児）より後に見る。**
    # 「原則として生後28日以内の新生児です。ただし、里帰り出産などで訪問を受けられなかった
    # 場合には、生後4か月になる前日まで」のように、日数は原則で、例外がもっと広い。
    # 日数を優先すると 0か月に狭まり、1か月の子に新生児訪問が出なくなる（dev で 25件）。
    m = re.search(r"生後\s*" + N + r"\s*日\s*(?:未満|まで|以内)", t)
    if m and _num(m.group(1)) is not None:
        return 0, max((_num(m.group(1)) - 1) // 30, 0), "upper_days"
    return None


PRENATAL_RE = re.compile(r"妊婦|妊娠|プレママ|出産予定|産前|マタニティ")


def is_prenatal(text):
    return bool(_norm(text) and PRENATAL_RE.search(_norm(text)))
