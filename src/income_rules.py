"""制度の所得条件をテキストから抽出するルール群（正規表現のみ）。

条件文に所得・課税・収入を含む制度は833件（10.7%）あるが、実測（docs/income-conditions.md）で
分かったのは次の点。

- 書き方の型は7種類しかなく、88.8% がいずれかに掛かる
- ただし **しきい値が本文に存在しないものが49.0%** ある（額は別ページの限度額表にある）
- 26.9% は金額ではなく「児童扶養手当を受給しているか」という他制度との関係（→ issue #124）

ここで扱うのは本文だけで完結する ①金額 ②住民税の課税区分 ④生活保護 の3種類。
それ以外は抽出せず、条件原文の提示に倒す（issue #63）。

**「対象外なのに対象と出す」方が「対象なのに出ない」より害が大きい**ため、
向きが読み取れないものは抽出しない（`None` を返す）。金額は所得を表す語と
比較語の両方が近くにある場合しか拾わない（助成額・預貯金額を所得と誤認しないため）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 全角数字を半角に寄せる
_ZEN = str.maketrans("０１２３４５６７８９，", "0123456789,")

# 所得のしきい値であることを示す語。この語から離れた金額は拾わない
INCOME_WORD = r"所得割額?|所得金額|所得額|所得|課税標準額|総収入|収入|年収"

# 金額。「46万円」「23万5千円」「600万円」「1,000円」を1つの塊として捉える
MONEY = r"(?P<money>[0-9][0-9,]*(?:億)?(?:[0-9,]*万)?(?:[0-9,]*千)?[0-9,]*\s*円)"

# 上限を意味する比較語。これが付いていれば「その額まで対象」と読める
# **境界を含むかどうかで分ける。** 「23万5千円未満」と「23万5千円以下」は
# 235,000円ちょうどの人の扱いが逆になる。77,100円や235,000円は国の基準額そのもので、
# ちょうどの人は実在するため、1円の差でも「対象外なのに対象と出す」向きの誤りになる。
UPPER_INCLUSIVE = r"以下|を?超えない|を?こえない|以内"
UPPER_EXCLUSIVE = r"未満"
UPPER = rf"{UPPER_INCLUSIVE}|{UPPER_EXCLUSIVE}"
# 下限を意味する比較語。単体では向きが決まらない（後述）。
# **除外の文脈では上限になり、境界が反転する。**
#   「46万円以上は対象外」   → 対象は 46万円**未満**（境界を含まない）
#   「46万円を超えると対象外」 → 対象は 46万円**以下**（境界を含む）
LOWER_EXCLUDES_BOUNDARY = r"以上"
LOWER_KEEPS_BOUNDARY = r"を?超える|を?こえる|を?上回る|超"
LOWER = rf"{LOWER_EXCLUDES_BOUNDARY}|{LOWER_KEEPS_BOUNDARY}"

# 対象から外すことを示す語
EXCLUDE = r"対象外|対象となりません|対象になりません|除く|除き|除いた|支給されません|支給しません"

# 課税・非課税の主体。自治体ごとに呼び方が違う
TAX_SUBJECT = r"住民税|市民税|区民税|町民税|村民税|市区町村民税|特別区民税|市町村民税|都民税|地方税"

# しきい値が「何の額」なのか。所得と所得税額と所得割額はまったく別の数字で、
# 取り違えると利用者の所得を別の尺度と比較してしまう（入院助産の 8,400円 は所得税額）。
BASIS = (
    ("tax_levy", r"所得割"),  # 住民税の所得割額
    ("income_tax", r"所得税"),  # 所得税額。所得そのものではない
    ("salary", r"総収入|収入|年収"),  # 収入ベース
    ("income", r"所得"),  # 所得ベース
)


@dataclass(frozen=True)
class IncomeCondition:
    """抽出できた所得条件。抽出できなかった項目は None / False のままにする。"""

    max_yen: int | None = None
    # `max_yen` ちょうどの人が対象かどうか。True なら「以下」、False なら「未満」。
    # **しきい値だけでは判定できない。** 国の基準額（77,100円など）ちょうどの人は実在する。
    max_inclusive: bool | None = None
    basis: str | None = None  # income / income_tax / tax_levy / salary
    requires_non_taxable: bool = False  # 住民税非課税であることが要件
    requires_taxable: bool = False  # 逆に課税世帯であることが要件（保育料の多子軽減など）
    requires_welfare: bool = False  # 生活保護受給が要件
    excludes_welfare: bool = False  # 生活保護受給者は対象外
    rule: str | None = None
    evidence: str | None = None

    def is_empty(self) -> bool:
        return (
            self.max_yen is None
            and not self.requires_non_taxable
            and not self.requires_taxable
            and not self.requires_welfare
            and not self.excludes_welfare
        )


def _norm(text: str | None) -> str:
    return (text or "").translate(_ZEN)


def parse_yen(text: str) -> int | None:
    """「23万5千円」「46万円」「1,000円」を円に直す。読めなければ None。"""
    s = text.translate(_ZEN).replace(",", "").replace(" ", "").rstrip("円")
    if not s:
        return None
    total = 0
    for unit, scale in (("億", 100_000_000), ("万", 10_000), ("千", 1_000)):
        if unit in s:
            head, _, s = s.partition(unit)
            if not head.isdigit():
                return None
            total += int(head) * scale
    if s:
        if not s.isdigit():
            return None
        total += int(s)
    return total or None


def _basis_of(fragment: str) -> str | None:
    for name, pattern in BASIS:
        if re.search(pattern, fragment):
            return name
    return None


def _sentences(text: str) -> list[str]:
    """句点・改行・中黒の箇条書きで切る。向きの判定を文単位に閉じるため。"""
    return [s for s in re.split(r"[。\n\r]+|(?=・)", text) if s.strip()]


def _evidence_around(sentence: str, start: int, end: int, width: int = 60) -> str:
    """**根拠になっている箇所の前後**を切り出す。

    文頭から120字で切ると、表組みの長い行では**根拠の金額が窓の外に落ちる**。
    `income_evidence` は抽出の根拠を確かめるための列なので、
    根拠そのものが入っていないと役に立たない（レビューで実例2件）。

        月の途中で生活保護法による保護の適用を受けたとき,その世帯の収入額、資産等が…
        ← 77,101円 が入っていない
    """
    text = sentence.strip()
    left = max(0, start - width)
    right = min(len(text), end + width)
    fragment = text[left:right].strip()
    if left > 0:
        fragment = "…" + fragment
    if right < len(text):
        fragment = fragment + "…"
    return fragment


def _extract_threshold(text: str) -> IncomeCondition | None:
    """本文に書かれた金額のしきい値を拾う。

    所得を表す語と金額と比較語が同じ文にそろっている場合だけ拾う。
    「助成額は月額5,000円」のような金額を所得条件と取り違えないため。
    """
    for sentence in _sentences(text):
        for m in re.finditer(MONEY, sentence):
            head = sentence[: m.start()]
            tail = sentence[m.end() :]
            # 金額の前に所得を表す語が無ければ、所得のしきい値ではない
            income = list(re.finditer(INCOME_WORD, head))
            if not income:
                continue
            # 語が金額から離れすぎているものは別の話をしている
            if m.start() - income[-1].end() > 20:
                continue
            yen = parse_yen(m.group("money"))
            if yen is None:
                continue
            basis = _basis_of(head[income[-1].start() :] or head)
            near = tail[:20]
            if re.match(rf"\s*(?:{UPPER})", near):
                return IncomeCondition(
                    max_yen=yen,
                    max_inclusive=not re.match(rf"\s*(?:{UPPER_EXCLUSIVE})", near),
                    basis=basis,
                    rule="threshold_upper",
                    evidence=_evidence_around(sentence, m.start(), m.end()),
                )
            # 「46万円以上の場合は対象外」は上限。除外語が無ければ向きが決まらないので取らない
            if re.match(rf"\s*(?:{LOWER})", near) and re.search(EXCLUDE, tail):
                return IncomeCondition(
                    max_yen=yen,
                    # 「以上が対象外」なら境界は対象外、「超えると対象外」なら境界は対象
                    max_inclusive=not re.match(rf"\s*(?:{LOWER_EXCLUDES_BOUNDARY})", near),
                    basis=basis,
                    rule="threshold_lower_excluded",
                    evidence=_evidence_around(sentence, m.start(), m.end()),
                )
    return None


def _extract_tax_status(text: str) -> IncomeCondition | None:
    """住民税の課税/非課税を拾う。除外の文脈では要件として扱わない。"""
    for sentence in _sentences(text):
        m = re.search(rf"(?:{TAX_SUBJECT})[^。]{{0,12}}?(非課税|課税)", sentence)
        if not m:
            continue
        # 「非課税世帯を除く」のような文は要件ではないので拾わない
        if re.search(EXCLUDE, sentence[m.end() :][:24]):
            continue
        evidence = sentence.strip()[:120]
        if m.group(1) == "非課税":
            return IncomeCondition(requires_non_taxable=True, rule="tax_non_taxable", evidence=evidence)
        # 「課税」は「非課税」を含まない場合のみ課税世帯要件として扱う
        return IncomeCondition(requires_taxable=True, rule="tax_taxable", evidence=evidence)
    return None


def _extract_welfare(text: str) -> IncomeCondition | None:
    """生活保護を拾う。受給が要件なのか対象外なのかを文単位で判定する。"""
    for sentence in _sentences(text):
        m = re.search(r"生活保護", sentence)
        if not m:
            continue
        evidence = sentence.strip()[:120]
        tail = sentence[m.end() :]
        if re.search(EXCLUDE, tail) or re.search(r"受けていない|受給していない", tail):
            return IncomeCondition(excludes_welfare=True, rule="welfare_excluded", evidence=evidence)
        if re.search(r"受給|受けている|世帯|受給者", tail[:24]):
            return IncomeCondition(requires_welfare=True, rule="welfare_required", evidence=evidence)
    return None


def extract_income_condition(text: str | None) -> IncomeCondition | None:
    """テキストから所得条件を抽出する。読み取れなければ None。

    確度の高い順（金額 → 課税区分 → 生活保護）に評価し、最初に取れたものを返す。
    複数の条件が併記されている場合でも、判定に使うのは1つだけにする。
    残りは条件原文として提示する（issue #63）。
    """
    t = _norm(text)
    if not t:
        return None
    for extract in (_extract_threshold, _extract_tax_status, _extract_welfare):
        found = extract(t)
        if found and not found.is_empty():
            return found
    return None
