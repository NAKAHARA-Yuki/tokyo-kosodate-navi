## 何を変えたか

<!-- 変更の概要を簡潔に -->

## なぜ必要か

<!-- 解決したい課題・背景。Issue があれば #123 のようにリンク -->

## どう確認したか

<!-- 例: make test / ローカルで台東区×3歳を検索して確認 / スクリーンショット -->

## スクリーンショット

<!-- UI を変更した場合は必須。Before / After があると助かります -->

---

## チェックリスト

- [ ] `make check`（lint + test）が通る
- [ ] 判定ロジック（`/api/benefits`, `/api/benefits/match`, `/api/timeline`）に **LLM を混ぜていない**
- [ ] 年齢の絞り込みは `effective_min_age_months` / `effective_max_age_months` を使っている
- [ ] 推定値（`age_source='inferred'`）をユーザーに断定的に見せていない
- [ ] 「なぜそうしたか」がコード内コメントかコミットメッセージに残っている

### 該当する場合のみ

- [ ] データモデルを変更した → `docs/data-model.md` と `CLAUDE.md` を更新した
- [ ] スキーマを変更した → `make graph` を実行した（PROPERTY GRAPH の再作成が必要）
- [ ] 非自明な設計判断をした → `docs/adr/` に追加した
- [ ] 本番の BigQuery を更新した（`make etl`）→ 件数の変化を下に記載

<!--
本番データを更新した場合は件数を記載してください:
benefits=____ / statuses=____ / documents=____ / benefit_leads_to=____
-->
