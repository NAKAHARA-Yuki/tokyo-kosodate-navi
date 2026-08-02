# ADR 0013: backend への呼び出しをフロントエンドの SA に限定する

- ステータス: 採用（**段階導入中。現時点で `allUsers` はまだ外していない**）
- 日付: 2026-08-01
- 関連: [ADR 0008](0008-scoped-credentials.md) / issue #33

## 背景

issue #33 で、フロントエンドを Next.js の別サービスに分離することにした
（[ADR 0008](0008-scoped-credentials.md) の「dev の Cloud Run サービスを増やすときの手順」で
`kosodate-frontend-dev` を作成済み）。

分離すると、FastAPI 側（`kosodate-graph-viewer-*`）は
**ブラウザから直接叩かれる必要がなくなる**。それなら公開したままにしておく理由がない。

現状の問題は2つある。

1. **backend が `allUsers` に開いている。** 認証なしで誰でも `/api/*` を叩ける。
   今は backend 自身が HTML を配っているので必要な状態だが、分離後は不要になる
2. **4サービスすべてが既定の compute SA で動いていた**
   （`531632442373-compute@developer.gserviceaccount.com`）。
   これは他の用途とも共有される既定 SA なので、
   仮に「SA 限定」にしてもこの SA を指定した時点で**絞ったことにならない**

## 決定

**フロントエンド専用の実行 SA を用意し、backend の invoker をその SA だけに限定する。**

```
ブラウザ ──公開──> kosodate-frontend-*（Next.js）
                        │ サーバ側で ID トークンを付けて呼ぶ
                        ↓ SA 限定
                   kosodate-graph-viewer-*（FastAPI）
```

専用 SA は**環境ごとに分ける**。

| 環境 | frontend サービス | 実行 SA | 呼べる backend |
|---|---|---|---|
| dev | `kosodate-frontend-dev` | `kosodate-frontend@` | `kosodate-graph-viewer-dev` |
| staging | `kosodate-frontend-staging` | `kosodate-frontend-staging@` | `kosodate-graph-viewer-staging` |
| prod | `kosodate-frontend` | `kosodate-frontend-prod@` | `kosodate-graph-viewer` |

1つの SA を使い回すと、その SA に全環境の backend の `run.invoker` を付けることになり、
**dev のフロントエンドから prod の backend を呼べてしまう**。
[ADR 0004](0004-environments.md) で環境を分けた意味が無くなるため、環境ごとに分けた。

dev だけ `-dev` が付かないのは、dev しか無かった時期に作った名残
（SA はリネームできないため、そのままにしている）。

**dev も SA 限定にする。** staging / prod と構成をそろえ、
「dev では動くのに staging で落ちる」を防ぐため。

### 順序を守ること

**backend から `allUsers` を外すのは、Next.js がサーバ側から呼べるようになった後。**
先に外すと画面が動かなくなる。prod は公開サービスなので最後。

| # | 内容 | 状態 |
|---|---|---|
| 1 | 専用 SA の作成 | **完了**（2026-08-01） |
| 2 | `kosodate-frontend-dev` の実行 SA を専用 SA に差し替え | **完了** |
| 3 | 専用 SA に backend(dev) の `run.invoker` を付与 | **完了** |
| 4 | `claude-dev` に専用 SA の `iam.serviceAccountUser` を付与 | **完了** |
| 5 | Next.js からサーバ側で ID トークン付き呼び出し | **完了**（2026-08-01。dev の実サービスで確認済み） |
| 5.5 | backend から HTML 応答（`/` `/debug`）を無くす | **完了**（2026-08-01。issue #33） |
| 6a | backend(**dev**) から `allUsers` を外す | **完了**（2026-08-02） |
| 6b | backend(**staging**) から `allUsers` を外す | **保留。先に staging の frontend が要る**（下記） |
| 6c | backend(**prod**) から `allUsers` を外す | **保留。今やると公開サービスが落ちる**（下記） |

5.5 により、backend が返すのは `/api/*` と `/api/healthz` だけになった。
既存画面（cytoscape.js）は `frontend/public/debug.html` に移し、frontend の `/debug` が配信する。
**ブラウザが backend に直接アクセスする理由が無くなったので、6 に進める状態。**

### 6a の結果（dev、2026-08-02）

`allUsers` を外したあと、`kosodate-frontend-dev` のタグ付き URL で
一覧・詳細・`/debug` がすべて動作することを実ブラウザで確認した。
backend を直接開くと 403。**ID トークン経由の SA 限定アクセスが実証された。**

IAM の伝播に **約90秒**かかった。外した直後は 200 のままなので、
すぐ確認して「効いていない」と判断しないこと。

