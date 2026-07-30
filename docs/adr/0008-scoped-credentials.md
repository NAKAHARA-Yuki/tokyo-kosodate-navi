# ADR 0008: 手元の認証を dev に絞り、staging/prod は CI 経由のみにする

- ステータス: 採用
- 日付: 2026-07-31

## 背景

手元の作業（人間・Claude Code を問わず）は `roles/owner` の個人アカウントで動いていた。
つまり次のどれも、コマンド1つで実行できる状態だった。

- `make etl ENV=prod` — 本番 BigQuery を `WRITE_TRUNCATE` で上書きする。**元に戻せない**
- `make deploy ENV=prod` — CI を通っていないコードを本番に出す
- `bq rm` — データセットごと消す

Claude Code に作業を任せて離席したいという要求があり、そのためには
「最悪でも本番が無事」が前提として必要だった。

実行環境を分ける案（専用 Unix ユーザー、コンテナ）も検討したが、採らなかった。
作業をするには結局同じ認証情報が要るため、**被害範囲は実行環境ではなく認証情報で決まる**。
ユーザーを分けてもコンテナに入れても、owner の認証を渡せば本番を壊せる。

## 決定

**手元の認証を dev だけ書けるサービスアカウントに切り替え、
staging と prod への反映は GitHub Actions 経由に一本化する。**

サービスアカウント `claude-dev@opendatahackathon-503500.iam.gserviceaccount.com`

| 対象 | 権限 |
|---|---|
| `gov_knowledge_db_dev` | OWNER（読み書き） |
| `gov_knowledge_db_staging` | READER |
| `gov_knowledge_db`（prod） | READER |
| Cloud Run `kosodate-graph-viewer-dev` | run.admin |
| Cloud Run staging / prod | **なし** |

prod を READER にしているのは `make clone-data` が prod から読むため。書き込みは落ちる。

Makefile は `$(HOME)/.config/gcloud/claude-dev-adc.json` があればそれを
`GOOGLE_APPLICATION_CREDENTIALS` に設定する。無い環境（CI や他のメンバー）では
既定の認証のまま動くので、この仕組みを知らなくても支障はない。

あわせて `make deploy` は `ENV=dev` 以外を受け付けない。

## 理由

### なぜ鍵ファイルを作らないのか

サービスアカウントキー（JSON）は、漏れたら失効させるまで誰でも使える。
`iam.serviceAccountTokenCreator` によるなりすましなら、人間アカウントの認証が
起点になるため、そちらを失効させれば連鎖して止まる。ファイルとして持ち回るものが無い。

WIF を使う CI 側（ADR には無いが `.github/workflows/deploy.yml`）と同じ方針。

### なぜ Makefile 側でもデプロイを止めるのか

権限だけでも staging デプロイは失敗する。しかし出るのが GCP の権限エラーで、
「なぜ落ちたか」「ではどうすればよいか」が分からない。
Makefile で先に止めて正しい手順（main へマージ / タグを push）を示す。

権限は事故を防ぐ層、Makefile は意図を伝える層として、両方置いている。

### この仕組みの限界

owner の認証情報は同じマシンに残っている。
`GOOGLE_APPLICATION_CREDENTIALS` を外せば owner に戻れるため、
これは**事故を防ぐ仕組みであって、意図的な回避を防ぐものではない**。

本当に不可能にするには、手元から owner の ADC を削除し、
必要なときだけ `gcloud auth application-default login` で取り直す運用にする。
現時点ではそこまでしていない。

## 帰結

- 手元の操作で本番データを壊せなくなった（権限層で落ちる）
- staging と prod に入るものは必ず CI を通ったコードになった
- dev は自由に壊してよい領域として明確になり、Claude Code に任せやすくなった
- dev の Cloud Run サービスを新設したため、公開 URL が1つ増えた
  （中身は公開オープンデータのコピーで、認証情報は含まない）
