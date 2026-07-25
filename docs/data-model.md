# データモデル

BigQuery データセット `gov_knowledge_db`（location: `asia-northeast1`）

## テーブル一覧

| テーブル | 種別 | 件数 | 列数 | 役割 |
|---|---|---|---|---|
| `benefits` | ノード | 7,812 | 100 | 制度（自治体ごとの1レコード） |
| `schemes` | ノード | 300 | 6 | 制度マスタ（自治体をまたいだ同一制度） |
| `statuses` | ノード | 453 | 6 | 適用条件（年齢・地域・分類） |
| `documents` | ノード | 9,373 | 5 | 必要書類 |
| `benefit_requires_status` | エッジ | 42,317 | 2 | 制度 → 条件 |
| `benefit_requires_doc` | エッジ | 14,721 | 2 | 制度 → 書類 |
| `benefit_in_scheme` | エッジ | 7,812 | 2 | 制度 → 制度マスタ |
| `benefit_leads_to` | エッジ | 6,192 | 4 | 制度 → 制度（スキルツリー） |

PROPERTY GRAPH: `kosodate_graph`
ラベル: `Benefit` / `Scheme` / `Status` / `Document`、
`REQUIRES` / `REQUIRES_DOC` / `IN_SCHEME` / `LEADS_TO`

## benefits（主要カラム）

100列あるため、使用頻度の高いものと注意が必要なものだけ記載します。
全列は `bq show --schema` で確認してください。

### 識別・分類

| カラム | 型 | 備考 |
|---|---|---|
| `benefit_id` | STRING | 主キー。元データの `basicInformation.psid` |
| `scheme_id` | STRING | `schemes` への参照 |
| `title` | STRING | 表示用の制度名 |
| `canonical_name` / `short_name` | STRING | 元データの正式名 / 略称 |
| `category` | STRING | カテゴリ（`canonical_name` 由来） |
| `organization_code` / `department` | STRING | 実施主体 / 担当部署 |

### 年齢（**最重要・間違えやすい**）

| カラム | 型 | 備考 |
|---|---|---|
| `min_age_months` / `max_age_months` | INT64 | 元データに**明示されていた**年齢。6割超が NULL |
| `inferred_min_age_months` / `inferred_max_age_months` | INT64 | テキストから推定した年齢 |
| `effective_min_age_months` / `effective_max_age_months` | INT64 | **絞り込みに使うのはこれ**。明示値を優先し、無ければ推定値 |
| `age_source` | STRING | `explicit` / `inferred` / `unknown` |
| `age_inference_rule` | STRING | どのルールで推定したか |
| `is_prenatal` | BOOL | 妊娠期の制度（1,279件）。子の月齢では表せないため別軸 |

> ⚠️ **年齢で絞るときは必ず `effective_*` を使ってください。**
> `min_age_months` だけで絞ると6割超が NULL のため素通りし、
> 「10歳なのに新生児向け制度が出る」状態になります（過去に実際に発生）。

カバー率: `explicit` 2,794件 / `inferred` 2,346件 / `unknown` 2,672件（= 絞り込み可能 66%）

### 地域・分類コード

| カラム | 型 | 備考 |
|---|---|---|
| `area_code` / `area_name` | STRING | 市区町村コード（63自治体）。`130001` は東京都全域 |
| `category_codes` / `target_codes` / `content_codes` | ARRAY\<STRING\> | 都の標準分類コード |
| `*_code_labels` | ARRAY\<STRING\> | 上記の日本語ラベル（**統計的推定**。公式マスタは非公開） |

### 条件・費用

| カラム | 型 | 備考 |
|---|---|---|
| `has_free_text_conditions` | BOOL | 機械判定できない条件が残っている（3,808件） |
| `conditions_text` / `target_persons_text` | STRING | 条件の原文 |
| `is_free` | BOOL | 無料の制度 |
| `cost_text` / `monetary_support_text` / `materially_support_text` | STRING | 費用・助成額。**数値化していない**（書式が制度ごとに違い、誤った金額を断定するリスクがあるため原文保持） |

### 手続き・問い合わせ

`procedure_method` / `procedure_counter` / `electronic_submission`（BOOL、1,179件が可）/
`contact_name` / `contact_phone` / `contact_email` / `contact_address` / `contact_zip` /
`official_url` / `official_title` / `regulation_name` / `update_date`(DATE)

### 本文とリンク

| カラム | 型 | 備考 |
|---|---|---|
| `summary` / `description` / `utilization` | STRING | **リンク除去済み**の読みやすい本文 |
| `summary_raw` / `description_raw` / `utilization_raw` | STRING | 原文 |
| `related_links` / `form_links` / `embedded_links` | ARRAY\<STRUCT\<title, uri\>\> | リンク類 |

> 元データは本文中に `タイトル;https://...` という独自形式でリンクを直接埋め込んでいます
> （平均 4.5 リンク/レコード）。`extract_links()` で全テキスト列から分離しています。
> **テキスト列を追加するときも必ずこの処理を通してください。**

## statuses

| カラム | 型 | 備考 |
|---|---|---|
| `status_id` | STRING | 主キー |
| `name` | STRING | 表示名（例: `2歳〜17歳11か月`、`台東区`、`予防接種（003）`） |
| `type` | STRING | `AGE` / `LOCATION` / `TAG_CATEGORY` / `TAG_TARGET` / `TAG_CONTENT` |
| `min_age_months` / `max_age_months` | INT64 | AGE のとき |
| `code` | STRING | LOCATION / TAG_* のとき |

## documents

| カラム | 型 | 備考 |
|---|---|---|
| `doc_id` | STRING | 主キー |
| `doc_name` | STRING | 表記ゆれを統合した代表名 |
| `original_name` | STRING | 統合前の表記 |
| `is_probable_document` | BOOL | **書類らしいか**。必要書類欄には注意書きの文章も混ざるため、UI ではこれが true のものだけ表示 |
| `doc_url` | STRING | 書類名に紐づく URL |

## schemes

自治体をまたいだ同一制度を束ねたマスタ。「定期予防接種」は62自治体685件、
「児童手当」は61自治体120件に分散していたものを1件として扱えます。

`scheme_id` / `scheme_name` / `municipality_count` / `benefit_count` / `min_age_months` / `max_age_months`

## benefit_leads_to（スキルツリー）

| カラム | 型 | 備考 |
|---|---|---|
| `from_benefit_id` / `to_benefit_id` | STRING | 制度間の関係 |
| `relation` | STRING | `NEXT_STEP`（年齢帯が地続き）/ `SHARED_DOC`（同じ書類が必要） |
| `reason` | STRING | 根拠の説明文 |

生成ルール（`build_benefit_edges()`）:
- `NEXT_STEP`: 同一自治体内で年齢帯が連続する制度をつなぐ（間隔が1年以上空くものは除外）
- `SHARED_DOC`: 特徴的な書類を共有する制度をつなぐ。
  全体の5%超に登場する汎用書類（保険証など）は除外しないとエッジが爆発する

## 更新手順

```bash
make etl      # 取得 → 整形 → ロード（WRITE_TRUNCATE で全置換）
make graph    # PROPERTY GRAPH 再作成（スキーマ変更時は必須）
make verify   # 検証
```
