"""レコード -> ノード/エッジ行の変換（benefits 行の構築、スキルツリー生成、全体オーケストレーション）。"""

import pandas as pd

from age_rules import (
    extract_age_range,
    extract_disability_max_age,
    has_multiple_age_stages,
    is_prenatal,
)
from benefit_refs import extract_required_benefit_names, title_refers_to
from etl_documents import (
    DOCS_CANDIDATES,
    canonical_document_name,
    looks_like_document,
    split_belongings,
)
from etl_normalize import (
    _period,
    extract_links,
    normalize_date,
    normalize_link_list,
    normalize_time,
    normalize_zip,
)
from etl_statuses import (
    _clean_codes,
    compute_age_bounds,
    describe_age_status,
    describe_location_status,
    describe_tag_statuses,
    split_area,
    tag_code_label,
)
from etl_util import _clean_text, _first_present, _get, _short_hash
from income_rules import extract_income_condition

# 制度ID/制度名など、想定される代替フィールド名（自動判別用の候補）
BENEFIT_ID_CANDIDATES = ["制度ID", "benefit_id", "benefitId", "id", "psid"]
TITLE_CANDIDATES = ["制度名", "title", "name", "serviceName"]
CATEGORY_CANDIDATES = ["カテゴリ", "category", "categoryName", "genre"]
SUMMARY_CANDIDATES = ["概要", "summary", "description", "outline"]

# basicInformation.institutionType / class の値。実データはほぼ単一値だが意味を持たせて保持する。
INSTITUTION_TYPE_LABELS = {1: "地方公共団体", 2: "その他"}


def _ranges_overlap(lo1, hi1, lo2, hi2) -> bool:
    """2つの年齢範囲が重なるか。None は「制限なし」として扱う。"""
    lo1 = lo1 if lo1 is not None else -(10**9)
    hi1 = hi1 if hi1 is not None else 10**9
    lo2 = lo2 if lo2 is not None else -(10**9)
    hi2 = hi2 if hi2 is not None else 10**9
    return lo1 <= hi2 and lo2 <= hi1


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

    # **上下が逆に入っている行がある**（issue #173）。dev 実測で9件。
    #
    #   利島村「放課後児童クラブ」   144〜72   ← 12歳〜6歳。小学生向けなので 72〜143 が正しい
    #   荒川区「私立幼稚園等の施設案内」 83〜36   ← 私立幼稚園なので 36〜83
    #
    # 絞り込みは min <= 子の月齢 <= max なので、**どの年齢にもマッチしない**。
    # 制度としては存在するのに、属性から探している人には決して出てこない。
    #
    # 入れ替えるだけにする。**どちらか片方が誤記**という可能性は残るが、
    # 9件すべて入れ替えると意味が通る（放課後児童クラブ＝小学生、幼稚園＝3〜6歳）。
    # 触ったことは age_source='corrected' で追える。
    # **元の欄は書き換えない**（#114 と同じ方針）。元データに何が入っていたかは追えるようにし、
    # 判定に使う値（effective_*）だけを入れ替える。
    age_columns_swapped = (
        min_age_months is not None and max_age_months is not None and min_age_months > max_age_months
    )
    usable_min, usable_max = (
        (max_age_months, min_age_months) if age_columns_swapped else (min_age_months, max_age_months)
    )

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

    effective_min = usable_min if usable_min is not None else inferred_min
    effective_max = usable_max if usable_max is not None else inferred_max

    # **元データの年齢欄が制度名と食い違うことがある**（issue #114）。
    # 三鷹市「3～4カ月児健康診査」の年齢欄は 36〜71（＝3〜5歳）で、
    # 月数をそのまま歳の欄に入れている。ADR 0002 は explicit を最優先すると
    # 決めているが、その前提（元データの年齢欄は正しい）が成り立っていない。
    #
    # そのまま使うと **0歳の子に4か月児健診が出ず、3〜5歳の子に出る**。
    # 探している人からは「無い」ようにしか見えないので気づけない。
    #
    # **制度名から読めた範囲と重ならないときだけ**、制度名を採る。
    # 重なっていれば元データを尊重する（少しのずれで上書きしない）。
    #
    # **複数の段階が並んだ制度名では補正しない**（PR #170 のレビュー）。
    # 「3から4か月児・1歳6か月児・3歳児健康診査」は同じ制度名で複数行あり、
    # 行ごとに違う段階の年齢が入っている。制度名からは「この行がどの段階か」を
    # 決められないので、最初に出てきた年齢を全行に当てると**正しい元データを壊す**。
    # 実データで小金井市・檜原村の3行がこれに当たっていた。
    if age_columns_swapped:
        age_source = "corrected"

    if age_source == "explicit" and not has_multiple_age_stages(title):
        from_title = extract_age_range(title)
        if from_title and not _ranges_overlap(from_title[0], from_title[1], effective_min, effective_max):
            effective_min, effective_max = from_title[0], from_title[1]
            age_rule = from_title[2]
            age_source = "corrected"

    # **障害のある子にだけ適用される上限**（issue #157）。
    # 「原則18年度末まで。ただし障害のある児童は20歳未満」という二段構えの制度があり、
    # 前段だけを読むと 18〜19歳で障害のあるお子さんを持つひとり親に制度が出ない。
    # 障害の有無で上限が変わるので effective_max_age_months 1本では表せない。
    disability_max = None
    for candidate in (
        target.get("targetPersons") if isinstance(target, dict) else None,
        target.get("conditions") if isinstance(target, dict) else None,
        description_plain,
    ):
        found = extract_disability_max_age(candidate)
        if found is not None:
            disability_max = found
            break
    # 広い側にしか意味が無い。狭める向きに使うと「対象なのに出ない」を作る。
    #
    # **上限が無い制度には入れない。** ここが抜けていたため、元々どの年齢にも
    # 出ていた制度（effective_max が NULL）に、**障害があると答えた人にだけ**
    # 新しい上限が付いていた（PR #169 のレビュー。実データで34件が同じ形）。
    # 正直に申告した人にだけ制度が見えなくなる、といういちばん避けたい壊れ方になる。
    if disability_max is not None and (effective_max is None or disability_max <= effective_max):
        disability_max = None

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

    # 所得条件を本文から抽出する。読み取れたものだけ構造化列に入れ、
    # 読み取れなかったものは conditions_text の提示に倒す（issue #76 / #63）
    income = extract_income_condition(f"{conditions_text or ''} {target_persons_text or ''}")

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
        "disability_max_age_months": disability_max,
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
        # 本文から読み取れた所得条件。読み取れなければ NULL / False のまま
        "income_max_yen": income.max_yen if income else None,
        # しきい値ちょうどの人が対象かどうか。**None はしきい値そのものが無いという意味**で、
        # False（＝未満）とは違う。判定に使うときは NULL を「対象」に倒さないこと。
        "income_max_inclusive": income.max_inclusive if income else None,
        # しきい値が何の額か（income / income_tax / tax_levy / salary）。
        # 所得・所得税額・所得割額は別の数字なので、揃えずに比較してはいけない
        "income_basis": income.basis if income else None,
        "requires_non_taxable": bool(income and income.requires_non_taxable),
        "requires_taxable": bool(income and income.requires_taxable),
        "requires_welfare": bool(income and income.requires_welfare),
        "excludes_welfare": bool(income and income.excludes_welfare),
        "income_rule": income.rule if income else None,
        "income_evidence": income.evidence if income else None,
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


