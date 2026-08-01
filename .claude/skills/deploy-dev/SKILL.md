---
name: deploy-dev
description: dev の Cloud Run に出して画面の動作を確認し、確認後に後片付けする。「動作確認して」「画面を見せて」「dev にデプロイして」のように、実際にアプリの画面を見る必要があるときに使う。
---

**画面の確認が必要なときは dev の Cloud Run に出すこと。**
スマホから指示を受けて作業する場面が多く、ローカルに立てても利用者からは見えない。
URL を返せる形にする。

**画面は frontend（Next.js）が持つ**ので、画面を見せるときは frontend 側を出す。
backend（FastAPI）は API 専用で HTML を返さない（issue #33 / ADR 0013）。

```bash
make deploy-frontend ENV=dev   # frontend（画面）を dev へ
make url-frontend ENV=dev      # 画面の URL を確認して利用者に伝える

make deploy ENV=dev            # backend（API）を dev へ。API を変えたときはこちらも
make url ENV=dev               # backend の URL
```

frontend は backend を環境変数 `BACKEND_URL` で見る。**backend 側の変更を画面から
確認したいときは、backend を先にデプロイしてから frontend を出す**（`make deploy-frontend`
が最新の backend URL を読んで渡す）。

**URL はユーザーごとに分かれる**（`https://<ユーザー名>---kosodate-frontend-dev-....run.app`）。
dev の Cloud Run サービスはチームで1つを共有しているが、リビジョンタグを付けて
`--no-traffic` で出しているため、他の人のデプロイに上書きされない。
**サービスの既定 URL には出ないので、必ずタグ付き URL で確認すること。**

見せる画面:
- `/` — トップページ（制度の一覧ビュー）
- `/debug` — 既存の cytoscape.js のグラフ画面（移行期間中の動作確認用）

staging と prod へは権限が無く、そもそもデプロイできない。

### ただし後片付けはすること

確認のたびに Cloud Run のリビジョンが増え、BigQuery には検証用テーブルが残る。
放置すると「どれが今の状態か」が分からなくなる。

```bash
make cleanup              # 古いリビジョンと検証用テーブルを消す
```

- 確認が終わったら `make cleanup` を実行する
- 検証用に作ったテーブルは `benefits` などの正規8テーブル以外の名前にする
  （`make cleanup` が正規テーブル以外を消す判定をしている）
- Artifact Registry のイメージは権限が無く消せない。溜まったら管理者に伝える
