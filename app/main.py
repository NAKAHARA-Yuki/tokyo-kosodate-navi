"""
子育て支援制度ナレッジグラフ API (FastAPI + BigQuery)

制度の適用判定は BigQuery への定型クエリのみで行い（LLM不使用・ミリ秒・誤判定ゼロ）、
Gemini は制度のやさしい解説や書類添削といった伴走サポートにのみ使う。

**このサービスは API 専用で、HTML は一切返さない。** 画面はすべて frontend/（Next.js の
別 Cloud Run サービス）が持つ（issue #33）。backend への呼び出しはフロントエンド専用の
サービスアカウントに限定する方針のため、ブラウザが直接来る理由を無くしてある（ADR 0013）。

エンドポイントは責務ごとに routers/ 以下に分割している:
- routers/benefits.py : /api/categories, /api/areas, /api/benefits, /api/subgraph
- routers/match.py    : /api/user/profile, /api/benefits/match（Phase2）
- routers/timeline.py : /api/timeline（Phase3）
- routers/support.py  : /api/support/draft-review（Gemini, Phase2）
"""

from config import APP_ENV, DATASET_ID
from fastapi import FastAPI
from routers import benefits, match, support, timeline

app = FastAPI(title="子育て支援制度ナレッジグラフ API")

app.include_router(benefits.router)
app.include_router(match.router)
app.include_router(timeline.router)
app.include_router(support.router)


# パスが `/api/` 配下なのは意図的。`/healthz` は Google Frontend が手前で横取りし、
# コンテナまでリクエストが届かない（Cloud Run のログにも一切残らず、Google の 404 HTML が返る）。
# デプロイ後の確認が常に失敗して気づいた。
@app.get("/api/healthz")
def healthz():
    # どの環境・どのデータセットを見ているかは、デプロイ事故の切り分けで必ず要る
    return {"status": "ok", "env": APP_ENV, "dataset": DATASET_ID}
