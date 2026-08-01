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

専用 SA: `kosodate-frontend@opendatahackathon-503500.iam.gserviceaccount.com`

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
| 6 | backend から `allUsers` を外す（dev → staging → prod） | 未着手 |

1〜4 は既存サービスに影響しない。実際に dev の backend / frontend とも 200 のままであることを確認した。

5 は `kosodate-frontend-dev` に実際にデプロイし、タグ付きURL経由で
Server Component（直接呼び出し）・Route Handler プロキシの両方から
backend の `/api/healthz` を ID トークン付きで呼べることを確認した
（`env: dev` が正しく返る）。

### 追記: `--source` デプロイ用 GCS バケット（**解決済み。個別の権限付与は不要だった**）

`gcloud run deploy --source` は Cloud Build がソースをアップロードする先として
`run-sources-{project}-{region}` という GCS バケットを使う。
frontend の初回デプロイ時、このバケットが**まだ存在せず**、
`claude-dev` は `storage.buckets.create` を持たないため失敗した。

当初これを「バケットへの権限が `claude-dev` に無い。個別付与が必要」と記録したが、
**それは誤りだった。** 実際に必要だったのは最初の1回の作成だけで、
バケットができた後は `claude-dev` でそのまま回る。

バケット作成時に `claude-dev` へ `roles/storage.objectAdmin` が付く。

```
roles/storage.objectAdmin
  serviceAccount:claude-dev@opendatahackathon-503500.iam.gserviceaccount.com
```

2026-08-02、バケット作成後に `claude-dev` の認証情報で実測した結果:

| 操作 | 結果 |
|---|---|
| `gcloud storage buckets describe` | 成功 |
| `gcloud storage ls`（オブジェクト一覧） | 成功 |
| `make deploy-frontend ENV=dev` | 成功（`kosodate-frontend-dev-00004-yos`） |
| `make deploy ENV=dev`（backend） | 成功（`kosodate-graph-viewer-dev-00005-sos`） |

**frontend・backend とも `claude-dev` で継続的にデプロイできる。追加の権限付与は不要。**

これは Cloud Run サービスそのものと同じ構図で、
[ADR 0008](0008-scoped-credentials.md)「dev の Cloud Run サービスを増やすときの手順」の
**初回だけ広い権限の人が作る**というパターンがそのまま当てはまる。
リージョンごとに1つなので、この作成はプロジェクトで実質1回きり。

> 教訓: 「権限エラーが出た」から「権限の恒久的な付与が要る」を導かないこと。
> リソースが存在しないことによる作成権限の不足なのか、
> 既存リソースへのアクセス権限の不足なのかで、対応がまったく変わる。

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

- staging / prod のフロントエンドサービスはまだ無い。作るときに同じ専用 SA を使うか、
  環境ごとに SA を分けるかは決めていない。**環境ごとに分ける方が
  ADR 0008 の考え方とは一貫する**が、運用の手間と釣り合うかは要検討
- backend 側で「呼び出し元が想定の SA か」を検証してはいない。
  Cloud Run の IAM が手前で弾くので現状は不要だが、
  多層防御として ID トークンの検証を足す余地はある
