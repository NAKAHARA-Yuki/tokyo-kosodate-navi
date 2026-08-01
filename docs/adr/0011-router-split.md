# ADR 0011: main.py / etl_to_bq.py の責務分割

- ステータス: 採用
- 日付: 2026-08-01

## 背景

`app/main.py`（約660行）と `src/etl_to_bq.py`（約1061行）が単一ファイルに責務混在しており、
見通しが悪くなっていた（issue #13）。挙動は変えず、構造だけを分割する。

## 決定

### app/ の分割と `dependencies.py` の呼び出し規約

`get_client()` / `_build_genai_client()` を `app/dependencies.py` に切り出し、
`/api/categories` 等のエンドポイントは責務ごとに `app/routers/benefits.py` /
`match.py` / `timeline.py` / `support.py` に分割した。`app/main.py` は
`FastAPI()` の生成とルーター登録だけを残す。

**ルーター側は `import dependencies` した上で `dependencies.get_client()` のように
モジュール経由で呼ぶ。** `from dependencies import get_client` で名前を束縛すると、
`tests/conftest.py` の `monkeypatch.setattr(dependencies, "get_client", ...)` や
`e2e/server.py` の直接差し替え（`dependencies.get_client = lambda: ...`）が効かなくなる
（Python の属性差し替えは呼び出し側がどのモジュール経由で参照しているかに依存するため）。
FastAPI の `Depends()` による DI は採用しなかった。挙動を変えないリファクタリングという
issueの制約に対し、DI導入は `tests/conftest.py` の差し替え方式ごと作り直すことになり、
影響範囲が本来の分割作業を超えるため。

### 年齢フィルタSQLは2箇所だけ共通化した

`search_benefits` と `match_benefits` の「単一の年齢が範囲内か」判定は同型だったため
`app/queries.py` の `age_filter_sql()` に共通化した。一方 `get_timeline` の
ライフステージ範囲との重複判定は、NULLの扱いが逆（NULLを許容ではなく除外）で
`age_source` の分岐も2値/3値と異なる構造のため、無理に共通化せず現状のまま残した。

### src/etl_to_bq.py の分割はフラットな `etl_*.py`

既存の `src/age_rules.py`（独立ファイル・正規表現のみ）と同じ「1ファイル1責務」の
命名慣習に合わせ、`src/etl/` のようなサブパッケージ化はせず `src/` 直下にフラットな
`etl_util.py` / `etl_normalize.py` / `etl_documents.py` / `etl_statuses.py` /
`etl_graph.py` / `etl_schema.py` / `etl_load.py` として分割した。
`etl_to_bq.py` は `python src/etl_to_bq.py`（Makefile / CI から呼ばれる）という
エントリポイントの役割だけを残し、取得（`fetch_json`/`extract_records`）と
実行順序（`main`）のみを持つ。ファイル名は `docs/data-model.md` の整形仕様の見出しと
対応させ、トレーサビリティを上げている。

## 帰結

- `tests/conftest.py` の `bq` フィクスチャと `tests/test_api.py` の Gemini 差し替えは
  パッチ対象を `main.*` から `dependencies.*` に変更した
- `e2e/server.py` のスタブ差し替えも同様に `dependencies.*` に変更した
- `tests/test_etl_transform.py` の import 元を新しいモジュールに追従させた
