"""E2E 用のスタブデータ。

CI では GCP 認証が使えないため、BigQuery を差し替えて実データに似た応答を返す。
狙いは「ブラウザ上で主要フローが破綻しないこと」の検証であり、
BigQuery そのものの挙動確認ではない。
"""

AREAS = [
    {"area_code": "130001", "area_name": "東京都", "count": 27},
    {"area_code": "131016", "area_name": "千代田区", "count": 135},
    {"area_code": "131067", "area_name": "台東区", "count": 171},
]

CATEGORIES = [
    {"category": "定期予防接種", "count": 685},
    {"category": "3歳児健康診査", "count": 62},
    {"category": "児童手当", "count": 120},
]

BENEFITS = [
    {
        # 実データの benefit_id は `psid3.0+1000020132152+1+UM5036` のように `+` を含む。
        # URL に載せるときのエンコードを誤ると詳細ページが 404 になるため、
        # スタブでも `+` 入りの ID を使って回帰を検出できるようにしている。
        "benefit_id": "psid3.0+3sai+1+UM1",
        "title": "3歳児健康診査",
        "category": "3歳児健康診査",
        "summary": "3歳のお子さんを対象とした健康診査です。",
        "min_age_months": 36,
        "max_age_months": 47,
        "age_source": "explicit",
        "area_name": "台東区",
        "has_free_text_conditions": True,
        "is_free": True,
        "monetary_support_text": None,
        "cost_text": None,
        "electronic_submission": False,
    },
    {
        "benefit_id": "psid-jidoteate",
        "title": "児童手当",
        "category": "児童手当",
        "summary": "高校生年代までのお子さんを養育している方に支給されます。",
        "min_age_months": 0,
        "max_age_months": 227,
        "age_source": "explicit",
        "area_name": "台東区",
        "has_free_text_conditions": False,
        "is_free": False,
        "monetary_support_text": "第1子、第2子：月額1万5,000円",
        "cost_text": None,
        "electronic_submission": True,
    },
    {
        "benefit_id": "psid-asobi",
        "title": "あそびひろば",
        "category": "子育て広場",
        "summary": "未就学のお子さんと保護者が遊べる場所です。",
        "min_age_months": 0,
        "max_age_months": 36,
        "age_source": "inferred",
        "area_name": "台東区",
        "has_free_text_conditions": False,
        "is_free": True,
        "monetary_support_text": None,
        "cost_text": None,
        "electronic_submission": False,
    },
]

# /api/subgraph は「制度1件 × 条件/書類」の直積で行が返る形
SUBGRAPH_ROWS = [
    {
        # BENEFITS の1件目と同じ ID にする（一覧→詳細の遷移を E2E で通すため）
        "benefit_id": "psid3.0+3sai+1+UM1",
        "title": "3歳児健康診査",
        "category": "3歳児健康診査",
        "summary": "3歳のお子さんを対象とした健康診査です。",
        "area_name": "台東区",
        "min_age_months": 36,
        "max_age_months": 47,
        "cost_text": None,
        "cost_conditions_text": None,
        "monetary_support_text": None,
        "materially_support_text": "健康診査を無料で実施します。",
        "is_free": True,
        "department": "浅草保健相談センター",
        "contact_name": "健康課",
        "contact_phone": "03-0000-0000",
        "contact_email": None,
        "contact_address": "東京都台東区東上野4-5-6",
        "official_url": "https://example.com/3sai",
        "official_title": "3歳児健康診査のご案内",
        "procedure_method": "対象の方に個別に通知します。",
        "procedure_counter": "浅草保健相談センター",
        "electronic_submission": False,
        "regulation_name": "母子保健法",
        "update_date": "2024-03-01",
        "status_id": status_id,
        "status_name": status_name,
        "status_type": status_type,
        "doc_id": doc_id,
        "doc_name": doc_name,
    }
    for status_id, status_name, status_type in [
        ("AGE_3sai", "3歳〜3歳11か月", "AGE"),
        ("LOCATION_taito", "台東区", "LOCATION"),
    ]
    for doc_id, doc_name in [
        ("DOC_boshi", "母子健康手帳"),
        ("DOC_monshin", "3歳児健康診査問診票（記入済みのもの）"),
    ]
]

TIMELINE_ROWS = [
    {
        "stage_key": stage,
        "benefit_id": f"psid-{stage}",
        "title": title,
        "category": "子育て支援",
        "area_name": "台東区",
        "is_free": True,
        "electronic_submission": False,
        "age_source": "explicit",
    }
    for stage, title in [
        ("prenatal", "妊婦健康診査"),
        ("0y", "こんにちは赤ちゃん訪問"),
        ("1y", "1歳6か月児健康診査"),
        ("3-5y", "3歳児健康診査"),
        ("6-11y", "就学援助制度"),
    ]
]

PROFILE_ROWS = [{"area_name": "台東区"}]

DRAFT_REVIEW_ROWS = [
    {
        "title": "3歳児健康診査",
        "category": "3歳児健康診査",
        "area_name": "台東区",
        "summary": "3歳のお子さんを対象とした健康診査です。",
        "description": "身体計測、内科・歯科健診を行います。",
        "target_persons_text": "3歳になったお子さん",
        "conditions_text": None,
        "monetary_support_text": None,
        "procedure_method": "対象の方に個別に通知します。",
        "official_url": "https://example.com/3sai",
    }
]


def rows_for(query: str, params: dict | None = None) -> list[dict]:
    """発行されたクエリの内容から、返すべきスタブ行を選ぶ。

    params にはクエリパラメータ（`@benefit_id` など）が入る。
    subgraph は **存在しない ID なら空を返す**（実データと同じく 404 になる）。
    ここで常に行を返してしまうと、URLエンコードを誤って別のIDを問い合わせていても
    E2E が気づけない。
    """
    params = params or {}
    if "stage_key" in query:
        return TIMELINE_ROWS
    if "benefit_leads_to" in query:
        return []  # next_steps は E2E では空でよい
    if "benefit_requires_status" in query or "benefit_requires_doc" in query:
        requested = params.get("benefit_id")
        if requested is not None and requested != SUBGRAPH_ROWS[0]["benefit_id"]:
            return []
        return SUBGRAPH_ROWS
    if "area_code, area_name" in query:
        return AREAS
    if "SELECT category, COUNT" in query:
        return CATEGORIES
    if "target_persons_text" in query and "procedure_method" in query:
        return DRAFT_REVIEW_ROWS
    if "SELECT area_name" in query:
        return PROFILE_ROWS
    return BENEFITS
