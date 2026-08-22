# データを戻す（ETL で壊したとき）

ETL は `WRITE_TRUNCATE` で **8テーブルを全置換**する。取り違えや元データ側の破損で
おかしくなったとき、ここに書いた手順で戻す。

**先に確認すること。** どの環境が、いつから、どうおかしいのか。
`/api/healthz` が `{"env": ..., "dataset": ...}` を返すので、見ている先はそこで分かる。

---

## 手段は2つある

| | 戻せる範囲 | 誰が実行できるか |
|---|---|---|
| **A. スナップショット**（推奨） | **30日** | dev は `claude-dev` でも可。staging / prod は owner か editor |
| B. タイムトラベル | **7日**（168時間） | 同上 |

**A を使う。** B は「スナップショットを撮る前の状態に戻したい」ときだけ。

ETL はロードの直前に、そのとき入っていた8テーブルを
`snap_<テーブル名>_<UTCの時刻>` という名前で退避している（issue #160）。
**壊れたロードの直前の状態が、必ず1つ残っている。**

---

## A. スナップショットから戻す

### 1. どの時点が残っているかを見る

```sql
SELECT table_name, creation_time
FROM `opendatahackathon-503500.gov_knowledge_db.INFORMATION_SCHEMA.TABLES`
WHERE table_name LIKE 'snap_%'
ORDER BY creation_time DESC;
```

`snap_benefits_20260822T081500Z` のように、**8テーブルが同じ時刻の接尾辞**で並んでいる。
戻すときは**接尾辞を揃える**。揃えないと「benefits だけ新しい」状態になる。

### 2. 戻す

データセットと接尾辞を書き換えて実行する。

```sql
DECLARE suffix STRING DEFAULT '20260822T081500Z';   -- ← 戻したい時点

CREATE OR REPLACE TABLE `opendatahackathon-503500.gov_knowledge_db.benefits`
  CLONE `opendatahackathon-503500.gov_knowledge_db.snap_benefits_20260822T081500Z`;
```

8テーブルすべてに対して繰り返す。

```
benefits / schemes / statuses / documents
benefit_requires_status / benefit_requires_doc / benefit_in_scheme / benefit_leads_to
```

### 3. PROPERTY GRAPH を作り直す

**テーブルを戻しただけでは終わらない。** グラフは列を参照している。

```bash
APP_ENV=prod python src/create_graph.py
APP_ENV=prod python src/verify_graph.py
```

### 4. 確かめる

```bash
curl -sS https://<フロントのURL>/api/healthz
curl -sS https://<フロントのURL>/api/areas > /dev/null
```

件数も見る。

```sql
SELECT COUNT(*) FROM `opendatahackathon-503500.gov_knowledge_db.benefits`;  -- 7,812 前後
```

---

## B. タイムトラベルから戻す（7日以内）

スナップショットが無い時点（この仕組みを入れる前、または30日より古い）に戻すとき。

```sql
CREATE OR REPLACE TABLE `opendatahackathon-503500.gov_knowledge_db.benefits` AS
SELECT * FROM `opendatahackathon-503500.gov_knowledge_db.benefits`
  FOR SYSTEM_TIME AS OF TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE);
```

**`INTERVAL` は「壊す前」を指すように調整する。** 8テーブル分繰り返し、
そのあと PROPERTY GRAPH を作り直す（Aの3と同じ）。

`max_time_travel_hours` は 168（7日）。**それより古い時点には戻せない。**

---

## 実際に試した記録（dev、2026-08-22）

手順が机上の空論でないことを確かめてある。

```
1. snapshot_tables() で8テーブルを退避     snap_*_20260822T143404Z
2. benefits を 7,812 → 3,000 件に壊す      （元データ破損の再現）
3. スナップショットから CLONE で戻す
4. 7,812件 / 63自治体 に復帰。
   age_source='inferred' 2,406 件も一致（件数だけでなく中身も戻っている）
```

---

## 気をつけること

- **スナップショットは30日で自動的に消える。** `expiration_timestamp` を付けているため。
  長く残したいものがあれば、期限の無いテーブルへコピーしておく
- **`make cleanup` は `snap_` を消さない。** 正規8テーブル以外を消す掃除だが、
  退避だけは除外している（消すと、この手順書が成り立たなくなる）
- **戻したあとに ETL を回すと、また上書きされる。** 原因（元データ側か、こちらの
  変換か）を特定するまで回さない
- staging / prod のテーブルを書き換えられるのは **owner か `roles/editor` のメンバー**。
  `claude-dev` は dev しか触れない
