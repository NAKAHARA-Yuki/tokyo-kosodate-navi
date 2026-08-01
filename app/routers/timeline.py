"""Phase3: 年齢軸に制度を並べたタイムライン。

/api/timeline のライフステージ範囲との重複判定は、routers/benefits.py や
routers/match.py の age_filter_sql（単一の年齢が範囲内かの判定）とは構造が異なる
（NULLを許容ではなく除外する）ため、queries.py には切り出さずここに残している。
"""

import dependencies
from config import DATASET_ID, PROJECT_ID
from fastapi import APIRouter, Query
from google.cloud import bigquery

router = APIRouter()

# 妊娠期〜18歳までのライフステージ。年齢軸に制度を並べるために使う。
LIFE_STAGES = [
    {"key": "prenatal", "label": "妊娠中", "min": None, "max": None},
    {"key": "0y", "label": "0歳", "min": 0, "max": 11},
    {"key": "1y", "label": "1歳", "min": 12, "max": 23},
    {"key": "2y", "label": "2歳", "min": 24, "max": 35},
    {"key": "3-5y", "label": "3〜5歳（未就学）", "min": 36, "max": 71},
    {"key": "6-11y", "label": "6〜11歳（小学生）", "min": 72, "max": 143},
    {"key": "12-14y", "label": "12〜14歳（中学生）", "min": 144, "max": 179},
    {"key": "15-18y", "label": "15〜18歳（高校生）", "min": 180, "max": 227},
]


@router.get("/api/timeline")
def get_timeline(
    area_code: str | None = Query(default=None, description="居住地の市区町村コード"),
    per_stage: int = Query(default=8, le=20, description="各ステージで返す制度数"),
):
    """ライフステージごとに制度を並べたタイムラインを返す。

    子育ては本質的に時系列のため、グラフより「次に何が来るか」を掴みやすい。
    """
    params = [bigquery.ScalarQueryParameter("per_stage", "INT64", per_stage)]
    area_filter = ""
    if area_code:
        area_filter = "AND (area_code = @area_code OR area_code = '130001')"
        params.append(bigquery.ScalarQueryParameter("area_code", "STRING", area_code))

    # ステージ定義をクエリ内の UNNEST に展開し、1回のクエリで全ステージ分を取得する
    # （ステージごとにクエリを投げると往復が増えて体感が遅くなるため）
    stage_rows = ", ".join(
        f"STRUCT('{s['key']}' AS key, "
        f"{'CAST(NULL AS INT64)' if s['min'] is None else s['min']} AS lo, "
        f"{'CAST(NULL AS INT64)' if s['max'] is None else s['max']} AS hi)"
        for s in LIFE_STAGES
    )

    query = f"""
        WITH stages AS (
          SELECT * FROM UNNEST([{stage_rows}])
        ),
        matched AS (
          SELECT
            s.key AS stage_key,
            b.benefit_id, b.title, b.category, b.area_name, b.is_free,
            b.electronic_submission, b.age_source, b.effective_min_age_months,
            ROW_NUMBER() OVER (
              PARTITION BY s.key
              ORDER BY CASE b.age_source WHEN 'explicit' THEN 0 ELSE 1 END,
                       b.effective_min_age_months NULLS LAST, b.title
            ) AS rn
          FROM `{PROJECT_ID}.{DATASET_ID}.benefits` b
          JOIN stages s
            ON (s.key = 'prenatal' AND b.is_prenatal)
            OR (s.key != 'prenatal'
                AND NOT b.is_prenatal
                AND b.effective_min_age_months IS NOT NULL
                AND b.effective_max_age_months IS NOT NULL
                AND b.effective_min_age_months <= s.hi
                AND b.effective_max_age_months >= s.lo)
          WHERE TRUE {area_filter}
        )
        SELECT stage_key, benefit_id, title, category, area_name,
               is_free, electronic_submission, age_source
        FROM matched
        WHERE rn <= @per_stage
        ORDER BY stage_key, rn
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = list(dependencies.get_client().query(query, job_config=job_config).result())

    by_stage = {}
    for r in rows:
        by_stage.setdefault(r["stage_key"], []).append(
            {
                "benefit_id": r["benefit_id"],
                "title": r["title"],
                "category": r["category"],
                "area_name": r["area_name"],
                "is_free": r["is_free"],
                "electronic_submission": r["electronic_submission"],
                "age_source": r["age_source"],
            }
        )

    return {
        "stages": [
            {"key": s["key"], "label": s["label"], "benefits": by_stage.get(s["key"], [])}
            for s in LIFE_STAGES
        ]
    }
