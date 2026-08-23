# テストの有効性（何を守れていて、何を守れていないか）

issue #64。**テストが「通っていること」と「壊れたときに落ちること」は別問題**なので、
後者を確かめた結果を残す。

このリポジトリには、テストが緑のままバグが本番に出た実例がある。`benefit_id` の
二重URLエンコード（`+` が `%252B` になり backend が 404 を返す）が E2E をすり抜けた。
原因は E2E のスタブが `benefit_id` を見ずにどんなIDでも行を返していたことで、
詳細ページは常に 200 を返していた。**テストは何も検証していなかったが、緑だった。**

再現方法:

```bash
make mutations      # わざとバグを入れて、落ちるべきテストが落ちるかを見る
make cov            # 一度も実行されていない経路を探す
```

## 1. 変異検証（バグを入れたら落ちるか）

`scripts/check_mutations.py` が、意図的なバグを1つずつ当ててテストを走らせ、
必ず元に戻す。**11件すべてで、狙ったテストが落ちる。**

| 入れたバグ | 落ちるテスト |
|---|---|
| `match_benefits` の中で Gemini を呼ぶ | `TestJudgementPathDoesNotUseLLM::test_no_gemini_client_is_built` |
| `/api/benefits/match` の年齢を素の `min/max_age_months` にする | `TestMatchBenefits::test_age_filter_uses_effective_columns` |
| `/api/benefits` の年齢を素の列にする | `TestSearchBenefits::test_age_filter_uses_effective_columns` |
| `/api/timeline` の年齢を素の列にする | `TestTimeline::test_age_range_uses_effective_columns` |
| `/api/subgraph` の年齢を素の列にする | `TestSubgraph::test_uses_effective_age_columns` |
| 属性で WHERE を足して該当しない制度を隠す | `TestMatchBenefits::test_attributes_do_not_filter_out_benefits` |
| AI生成の disclaimer を空にする | `TestDraftReview::test_prompt_forbids_inventing_facts` ほか3件 |
| 解説キャッシュが常に外れるようにする | `TestCacheBehaviour::test_second_call_does_not_call_gemini` ほか5件 |
| 所得のしきい値を常に「以下」扱いにする | `TestThreshold::test_境界を含むかどうかを持つ` |
| 書類名の長さを飾りを外す前に測る | `test_length_is_checked_after_stripping_decorations` |
| E2E スタブが `area_code` を無視する | `TestAttributeFilter::test_area_filter_narrows_to_that_area` ほか1件 |
| 退避をロードの**後**に回す | `TestSnapshotHappensBeforeLoad::test_order_is_quality_then_snapshot_then_load` |
| ロードを追記（`WRITE_APPEND`）にする | `TestLoadTables::test_every_load_is_write_truncate` |
| PROPERTY GRAPH の `DROP PRIMARY KEY` を落とす | `TestCreateGraphSql::test_every_add_is_preceded_by_a_drop` |
| `benefits` スキーマの年齢列を STRING にする | `TestSchemaMatchesTransform::test_age_columns_are_integers` |

### 検証で見つかった穴（このPRで塞いだ）

**最初に走らせた時点では、上のうち3件が MISSED だった。**
どれも CLAUDE.md が繰り返し書いている原則そのもので、**規約とレビューだけで守られていた。**

| 穴 | なぜ気づけなかったか |
|---|---|
| **判定経路に LLM を入れても落ちない** | この原則を見るテストが1件も無かった |
| `/api/benefits/match` の年齢を素の列にしても落ちない | `/api/benefits` 側にはテストがあり、**守られているように見えていた** |
| `/api/timeline` の年齢を素の列にしても落ちない | 同上。timeline は NULL の扱いが逆で `queries.py` に共通化されていない |

年齢の件は、**4箇所のうち2箇所だけが守られていた**という形だった。
`queries.py` に共通化されている `age_filter_sql`（`/api/benefits`）にはテストがあり、
同じファイルの `ages_filter_sql`（`/api/benefits/match`）には無かった。
**同じファイルに並んでいるので、片方を見て両方守られていると思い込みやすい。**

### 変異を書くときの注意

**「落ちた」だけでは検証にならない。落ちた理由を見ること。**

