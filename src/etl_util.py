"""ETL 全体で使う汎用ヘルパー（対象データの意味を持たない、純粋なユーティリティ）。"""

import hashlib


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
