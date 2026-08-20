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

# 書類名ではなく「リンクの文言」や「表組みの断片」が混ざる。
# is_probable_document=true の 4,880件を実データで見たときの内訳:
#   HTML の断片（<br> や | の表組み）  213件  例: |求職中|求職活動に関する申立書（PDF 310KB）|
#   「こちら」（リンク文言）              8件  例: 委任状はこちら
#   「外部サイト」「外部リンク」            4件
# いずれも1つの書類を指しておらず、ノードにすると「同じ書類で束ねる」が成立しない（issue #112）。
NON_DOCUMENT_RE = re.compile(r"<[a-zA-Z/]|\||こちら|外部サイト|外部リンク")

# リンクへの導入句。これで始まるものは書類名ではなく案内文
LINK_INTRO_PREFIXES = ("ダウンロードは", "詳しくは", "詳細は", "くわしくは")

# 書類名に付く飾り。**判定ではなく表記の統一に使う**（これ自体は書類名を否定しない）。
# 「医療証交付申請書 （PDF 117.1KB）新しいウィンドウで開きます」のように、
# 自治体ごとにサイズが違うだけで別ノードに割れる（実データで11種類）。
DECORATION_RE = re.compile(
    r"\s*[（(][^（）()]*(?:PDF|ＰＤＦ|エクセル|Excel|ワード|Word|形式|KB|MB)[^（）()]*[）)]"
    r"|新しいウィンドウで開きます|別ウィンドウで開きます",
    re.IGNORECASE,
)

# 行頭の箇条書きマーカー: ・ - * ● (1) （1） 1. 1) 注釈1) など
LIST_MARKER_RE = re.compile(
    r"^[\s]*(?:[・\-\*●○◆■]|[（(]?\s*[0-9０-９]{1,2}\s*[）)\.]|注釈\s*[0-9０-９]+\s*[）)])\s*"
)


def strip_decorations(name: str) -> str:
    """書類名から表記の揺れを生む飾りを外す（ファイルサイズ・リンクの補助文言）。

    **判定には使わない。** 「委任状（PDF：30KB）」は書類名であって、
    括弧の中身が違うだけで別の書類になるわけではない。
    """
    if not name:
        return name
    return DECORATION_RE.sub("", name).strip(" 　,、")


def looks_like_document(name: str) -> bool:
    """書類名らしいか判定する。説明文・注意書きを書類ノードとして表示しないための足切り。"""
    if not name:
        return False
    raw = name.strip()
    text = strip_decorations(raw).strip()
    if not text:
        return False
    # **長さは飾りを外してから測る。** 元の文字列で測ると
    # 「（PDF：98KB）」「新しいウィンドウで開きます」が字数を押し上げ、
    # 40字の足切りに正当な申請書が引っかかる（実データで50件。うち47件はまっとうな申請書等）。
    #
    #   北区ベビーシッター利用支援事業（一時預かり利用支援）補助金交付申請書兼交付請求書（PDF：98KB）
    #     → 50字だが、飾りを外せば40字
    #
    # 生で測ると余計に落とせる非書類は3件だけで、代償に合わない。
    # 「必要な書類が画面に出ない」方が「余計な行が数件混ざる」より重い（CLAUDE.md）。
    if len(text) > 40:
        return False
    # リンクの導入句で始まるものは書類名ではなく文（「ダウンロードは○○（PDF：674KB）」）。
    # 長さを外した分をここで補う。実データでの巻き添えは0件。
    if raw.startswith(LINK_INTRO_PREFIXES):
        return False
    # 1つの書類を指していないもの（表組みの断片・リンクの文言）
    if NON_DOCUMENT_RE.search(text):
        return False
    # **句読点は元の文字列で見る。** strip_decorations は末尾の読点も落とすので、
    # 外した後に見ると「…確認をしますので、」のような文の断片が通る（実際に踏んだ）。
    #
    # 文として終わっているものは書類名ではなく説明文。
    # 読点で終わるものは**文の途中で切れた断片**（分割の副産物）。
    if raw.endswith(("。", "！", "？", "、", "，")):
        return False
    # 接続表現で始まるものは前の文の続き。書類名が「ただし」で始まることはない
    if raw.startswith(("ただし", "また", "なお", "その他", "および", "及び", "かつ")):
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
    # ファイルサイズ等の飾りは代表名の一部ではない。外さないと
    # 「医療証交付申請書（PDF 117.1KB）」と「…（PDF 123.0KB）」が別ノードになる。
    trimmed = strip_decorations(name).strip().strip("「」『』（）()")
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
