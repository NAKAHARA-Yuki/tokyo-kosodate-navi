"""必要書類欄の分解・書類名の表記ゆれ統合（docs/data-model.md の整形仕様に対応）。"""

import re

from etl_util import _clean_text

# 必要書類欄の代替フィールド名（自動判別用の候補）
DOCS_CANDIDATES = ["必要書類", "documents", "requiredDocuments", "belongings"]

# 「必要書類」欄には書類名だけでなく注意書きの文章も混ざるため、
# 書類名らしさを判定して is_probable_document フラグを立てる（データ自体は捨てない）。
NON_DOCUMENT_HINTS = (
    "ください",
    "ます。",
    "です。",
    "注意",
    "お問い合わせ",
    "場合は",
    "場合があります",
    "ご覧",
    "できません",
    "必要です",
)

# 行頭の箇条書きマーカー: ・ - * ● (1) （1） 1. 1) 注釈1) など
LIST_MARKER_RE = re.compile(
    r"^[\s]*(?:[・\-\*●○◆■]|[（(]?\s*[0-9０-９]{1,2}\s*[）)\.]|注釈\s*[0-9０-９]+\s*[）)])\s*"
)


def looks_like_document(name: str) -> bool:
    """書類名らしいか判定する。説明文・注意書きを書類ノードとして表示しないための足切り。"""
    if not name:
        return False
    text = name.strip()
    if len(text) > 40:
        return False
    # 文として終わっているものは書類名ではなく説明文
    if text.endswith(("。", "！", "？")):
        return False
    if any(hint in text for hint in NON_DOCUMENT_HINTS):
        return False
    # 読点を複数含むものは文章の可能性が高い
    return text.count("、") < 2


# 同じ書類が自治体ごとに違う名前で書かれるため、代表名に寄せてノードを統合する。
# （左の正規表現にマッチしたら右の代表名にする）
DOCUMENT_ALIASES = [
    (re.compile(r"^(?:母子|親子)(?:健康)?手帳"), "母子健康手帳"),
    (re.compile(r"^(?:健康)?保険証|^健康保険被保険者証|^資格確認書"), "健康保険証"),
    (re.compile(r"^(?:こども|子ども|子供)医療[証費]"), "こども医療証"),
    (re.compile(r"^(?:予防接種)?予診票"), "予防接種予診票"),
    (re.compile(r"^(?:個人番号|マイナンバー)(?:カード)?|^マイナンバーカード"), "マイナンバーカード"),
    (re.compile(r"^印鑑|^印章|^はんこ"), "印鑑"),
    (re.compile(r"^本人確認(?:書類|できるもの)"), "本人確認書類"),
]


def canonical_document_name(name: str) -> str:
    """書類名の表記ゆれを代表名に寄せる。該当しなければ元の名前をそのまま返す。"""
    if not name:
        return name
    trimmed = name.strip().strip("「」『』（）()")
    for pattern, canonical in DOCUMENT_ALIASES:
        if pattern.match(trimmed):
            return canonical
    return trimmed or name


def split_belongings(text: str):
    """必要書類欄のテキストを項目リストに分解する。

    読点「、」では分割しない。「上記以外にも資格審査上、別途書類等をご用意いただく場合があります。」
    のような一文が途中でぶつ切りにされ、意味不明な断片が書類として並んでしまうため。
    行単位で切り、行頭の箇条書きマーカーだけを取り除く。
    """
    if not text:
        return []
    items = []
    for raw_line in text.split("\n"):
        line = raw_line.replace("　", " ").strip()
        if not line:
            continue
        line = LIST_MARKER_RE.sub("", line)
        line = _clean_text(line)
        if line:
            items.append(line)
    return items
