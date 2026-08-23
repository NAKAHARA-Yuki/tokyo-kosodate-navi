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

# 元データは必要書類欄に **Markdown の表**と `<br>` を埋め込んでいる（issue #120）。
# 行単位でしか切っていなかったため、表の1行が丸ごと1つの「書類」になっていた。
#
#   |健康保険証|住民票の写し|国民年金手帳|社員証|   ← 4つの書類が1ノードに潰れている
#   |:----|:----|                                  ← 区切り行が「書類」になっている
#
# dev 実測: `|` を含む書類ノードが 579件、区切り行だけのものが 5件で 80制度に繋がっていた。
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
TABLE_SEPARATOR_RE = re.compile(r"^[|｜\s:：]*-{2,}[|｜\s:：\-]*$")

# 「必要書類」「持ち物」のような**欄の見出し**。書類名ではない。
# 見出しなので中身が無く、どの制度にも同じ文字列で現れるため、
# そのままノードにすると「同じ書類が要る制度」が大量に生える（dev で 70制度が「必要書類」で繋がっていた）。
#
# **後方一致にしない。** 「本人確認書類」「世帯調書」のような正当な書類名を巻き込む。
HEADING_RE = re.compile(
    r"^(?:[（(]?[0-9０-９]{1,2}[）)]?[\s.、]*)?"
    r"(?:申請|申込み?|届出|手続き?|接種当日|来所|来庁|受診)?"
    r"(?:時|当日)?(?:に|の)?"
    r"(?:必要(?:な)?(?:もの|書類|物)|持ち物|持参するもの|お持ちいただくもの"
    r"|用意するもの|ご用意いただくもの|持参物)$"
)


# 表を割ってよいかの判断に使う「書類名らしい末尾」。
# 必要書類欄には日程表・料金表も埋まっているので、書類名が1つも無い表は割らない。
DOC_SUFFIX_RE = re.compile(r"(?:証|書|票|手帳|カード|印鑑|通帳|一式|写し)(?:[（(][^）)]*[）)])?$")

# 表をセルに割ると、書類ではないセル（見出し・値・注記）も出てくる（issue #120）。
JAPANESE_RE = re.compile(r"[ぁ-んァ-ヶ一-龥]")
PARENTHESIZED_RE = re.compile(r"^[（(【\[][^）)】\]]*[）)】\]]$")
VALUE_CELL_RE = re.compile(r"^[0-9０-９]+\s*(?:人|円|件|枚|部|通|歳|か月|年|回|割)?$")


def is_heading_line(text: str) -> bool:
    """必要書類欄の**見出し行**か（「必要書類」「申請に必要なもの」など）。"""
    if not text:
        return False
    return bool(HEADING_RE.match(text.strip().strip("【】「」『』［］[]：:")))


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
    # 欄の見出し（「必要書類」「申請に必要なもの」）は書類ではない（issue #120）
    if is_heading_line(text):
        return False
    # **括弧が閉じていないものは分割の副産物。** 表やリンクの途中で切れている。
    #   A：個人番号カード（写真のあるマイナンバーカード
    if text.count("（") != text.count("）") or text.count("(") != text.count(")"):
        return False
    # 表をセルに割った副産物（issue #120）。**書類ではなく表の見出しや値**が混ざる。
    #   03(3831)2181 / 0人 / 1 / (注1) / (外勤者)
    if not JAPANESE_RE.search(text):
        return False  # 電話番号・数字・記号だけ
    if PARENTHESIZED_RE.match(text):
        return False  # 全体が括弧＝注記や区分のラベル
    if VALUE_CELL_RE.match(text):
        return False  # 「0人」「3枚」のような値のセル
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

    **`<br>` と Markdown の表は行として扱う**（issue #120）。元データはこの2つを
    必要書類欄に埋め込んでおり、改行だけで切ると表の1行が丸ごと1つの書類になる。
    表の行はセルに割って、中に入っている書類名を取り出す。区切り行は落とす。
    """
    if not text:
        return []
    items = []
    for raw_line in BR_RE.sub("\n", text).split("\n"):
        line = raw_line.replace("　", " ").strip()
        if not line:
            continue
        for cell in _table_cells(line):
            cell = LIST_MARKER_RE.sub("", cell)
            cell = _clean_text(cell)
            if cell:
                items.append(cell)
    return items


def _table_cells(line: str):
    """Markdown の表なら**セルに割って**返す。表でなければそのまま1件返す。

    区切り行（`|:----|:----|`）は書類ではないので何も返さない。

    **書類名が入っている表だけを割る。** 必要書類欄には日程表や料金表も
    埋まっており（`|12時30分～13時45分|` `|1,920,000円 未満|`）、区別せずに割ると
    それらが書類として並ぶ。割らなければ `|` を含んだままなので、
    従来どおり `NON_DOCUMENT_RE` が書類ではないと判定する。
    """
    if "|" not in line:
        return [line]
    if TABLE_SEPARATOR_RE.match(line):
        return []
    cells = [cell.strip() for cell in line.split("|") if cell.strip()]
    if not any(DOC_SUFFIX_RE.search(cell) for cell in cells):
        return [line]
    return cells