### 6b / 6c を保留する理由

**staging と prod には frontend サービスが存在しない。**
`kosodate-frontend-dev` しか無く、`.github/workflows/deploy.yml` も
backend（`--source ./app`）しかデプロイしていない。

| 環境 | backend の `/` | 状態 |
|---|---|---|
| dev | — | frontend あり。6a 完了 |
| staging | **404** | backend は HTML を返さなくなったのに frontend が無い。**UI が無い** |
| prod | 200 | 公開中。タグ（`v0.1.3`）が 5.5 より前なので、まだ HTML を返す旧リビジョン |

- **staging**: `allUsers` を外すと CI の staging E2E が壊れる。
  あれは staging **backend** の URL を直接叩いている（`E2E_BASE_URL`）
- **prod**: 今 `allUsers` を外すと、公開サービスが即座に落ちる。
  prod は旧リビジョンのままで UI を配信しており、代わりになる frontend が無い

#### 2026-08-02 時点の進捗

インフラ側は用意した（下記）。**残るのは `deploy.yml` への frontend ジョブ追加と
staging E2E の対象変更で、これはコード側の作業。**

作成済み:

| リソース | 内容 |
|---|---|
| `kosodate-frontend-staging` / `kosodate-frontend` | Cloud Run サービス。中身はプレースホルダー画像 |
| `kosodate-frontend-staging@` / `kosodate-frontend-prod@` | 実行 SA。プロジェクト全体の権限はゼロ |
| 各 backend の `run.invoker` | 対応する環境のフロント SA だけに付与 |
| `github-deployer` の `iam.serviceAccountUser` | 各フロント SA に付与（無いと CI が SA 指定でデプロイできない） |

`claude-dev` には staging / prod のフロントに**何も付けていない**。
ADR 0008 のとおり、staging / prod への反映は CI 経由のみ。

先に必要な作業:

1. `kosodate-frontend-staging` / `kosodate-frontend`（prod）を作る
   （[ADR 0008](0008-scoped-credentials.md) の初回ブートストラップ手順）
2. `deploy.yml` に frontend のデプロイジョブを足す
3. staging E2E の対象を frontend の URL に変える
4. そのうえで staging → prod の順に `allUsers` を外す

**staging は既に UI が無い状態**なので、1〜3 は `allUsers` とは無関係に急ぐ。

1〜4 は既存サービスに影響しない。実際に dev の backend / frontend とも 200 のままであることを確認した。

5 は `kosodate-frontend-dev` に実際にデプロイし、タグ付きURL経由で
Server Component（直接呼び出し）・Route Handler プロキシの両方から
backend の `/api/healthz` を ID トークン付きで呼べることを確認した
（`env: dev` が正しく返る）。

### 追記: `--source` デプロイ用 GCS バケットの権限（**再訂正。対応は途中**）

この節は2度書き換えている。経緯ごと残す。同じ誤りを繰り返さないため。

`gcloud run deploy --source` は Cloud Build がソースをアップロードする先として
`run-sources-{project}-{region}` という GCS バケットを使う。
frontend の初回デプロイ時、このバケットがまだ存在せず、
`claude-dev` は `storage.buckets.create` を持たないため失敗した。ここまでは事実。

#### 1度目の記録（誤り）

「バケットへの権限が無い。個別付与が必要」と書いた。方向としては正しかった。

#### 2度目の記録（これも誤り。より悪かった）

「バケット作成後は `claude-dev` でそのまま回る。追加の権限付与は不要」と訂正した。
**これは検証方法が間違っていた。**

Makefile の `GOOGLE_APPLICATION_CREDENTIALS` を設定して `gcloud` を実行し、
成功したので「`claude-dev` で回る」と判断したが、
**`gcloud` CLI は `GOOGLE_APPLICATION_CREDENTIALS` を無視する**（後述）。
実際には検証者本人の広い権限で走っていた。

```
GAC 未設定             : nakahara.yuki.dev@gmail.com
GAC=claude-dev-adc.json: nakahara.yuki.dev@gmail.com   ← 変わらない
```

`--impersonate-service-account` で測り直すと、`claude-dev` では通らなかった。

#### 実際に必要だったもの

バケットには `roles/storage.objectAdmin` が付いていた。
**この役割は `storage.buckets.get` を含まない。**
「バインディングがある」ことと「必要な権限が揃っている」ことは別。

| 不足していた権限 | 与える役割 | 範囲 | 状態 |
|---|---|---|---|
| `storage.buckets.get` | `roles/storage.legacyBucketReader` | バケット単位 | **付与済み** |
| `serviceusage.services.use` | `roles/serviceusage.serviceUsageConsumer` | **プロジェクト単位** | **未付与** |

