# CLAUDE.md

このリポジトリで Claude Code が作業するときの前提と規約。

## このプロダクトは何か

東京都「子育て支援制度レジストリ」（7,812件）をナレッジグラフ化し、
ユーザー属性から対象制度を漏れなく届ける GovTech サービス。
都民が陥る「知りそびれ・申し込みそびれ・貰いそびれ」の3つのそびれを解消することが目的。

## 最重要の設計原則：判定と伴走を混ぜない

**制度の適用判定に LLM を使ってはいけない。**

| 層 | 使うもの | 責務 |
|---|---|---|
| 判定 | BigQuery Graph への定型クエリ | 対象制度の絞り込み。ミリ秒・誤判定ゼロ |
| 伴走 | Gemini | やさしい言い換え、書類添削。**判定結果は変えない** |

理由: 行政制度のマッチングは誤りが許されない。「対象なのに出ない」「対象外なのに出る」は
ユーザーの不利益に直結する。LLM のハルシネーションと応答遅延をこの経路に持ち込まない。

守るべきこと:
- `/api/benefits`, `/api/benefits/match`, `/api/timeline` に LLM を挟まない
- Gemini を呼ぶのは `/api/support/draft-review` だけ
- Gemini のプロンプトには必ず「制度情報に書かれていないことは補わない／曖昧なら窓口に確認と明記」を入れ、
  レスポンスに AI 生成である旨の disclaimer を付ける
- プロフィール入力はチャットではなく選択式フォーム（将来のマイナポータル連携を見越した疎結合設計）

## 構成

```
src/            ETL とグラフ構築（ローカル or CI から実行）
  etl_to_bq.py    レジストリJSON取得 → 整形 → BigQueryロード
  age_rules.py    対象年齢をテキストから推定するルール（正規表現のみ）
  create_graph.sql/.py  PROPERTY GRAPH 定義
  verify_graph.py 動作検証クエリ
app/            Cloud Run で動く FastAPI アプリ
  config.py       環境（dev/staging/prod）ごとの設定。ETL からも参照する
  main.py         API 本体
  templates/index.html  フロントエンド（素のJS + cytoscape.js。Next.jsではない）
tests/          ユニット・API結合テスト（BigQuery はモック）
e2e/            Playwright による画面操作テスト
docs/           設計ドキュメントと ADR
```

## 環境（dev / staging / prod）

GCP プロジェクトは1つのまま、**BigQuery データセットと Cloud Run サービスを分けている**。
切り替えは `APP_ENV`。**未指定なら dev**（誤って本番を触らないため）。詳細は
[docs/adr/0004](docs/adr/0004-environments.md)。

| 環境 | データセット | 用途 |
|---|---|---|
| dev | `gov_knowledge_db_dev` | 各自の検証。壊してよい |
| staging | `gov_knowledge_db_staging` | main マージで自動デプロイ。本番前ゲート |
| prod | `gov_knowledge_db` | 公開環境 |

`/healthz` が `{"env": ..., "dataset": ...}` を返すので、どこを見ているかは常に確認できる。

## よく使うコマンド

`make help` で一覧。`ENV=` で環境を指定する（既定は dev）。

```bash
make setup                # 仮想環境・依存関係・Playwright
make lint                 # ruff check + format --check
make fmt                  # 自動整形
make test                 # ユニット・API結合テスト（GCP不要）
make e2e                  # E2E（スタブ版アプリを自動起動。GCP不要）
make check                # lint + test + e2e
make dev                  # ローカル起動 (http://localhost:8080)

make etl ENV=dev          # BigQuery へデータ投入（対象環境を上書き。確認あり）
make graph ENV=dev        # PROPERTY GRAPH 再作成
make verify ENV=dev       # グラフの動作検証
make clone-data ENV=dev   # 本番データを dev にコピー（ETLより速い）
make deploy ENV=staging   # Cloud Run へデプロイ
```

## データモデルの要点

詳細は `docs/data-model.md`。特に間違えやすい点だけここに書く。

- **年齢で絞るときは必ず `effective_min_age_months` / `effective_max_age_months` を使う。**
  素の `min_age_months` / `max_age_months` は6割超が NULL で、それだけで絞ると
  「10歳なのに新生児向け制度が出る」状態になる（実際に一度やらかしている）。
- `age_source` は `explicit`（元データに年齢あり）/ `inferred`（テキストから推定）/ `unknown`。
  推定値をユーザーに見せるときは「推定」と明示する。
- `has_free_text_conditions=true` の制度は機械判定しきれない条件が残っている。
  マッチさせるだけでなく条件文言を提示するか Gemini に補足させる。
- `is_prenatal` は妊娠期の制度。子どもの月齢では表現できないので別軸で持っている。

## 落とし穴（踏んだもの）

- **PROPERTY GRAPH には PRIMARY KEY が必須。** ノード/エッジの元テーブルに
  `ALTER TABLE ... ADD PRIMARY KEY (...) NOT ENFORCED` が事前に必要。
  再実行時は `DROP PRIMARY KEY IF EXISTS` を先に挟まないと "Already Exists" になる。
- **cytoscape の `text-wrap: wrap` は空白でしか折り返さない。** 空白のない日本語は
  改行されずノードからはみ出す。`wrapLabel()` で自前で改行を入れている。
- **`preset` レイアウトの `positions` にはノードIDではなく要素が渡る。** `ele.id()` で引く。
- **`preset` レイアウトはアニメーションさせない。** `animate: true` にすると
  `cy.fit()` がアニメーション途中の座標で走り、スマホでグラフが画面外にはみ出す。
  座標は計算済みなのでアニメーションの必要がない。
- **layout インスタンスの `one('layoutstop')` は発火しない。** 購読するなら `cy.one('layoutstop')`。
  これに気づかず、スマホで fit が一度も走っていない状態が続いていた。
- **CSS のベース定義はメディアクエリより前に置く。** 後ろに置くと同じ詳細度で後勝ちになり、
  メディアクエリ内の指定が無効になる（詳細シートの閉じるボタンが表示されない不具合の原因）。
- **元データは本文に `タイトル;https://...` 形式でリンクを直接埋め込んでいる。**
  `extract_links()` で全テキスト列から分離済み。新しいテキスト列を追加するときも通すこと。
- **必要書類欄を読点「、」で分割してはいけない。** 一文が途中で切れて意味不明な書類ノードになる。
- **Gemini は `thinking_level` と `thinking_budget` を併用できない**（400 になる）。

## コードを書くときの約束

- コメントは「なぜそうしたか」を書く。何をしているかはコードを読めば分かる。
- 日本語のコメント・ドキュメントで統一（チームの共通言語）。
- 推測でモデル名やAPIの仕様を書かない。動かして確かめてから書く。
- 本番データを触る操作（`make etl` など）は影響を明示してから実行する。

## 変更したら

1. `make lint && make test` を通す
2. データモデルを変えたら `docs/data-model.md` と本ファイルの該当箇所も更新
3. 非自明な設計判断をしたら `docs/adr/` に1枚足す
