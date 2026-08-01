"""
子育て支援制度ナレッジグラフ 可視化アプリ (FastAPI + BigQuery Graph)

制度の適用判定は BigQuery Graph への定型クエリのみで行い（LLM不使用・ミリ秒・誤判定ゼロ）、
Gemini は制度のやさしい解説や書類添削といった伴走サポートにのみ使う。

エンドポイントは責務ごとに routers/ 以下に分割している:
- routers/benefits.py : /api/categories, /api/areas, /api/benefits, /api/subgraph
- routers/match.py    : /api/user/profile, /api/benefits/match（Phase2）
- routers/timeline.py : /api/timeline（Phase3）
- routers/support.py  : /api/support/draft-review（Gemini, Phase2）

- /               : 新しい画面のための仮プレースホルダー（#12）
- /debug          : 既存の動作確認用画面（cytoscape.js）
"""

import os

from config import APP_ENV, DATASET_ID
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from routers import benefits, match, support, timeline

app = FastAPI(title="子育て支援制度ナレッジグラフ")

BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

app.include_router(benefits.router)
app.include_router(match.router)
app.include_router(timeline.router)
app.include_router(support.router)


@app.get("/", response_class=HTMLResponse)
def index():
    """新しい画面ができるまでの仮対応（#12）。動作確認は /debug で行う。"""
    return "<p>準備中です。動作確認は <a href='/debug'>/debug</a> からどうぞ。</p>"


@app.get("/debug", response_class=HTMLResponse)
def debug_view():
    """既存画面（cytoscape.js の単一ファイル画面）。動作確認・デバッグ用として残す。"""
    with open(os.path.join(BASE_DIR, "templates", "index.html"), encoding="utf-8") as f:
        return f.read()


# パスが `/api/` 配下なのは意図的。`/healthz` は Google Frontend が手前で横取りし、
# コンテナまでリクエストが届かない（Cloud Run のログにも一切残らず、Google の 404 HTML が返る）。
# デプロイ後の確認が常に失敗して気づいた。
@app.get("/api/healthz")
def healthz():
    # どの環境・どのデータセットを見ているかは、デプロイ事故の切り分けで必ず要る
    return {"status": "ok", "env": APP_ENV, "dataset": DATASET_ID}
