"""E2E 用のスタブデータ。

CI では GCP 認証が使えないため、BigQuery を差し替えて実データに似た応答を返す。
狙いは「ブラウザ上で主要フローが破綻しないこと」の検証であり、
BigQuery そのものの挙動確認ではない。
"""

# backend 側の障害を E2E から再現するための ID。
# この ID を問い合わせるとスタブが例外を投げ、backend が 500 を返す。
# frontend のエラー画面（app/error.tsx）が「内部事情を出さずに」表示されることを
# 検証するために使う（実データには存在しない ID なので実環境の挙動には影響しない）。
FAILING_BENEFIT_ID = "e2e-backend-failure"

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
        "area_code": "131067",
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
        "area_code": "131067",
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
        "area_code": "131067",
        "area_name": "台東区",
        "has_free_text_conditions": False,
        "is_free": True,
        "monetary_support_text": None,
        "cost_text": None,
        "electronic_submission": False,
    },
    {
        # 年齢の条件が読み取れなかった制度。実データの 34.2%（2,672件）がこれで、
        # 範囲が NULL のため年齢で絞っても素通りする（issue #61）。
        "benefit_id": "psid-soudan",
        "title": "子育て相談窓口",
        "category": "相談",
        "summary": "子育ての悩みについて相談できます。",
        "min_age_months": None,
        "max_age_months": None,
        "age_source": "unknown",
        "area_code": "131067",
        "area_name": "台東区",
        "has_free_text_conditions": False,
        "is_free": True,
        "monetary_support_text": None,
        "cost_text": None,
        "electronic_submission": False,
    },
    {
        # **別の自治体の制度。** これが無いと area_code の絞り込みが効いているか
        # 分からない（全件が同じ区なら、絞っても絞らなくても結果が変わらないため）。
        "benefit_id": "psid-chiyoda-ninshin",
        "title": "妊婦健康診査（千代田区）",
        "category": "妊婦健康診査",
        "summary": "千代田区にお住まいの妊婦の方が対象です。",
        "min_age_months": None,
        "max_age_months": None,
        "age_source": "unknown",
        "area_code": "131016",
        "area_name": "千代田区",
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
        # 詳細ページの本体。一覧の要約とは別物であることを検証できるようにする
        "description": "身体計測、内科診察、歯科健診、視聴覚検査を行います。",
        "utilization": "対象月齢になったら郵送される受診票を持参してください。",
        # 機械判定しきれない条件の原文。has_free_text_conditions=True と対応させる
        "conditions_text": "前年の所得が一定額を下回る世帯に限ります。",
        "target_persons_text": "台東区に住民登録のある3歳のお子さん",
        "has_free_text_conditions": True,
        "related_links": [{"title": "子育て支援のご案内", "uri": "https://example.com/kosodate"}],
        "form_links": [{"title": "問診票（PDF）", "uri": "https://example.com/monshin.pdf"}],
        # related_links と同じ URI を混ぜ、表示側で重複が排除されることを検証する
        "embedded_links": [{"title": "子育て支援のご案内", "uri": "https://example.com/kosodate"}],
        "area_name": "台東区",
        "min_age_months": 36,
        "max_age_months": 47,
        "age_source": "explicit",
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
        "doc_url": doc_url,
    }
    for status_id, status_name, status_type in [
        ("AGE_3sai", "3歳〜3歳11か月", "AGE"),
        ("LOCATION_taito", "台東区", "LOCATION"),
    ]
    for doc_id, doc_name, doc_url in [
        ("DOC_boshi", "母子健康手帳", None),
        # 様式のURLを持つ書類（実データでは 462/4,919 件だけ）
        ("DOC_monshin", "3歳児健康診査問診票（記入済みのもの）", "https://example.com/monshin.pdf"),
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

# /api/data-source。フッターに出す件数と鮮度。
DATA_SOURCE_ROWS = [{"benefit_count": 7812, "area_count": 63, "latest_update_date": "2026-03-31"}]

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


# --- /api/benefits/match 用 -------------------------------------------------
#
# match は一覧と違う列を読む（effective_*, is_prenatal, is_single_parent_target …）。
# 一覧と同じ行を返すと KeyError になるので、必要な列を足した別の集合にしている。
AREA_TAITO = "131067"
AREA_CHIYODA = "131016"


def _match_row(
    base: dict,
    *,
    area_code: str,
    is_prenatal: bool = False,
    single_parent: bool = False,
    disability: bool = False,
) -> dict:
    """一覧用の行に、match が読む列を足す。"""
    row = dict(base)
    row["effective_min_age_months"] = base.get("min_age_months")
    row["effective_max_age_months"] = base.get("max_age_months")
    row.pop("min_age_months", None)
    row.pop("max_age_months", None)
    row.update(
        area_code=area_code,
        is_prenatal=is_prenatal,
        is_single_parent_target=single_parent,
        is_disability_target=disability,
        conditions_text=None,
        official_url="https://example.com/apply",
        scheme_id="SCHEME_x",
    )
    return row


MATCH_BENEFITS = [
    _match_row(BENEFITS[0], area_code=AREA_TAITO),  # 3歳児健診 36〜47
    _match_row(BENEFITS[1], area_code=AREA_TAITO),  # 児童手当 0〜227
    _match_row(BENEFITS[2], area_code=AREA_TAITO),  # あそびひろば 0〜36（推定）
    _match_row(BENEFITS[3], area_code=AREA_TAITO),  # 子育て相談窓口（年齢不明）
    # 一覧と同じ行を使う（別々に作ると同じ名前で ID が違う制度が2つできる）
    _match_row(BENEFITS[4], area_code=AREA_CHIYODA, is_prenatal=True),
    # 属性で束ねる検証用（issue #53）。分類コードに該当する制度と、しない制度の
    # 両方が要る。片方しか無いと「該当しないものを隠していないか」を確かめられない。
    _match_row(
        {
            **BENEFITS[1],
            "benefit_id": "psid-hitorioya",
            "title": "ひとり親家庭等医療費助成",
        },
        area_code=AREA_TAITO,
        single_parent=True,
    ),
    _match_row(
        {
            **BENEFITS[1],
            "benefit_id": "psid-shougai",
            "title": "障害児福祉手当",
        },
        area_code=AREA_TAITO,
        disability=True,
    ),
]


def _in_age_range(lo, hi, age: int) -> bool:
    """`app/queries.py` の年齢判定と同じ。**NULL は素通り**させる。

    素通りが正しい。実データの 34.2% は年齢が読み取れておらず、ここで落とすと
    「対象なのに出ない」を作る（CLAUDE.md）。スタブの方が実装より厳しいと、
    実装が正しいのにテストが落ちる、という逆転が起きる。

    検索は `min/max_age_months`、match は `effective_*` と列名が違うだけなので、
    判定はここに1つだけ置く。
    """
    return (lo is None or lo <= age) and (hi is None or hi >= age)


def _matches_ages(row: dict, ages: list[int]) -> bool:
    """`app/queries.py` の `ages_filter_sql()` と同じ判定。**NULL は素通り**させる。

    素通りが正しい。実データの 34.2% は年齢が読み取れておらず、ここで落とすと
    「対象なのに出ない」を作る（CLAUDE.md）。スタブの方が実装より厳しいと、
    実装が正しいのにテストが落ちる、という逆転が起きる。
    """
    lo = row.get("effective_min_age_months")
    hi = row.get("effective_max_age_months")
    return any(_in_age_range(lo, hi, a) for a in ages)


def _match(query: str, params: dict) -> list[dict]:
    """`/api/benefits/match` の絞り込みをスタブ側でも再現する。

    **params を無視して全件返してはいけない。** それだと「属性を指定したら件数が変わる」
    という検証が、絞り込みを丸ごと外しても通ってしまう（issue #64 / #110 と同じ穴）。
    """
    # 妊娠中かどうかはクエリパラメータではなく SQL 文に現れる（is_prenatal を OR で足す）
    include_prenatal = "is_prenatal" in query
    rows = list(MATCH_BENEFITS)
    if (area := params.get("area_code")) is not None:
        rows = [r for r in rows if r["area_code"] in (area, "130001")]
    ages = params.get("ages")
    if ages:
        rows = [r for r in rows if _matches_ages(r, list(ages)) or (include_prenatal and r["is_prenatal"])]
    elif include_prenatal:
        rows = [r for r in rows if r["is_prenatal"]]

    return rows


def _search_benefits(params: dict) -> list[dict]:
    """`/api/benefits` の絞り込みをスタブ側でも再現する。

    **ここで params を無視して全件返してはいけない。** 以前はそうなっており、
    `benefit_id` を見ずに詳細ページの行を返していたせいで、二重URLエンコードのバグが
    E2E をすり抜けて dev に出た（issue #64）。同じ穴が検索側にも残っていて、
    `area_code` や `age_months` を渡しても結果が変わらないため、
    絞り込みのテストが「1件以上ある」ことしか担保できていなかった。

    判定は backend が渡した**パラメータ**を見て行う。backend が条件を組み立て忘れれば
    パラメータ自体が来ないので、絞り込みを検証しているテストが落ちる。
    """
    rows = list(BENEFITS)
    if (q := params.get("q")) is not None:
        # backend は LIKE 用に %...% を付けて渡す
        needle = str(q).strip("%").lower()
        rows = [r for r in rows if needle in r["title"].lower()]
    if (category := params.get("category")) is not None:
        rows = [r for r in rows if r["category"] == category]
    if (area_code := params.get("area_code")) is not None:
        rows = [r for r in rows if r.get("area_code") == area_code]
    if (age_months := params.get("age_months")) is not None:
        rows = [
            r for r in rows if _in_age_range(r.get("min_age_months"), r.get("max_age_months"), age_months)
        ]
    if (limit := params.get("limit")) is not None:
        rows = rows[:limit]
    return rows


def rows_for(query: str, params: dict | None = None) -> list[dict]:
    """発行されたクエリの内容から、返すべきスタブ行を選ぶ。

    params にはクエリパラメータ（`@benefit_id` など）が入る。
    subgraph は **存在しない ID なら空を返す**（実データと同じく 404 になる）。
    ここで常に行を返してしまうと、URLエンコードを誤って別のIDを問い合わせていても
    E2E が気づけない。

    検索（`/api/benefits`）も同様にパラメータを見て絞り込む（`_search_benefits`）。
    """
    params = params or {}
    if "COUNT(DISTINCT area_code)" in query:
        return DATA_SOURCE_ROWS
    if params.get("benefit_id") == FAILING_BENEFIT_ID:
        raise RuntimeError("E2E 用に意図的に発生させた backend の障害")
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
    if "is_single_parent_target" in query:
        return _match(query, params)
    if "SELECT area_name" in query:
        # 存在しない area_code なら空を返す（backend はこれを 400 に変える）。
        # 常に行を返すと、どんな値を送っても「台東区」に解決されてしまい、
        # 検証している側は何も担保できない。
        requested = params.get("area_code")
        if requested is not None and requested not in {a["area_code"] for a in AREAS}:
            return []
        return PROFILE_ROWS
    return _search_benefits(params)
