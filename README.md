# 東京都 子育て支援制度ナレッジグラフ

東京都「子育て支援制度レジストリ」の 7,812 件をナレッジグラフ化し、
居住地と子どもの年齢から**対象になる制度を漏れなく届ける**アプリです。

🔗 **公開URL**: https://kosodate-graph-viewer-531632442373.asia-northeast1.run.app

## 解決したい課題

都内には子育て関連だけで数千の行政制度がありますが、制度が複雑で受給要件も分かりにくく、
都民は「知りそびれ・申し込みそびれ・貰いそびれ」の3つのそびれに陥っています。
本アプリは属性と制度要件をグラフで結び、開くだけで自分向けの制度が届く状態を目指します。

## できること

- **属性マッチング** — 居住地・子どもの月齢を選ぶと対象制度を一覧＋グラフ表示（マッチ理由付き）
- **スキルツリー** — 「1歳6か月児健診 → 2歳児歯科健診 → 3歳児健診」のような制度の連鎖を可視化
- **タイムライン** — 妊娠中〜18歳の8ステージに制度を並べ、次に何が来るかを俯瞰
- **費用・助成額** — いくらもらえる/かかるかを表示
- **AIやさしい解説** — Gemini が制度をかみ砕いて説明（判定には一切関与しない）

## アーキテクチャ

```
東京都レジストリ JSON (92MB)
        │  src/etl_to_bq.py（取得・整形・正規化）
        ▼
   BigQuery  gov_knowledge_db
     ├ ノード: benefits / statuses / documents / schemes
     └ エッジ: REQUIRES / REQUIRES_DOC / IN_SCHEME / LEADS_TO
        │  PROPERTY GRAPH: kosodate_graph
        ▼
   Cloud Run (FastAPI)  ──判定: 定型GQL（LLM不使用）
        │                └伴走: Gemini（解説・添削のみ）
        ▼
   ブラウザ (cytoscape.js)
```

**設計の核**: 制度の適用判定は BigQuery Graph の確定的クエリだけで行い、LLM を挟みません。
誤判定が許されない領域だからです。詳細は [docs/adr/0001](docs/adr/0001-judgment-vs-llm-separation.md)。

## セットアップ

前提: Python 3.12+、`gcloud` 認証済み、GCP プロジェクトへのアクセス権

```bash
git clone <このリポジトリ>
cd 16_OpenDataHackathon

cp .env.example .env      # 必要なら GCP_PROJECT_ID を書き換える
make setup                # 仮想環境 + 依存関係
gcloud auth application-default login   # 未認証の場合

make dev                  # http://localhost:8080
```

## 環境

GCP プロジェクトは1つのまま、BigQuery データセットと Cloud Run サービスを分けています。
切り替えは `APP_ENV`（`make` なら `ENV=`）。**未指定なら dev** です。

| 環境 | データセット | 用途 |
|---|---|---|
| dev | `gov_knowledge_db_dev` | 各自の検証。壊してよい |
| staging | `gov_knowledge_db_staging` | main マージで自動デプロイ。本番前ゲート |
| prod | `gov_knowledge_db` | 公開環境 |

`/healthz` が現在の環境とデータセットを返します。

## 開発

```bash
make lint     # ruff（チェックのみ）
make fmt      # 自動整形
make test     # ユニット・API結合テスト（GCP不要）
make e2e      # E2E（ブラウザ操作。GCP不要）
make check    # 上記まとめて
make help     # コマンド一覧
```

データパイプラインを回す場合（**対象環境の BigQuery を上書きします**）:

```bash
make clone-data ENV=dev   # 本番データを dev にコピー（ETLより速い）
make etl ENV=dev          # レジストリ取得 → 整形 → ロード（5分程度）
make graph ENV=dev        # PROPERTY GRAPH 再作成
make verify ENV=dev       # 検証クエリ
```

## デプロイ

```
PR            → CI: lint + テスト + E2E(スタブ) + Docker build
main へマージ  → staging へ自動デプロイ → E2E(staging 実データ)
本番リリース   → GitHub Actions を手動実行 + 承認 → prod へデプロイ
```

## ドキュメント

| ファイル | 内容 |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Claude Code 向けの前提・規約・落とし穴 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | ブランチ運用・コミット規約・レビュー基準 |
| [docs/architecture.md](docs/architecture.md) | 全体構成と各コンポーネントの責務 |
| [docs/data-model.md](docs/data-model.md) | BigQuery のテーブル・カラム定義 |
| [docs/adr/](docs/adr/) | 非自明な設計判断の記録 |

## 技術スタック

| 領域 | 採用 |
|---|---|
| データ基盤 | BigQuery（BigQuery Graph / GQL） |
| API | Cloud Run + FastAPI |
| フロント | 素の JS + cytoscape.js（サーバーサイドレンダリング） |
| AI | Gemini（Vertex AI, `gemini-3.5-flash-lite`） |
| データ | [東京都子育て支援制度レジストリ](https://portal.data.metro.tokyo.lg.jp/visualization/childcare-support-system-registry/) |

## ライセンス / データの出典

元データは東京都オープンデータカタログサイトで公開されている
「東京デジタル2030ビジョン（こどもDX）子育て支援制度レジストリ」を利用しています。
表示される制度情報は参考であり、**最終的な判断は各自治体の公式情報を確認してください。**
