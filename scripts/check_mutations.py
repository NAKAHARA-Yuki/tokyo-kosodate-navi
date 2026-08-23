"""テストが本当にバグを捕まえるかを、わざとバグを入れて確かめる（issue #64）。

**テストが「通っていること」と「壊れたときに落ちること」は別問題。**
このリポジトリでは後者が担保できていなかった実例がある（`benefit_id` の
二重URLエンコードが E2E を通り抜けて dev にデプロイされた）。

使い方:

    make mutations              # 全部
    python scripts/check_mutations.py --only llm-in-judgement

各変異は「入れたら**落ちるべき**バグ」。結果は docs/test-effectiveness.md に記録している。

| 結果 | 意味 |
|---|---|
| `DETECTED` | 狙いどおり。**落ちたテストが1件以上ある** |
| `MISSED` | テストの穴。バグを入れても落ちない |
| `UNCLEAR` | 落ちたが、変異が原因とは言えない。**検証できていない** |
| `ANCHOR` | 置換が当たっていない。変異の定義が古い |

**入力側と出力側の両方で「空振り」を弾く。**

- 入力: アンカーが1箇所だけ一致することを確認してから置換する。空振りしたまま
  「テストが緑だから検出できていない」と誤読した事故があるため（PR #111 のレビュー）
- 出力: **落ちたテストが1件以上あること**を DETECTED の条件にする。`returncode != 0` だけを
  見ていたときは、収集エラーで赤い環境だと**変異と無関係に DETECTED になっていた**
  （PR #146 のレビュー）。変異前に素の状態が緑であることも先に確かめる

**偽の DETECTED は偽の MISSED より危険。** 穴が塞がったように見えて、実際は開いたままになる。

ETL や GCP には一切触らない（テストは BigQuery をモックしている）。
"""

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 変異の一覧。`expect` は「この変異を入れたときにテストが落ちてほしいか」。
# いまのところ全部 True（落ちてほしい）。
MUTATIONS = [
    {
        "id": "llm-in-judgement",
        "why": "判定経路に LLM を入れてはいけない（CLAUDE.md の最重要原則 / ADR 0001）",
        "file": "app/routers/match.py",
        "old": '    params.append(bigquery.ScalarQueryParameter("limit", "INT64", limit))',
        # 例外を握りつぶす形にするのが要点。素朴に呼ぶと「認証が無くて落ちる」だけで
        # 検出できているように見えてしまう（本番では認証があるので落ちない）。
        "new": (
            "    try:\n"
            "        dependencies._build_genai_client().models.generate_content(\n"
            '            model="gemini-3.5-flash-lite", contents="どれが対象?"\n'
            "        )\n"
            "    except Exception:\n"
            "        pass\n"
            '    params.append(bigquery.ScalarQueryParameter("limit", "INT64", limit))'
        ),
        "tests": ["tests"],
    },
    {
        "id": "ages-filter-raw-columns",
        "why": "きょうだい対応の年齢絞り込みも effective_* を使うこと（/api/benefits/match）",
        "file": "app/queries.py",
        "old": (
            '        "WHERE (effective_min_age_months IS NULL OR effective_min_age_months <= a) "\n'
            '        "AND (effective_max_age_months IS NULL OR effective_max_age_months >= a))"'
        ),
        "new": (
            '        "WHERE (min_age_months IS NULL OR min_age_months <= a) "\n'
            '        "AND (max_age_months IS NULL OR max_age_months >= a))"'
        ),
        "tests": ["tests"],
    },
    {
        "id": "age-filter-raw-columns",
        "why": "単一年齢の絞り込みも effective_* を使うこと（/api/benefits）",
        "file": "app/queries.py",
        "old": (
            '        f"(effective_min_age_months IS NULL OR effective_min_age_months <= @{param_name}) "\n'
            '        f"AND (effective_max_age_months IS NULL OR effective_max_age_months >= @{param_name})"'
        ),
        "new": (
            '        f"(min_age_months IS NULL OR min_age_months <= @{param_name}) "\n'
            '        f"AND (max_age_months IS NULL OR max_age_months >= @{param_name})"'
        ),
        "tests": ["tests"],
    },
    {
        "id": "timeline-raw-columns",
        "why": "ライフステージとの重なり判定も effective_* を使うこと（/api/timeline）",
        "file": "app/routers/timeline.py",
        "old": (
            "                AND b.effective_min_age_months <= s.hi\n"
            "                AND b.effective_max_age_months >= s.lo)"
        ),
        "new": (
            "                AND b.min_age_months <= s.hi\n                AND b.max_age_months >= s.lo)"
        ),
        "tests": ["tests"],
    },
    {
        "id": "subgraph-raw-columns",
        "why": "制度詳細が返す年齢も effective_* であること（/api/subgraph）",
        "file": "app/routers/benefits.py",
        "old": (
            "          b.effective_min_age_months AS min_age_months,\n"
            "          b.effective_max_age_months AS max_age_months,"
        ),
        "new": "          b.min_age_months AS min_age_months,\n          b.max_age_months AS max_age_months,",
        "tests": ["tests"],
    },
    {
        "id": "attributes-filter-out",
        "why": "属性は並べ替えと見出しにだけ使い、該当しない制度を隠さないこと（#118）",
        "file": "app/routers/match.py",
        "old": '    params.append(bigquery.ScalarQueryParameter("sp_code", "STRING", TARGET_CODE_SINGLE_PARENT))',
        "new": (
            "    if is_single_parent:\n"
            '        conditions.append("@sp_code IN UNNEST(target_codes)")\n'
            "        where_clause = f\"WHERE {' AND '.join(conditions)}\"\n"
            '    params.append(bigquery.ScalarQueryParameter("sp_code", "STRING", TARGET_CODE_SINGLE_PARENT))'
        ),
        "tests": ["tests"],
    },
    {
        "id": "no-disclaimer",
        "why": "AI生成である旨の注記を必ず付けること（ADR 0001）",
        "file": "app/routers/support.py",
        "old": (
            '            "disclaimer": "この文章はAIが制度情報をもとに生成したものです。'
            '最終的な判断は自治体の公式情報をご確認ください。",'
        ),
        "new": '            "disclaimer": "",',
        "tests": ["tests"],
    },
    {
        "id": "cache-always-misses",
        "why": "やさしい解説のキャッシュが効いていること（#68 / ADR 0015）",
        "file": "app/explanation_cache.py",
        "old": "def lookup(",
        "new": "def lookup(*_a, **_kw):\n    return None\n\n\ndef _unused_lookup(",
        "tests": ["tests"],
    },
    {
        "id": "income-boundary-always-inclusive",
        "why": "所得のしきい値が境界を含むかを取り違えないこと（#123）",
        "file": "src/income_rules.py",
        "old": '                    max_inclusive=not re.match(rf"\\s*(?:{UPPER_EXCLUSIVE})", near),',
        "new": "                    max_inclusive=True,",
        "tests": ["tests"],
    },
    {
        "id": "document-length-before-strip",
        "why": "書類名の長さは飾りを外してから測ること（#119）",
        "file": "src/etl_documents.py",
        "old": "    if len(text) > 40:",
        "new": "    if len(raw) > 40:",
        "tests": ["tests"],
    },
    {
        "id": "snapshot-after-load",
        "why": "退避はロードの前でなければ意味が無い（#160）。後ろに回ると「壊した後」を撮る",
        "file": "src/etl_to_bq.py",
        "old": "    snapshot_tables(client, project_id, tables.keys())\n\n    load_tables(client, project_id, tables)",
        "new": "    load_tables(client, project_id, tables)\n\n    snapshot_tables(client, project_id, tables.keys())",
        "tests": ["tests"],
    },
    {
        "id": "load-appends-instead-of-truncating",
        "why": "ロードは全置換であること（#152）。追記になると実行のたびに件数が倍々に増える",
        "file": "src/etl_load.py",
        "old": "            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,",
        "new": "            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,",
        "tests": ["tests"],
    },
    {
        "id": "graph-primary-key-not-reentrant",
        "why": "PROPERTY GRAPH の PRIMARY KEY は DROP を先に挟むこと（#152）。2回目が Already Exists で落ちる",
        "file": "src/create_graph.sql",
        "old": "ALTER TABLE `{{PROJECT_ID}}.{{DATASET}}.benefits`\n  DROP PRIMARY KEY IF EXISTS;",
        "new": "-- 変異: benefits の DROP PRIMARY KEY を落とす",
        "tests": ["tests"],
    },
    {
        "id": "benefits-schema-drops-a-column",
        "why": "スキーマと transform() の出力がずれたら落ちること（#152）",
        "file": "src/etl_schema.py",
        "old": '    bigquery.SchemaField("effective_max_age_months", "INT64"),',
        "new": '    bigquery.SchemaField("effective_max_age_months", "STRING"),',
        "tests": ["tests"],
    },
    {
        "id": "disability-limit-creates-new-ceiling",
        "why": "上限が無い制度に新しい上限を付けないこと（#157 / PR #169 のレビュー）。障害を申告した人にだけ制度が消える",
        "file": "src/etl_graph.py",
        "old": "    if disability_max is not None and (effective_max is None or disability_max <= effective_max):",
        "new": "    if disability_max is not None and effective_max is not None and disability_max <= effective_max:",
        "tests": ["tests"],
    },
    {
        "id": "disability-years-unbounded",
        "why": "拾う年数を19〜25に絞ること（PR #169 のレビュー）。書類の注記の「3歳未満」まで上限になる",
        "file": "src/age_rules.py",
        "old": "    if years is None or not (19 <= years <= 25):",
        "new": "    if years is None:",
        "tests": ["tests"],
    },
    {
        "id": "disability-limit-ignored",
        "why": "障害があると答えた人に広い上限を使うこと（#157）。無視すると18〜19歳で対象から外れる",
        "file": "app/queries.py",
        "old": '        return "IFNULL(disability_max_age_months, effective_max_age_months)"',
        "new": '        return "effective_max_age_months"',
        "tests": ["tests"],
    },
    {
        "id": "disability-limit-always-on",
        "why": "障害と答えていない人にまで広げないこと（#157）。対象外の人に出る",
        "file": "app/queries.py",
        "old": "    if has_disability:",
        "new": "    if True:",
        "tests": ["tests"],
    },
    {
        "id": "cache-failure-is-silent",
        "why": "キャッシュの保存失敗を黙って捨てないこと（#164）。壊れても画面は動くので気づけない",
        "file": "app/explanation_cache.py",
        "old": '        errors = client.insert_rows_json(table_id(), [row])\n        if errors:\n            _warn(f"保存に失敗した行がある: {errors}")',
        "new": "        client.insert_rows_json(table_id(), [row])",
        "tests": ["tests"],
    },
    {
        "id": "shared-doc-from-non-documents",
        "why": "書類でないもの（見出し・表の区切り行）でエッジを張らないこと（#120）",
        "file": "src/etl_graph.py",
        "old": '            if documents[doc_id]["is_probable_document"]:\n                benefit_docs.setdefault(benefit_id, set()).add(doc_id)',
        "new": "            benefit_docs.setdefault(benefit_id, set()).add(doc_id)",
        "tests": ["tests"],
    },
    {
        "id": "heading-line-is-a-document",
        "why": "欄の見出し（「必要書類」「持ち物」）を書類として扱わないこと（#120）",
        "file": "src/etl_documents.py",
        "old": "    if is_heading_line(text):\n        return False",
        "new": "    if False:\n        return False",
        "tests": ["tests"],
    },
    {
        "id": "table-row-not-split",
        "why": "書類が並ぶ表はセルに割ること（#120）。割らないと4つの書類が1ノードに潰れる",
        "file": "src/etl_documents.py",
        "old": "    if not any(DOC_SUFFIX_RE.search(cell) for cell in cells):\n        return [line]\n    return cells",
        "new": "    return [line]",
        "tests": ["tests"],
    },
    {
        "id": "stub-ignores-search-params",
        "why": "E2E のスタブが検索条件を無視して常に成功を返さないこと（#110）",
        "file": "e2e/fake_data.py",
        "old": '    if (area_code := params.get("area_code")) is not None:\n        rows = [r for r in rows if r.get("area_code") == area_code]',
        "new": "    # 変異: area_code を無視する",
        "tests": ["e2e"],
    },
]


