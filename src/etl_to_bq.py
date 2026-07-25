"""
東京都「子育て支援制度レジストリ」JSON -> BigQuery ETL

ソースURLからJSONを直接ダウンロードし（ローカルファイルは一切使わない）、
3ノードテーブル（benefits, statuses, documents）と
2エッジテーブル（benefit_requires_status, benefit_requires_doc）に整形して
BigQuery にロードする。

設計方針:
- 元JSONに存在するフィールドは原則すべて benefits テーブルに取り込む（情報の欠落をなくす）。
- 「そのまま使える形」に整形する:
  - 日付は DATE 型、時刻は "HH:MM" 文字列に正規化。'随時' 等の自由記述は *_text 列に退避。
  - 郵便番号は 1020073 -> 102-0073 のようにハイフン区切りへ。
  - 本文に `タイトル;https://...` 形式で埋め込まれたリンクを抽出し、リンク配列＋リンク除去済み本文に分離。
  - リンク類は ARRAY<STRUCT<title, uri>> で保持。
- 判定に使えない自由記述は捨てずに保持し、フラグ列で判別できるようにする。
"""

import hashlib
import os
import re
import sys

import pandas as pd
import requests
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from age_rules import extract_age_range, is_prenatal  # noqa: E402

SOURCE_URL = "https://data.storage.data.metro.tokyo.lg.jp/digitalservice/130001_kosodateshienseido_tokyo.json"
DATASET_ID = "gov_knowledge_db"
LOCATION = "asia-northeast1"

# 制度ID/制度名など、想定される代替フィールド名（自動判別用の候補）
BENEFIT_ID_CANDIDATES = ["制度ID", "benefit_id", "benefitId", "id", "psid"]
TITLE_CANDIDATES = ["制度名", "title", "name", "serviceName"]
CATEGORY_CANDIDATES = ["カテゴリ", "category", "categoryName", "genre"]
SUMMARY_CANDIDATES = ["概要", "summary", "description", "outline"]
DOCS_CANDIDATES = ["必要書類", "documents", "requiredDocuments", "belongings"]

# tag.categoryCode/targetCode/contentsCode の公式コードマスタは未公開のため、
# 全7,812件を集計し「そのコードが付いているレコードのカテゴリ分布」から多数派の意味を推定したラベル。
# 統計的推定であり公式定義ではない点に留意（低頻度コードは「分類XXX」のまま）。
TAG_CODE_LABELS = {
    "categoryCode": {
        "000": "その他",
        "001": "難病・医療費助成",
        "002": "妊娠・出産サポート",
        "003": "予防接種",
        "004": "保育・幼児教育",
        "005": "就学援助・放課後児童クラブ",
        "006": "ひとり親支援",
        "008": "職業訓練給付",
        "009": "難病医療",
        "010": "障がい児支援",
        "013": "出生届出",
        "015": "出産育児一時金等",
        "016": "産前産後の年金保険料免除",
        "018": "子育て支援(自治体独自)",
        "025": "障がい児支援",
        "027": "予防接種・産後ケア",
        "032": "救急窓口",
        "087": "歯科健診",
    },
    "targetCode": {
        "000": "その他",
        "079": "歯科健診対象",
        "086": "妊産婦",
        "087": "子ども全般",
        "088": "ひとり親家庭",
        "089": "未熟児",
        "090": "障がい児",
        "091": "遺児",
        "092": "就学児童",
    },
    "contentsCode": {
        "000": "その他",
        "077": "妊娠届出・出生届",
        "078": "手当・給付金",
        "079": "予防接種",
        "080": "教室・講習会",
        "081": "子育て支援拠点・保育施設一覧",
        "082": "相談窓口",
    },
}

# basicInformation.institutionType / class の値。実データはほぼ単一値だが意味を持たせて保持する。
INSTITUTION_TYPE_LABELS = {1: "地方公共団体", 2: "その他"}


# ---------------------------------------------------------------- 基本ユーティリティ


def _short_hash(text: str, prefix: str, length: int = 12) -> str:
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def _first_present(d: dict, candidates):
    for key in candidates:
        if key in d and d[key] not in (None, ""):
            return key, d[key]
    return None, None


def _get(d, *path):
    """ネストした辞書から安全に値を取り出す。途中が辞書でなければ None。"""
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _clean_text(value):
    """空文字を None に寄せ、前後の空白を落とす。"""
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    text = value.strip()
    return text or None


