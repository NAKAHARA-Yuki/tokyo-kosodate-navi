"""Phase2: Gemini 伴走サポート（/api/support/draft-review のみ）。

制度の適用判定には一切使わない（判定は BigQuery の確定クエリのみ、routers/benefits.py や
routers/match.py）。ここで扱うのは「取得済みの制度情報を分かりやすく言い換える」ことだけ。
"""

from datetime import UTC, datetime

import dependencies
import explanation_cache
from config import DATASET_ID, PROJECT_ID
from fastapi import APIRouter, HTTPException
from google.cloud import bigquery
from pydantic import BaseModel, Field

router = APIRouter()

GEMINI_MODEL = "gemini-3.5-flash-lite"
# 行政制度の言い換えは誤りが許されないため、軽量モデルでも thinking を厚めに確保する。
# thinking_level と thinking_budget は併用不可（400になる）。実測で thinking_level=HIGH の方が
# thinking_budget 指定より多く思考する（509 vs 206 tokens）ため HIGH を採用。
GEMINI_THINKING_LEVEL = "HIGH"


class DraftReviewRequest(BaseModel):
    benefit_id: str = Field(description="対象の制度ID")
    mode: str = Field(default="explain", description="explain（やさしい解説）/ review（下書き添削）")
    draft: str | None = Field(default=None, description="review モードで添削したい下書き本文")


@router.post("/api/support/draft-review")
def draft_review(req: DraftReviewRequest):
    """Gemini で制度のやさしい解説、または申請書下書きの添削を行う。

    制度の適用判定には一切使わない（判定は BigQuery Graph の確定クエリのみ）。
    ここで扱うのは「取得済みの制度情報を分かりやすく言い換える」ことだけ。
    """
    query = f"""
        SELECT title, category, area_name, summary, description,
               target_persons_text, conditions_text, monetary_support_text,
               procedure_method, official_url
        FROM `{PROJECT_ID}.{DATASET_ID}.benefits`
        WHERE benefit_id = @benefit_id LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("benefit_id", "STRING", req.benefit_id)]
    )
    rows = list(dependencies.get_client().query(query, job_config=job_config).result())
    if not rows:
        raise HTTPException(status_code=404, detail="benefit not found")
    b = rows[0]

    facts = "\n".join(
        f"{label}: {value}"
        for label, value in [
            ("制度名", b["title"]),
            ("カテゴリ", b["category"]),
            ("自治体", b["area_name"]),
            ("概要", b["summary"]),
            ("詳細", (b["description"] or "")[:2000]),
            ("対象者", b["target_persons_text"]),
            ("その他の条件", b["conditions_text"]),
            ("金銭的支援", b["monetary_support_text"]),
            ("申請方法", (b["procedure_method"] or "")[:1000]),
        ]
        if value
    )

    def payload(text, generated_at, cached):
        return {
            "benefit_id": req.benefit_id,
            "title": b["title"],
            "mode": req.mode,
            "result": text,
            "official_url": b["official_url"],
            # キャッシュから返す場合も必ず付ける（AI生成であることは古くならない）。
            "disclaimer": "この文章はAIが制度情報をもとに生成したものです。最終的な判断は自治体の公式情報をご確認ください。",
            "generated_at": generated_at,
            "cached": cached,
        }

    # やさしい解説は同じ制度に対して何度でも同じものになるので、一度作ったら使い回す（issue #68）。
    # 添削（review）の入力は利用者が書いた文章で個人情報が入りうるため、読みも書きもしない。
    key = None
    if req.mode != "review":
        key = explanation_cache.cache_key(req.benefit_id, facts, GEMINI_MODEL)
        hit = explanation_cache.lookup(key)
        if hit:
            return payload(hit["result"], hit["generated_at"], cached=True)

    if req.mode == "review":
        if not req.draft:
            raise HTTPException(status_code=400, detail="draft is required for review mode")
        prompt = (
            "あなたは行政手続きに詳しい相談員です。以下の制度情報を踏まえ、"
            "利用者が書いた申請書の下書きを添削してください。\n"
            "・不足している情報、誤解を招く表現を具体的に指摘する\n"
            "・修正後の文例を提示する\n"
            "・制度情報に書かれていないことは推測せず「窓口に確認」と案内する\n\n"
            f"【制度情報】\n{facts}\n\n【利用者の下書き】\n{req.draft}"
        )
    else:
        prompt = (
            "あなたは子育て中の保護者を支援する相談員です。以下の制度情報をもとに、"
            "はじめての人にも分かるようやさしく説明してください。\n"
            "・「どんな制度か」「誰が対象か」「いくらもらえる/かかるか」「何をすればいいか」の順\n"
            "・専門用語は言い換える。箇条書きを使う。400字程度\n"
            "・制度情報に書かれていないことは絶対に補わない。"
            "条件が曖昧な場合は「詳細は自治体窓口にご確認ください」と明記する\n\n"
            f"【制度情報】\n{facts}"
        )

    try:
        from google.genai import types

        # Client は必ず変数で受けること。`dependencies._build_genai_client().models.generate_content(...)`
        # と繋げると、`.models` を取り出した時点で Client の参照が消えて GC され、
        # その終了処理が内部の httpx 接続を閉じてしまう。Models は Client ではなく
        # 内部の api_client しか保持しないため、閉じられた接続で送信しようとして
        # "Cannot send a request, as the client has been closed" になる。
        # google-genai 2.16 で顕在化した（それ以前のバージョンでは動いていた）。
        client = dependencies._build_genai_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_level=GEMINI_THINKING_LEVEL),
            ),
        )
        text = response.text
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Gemini呼び出しに失敗しました: {exc}") from exc

    generated_at = (
        explanation_cache.store(key, req.benefit_id, facts, GEMINI_MODEL, text)
        if key
        else datetime.now(UTC).isoformat()
    )
    return payload(text, generated_at, cached=False)
