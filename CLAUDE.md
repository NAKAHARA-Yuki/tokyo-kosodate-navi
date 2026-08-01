# CLAUDE.md

このリポジトリで Claude Code が作業するときの前提と規約。

## やり取りの言語

ユーザーへの応答は日本語で行うこと。コード・コミット・ドキュメントの日本語統一は
「コードを書くときの約束」を参照。

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

**手元から書き込めるのは dev だけ。** `make auth` で GCP アクセスが `claude-dev`
サービスアカウントに切り替わる（[docs/adr/0008](docs/adr/0008-scoped-credentials.md)）。
`make etl ENV=prod` や `make graph ENV=staging` は権限エラーで落ちる。これは仕様。
staging へ反映したいなら main へマージ、prod なら `v*.*.*` タグを push する。

開発は `docker/compose.yaml` のコンテナ内で行うのが正（`make agent-up` → `make agent-shell`）。
Python は本番と同じ 3.12 で、依存も Playwright もイメージに焼き込み済み
（`make setup` 不要、`VENV=/usr/local`）。コンテナ内は sudo 可・ホストからは隔離
（[docs/adr/0009](docs/adr/0009-agent-container.md)）。外向き通信は絞っていない
（許可リストは効果より害が大きく撤回した。[docs/adr/0011](docs/adr/0011-drop-egress-allowlist.md)）。
**被害範囲を押さえているのは通信ではなく認証のスコープ**なので、
`make auth` を通さずに GCP を触らないこと。
docker ソケットは渡していないので、コンテナ内から docker は使えない。
GitHub の操作は `gh` を使う（`~/.git-credentials` のトークンを実行時に読むので
ログイン不要。トークンをユーザーに要求しないこと）。

`/api/healthz` が `{"env": ..., "dataset": ...}` を返すので、どこを見ているかは常に確認できる。

## よく使うコマンド

`make help` で一覧。`ENV=` で環境を指定する（既定は dev）。

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

## 動作確認は dev の Cloud Run に出してよい

画面の確認が必要なときの手順（デプロイ・URL の伝え方・後片付け）は `deploy-dev` スキルを参照。

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
- **Cloud Run で `/healthz` は使えない。** Google Frontend が手前で横取りするため
  コンテナまでリクエストが届かず、Google の 404 HTML が返る（Cloud Run のログにも残らない）。
  FastAPI に登録されていても無関係。ヘルスチェックは `/api/healthz` に置いている。
- **本番イメージの依存は `app/requirements.lock` からしか入れない。** `app/requirements.in` を
  変えたら `make lock` を実行してロックを再生成すること。緩い指定のままビルドすると
  ビルドした日によって中身が変わり、prod と staging が別物になる
  （google-genai が 2.14→2.16 に上がって Gemini 呼び出しが 503 になった実績あり）。
  ロックは本番と同じ `python:3.12-slim` の中で生成する。ローカル（3.14）で作ると本番で入らない。
- **BigQuery の GQL（`GRAPH ... MATCH`）は Enterprise エディションの予約が必須になった。**
  コード側の変更なしに `BigQuery Graph queries require a reservation with Enterprise or
  Enterprise Plus edition.` で全滅する（詳細は [docs/adr/0003](docs/adr/0003-graph-schema.md)）。
  `/api/subgraph` だけでなく `/api/benefits/match` の `next_steps`（`_fetch_next_steps`）も
  同じ理由で壊れていた。PROPERTY GRAPH の定義は残したまま、REQUIRES / REQUIRES_DOC /
  LEADS_TO を辿るクエリは通常SQLの JOIN に書き換えて回避した。
- **E2E が「Playwright / Chromium が動かない」ように見えたら、サブリソースの取得を疑う。**
  `page.goto` は `waitUntil="load"` で全サブリソースを待つため、1つでも取れないと
  HTML 自体が 200 で返っていてもタイムアウトする。エラーは「タイムアウト」としか
  言わないので、ブラウザ側の制約に見えてしまう（実際に誤診したことがある）。
  `page.on("response")` を張れば切り分けられる。Chromium の起動可否は
  `p.chromium.launch()` + `set_content()` で単体で確かめること。

## コードを書くときの約束

- **コミットのメールアドレスは GitHub の noreply を使う**（`<数字>+<ユーザー名>@users.noreply.github.com`）。
  リポジトリは public なので、実アドレスを設定すると履歴として公開される。
- 日本語のコメント・ドキュメントで統一（チームの共通言語）。
- 推測でモデル名やAPIの仕様を書かない。動かして確かめてから書く。
- **ブラウザが実行時に読むものを外部 CDN から取らない。** ライブラリは `app/static/` に
  取り込んで自分で配り、版と sha256 を `app/static/README.md` に記録する
  （[docs/adr/0010](docs/adr/0010-no-runtime-cdn.md)）。第三者のホストが返したものを
  無検証で実行するのは、本番の依存をハッシュで固定している方針（ADR 0007）と矛盾する。
- 本番データを触る操作（`make etl` など）は影響を明示してから実行する。

## ブランチとリリース

GitHub Flow + タグでの本番リリース。詳細は [CONTRIBUTING.md](CONTRIBUTING.md)。

- `main` へ直接コミットしない。`<種別>/<説明>` のトピックブランチから PR
- `main` へは **Squash merge** のみ
- `main` マージ → staging へ自動デプロイ
- **本番は `v*.*.*` タグの push でリリース**（main の HEAD ではない）
- 切り戻しは前のタグを再デプロイ

## 変更したら

1. `make check`（lint + test + e2e）を通す
2. 画面の挙動を変えたら E2E を追加/更新する（staging の実データでも通る書き方で）
3. データモデルを変えたら `docs/data-model.md` と本ファイルの該当箇所も更新
4. 非自明な設計判断をしたら `docs/adr/` に1枚足す
