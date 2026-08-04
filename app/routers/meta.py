"""データの出典と鮮度を返すエンドポイント（判定層。LLM不使用）。

- /api/data-source : 元データの出典と、いま入っているデータがいつ時点のものか

行政情報を扱う以上、「どこから持ってきた、いつ時点の情報か」は
機能ではなく責任として画面に出す必要がある（issue #57）。
"""

import time

import dependencies
from config import DATASET_ID, PROJECT_ID
from fastapi import APIRouter

router = APIRouter()

# 元データの出典。東京都オープンデータカタログサイトで公開されているもの。
SOURCE_NAME = "東京デジタル2030ビジョン（こどもDX）子育て支援制度レジストリ"
SOURCE_URL = "https://portal.data.metro.tokyo.lg.jp/visualization/childcare-support-system-registry/"
SOURCE_PUBLISHER = "東京都"

# フッターは全ページで描画されるため、素直に実装すると1ページ表示ごとに
# BigQuery へ集計クエリが飛ぶ。中身は日単位でしか変わらないので、
# プロセス内に一定時間持っておく。
#
# Cloud Run のインスタンスは使い捨てなので、これはインスタンスをまたいで共有されない
# （＝コールドスタート直後の1回だけは必ずクエリが飛ぶ）。それで十分な性質のデータ。
_CACHE_TTL_SECONDS = 3600
_cache: dict | None = None
_cache_expires_at: float = 0.0


def _fetch_freshness() -> dict:
    query = f"""
        SELECT
          COUNT(*) AS benefit_count,
          COUNT(DISTINCT area_code) AS area_count,
          MAX(update_date) AS latest_update_date
        FROM `{PROJECT_ID}.{DATASET_ID}.benefits`
    """
    row = next(iter(dependencies.get_client().query(query).result()))
    latest = row["latest_update_date"]
    return {
        "benefit_count": row["benefit_count"],
        "area_count": row["area_count"],
        "latest_update_date": str(latest) if latest else None,
    }


@router.get("/api/data-source")
def get_data_source():
    """出典と、データの鮮度を返す。

    出典は固定値なので、BigQuery が落ちていても必ず返る。
    鮮度の取得に失敗した場合は null にして、出典だけでも表示できるようにする。
    """
    global _cache, _cache_expires_at

    now = time.monotonic()
    if _cache is None or now >= _cache_expires_at:
        try:
            _cache = _fetch_freshness()
            # 成功したときだけ期限を延ばす。
            _cache_expires_at = now + _CACHE_TTL_SECONDS
        except Exception:  # noqa: BLE001
            # 鮮度が取れないことを理由に出典・免責まで出せなくなるのは本末転倒。
            # 失敗は握りつぶすが、**期限は延ばさない**。ここで延ばすと、
            # コールドスタート直後に BigQuery が一瞬詰まっただけで、そのインスタンスは
            # 復旧後も1時間ずっと件数と更新日を出さなくなる。
            # トラフィックが少ないほどインスタンスは長生きするので、むしろ当たりやすい。
            _cache = {"benefit_count": None, "area_count": None, "latest_update_date": None}

    return {
        "source": {"name": SOURCE_NAME, "url": SOURCE_URL, "publisher": SOURCE_PUBLISHER},
        **_cache,
    }
