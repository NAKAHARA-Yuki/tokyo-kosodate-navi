# ADR 0004: 環境は「データセット分離」で dev / staging / prod を分ける

- ステータス: 採用
- 日付: 2026-07-25

## 背景

ハッカソン期間中は本番の BigQuery データセットと Cloud Run サービスが1つだけで、
ETL を流すと即座に公開中のアプリに反映されていた。
チーム開発に移行するにあたり、以下ができない状態だった。

- 壊れるかもしれない変更を試す場所がない
- デプロイ前に「実データで動くか」を確認する場所がない
- 誰かの検証作業が本番の表示に影響する

## 決定

**GCP プロジェクトは1つのまま、BigQuery データセットと Cloud Run サービスを環境ごとに分ける。**

| 環境 | BigQuery データセット | Cloud Run サービス | 用途 |
|---|---|---|---|
| dev | `gov_knowledge_db_dev` | `kosodate-graph-viewer-dev` | 各自の検証。壊してよい |
| staging | `gov_knowledge_db_staging` | `kosodate-graph-viewer-staging` | main マージ後の自動デプロイ先。本番前ゲート |
| prod | `gov_knowledge_db` | `kosodate-graph-viewer` | 公開環境 |

切り替えは環境変数 `APP_ENV` で行い、定義は `app/config.py` に集約する。
アプリと ETL の両方が同じ定義を参照する。

```bash
make dev ENV=dev
make etl ENV=staging
make deploy ENV=prod     # 確認プロンプトあり
```

**未指定時のデフォルトは `dev`。** 誤って本番を触る事故を防ぐため。
`APP_ENV` に想定外の値が入ったら起動時に例外にする。

## 理由

### なぜ GCP プロジェクトを分けないのか

環境ごとにプロジェクトを分けるのが本来は望ましい（IAM とクォータが完全に独立するため）。
しかし今の体制では

- 請求先アカウントとプロジェクト作成権限の管理コストが増える
- サービスアカウントと Workload Identity の設定が3倍になる
- ハッカソン起点のプロダクトで、そこまでの分離を維持する人手がない

一方、**「本番を壊さずに試せる」「デプロイ前に実データで確認できる」という目的は
データセット分離で達成できる。** まずここから始め、必要になったらプロジェクト分割に進む。

### 引き受けるリスク

- 同一プロジェクトなので、IAM や API 割り当てのミスは全環境に波及する
- 誤って `APP_ENV=prod` で ETL を流せば本番を壊せる
  → `make etl` に確認プロンプトを入れ、CI からは本番 ETL を実行しない

## データの用意

staging / dev のデータセットは、ETL を回さず**本番からコピー**できる。

```bash
make clone-data ENV=dev
```

92MB の JSON を再取得して整形し直すより速く、費用もかからない。
ETL のロジック自体を検証したいときだけ `make etl ENV=dev` を使う。

## デプロイの流れ

```
PR                 → CI: lint + テスト + E2E(スタブ) + Docker build
main へマージ       → staging へ自動デプロイ → E2E(staging 実データ)
本番リリース        → 手動実行 + GitHub Environments の承認 → prod へデプロイ → スモーク
```

E2E をスタブと実データの2段構えにしているのは、
スタブだけだと BigQuery のスキーマ変更やデータ欠損を検出できないため（→ [ADR 0005](0005-e2e-strategy.md)）。

## 環境の見分け方

`/api/healthz` が現在の環境とデータセットを返す。デプロイ事故の切り分けに使う。

```json
{"status": "ok", "env": "staging", "dataset": "gov_knowledge_db_staging"}
```

CI ではデプロイ直後にこの値を検証し、意図しない環境を指していたら失敗させている。
