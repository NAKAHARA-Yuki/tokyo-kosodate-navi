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
- **リクエスト時に** Gemini を呼ぶのは `/api/support/draft-review` だけ
- Gemini のプロンプトには必ず「制度情報に書かれていないことは補わない／曖昧なら窓口に確認と明記」を入れ、
  レスポンスに AI 生成である旨の disclaimer を付ける
- プロフィール入力はチャットではなく選択式フォーム（将来のマイナポータル連携を見越した疎結合設計）

**ETL 時のデータ抽出・正規化には LLM を使ってよい**（禁じているのは判定に使うこと）。
ただし**むやみに叩かない。必要なときに、必要な分だけ、必要なモデルを使う** —
規則で拾えなかった残りだけに当てる／結果はテーブルに保存して二度叩かない／
既定は軽いモデル／バッチで回す／抽出元と確度を残して推定を断定として見せない。
詳細は [docs/adr/0001](docs/adr/0001-judgment-vs-llm-separation.md) の追記。

## 構成

```
src/            ETL とグラフ構築（ローカル or CI から実行）。etl_to_bq.py はエントリポイントで、
                 実処理は etl_util/etl_normalize/etl_documents/etl_statuses/etl_graph/etl_schema/etl_quality/etl_load に分割
  age_rules.py    対象年齢をテキストから推定するルール（正規表現のみ）
  create_graph.sql/.py  PROPERTY GRAPH 定義
  verify_graph.py 動作検証クエリ
app/            Cloud Run で動く FastAPI。**API専用でHTMLは返さない**（docs/adr/0013）。
                 main.py はルーター登録のみで、実処理は routers/（benefits/match/timeline/support/meta）に分割
  config.py       環境（dev/staging/prod）ごとの設定。ETL からも参照する
  dependencies.py BigQuery / Gemini クライアントの生成
  explanation_cache.py  やさしい解説の生成結果を BigQuery に保存して使い回す（docs/adr/0015）
frontend/       Cloud Run で動く Next.js（別サービス）。画面はすべてこちら
  lib/backend.ts  backend を ID トークン付きで呼ぶ（サーバ側からのみ。docs/adr/0013）
  lib/profile.ts  利用者の属性。**URL のクエリを正とし、localStorage は補助**（issue #53）
  app/settings/   属性の入力画面。トップには入力欄を置かない
  app/api/[...path]/route.ts  backend への catch-all プロキシ
  public/debug.html  開発用画面（素のJS + cytoscape.js）。/debug で配信。撤去しない（docs/adr/0014）
tests/          ユニット・API結合テスト（BigQuery はモック）
e2e/            Playwright による画面操作テスト。
                 test_accessibility.py は axe で WCAG AA 相当を機械的に守る（docs/adr/0016）
docs/           設計ドキュメントと ADR
```

ファイルごとの詳細な責務は [docs/architecture.md](docs/architecture.md) を参照。

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

**デプロイしたら既定 URL でも確認する。** `make deploy` 系は `--tag <ユーザー名> --no-traffic`
なので、**リビジョンができたことと、トラフィックがそこへ向いたことは別**。
タグ付き URL だけ見て「出た」と判断すると、既定 URL が古いリビジョンを配り続ける。
実際に dev の frontend は数日間、既定 URL で Cloud Run のプレースホルダー画像を配信していた
（prod の backend でも同じことが起きた）。向け直すなら `gcloud run services update-traffic --to-latest`。

## 手で変えた設定はデプロイで元に戻る

コンソールや `gcloud` で変えた設定のうち、`deploy.yml` / `Makefile` が同じ項目を
書いているものは、**次のデプロイで必ず上書きされる**。「今は正しい」と
「これからも正しい」は別。手で変えたら、設定しているコード側も同時に直すこと。

実例: backend から `allUsers` を手で剥がして 403 を実測したのに、`deploy.yml` に
`--allow-unauthenticated` が残っていたため、次のマージ3回で公開状態に戻っていた（#51）。

権限によって、手で変えられる範囲が違う。

| 誰 | 変えられるもの |
|---|---|
| owner（`nakahara.yuki.dev@`） | 全部。**IAM を触れるのはここだけ** |
| `roles/editor` のメンバー | Cloud Run のサービス設定・環境変数・トラフィック・リビジョン |
| `claude-dev`（手元・コンテナ） | dev の Cloud Run のみ |

`roles/editor` には `run.services.setIamPolicy` が無いので、`allUsers` の付け外しは
owner にしか実行できない。必要なら依頼すること。

### 権限を確かめるときは「誰として成功したか」を先に見る

`gcloud` は `GOOGLE_APPLICATION_CREDENTIALS` を見ない（[ADR 0013](docs/adr/0013-backend-sa-only-access.md)）。
Makefile がそれを設定していても、`gcloud` は自分の認証ストアを使う。
これを知らずに「`claude-dev` で回る」と誤った結論を出し、ADR を書き換える事故を起こしている。

```bash
curl -sS "https://oauth2.googleapis.com/tokeninfo?access_token=$(gcloud auth print-access-token)" | jq -r .email
```

`claude-dev` として試すなら `--impersonate-service-account` を使う。

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
- **BigQuery にストリーミング挿入した行は、直後に UPDATE / DELETE できない**（400 になる。
  `would affect rows in the streaming buffer`）。`insert_rows_json` で書いたものを
  すぐ消せる前提のコードを書かないこと。全消しが要るなら `DROP TABLE`
  （やさしい解説のキャッシュがこれに該当する。[docs/adr/0015](docs/adr/0015-cache-ai-explanations.md)）。
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
- **ブラウザが実行時に読むものを外部 CDN から取らない。** ライブラリは `frontend/public/` に
  取り込んで自分で配り、版と sha256 を `frontend/public/README.md` に記録する
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

### issue を扱うとき

- **着手する issue には必ず自分をアサインする。** アサインが「今それを誰が持っているか」を
  表す唯一の印なので、付けずに始めると他の人が同じものに手を出す
- **すでに誰かがアサインされている issue には触らない。** 手が空いていても、
  横から進めると作業が重複し、コンフリクトと無駄なやり直しになる。
  引き取りたいときは issue にコメントして、アサインされている人の返事を待つ

### PR を出したら

- **必ず誰かをアサインする。** `main` は approve が1件以上ないとマージできない
  （GitHub は自己承認を許可しないため、自分では通せない）。
  アサインが無い PR は「誰も自分ごとだと思っていない PR」になり、そのまま滞留する
- レビュー依頼を複数人に出すのは構わないが、**アサインは1人に絞る。**
  主担当が誰かを曖昧にしない

### レビューを依頼されたら

**必ず PR 上に結果を残すこと。** 口頭やチャットで済ませない。後から
「なぜこれが入ったのか」を追えるのは PR に残っている記録だけ。

| 判断 | やること |
|---|---|
| 問題なし | **Approve する** |
| 直してほしい点がある | **指摘事項を PR のコメントに書く。** 該当行があれば行コメントで |

黙って放置しない。見たうえで判断がつかないなら、その旨をコメントに書く。
見る観点は [CONTRIBUTING.md](CONTRIBUTING.md) の「レビューで必ず見る点」。

## 変更したら

1. `make check`（lint + test + e2e）を通す
2. 画面の挙動を変えたら E2E を追加/更新する（staging の実データでも通る書き方で）
3. データモデルを変えたら `docs/data-model.md` と本ファイルの該当箇所も更新
4. 非自明な設計判断をしたら `docs/adr/` に1枚足す