判定経路に Gemini を入れる変異は、素朴に1行足すと 18件のテストが落ちる。
だがそれは**認証が無くてクライアント生成に失敗している**だけで、
本番には認証があるので落ちない。例外を握りつぶす形（＝本番では動く形）にすると、
**288件すべてが緑のままだった。**

**入力側と出力側の両方で「空振り」を弾く必要がある。**

| どちら | やること | なぜ |
|---|---|---|
| 入力 | アンカーが**1箇所だけ**一致することを確認してから置換する | 置換が空振りしたまま「緑だから検出できていない」と誤読した（PR #111 のレビュー） |
| 出力 | **落ちたテストが1件以上あること**を DETECTED の条件にする | `returncode != 0` だけを見ていたら、収集エラーで赤い環境で**変異と無関係に DETECTED**になっていた（PR #146 のレビュー） |

変異を当てる前に、素の状態が緑であることも確かめる。赤いところから始めると
何を測っても意味がないので、`UNCLEAR`（検証できていない）として**赤で報告する**。

**偽の DETECTED は偽の MISSED より危険。** 穴が塞がったように見えて、実際は開いたままになる。

### 変異検証をやって初めて分かったこと: E2E がプロセスを漏らしていた

変異を全件回そうとしたら、**それまで2分だった E2E が30分経っても終わらなくなった。**
原因はテストの中身ではなく、`e2e/conftest.py` が frontend を落としきれていないことだった。

```
npm start ─┬─ (npm 自身)          ← _terminate はこちらしか止めていない
           └─ next-server         ← 生き残って init に引き取られる（ppid=1）
```

**E2E を1回回すたびに `next-server` が1つ残る。** 15日で 47個・約2.5GB を占有し、
swap を食い潰して `npm run build` が終わらなくなっていた。

**テストは緑のままで、遅くなる以外に兆候が出ない。**
「テストが通っている」だけでは分からないことの一例として残しておく。
`start_new_session=True` で起動してプロセスグループごと落とすように直した。
直した後は、E2E 76件を回しても残るプロセスは 0 になる。

## 2. 一度も実行されていない経路（`make cov`）

**カバレッジは数値目標に使わない。**「一度も実行されていない経路」を見つける道具として使う。
閾値での失敗も設定していない。

`pytest tests` での実測（`app/` と `src/` 合計 80%）のうち、**0% のもの**:

| モジュール | 中身 | 判断 |
|---|---|---|
| `src/etl_to_bq.py` | ETL のエントリポイント | 退避→ロードの順序を `test_etl_snapshot.py` で固定（#160）。<br>取得・整形の全体経路は引き続き未実行 |
| `src/etl_load.py` | データセット作成・テーブルロード | **`test_etl_load_path.py` で塞いだ**（#152） |
| `src/etl_schema.py` | テーブルスキーマ定義 | 同上 |
| `src/create_graph.py` | PROPERTY GRAPH の作成 | 同上 |
| `src/verify_graph.py` | グラフの検証クエリ | 実行して目視する道具 |

**書き込み側（`WRITE_TRUNCATE` で本番データセットを上書きする経路）に、テストが1本も無い。**
`transform()` までは `tests/test_etl_transform.py` が厚く見ているが、
**そこから先は誰も見ていない。** ここが壊れると本番データが壊れるので、影響は判定経路より大きい。

いま塞いでいないのは、GCP クライアントをモックする土台がこの層に無く、
このPRの範囲（#64 の完了条件は「未実行の経路が把握できている」）を超えるため。
**別 issue にする。**

その次に薄いのは `src/etl_statuses.py`（62%）で、未実行なのは
`_age_label()` の「下限だけ」「上限だけ」の分岐など、
**年齢が片側しか無い制度の表示ラベル**にあたる部分。

### 0% でも問題にしていないもの

- `app/dependencies.py`（50%）— 未実行なのは実際に GCP クライアントを作る行だけ。
  テストは常に差し替えるので、ここが通ることはない（通ってはいけない）

## 3. まだ守られていないもの

- **ETL の書き込み経路**（上記）。別 issue
- `frontend` は `lib/backend.ts` のみ（#111）。画面側のコンポーネントは E2E 経由だけ
- 変異検証は手で走らせる。CI には入れていない（E2E を含むため1回2分ほどかかる）