# ---------------------------------------------------------------- 正規化ヘルパー

# '2024-04-01' と '2024/04/01' の両方が実データに存在する
DATE_RE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
# 本文に `タイトル;https://...` の形式で埋め込まれたリンク。
# URLの終端は空白・全角/半角括弧・パイプ（表組み）とする。
EMBEDDED_LINK_RE = re.compile(r"([^\s;|]{0,80}?);(https?://[^\s|（）()]+)")


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
        links.append({"title": title or None, "uri": uri})
        # 本文にはタイトルだけ残す（URLは links 側で参照する）
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


# ---------------------------------------------------------------- 取得・抽出


def fetch_json(url: str):
    print(f"[fetch] downloading JSON from {url}", flush=True)
    resp = requests.get(url, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    print(f"[fetch] downloaded {len(resp.content):,} bytes", flush=True)
    return data


def extract_records(payload):
    """トップレベルが配列、または {'data': [...]} 等の場合を自動判別してレコード配列を返す。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "results", "items", "records"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    raise ValueError("Unsupported JSON top-level structure")


# ---------------------------------------------------------------- 書類テキストの分解

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


# ---------------------------------------------------------------- 年齢・地域・タグ

STATUS_COLUMNS = ["status_id", "name", "type", "min_age_months", "max_age_months", "code"]


def _blank_status(**overrides):
    row = {c: None for c in STATUS_COLUMNS}
    row.update(overrides)
    return row


def _age_to_months(age_dict: dict):
    """{'targetAge': 1, 'targetAgeOfMonths': 6} -> 18 (歳+か月を通算月数に変換)。両方Noneならnull。"""
    if not age_dict:
        return None
    y = age_dict.get("targetAge")
    m = age_dict.get("targetAgeOfMonths")
    if y is None and m is None:
        return None
    return (y or 0) * 12 + (m or 0)


def compute_age_bounds(target: dict):
    """target の greaterThan(OrEqualTo)/lessThan(OrEqualTo) を月齢の [min, max] 閉区間に正規化する。
    exclusive な境界（greaterThan / lessThan）は ±1 か月して inclusive に寄せる。
    """
    ge = _age_to_months(target.get("greaterThanOrEqualTo"))
    gt = _age_to_months(target.get("greaterThan"))
    lt = _age_to_months(target.get("lessThan"))
    le = _age_to_months(target.get("lessThanOrEqualTo"))

    min_months = ge if ge is not None else (gt + 1 if gt is not None else None)
    max_months = le if le is not None else (lt - 1 if lt is not None else None)
    return min_months, max_months


def _age_label(min_months, max_months) -> str:
    def fmt(months):
        if months is None:
            return None
        y, m = divmod(months, 12)
        parts = []
        if y:
            parts.append(f"{y}歳")
        if m:
            parts.append(f"{m}か月")
        return "".join(parts) if parts else "0歳"

    lo, hi = fmt(min_months), fmt(max_months)
    if lo and hi:
        return f"{lo}〜{hi}"
    if lo:
        return f"{lo}以上"
    return f"{hi}以下"


def describe_age_status(min_months, max_months):
    if min_months is None and max_months is None:
        return None
    status_id = _short_hash(f"AGE|{min_months}|{max_months}", "AGE")
    return _blank_status(
        status_id=status_id,
        name=_age_label(min_months, max_months),
        type="AGE",
        min_age_months=min_months,
        max_age_months=max_months,
    )


def describe_location_status(area_code: str, area_name: str):
    if not area_code:
        return None
    status_id = _short_hash(f"LOCATION|{area_code}", "LOCATION")
    return _blank_status(status_id=status_id, name=area_name or area_code, type="LOCATION", code=area_code)


def _clean_codes(raw_codes):
    """'027 ' や '002，003' のような表記ゆれ（前後空白・カンマ混入）を分割・正規化する。"""
    cleaned = []
    for raw in raw_codes or []:
        for part in re.split(r"[,，]", str(raw)):
            part = part.strip()
            if part:
                cleaned.append(part)
    return cleaned


def tag_code_label(field: str, code: str) -> str:
    return TAG_CODE_LABELS.get(field, {}).get(code, f"分類{code}")


def describe_tag_statuses(tag: dict):
    """tag.categoryCode / targetCode / contentsCode （都の標準分類コード）を属性セグメントの status として展開する。"""
    tag = tag or {}
    mapping = {
        "categoryCode": "TAG_CATEGORY",
        "targetCode": "TAG_TARGET",
        "contentsCode": "TAG_CONTENT",
    }
    results = []
    for field, status_type in mapping.items():
        for code in _clean_codes(tag.get(field)):
            status_id = _short_hash(f"{status_type}|{code}", status_type)
            results.append(
                _blank_status(
                    status_id=status_id,
                    name=f"{tag_code_label(field, code)}（{code}）",
                    type=status_type,
                    code=code,
                )
            )
    return results


def split_area(area: dict):
    """area.areaCode ('131016;千代田区') を (コード, 名称) に分解する。"""
    area_code = (area or {}).get("areaCode")
    if not area_code:
        return None, None
    code, _, name = str(area_code).partition(";")
    return code or None, name or None


# ---------------------------------------------------------------- レコード -> 行


def build_benefit_row(rec: dict, benefit_id: str) -> dict:
    """1レコードを benefits テーブルの1行（全フィールド取り込み済み）に変換する。"""
    basic = rec.get("basicInformation") or {}
    institution_name = rec.get("institutionName") or {}
    contact = rec.get("contact") or {}
    support = rec.get("support") or {}
    target = rec.get("target") or {}
    tag = rec.get("tag") or {}
    regulation = rec.get("regulation") or {}
    od_info = rec.get("odInformation") or {}
    impl_place = rec.get("implementationPlace") or {}
    gov_link = rec.get("localGovernmentLink") or {}

    # --- 名称・分類 ---
    _, direct_title = _first_present(rec, TITLE_CANDIDATES)
    canonical_name = _clean_text(institution_name.get("canonicalName"))
    short_name = _clean_text(institution_name.get("shortName"))
    title = _clean_text(direct_title) or short_name or canonical_name or "(不明)"
    _, direct_category = _first_present(rec, CATEGORY_CANDIDATES)
    category = _clean_text(direct_category) or canonical_name or "未分類"

    # --- 本文（埋め込みリンクを分離して読みやすくする） ---
    _, direct_summary = _first_present(rec, SUMMARY_CANDIDATES)
    summary_raw = _clean_text(direct_summary) or _clean_text(rec.get("summary"))
    description_raw = _clean_text(rec.get("description"))
    utilization_raw = _clean_text(rec.get("utilization"))

    # 本文以外の説明文にも `タイトル;URL` 形式のリンクが混ざるため、テキスト列は一律で分離する。
    # 収集したリンクは embedded_links にまとめ、各列にはリンク除去済みの読みやすい文面を入れる。
    collected_links = []

    def clean_with_links(value):
        links, plain = extract_links(_clean_text(value))
        collected_links.extend(links)
        return plain

    summary_plain = clean_with_links(summary_raw)
    description_plain = clean_with_links(description_raw)
    utilization_plain = clean_with_links(utilization_raw)

    # --- 年齢・地域・タグコード ---
    min_age_months, max_age_months = compute_age_bounds(target) if isinstance(target, dict) else (None, None)

    # 明示的な年齢が無い場合はテキストから推定する。推定値は別カラムに保持し、
    # effective_* で「使う値」を提供しつつ age_source で確度を判別できるようにする。
    inferred_min = inferred_max = None
    age_rule = None
    if min_age_months is None and max_age_months is None:
        for candidate in (
            target.get("targetPersons") if isinstance(target, dict) else None,
            title,
            summary_plain,
        ):
            result = extract_age_range(candidate)
            if result:
                inferred_min, inferred_max, age_rule = result
                break

    if min_age_months is not None or max_age_months is not None:
        age_source = "explicit"
    elif age_rule:
        age_source = "inferred"
    else:
        age_source = "unknown"

    effective_min = min_age_months if min_age_months is not None else inferred_min
    effective_max = max_age_months if max_age_months is not None else inferred_max

    # 妊娠期の制度は「子どもの年齢」では表せないため独立したフラグで持つ
    prenatal = bool(
        is_prenatal(title)
        or is_prenatal(target.get("targetPersons") if isinstance(target, dict) else None)
        or is_prenatal(canonical_name)
    )
    area_code, area_name = split_area(rec.get("area") or {})
    category_codes = _clean_codes(tag.get("categoryCode"))
    target_codes = _clean_codes(tag.get("targetCode"))
    content_codes = _clean_codes(tag.get("contentsCode"))

    conditions_text = clean_with_links(target.get("conditions")) if isinstance(target, dict) else None
    target_persons_text = clean_with_links(target.get("targetPersons")) if isinstance(target, dict) else None

    # --- 金額（書式が制度ごとに違うため数値化せず原文保持） ---
    cost_text = clean_with_links(rec.get("cost"))
    monetary_support_text = clean_with_links(support.get("monetarySupport"))
    is_free = bool(
        (monetary_support_text and "無料" in monetary_support_text) or (cost_text and "無料" in cost_text)
    )

    row = {
        # ===== 識別・組織 =====
        "benefit_id": benefit_id,
        "organization_code": _clean_text(basic.get("organization")),
        "institution_type": basic.get("institutionType"),
        "institution_type_label": INSTITUTION_TYPE_LABELS.get(basic.get("institutionType")),
        "department": _clean_text(basic.get("compartment")),
        "department_code": _clean_text(basic.get("compartmentCode")),
        "class_code": rec.get("class"),
        # ===== 名称・分類 =====
        "title": title,
        "short_name": short_name,
        "canonical_name": canonical_name,
        "category": category,
        # ===== 本文 =====
        "summary": summary_plain,
        "summary_raw": summary_raw,
        "description": description_plain,
        "description_raw": description_raw,
        "utilization": utilization_plain,
        "utilization_raw": utilization_raw,
        "specific_date": clean_with_links(rec.get("specificDate")),
        "remarks": _clean_text(rec.get("remarks")),
        # ===== ユーザー属性で直接絞り込むための構造化列 =====
        "min_age_months": min_age_months,
        "max_age_months": max_age_months,
        # テキストから推定した年齢（explicit と混ぜず別カラムに保持）
        "inferred_min_age_months": inferred_min,
        "inferred_max_age_months": inferred_max,
        # 実際の絞り込みに使う値。explicit を優先し、無ければ推定値を使う
        "effective_min_age_months": effective_min,
        "effective_max_age_months": effective_max,
        "age_source": age_source,
        "age_inference_rule": age_rule,
        "is_prenatal": prenatal,
        "area_code": area_code,
        "area_name": area_name,
        "category_codes": category_codes,
        "target_codes": target_codes,
        "content_codes": content_codes,
        "category_code_labels": [tag_code_label("categoryCode", c) for c in category_codes],
        "target_code_labels": [tag_code_label("targetCode", c) for c in target_codes],
        "content_code_labels": [tag_code_label("contentsCode", c) for c in content_codes],
        # ===== 対象条件（自由記述は判定に使わず保持のみ） =====
        "has_free_text_conditions": bool(conditions_text),
        "conditions_text": conditions_text,
        "target_persons_text": target_persons_text,
        # ===== 費用・助成 =====
        "cost_text": cost_text,
        "cost_conditions_text": clean_with_links(rec.get("costConditions")),
        "monetary_support_text": monetary_support_text,
        "materially_support_text": clean_with_links(support.get("materiallySupport")),
        "support_description": clean_with_links(support.get("description")),
        "is_free": is_free,
        # ===== 問い合わせ先 =====
        "contact_name": _clean_text(contact.get("contactName")),
        "contact_phone": _clean_text(contact.get("contactPhone")),
        "contact_ext": _clean_text(contact.get("contactEx")),
        "contact_email": _clean_text(contact.get("contactEmail")),
        "contact_url": _clean_text(contact.get("contactUrl")),
        "contact_address": _clean_text(contact.get("contactAddress")),
        "contact_zip": normalize_zip(contact.get("zipCode")),
        "contact_comment": _clean_text(contact.get("contactComment")),
        # ===== リンク =====
        "official_url": _clean_text(gov_link.get("uri")),
        "official_title": _clean_text(gov_link.get("title")),
        "related_links": normalize_link_list(rec.get("relatedLink")),
        "form_links": normalize_link_list(rec.get("formLink")),
        # embedded_links は全テキスト列の処理後に確定するため、下で改めて設定する
        "embedded_links": [],
        # ===== 手続き =====
        "procedure_method": clean_with_links(rec.get("procedureMethod")),
        "procedure_counter": clean_with_links(rec.get("procedureCounter")),
        "procedure_persons": clean_with_links(rec.get("procedurePersons")),
        "procedure_documents_code": _clean_text(_get(rec, "procedureDocumentsCode", "code")),
        "procedure_documents_others": _clean_text(_get(rec, "procedureDocumentsCode", "others")),
        "electronic_submission": rec.get("electronicSubmission"),
        # ===== 実施場所・定員など =====
        "implementation_name": _clean_text(impl_place.get("implementationName")),
        "implementation_address": _clean_text(impl_place.get("implementationAddress")),
        "capacity_text": clean_with_links(rec.get("capacity")),
        "holiday_text": clean_with_links(rec.get("holiday")),
        # ===== 根拠法令 =====
        "regulation_name": _clean_text(regulation.get("regulationName")),
        "regulation_article": _clean_text(regulation.get("regulationArticle")),
        # ===== 更新メタ情報 =====
        "update_date": normalize_date(rec.get("updateDate")),
        "update_date_text": _clean_text(rec.get("updateDate")),
        "od_update_date": normalize_date(od_info.get("updateDate")),
        "od_updater": _clean_text(od_info.get("updater")),
        "od_content": _clean_text(od_info.get("content")),
        "use_consideration_flag": rec.get("useConsiderationFlag"),
    }

    # 期間系（実施期間・手続き期間・利用時間）
    row.update(_period(rec.get("implementationPeriod"), "implementation_period"))
    row.update(_period(rec.get("procedurePeriod"), "procedure_period"))

    utilization_time = rec.get("utilizationTime") or {}
    row.update(
        {
            "utilization_from_time": normalize_time(utilization_time.get("fromHM")),
            "utilization_to_time": normalize_time(utilization_time.get("toHM")),
            "utilization_from_time_text": _clean_text(utilization_time.get("fromHM")),
            "utilization_to_time_text": _clean_text(utilization_time.get("toHM")),
            # 元データのキー名が 'condiutilizationConditionstions' と壊れているためそのまま参照する
            "utilization_conditions": clean_with_links(
                utilization_time.get("condiutilizationConditionstions") or utilization_time.get("conditions")
            ),
        }
    )

    # 全テキスト列から集めた埋め込みリンクを URI 重複排除のうえ格納する
    seen = set()
    unique_links = []
    for link in collected_links:
        if link["uri"] in seen:
            continue
        seen.add(link["uri"])
        unique_links.append(link)
    row["embedded_links"] = unique_links
    return row


# ---------------------------------------------------------------- スキルツリー（制度間の関係）

# 同時に申請しやすいかを測る「代表的な書類」。どの制度にも出る汎用書類でつなぐと
# エッジが爆発して意味がなくなるため、出現数が極端に多い書類は除外する。
SYNERGY_DOC_MAX_SHARE = 0.05  # 全制度の5%超に出る書類は汎用すぎるとみなす
MAX_EDGES_PER_BENEFIT = 6


def build_benefit_edges(benefits: dict, benefit_docs: dict):
    """制度同士の関係（スキルツリー）を確定ルールで生成する。

    - NEXT_STEP : 同一自治体で年齢帯が地続きの制度（妊娠→出生→健診→予防接種…の流れ）
    - SHARED_DOC: 同一自治体で特徴的な必要書類を共有する制度（ついで申請できる）

    LLMは使わず、年齢と書類の一致という機械的に検証できる根拠のみを使う。
    """
    edges = []
    seen = set()

    by_area = {}
    for benefit_id, row in benefits.items():
        by_area.setdefault(row.get("area_code"), []).append(benefit_id)

    # 汎用的すぎる書類を除外するための出現数集計
    doc_usage = {}
    for doc_ids in benefit_docs.values():
        for doc_id in doc_ids:
            doc_usage[doc_id] = doc_usage.get(doc_id, 0) + 1
    max_usage = max(len(benefits) * SYNERGY_DOC_MAX_SHARE, 2)

    def add_edge(src, dst, relation, reason):
        key = (src, dst, relation)
        if src == dst or key in seen:
            return
        seen.add(key)
        edges.append({"from_benefit_id": src, "to_benefit_id": dst, "relation": relation, "reason": reason})

    for area_code, ids in by_area.items():
        if not area_code:
            continue

        # ---- NEXT_STEP: 年齢帯が隣接/接続する制度をつなぐ ----
        aged = [
            (benefits[i]["effective_min_age_months"], benefits[i]["effective_max_age_months"], i)
            for i in ids
            if benefits[i]["effective_max_age_months"] is not None
            and benefits[i]["effective_min_age_months"] is not None
        ]
        aged.sort(key=lambda x: (x[0], x[1]))
        for idx, (_, cur_max, src) in enumerate(aged):
            linked = 0
            for nxt_min, _, dst in aged[idx + 1 :]:
                if nxt_min < cur_max:  # まだ期間が重なっている＝次の段階ではない
                    continue
                if nxt_min - cur_max > 12:  # 1年以上空くならつながりとは言えない
                    break
                add_edge(src, dst, "NEXT_STEP", f"対象年齢が{cur_max}ヶ月→{nxt_min}ヶ月で連続")
                linked += 1
                if linked >= 2:
                    break

        # ---- SHARED_DOC: 特徴的な書類を共有する制度をつなぐ ----
        doc_to_benefits = {}
        for benefit_id in ids:
            for doc_id in benefit_docs.get(benefit_id, ()):
                if doc_usage.get(doc_id, 0) > max_usage:
                    continue
                doc_to_benefits.setdefault(doc_id, []).append(benefit_id)

        for doc_id, sharers in doc_to_benefits.items():
            if not (2 <= len(sharers) <= MAX_EDGES_PER_BENEFIT):
                continue
            for i, src in enumerate(sharers):
                for dst in sharers[i + 1 :]:
                    add_edge(src, dst, "SHARED_DOC", f"同じ書類（{doc_id}）が必要")

    return edges


def transform(records):
    benefits = {}
    statuses = {}
    documents = {}
    benefit_requires_status = []
    benefit_requires_doc = []
    benefit_docs = {}  # benefit_id -> {doc_id} （スキルツリーの書類シナジー判定に使う）

    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue

        # --- benefit_id の決定 ---
        _, direct_id = _first_present(rec, BENEFIT_ID_CANDIDATES)
        psid = _get(rec, "basicInformation", "psid")
        if direct_id and direct_id != psid:
            benefit_id = str(direct_id)
        elif psid:
            benefit_id = str(psid)
        else:
            benefit_id = _short_hash(str(rec), "BEN", length=16) + f"_{idx}"

        if benefit_id not in benefits:
            benefits[benefit_id] = build_benefit_row(rec, benefit_id)

        row = benefits[benefit_id]

        # --- statuses: AGE / LOCATION / TAG_* ---
        age_status = describe_age_status(row["effective_min_age_months"], row["effective_max_age_months"])
        location_status = describe_location_status(row["area_code"], row["area_name"])
        tag_statuses = describe_tag_statuses(rec.get("tag") or {})

        for status in [age_status, location_status, *tag_statuses]:
            if not status:
                continue
            statuses.setdefault(status["status_id"], status)
            benefit_requires_status.append({"benefit_id": benefit_id, "status_id": status["status_id"]})

        # --- documents ---
        _, direct_docs = _first_present(rec, DOCS_CANDIDATES)
        if isinstance(direct_docs, list):
            doc_names = [_clean_text(str(d)) for d in direct_docs if d]
        elif isinstance(direct_docs, str):
            doc_names = split_belongings(direct_docs)
        else:
            doc_names = split_belongings(rec.get("belongings"))

        for doc_name in doc_names:
            if not doc_name:
                continue
            # 書類名に埋め込まれたURLも分離して扱いやすくする
            doc_links, doc_plain = extract_links(doc_name)
            doc_plain = doc_plain or doc_name
            # 表記ゆれを寄せてから ID 化する（母子手帳 == 母子健康手帳 を同じノードにする）
            canonical_doc = canonical_document_name(doc_plain)
            doc_id = _short_hash(canonical_doc, "DOC")
            documents.setdefault(
                doc_id,
                {
                    "doc_id": doc_id,
                    "doc_name": canonical_doc,
                    "original_name": doc_plain,
                    "is_probable_document": looks_like_document(canonical_doc),
                    "doc_url": doc_links[0]["uri"] if doc_links else None,
                },
            )
            benefit_requires_doc.append({"benefit_id": benefit_id, "doc_id": doc_id})
            benefit_docs.setdefault(benefit_id, set()).add(doc_id)

    # --- schemes: 同名制度を1つの制度マスタに束ねる（自治体別レコードはその実装） ---
    schemes = {}
    benefit_in_scheme = []
    for benefit_id, row in benefits.items():
        scheme_key = row.get("canonical_name") or row.get("category") or "未分類"
        scheme_id = _short_hash(scheme_key, "SCHEME")
        row["scheme_id"] = scheme_id
        entry = schemes.setdefault(
            scheme_id,
            {
                "scheme_id": scheme_id,
                "scheme_name": scheme_key,
                "municipality_count": 0,
                "benefit_count": 0,
                "min_age_months": None,
                "max_age_months": None,
                "_areas": set(),
            },
        )
        entry["benefit_count"] += 1
        if row.get("area_code"):
            entry["_areas"].add(row["area_code"])
        lo, hi = row.get("effective_min_age_months"), row.get("effective_max_age_months")
        if lo is not None:
            entry["min_age_months"] = (
                lo if entry["min_age_months"] is None else min(entry["min_age_months"], lo)
            )
        if hi is not None:
            entry["max_age_months"] = (
                hi if entry["max_age_months"] is None else max(entry["max_age_months"], hi)
            )
        benefit_in_scheme.append({"benefit_id": benefit_id, "scheme_id": scheme_id})

    for entry in schemes.values():
        entry["municipality_count"] = len(entry.pop("_areas"))

    # --- スキルツリー（制度間エッジ） ---
    benefit_edges = build_benefit_edges(benefits, benefit_docs)

    df_benefits = pd.DataFrame(benefits.values())
    df_statuses = pd.DataFrame(statuses.values())
    df_documents = pd.DataFrame(documents.values())
    df_schemes = pd.DataFrame(schemes.values())
    df_brs = pd.DataFrame(benefit_requires_status).drop_duplicates()
    df_brd = pd.DataFrame(benefit_requires_doc).drop_duplicates()
    df_bis = pd.DataFrame(benefit_in_scheme).drop_duplicates()
    df_edges = pd.DataFrame(
        benefit_edges, columns=["from_benefit_id", "to_benefit_id", "relation", "reason"]
    ).drop_duplicates(subset=["from_benefit_id", "to_benefit_id", "relation"])

    print(
        f"[transform] benefits={len(df_benefits)} (cols={len(df_benefits.columns)}) "
        f"schemes={len(df_schemes)} statuses={len(df_statuses)} documents={len(df_documents)} "
        f"benefit_requires_status={len(df_brs)} benefit_requires_doc={len(df_brd)} "
        f"benefit_in_scheme={len(df_bis)} benefit_leads_to={len(df_edges)}",
        flush=True,
    )
    return {
        "benefits": df_benefits,
        "schemes": df_schemes,
        "statuses": df_statuses,
        "documents": df_documents,
        "benefit_requires_status": df_brs,
        "benefit_requires_doc": df_brd,
        "benefit_in_scheme": df_bis,
        "benefit_leads_to": df_edges,
    }


# ---------------------------------------------------------------- BigQuery ロード


def _link_field(name: str) -> bigquery.SchemaField:
    return bigquery.SchemaField(
        name,
        "RECORD",
        mode="REPEATED",
        fields=[bigquery.SchemaField("title", "STRING"), bigquery.SchemaField("uri", "STRING")],
    )


# 自動検出だと DATE が STRING に、空配列の STRUCT 列が推論できずに落ちるため、
# 型が曖昧な列だけ明示スキーマを与える。
BENEFITS_EXPLICIT_FIELDS = [
    bigquery.SchemaField("min_age_months", "INT64"),
    bigquery.SchemaField("max_age_months", "INT64"),
    bigquery.SchemaField("inferred_min_age_months", "INT64"),
    bigquery.SchemaField("inferred_max_age_months", "INT64"),
    bigquery.SchemaField("effective_min_age_months", "INT64"),
    bigquery.SchemaField("effective_max_age_months", "INT64"),
    bigquery.SchemaField("institution_type", "INT64"),
    bigquery.SchemaField("class_code", "INT64"),
    bigquery.SchemaField("update_date", "DATE"),
    bigquery.SchemaField("od_update_date", "DATE"),
    bigquery.SchemaField("implementation_period_from_date", "DATE"),
    bigquery.SchemaField("implementation_period_to_date", "DATE"),
    bigquery.SchemaField("procedure_period_from_date", "DATE"),
    bigquery.SchemaField("procedure_period_to_date", "DATE"),
    _link_field("related_links"),
    _link_field("form_links"),
    _link_field("embedded_links"),
]


def build_benefits_schema(df: pd.DataFrame):
    """DataFrame の列順に合わせて、明示型の列はそれを、他は STRING/BOOL を割り当てる。"""
    explicit = {f.name: f for f in BENEFITS_EXPLICIT_FIELDS}
    bool_columns = {
        "has_free_text_conditions",
        "is_free",
        "electronic_submission",
        "use_consideration_flag",
        "is_prenatal",
    }
    array_string_columns = {
        "category_codes",
        "target_codes",
        "content_codes",
        "category_code_labels",
        "target_code_labels",
        "content_code_labels",
    }

    schema = []
    for column in df.columns:
        if column in explicit:
            schema.append(explicit[column])
        elif column in bool_columns:
            schema.append(bigquery.SchemaField(column, "BOOL"))
        elif column in array_string_columns:
            schema.append(bigquery.SchemaField(column, "STRING", mode="REPEATED"))
        else:
            schema.append(bigquery.SchemaField(column, "STRING"))
    return schema


TABLE_SCHEMAS = {
    "statuses": [
        bigquery.SchemaField("status_id", "STRING"),
        bigquery.SchemaField("name", "STRING"),
        bigquery.SchemaField("type", "STRING"),
        bigquery.SchemaField("min_age_months", "INT64"),
        bigquery.SchemaField("max_age_months", "INT64"),
        bigquery.SchemaField("code", "STRING"),
    ],
    "documents": [
        bigquery.SchemaField("doc_id", "STRING"),
        bigquery.SchemaField("doc_name", "STRING"),
        bigquery.SchemaField("original_name", "STRING"),
        bigquery.SchemaField("is_probable_document", "BOOL"),
        bigquery.SchemaField("doc_url", "STRING"),
    ],
    "schemes": [
        bigquery.SchemaField("scheme_id", "STRING"),
        bigquery.SchemaField("scheme_name", "STRING"),
        bigquery.SchemaField("municipality_count", "INT64"),
        bigquery.SchemaField("benefit_count", "INT64"),
        bigquery.SchemaField("min_age_months", "INT64"),
        bigquery.SchemaField("max_age_months", "INT64"),
    ],
    "benefit_in_scheme": [
        bigquery.SchemaField("benefit_id", "STRING"),
        bigquery.SchemaField("scheme_id", "STRING"),
    ],
    "benefit_leads_to": [
        bigquery.SchemaField("from_benefit_id", "STRING"),
        bigquery.SchemaField("to_benefit_id", "STRING"),
        bigquery.SchemaField("relation", "STRING"),
        bigquery.SchemaField("reason", "STRING"),
    ],
    "benefit_requires_status": [
        bigquery.SchemaField("benefit_id", "STRING"),
        bigquery.SchemaField("status_id", "STRING"),
    ],
    "benefit_requires_doc": [
        bigquery.SchemaField("benefit_id", "STRING"),
        bigquery.SchemaField("doc_id", "STRING"),
    ],
}


def ensure_dataset(client: bigquery.Client, project_id: str):
    dataset_ref = bigquery.DatasetReference(project_id, DATASET_ID)
    try:
        client.get_dataset(dataset_ref)
        print(f"[bq] dataset {DATASET_ID} already exists", flush=True)
    except NotFound:
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = LOCATION
        client.create_dataset(dataset)
        print(f"[bq] created dataset {DATASET_ID} in {LOCATION}", flush=True)


def load_tables(client: bigquery.Client, project_id: str, tables: dict):
    for table_name, df in tables.items():
        table_id = f"{project_id}.{DATASET_ID}.{table_name}"
        schema = build_benefits_schema(df) if table_name == "benefits" else TABLE_SCHEMAS[table_name]
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=schema,
        )
        job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()
        print(f"[bq] loaded {len(df)} rows into {table_id}", flush=True)


def main():
    project_id = os.environ.get("GCP_PROJECT_ID", "opendatahackathon-503500")
    print(f"[main] using GCP project: {project_id}", flush=True)

    payload = fetch_json(SOURCE_URL)
    records = extract_records(payload)
    print(f"[main] extracted {len(records)} records", flush=True)

    tables = transform(records)

    client = bigquery.Client(project=project_id, location=LOCATION)
    ensure_dataset(client, project_id)
    load_tables(client, project_id, tables)

    print("[main] ETL completed successfully", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"[error] ETL failed: {exc}", file=sys.stderr, flush=True)
        raise
