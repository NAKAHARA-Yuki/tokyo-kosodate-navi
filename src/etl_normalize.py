"""日付・時刻・郵便番号・埋め込みリンクの正規化（docs/data-model.md の整形仕様に対応）。"""

import re

from etl_util import _clean_text

# '2024-04-01' と '2024/04/01' の両方が実データに存在する
DATE_RE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
# 本文に `タイトル;https://...` の形式で埋め込まれたリンク。
# URLの終端は空白・全角/半角括弧・パイプ（表組み）とする。
#
# タイトル側は **空白を含められる**。以前は `[^\s;|]` で空白を除いていたため、
# 「傷病届 (PDFファイル: 30.5KB);https://…」のように空白を含むタイトルが
# 直前のトークンだけ（`30.5KB)`）に切り詰められていた（issue #80。実データの4.2%）。
#
# 代わりに文の区切りで止める。改行・句点・パイプ（表組みのセル境界）を境界とし、
# 読点は跨ぐ（「AとB、詳しくは案内;URL」のようなタイトルがあるため）。
EMBEDDED_LINK_RE = re.compile(r"([^\n;|。]{0,80}?);(https?://[^\s|（）()]+)")
# タイトルが裸のURLそのものになっている場合の判定（表示名として使わない）
ONLY_URL_RE = re.compile(r"^https?://\S*$")


def normalize_date(value):
    """'2024-04-01' / '2024/04/01' を ISO 日付に正規化する。'随時' 等は None（原文は *_text に残す）。"""
    text = _clean_text(value)
    if not text:
        return None
    m = DATE_RE.match(text)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalize_time(value):
    """'09:00' のような時刻だけ 'HH:MM' に正規化して返す。それ以外は None。"""
    text = _clean_text(value)
    if not text:
        return None
    m = TIME_RE.match(text)
    if not m:
        return None
    hour, minute = int(m.group(1)), m.group(2)
    return f"{hour:02d}:{minute}"


def normalize_zip(value):
    """'1020073' -> '102-0073'。すでにハイフン付き・想定外形式はそのまま返す。"""
    text = _clean_text(value)
    if not text:
        return None
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) == 7:
        return f"{digits[:3]}-{digits[3:]}"
    return text


def extract_links(text):
    """本文中の `タイトル;URL` 形式のリンクを抽出し、(リンク配列, リンク除去済み本文) を返す。

    レジストリの本文にはリンクがこの独自形式で直接埋め込まれており（約4,800件）、
    そのままでは本文が読みにくく URL も構造的に扱えないため分離する。
    """
    if not text or not isinstance(text, str):
        return [], text

    links = []

    def _replace(match):
        title = (match.group(1) or "").strip()
        uri = match.group(2).rstrip("。、,")
        # タイトルが裸のURLそのものになることがある（本文にURLが並記されている箇所）。
        # 表示名として意味が無いので捨て、利用側（app/routers/benefits.py の _links）が
        # uri を表示名に使うフォールバックに任せる。実データで19件（issue #80）。
        link_title = None if ONLY_URL_RE.match(title) else (title or None)
        links.append({"title": link_title, "uri": uri})
        # 本文にはタイトルだけ残す（URLは links 側で参照する）。
        # 捨てた場合でも元の文字列を戻す。「本文の内容はタイトルの取り方に影響されない」
        # という性質を保つため（レビューで確認された点）。
        return title

    plain = EMBEDDED_LINK_RE.sub(_replace, text)
    return links, _clean_text(plain)


def normalize_link_list(raw_links):
    """formLink / relatedLink を ARRAY<STRUCT<title, uri>> 用に整える。"""
    result = []
    for item in raw_links or []:
        if not isinstance(item, dict):
            continue
        uri = _clean_text(item.get("uri"))
        if not uri:
            continue
        result.append({"title": _clean_text(item.get("title")), "uri": uri})
    return result


def _period(raw: dict, prefix: str):
    """implementationPeriod / procedurePeriod を日付・時刻・原文テキストに分解する。

    fromYMD には '随時' のような自由記述も入るため、DATE に落とせた場合のみ *_date に入れ、
    原文は必ず *_text に残す（情報を失わないため）。
    """
    raw = raw or {}
    from_ymd = _clean_text(raw.get("fromYMD"))
    to_ymd = _clean_text(raw.get("toYMD"))
    from_hm = _clean_text(raw.get("fromHM"))
    to_hm = _clean_text(raw.get("toHM"))
    return {
        f"{prefix}_from_date": normalize_date(from_ymd),
        f"{prefix}_to_date": normalize_date(to_ymd),
        f"{prefix}_from_date_text": from_ymd,
        f"{prefix}_to_date_text": to_ymd,
        f"{prefix}_from_time": normalize_time(from_hm),
        f"{prefix}_to_time": normalize_time(to_hm),
        f"{prefix}_from_time_text": from_hm,
        f"{prefix}_to_time_text": to_hm,
        f"{prefix}_conditions": _clean_text(raw.get("conditions")),
    }