# 同時に申請しやすいかを測る「代表的な書類」。どの制度にも出る汎用書類でつなぐと
# エッジが爆発して意味がなくなるため、出現数が極端に多い書類は除外する。
SYNERGY_DOC_MAX_SHARE = 0.05  # 全制度の5%超に出る書類は汎用すぎるとみなす
MAX_EDGES_PER_BENEFIT = 6


def build_benefit_edges(benefits: dict, benefit_docs: dict):
    """制度同士の関係（スキルツリー）を確定ルールで生成する。

    - NEXT_STEP : 同一自治体で年齢帯が地続きの制度（妊娠→出生→健診→予防接種…の流れ）
    - SHARED_DOC: 同一自治体で特徴的な必要書類を共有する制度（ついで申請できる）
    - REQUIRES_BENEFIT: 条件文に「〜を受けていること」と書かれている制度（issue #121）

    LLMは使わず、機械的に検証できる根拠のみを使う。

    **NEXT_STEP は根拠が弱い。** 年齢が隣り合っているだけで、制度としての関係は無い
    （「児童手当 → 日本脳炎予防接種」。ADR 0003 / issue #121）。
    REQUIRES_BENEFIT は**条件文にそう書いてある**ので、利用者に見せられる根拠になる。
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

        # ---- REQUIRES_BENEFIT: 条件文が前提として挙げている制度をつなぐ ----
        #
        # **向きは「前提 → その制度」。** 児童扶養手当が認定されたら次にこれが申請できる、
        # という導線になる。dev では 134本引け、うち 121本が児童扶養手当を前提にしている
        # （児童扶養手当が所得審査の代理になっているため。issue #124）。
        for dst in ids:
            row = benefits[dst]
            text = f"{row.get('conditions_text') or ''} {row.get('target_persons_text') or ''}"
            area_name = row.get("area_name") or ""
            for name in extract_required_benefit_names(text):
                # 「中央区児童育成手当」のように自治体名が頭に付く形がある。
                # `title` 側には付いていないので、外した形でも引く（レビューでの指摘）。
                candidates = {name}
                if area_name and name.startswith(area_name):
                    candidates.add(name[len(area_name) :])
                for src in ids:
                    title = benefits[src].get("title")
                    if src == dst or not any(title_refers_to(title, c) for c in candidates if len(c) >= 3):
                        continue
                    add_edge(src, dst, "REQUIRES_BENEFIT", f"条件に「{name}を受けている」とある")

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
                    # **生の文字列を渡す。** canonical_document_name は
                    # strip_decorations を通しており、飾りも末尾の読点も外れている。
                    # そちらを渡すと looks_like_document の中で
                    # 「長さ・句読点は元の文字列で見る」が効かなくなる（レビューで指摘）。
                    "is_probable_document": looks_like_document(doc_plain),
                    "doc_url": doc_links[0]["uri"] if doc_links else None,
                },
            )
            # **最初に見た書き方だけで決めない**（issue #120）。同じ書類でも自治体ごとに
            # 書き方が違い、「個人番号カード、運転免許証、パスポートなど」のように
            # 書類名に見えない列挙が先に来ると、マイナンバーカードのノード全体が
            # 「書類ではない」と判定されていた（dev で 407本のエッジが該当）。
            # 1つでも書類名らしい書き方があれば書類とみなす。
            if looks_like_document(doc_plain):
                documents[doc_id]["is_probable_document"] = True

            benefit_requires_doc.append({"benefit_id": benefit_id, "doc_id": doc_id})
            # **書類らしくないものはエッジに使わない**（issue #120）。
            # 「必要書類」「申請に必要なもの」のような欄の見出しや、表の区切り行でも
            # SHARED_DOC が張られていた（dev 実測: 見出し「申請に必要なもの」で 70制度、
            # 区切り行 `|:----|:----|` で 47本）。「同じ書類が要る」という根拠が無い。
            # 行そのものは documents に残す（データは捨てない）。
            if documents[doc_id]["is_probable_document"]:
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