2つ目はプロジェクト単位でしか付けられない。データへのアクセス権ではなく
「API 呼び出しをこのプロジェクトに課金して使ってよい」という性質のもので、
ADR 0008 の「書き込みは dev だけ」という前提は崩さない。

```bash
gcloud projects add-iam-policy-binding opendatahackathon-503500 \
  --member="serviceAccount:claude-dev@opendatahackathon-503500.iam.gserviceaccount.com" \
  --role="roles/serviceusage.serviceUsageConsumer"
```

これを付けるまで、`claude-dev` からの `--source` デプロイは通らない。**未対応。**

### 重要: `gcloud` は `GOOGLE_APPLICATION_CREDENTIALS` を見ない

上の誤りの根本原因であり、[ADR 0008](0008-scoped-credentials.md) の記述にも影響する。

`GOOGLE_APPLICATION_CREDENTIALS` は**クライアントライブラリ（ADC）用**の変数で、
`gcloud` CLI は自分の認証ストア（`gcloud auth list`）を使う。

| コマンド | 使う認証 | スコープが効くか |
|---|---|---|
| `make etl` / `make graph` / `make verify` | Python のクライアントライブラリ → ADC | **効く**（想定どおり） |
| `make deploy` / `make deploy-frontend` | `gcloud run deploy` | **効かない。実行者本人の権限で動く** |

つまり「手元から書き込めるのは dev だけ」という保証は、
BigQuery 側では権限層で成立しているが、**Cloud Run 側は Makefile の
`if [ "$(ENV)" != "dev" ]` というガードだけ**で守られている。

`gcloud` を `claude-dev` として動かしたいときは `--impersonate-service-account` を使う。
**検証のときは必ず主体を確認すること。**

```bash
curl -sS "https://oauth2.googleapis.com/tokeninfo?access_token=$(gcloud auth print-access-token)" | jq -r .email
```

> 教訓: 「成功した」ことより「**誰として**成功したか」を先に確かめる。
> 権限の検証で主体を取り違えると、無いはずの権限が有ることになってしまう。

## 理由

### なぜブラウザからの直接呼び出しでは駄目なのか

Cloud Run のサービス間認証は ID トークン（メタデータサーバから取得）で行う。
ブラウザにはメタデータサーバが無く、トークンを安全に持たせる方法もない。

したがって Next.js 側は **Route Handler / Server Component 経由**で backend を呼ぶ必要がある。
クライアントの `fetch` から直接 backend を叩く実装にすると、この ADR は成立しない。
**issue #33 の実装方針に直結する制約。**

### なぜ専用 SA を作るのか

既定の compute SA は他の用途とも共有され、既定で広い権限を持つ。
これを実行 SA にしたまま invoker を絞っても、「その SA を使える何か」がすべて通ってしまう。
呼び出し元を意味のある単位で絞るには、フロントエンド専用の SA が要る。

専用 SA にはプロジェクト全体の権限を一切付けていない。
持っているのは backend(dev) の `run.invoker` だけ。

### なぜ dev も閉じるのか

閉じないと dev だけ経路が変わり、staging で初めて認証まわりの不具合が出る。
[ADR 0004](0004-environments.md) で環境を分けた目的は「本番前に同じ形で確かめること」なので、
ここで構成を変えると意味が薄れる。

なお **`kosodate-frontend-*` は公開のまま**なので、
「dev の Cloud Run に出してスマホから確認する」運用（CLAUDE.md）は壊れない。
分離後に人が見るのはフロントエンド側になる。

## 帰結

- backend が公開エンドポイントでなくなる。攻撃面が減り、
  「公開 URL は認証不要なので管理系エンドポイントを足さない」という制約も緩む
- **backend の URL をブラウザで直接開けなくなる**（手順6の後）。
  API を直接確かめたいときは `gcloud run services proxy` か、
  ID トークンを付けた curl が必要になる
- E2E のうち staging 実データに対して実行するものは、
  frontend 側の URL を見るように変える必要がある
- フロントエンドとバックエンドの間に1ホップ増える。
  Next.js のサーバ側が経路に入るぶん、レイテンシと障害点が増える

## 積み残し

- ~~staging / prod のフロントエンドサービスはまだ無い~~
  → 2026-08-02 に作成し、SA も環境ごとに分けた（上表）。
  **ただし `deploy.yml` に frontend のデプロイジョブがまだ無く、中身はプレースホルダー画像のまま。**
  これが入るまで staging / prod に UI は出ない
- backend 側で「呼び出し元が想定の SA か」を検証してはいない。
  Cloud Run の IAM が手前で弾くので現状は不要だが、
  多層防御として ID トークンの検証を足す余地はある
