"""AGE / LOCATION / TAG_* の status ノード生成（対象条件のグラフ表現）。"""

import re

from etl_util import _short_hash

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