def _run_pytest(selection: list[str]):
    return subprocess.run(
        [sys.executable, "-m", "pytest", *selection, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
    )


def _failed_tests(proc) -> list[str]:
    return [ln.split(" ")[1] for ln in (proc.stdout + proc.stderr).splitlines() if ln.startswith("FAILED")]


_baselines: dict[tuple[str, ...], tuple[bool, str]] = {}


def baseline_is_green(selection: list[str]) -> tuple[bool, str]:
    """**変異を当てる前に、素の状態で緑であることを確かめる。**

    赤い状態から始めると、変異と無関係な失敗で `returncode != 0` になり、
    **検出できていないのに DETECTED と報告してしまう**（PR #146 のレビューで指摘）。
    アンカーの一致数を確認しているのと同じことを、出力側でもやる。

    同じ選択（`tests` / `e2e`）は1回だけ走らせて使い回す。
    """
    key = tuple(selection)
    if key not in _baselines:
        proc = _run_pytest(selection)
        if proc.returncode == 0:
            _baselines[key] = (True, "")
        else:
            failed = _failed_tests(proc)
            reason = f"{len(failed)}件が失敗" if failed else f"収集エラー等（returncode={proc.returncode}）"
            _baselines[key] = (False, reason)
    return _baselines[key]


def apply_and_run(mut: dict) -> dict:
    """変異を当ててテストを走らせ、必ず元に戻す。"""
    path = ROOT / mut["file"]
    original = path.read_text(encoding="utf-8")

    hits = original.count(mut["old"])
    if hits != 1:
        # **空振りを黙って通さない。** 0件なら当たっていないし、
        # 2件以上なら意図しない場所も書き換わる。
        return {"id": mut["id"], "status": "ANCHOR", "detail": f"一致 {hits} 箇所（1であること）"}

    green, why = baseline_is_green(mut["tests"])
    if not green:
        return {
            "id": mut["id"],
            "status": "UNCLEAR",
            "detail": f"変異前から赤いので検証できない（{' '.join(mut['tests'])}: {why}）",
        }

    try:
        path.write_text(original.replace(mut["old"], mut["new"]), encoding="utf-8")
        proc = _run_pytest(mut["tests"])
    finally:
        path.write_text(original, encoding="utf-8")

    failed = _failed_tests(proc)
    if failed:
        return {"id": mut["id"], "status": "DETECTED", "detail": f"{len(failed)}件が失敗", "failed": failed}
    if proc.returncode != 0:
        # **落ちたが、変異が原因とは言えない。** 「落ちた理由まで見る」を判定側にも効かせる。
        return {
            "id": mut["id"],
            "status": "UNCLEAR",
            "detail": f"落ちたテストが無いのに returncode={proc.returncode}",
        }
    return {"id": mut["id"], "status": "MISSED", "detail": "落ちたテストなし"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", help="変異IDを指定して1つだけ実行する")
    args = parser.parse_args()

    targets = [m for m in MUTATIONS if not args.only or m["id"] in args.only]
    if not targets:
        print(f"該当する変異がありません。指定できるID: {', '.join(m['id'] for m in MUTATIONS)}")
        return 2

    results = []
    for mut in targets:
        print(f"── {mut['id']}: {mut['why']}")
        result = apply_and_run(mut)
        results.append(result)
        print(f"   {result['status']}  {result['detail']}")
        for name in result.get("failed", [])[:3]:
            print(f"     {name}")

    bad = [r for r in results if r["status"] != "DETECTED"]
    print(f"\n検出 {len(results) - len(bad)} / {len(results)}")
    if bad:
        # **UNCLEAR も赤で報告する。** 「検証できていない」を緑にすると、
        # 穴が塞がったように見えて実際は開いたままになる。
        print("**検証できていない変異があります。**")
        print("  MISSED  = テストの穴。バグを入れても落ちない")
        print("  UNCLEAR = 落ちた理由が変異だと言えない。まず素の状態を緑にすること")
        print("  ANCHOR  = 置換が当たっていない。変異の定義が古い")
        for r in bad:
            print(f"  {r['status']:8} {r['id']}  {r['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
