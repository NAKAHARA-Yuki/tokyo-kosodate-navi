"""条件文が「他の制度を受けていること」を求めているかを読む（issue #121 / #124）。

`LEADS_TO` の根拠は年齢が地続きなことだけだった（`NEXT_STEP`）。

    児童手当 → 日本脳炎予防接種   対象年齢が35ヶ月→36ヶ月で連続

年齢が隣り合っているだけで、制度としての関係は無い（ADR 0003 / issue #121）。
一方、**条件文に「〜を受けていること」と書かれている関係は根拠が強い**。

    [ひとり親家庭高等職業訓練促進給付金]
      児童扶養手当の支給を受けているか、または同等の所得水準にある方

dev の実測（`scripts/survey_requires_benefit.py`）では、参照 196本のうち
**145本（74%）が児童扶養手当**で、児童扶養手当が所得審査の代理になっている。
ひとり親の方が認定されると、自治体あたり平均3.4件が芋づる式に申請できる。

**いちばん危ないのは否定条件。** 同じ形をしていて意味が逆のものが 151件ある。

    肯定   児童扶養手当の支給を受けている方
    否定   過去にこの給付金を受けたことがない方

肯定として拾うと「受けた人にだけ出す」ものが「受けた人には出さない」制度に化ける。
**同じ制度文に両方書かれている**ことがあるため、文単位で判定する。
否定側をエッジとして持つかは #29（併給関係）の範囲。
"""

import re

# 制度名らしい語。「〜手当」「〜給付金」などで終わるまとまり
BENEFIT_NAME = r"(?P<name>[一-龥ぁ-んァ-ヶ]{2,18}?(?:手当|給付金|助成|医療証|受給者証))"

# その制度を受けていることが条件
REQUIRES_RE = re.compile(
    BENEFIT_NAME + r"(?:の支給)?を?(?:受けている|受給している|受給し[てい]|支給を受けて|認定を受けて)"
)

# **受けて「いない」ことが条件。** 肯定とほとんど同じ形をしている。
EXCLUDES_RE = re.compile(
    r"(?:過去に|既に|すでに)[^。]{0,30}?(?:給付金|手当|訓練給付金)[^。]{0,20}?"
    r"(?:受けたことがない|受給していない|受けていない)"
    r"|受給していない|受けていない|受けることができません|対象となりません|対象外"
)

# 「過去にこの給付金」のような、制度名ではない捕捉を落とす
NOT_A_NAME_RE = re.compile(r"^(?:過去に|既に|すでに|同じ|本事業|この|当該)|過去")


def _sentences(text: str):
    """句点・改行で文に割る。**肯定と否定が同じ制度文に同居する**ため文単位で見る。"""
    for part in re.split(r"[。\n]", text or ""):
        part = part.strip()
        if part:
            yield part


def extract_required_benefit_names(text: str) -> list[str]:
    """「受けていることが条件」として挙げられている制度名を返す。

    **否定の文からは拾わない。** 「過去にこの給付金を受けたことがない方」を
    肯定として扱うと、エッジの意味が逆になる。
    """
    if not text:
        return []
    names: list[str] = []
    for sentence in _sentences(text):
        if EXCLUDES_RE.search(sentence):
            continue
        for m in REQUIRES_RE.finditer(sentence):
            name = m.group("name")
            if NOT_A_NAME_RE.search(name):
                continue
            if name not in names:
                names.append(name)
    return names


# 制度名を構成しうる文字。**この文字が名前の直前にあったら別の制度。**
NAME_CHAR_RE = re.compile(r"[一-龥ぁ-んァ-ヶA-Za-z0-9]")


def title_refers_to(title: str, name: str) -> bool:
    """制度名 `name` が、制度のタイトル `title` を指しているか。

    **単純な部分一致で引いてはいけない。** 「児童扶養手当」は
    「**特別**児童扶養手当」に部分一致するが、両者は別の制度
    （前者はひとり親、後者は障害のある児童が対象）。
    dev では、これを区別しないと誤ったエッジが 126本できる。

    名前の直前が制度名を構成しうる文字なら、より長い別の名前の一部とみなす。
    """
    if not title or not name:
        return False
    index = title.find(name)
    while index != -1:
        if index == 0 or not NAME_CHAR_RE.match(title[index - 1]):
            return True
        index = title.find(name, index + 1)
    return False
