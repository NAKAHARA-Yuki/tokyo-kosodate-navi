---
name: deploy-dev
description: dev の Cloud Run に出して画面の動作を確認し、確認後に後片付けする。「動作確認して」「画面を見せて」「dev にデプロイして」のように、実際にアプリの画面を見る必要があるときに使う。
---

**画面の確認が必要なときは `make deploy ENV=dev` で dev の Cloud Run に出すこと。**
スマホから指示を受けて作業する場面が多く、`make dev` でローカルに立てても
利用者からは見えない。URL を返せる形にする。

```bash
make deploy ENV=dev       # dev の Cloud Run へ（回数制限なし。自由に使ってよい）
make url ENV=dev          # URL を確認して利用者に伝える
```

**URL はユーザーごとに分かれる**（`https://<ユーザー名>---kosodate-graph-viewer-dev-....run.app`）。
dev の Cloud Run サービスはチームで1つを共有しているが、リビジョンタグを付けて
`--no-traffic` で出しているため、他の人のデプロイに上書きされない。

**画面を見せるときは URL の末尾に `/debug` を付けること。** `/` は新しい画面のための
仮プレースホルダーで、既存の動作確認用画面（cytoscape.js のグラフ表示）は `/debug` に
退避している（issue #12）。

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
