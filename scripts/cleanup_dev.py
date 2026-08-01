"""dev 環境に溜まった作業の跡を片付ける。

動作確認のたびに Cloud Run のリビジョンが増え、BigQuery には検証用のテーブルが残る。
放っておくと「どれが今の状態か」が分からなくなり、費用も少しずつ増える。

消すのは dev だけ。staging と prod は権限が無いので、間違えても消せない。

使い方: make cleanup
"""

import json
import subprocess
import sys

PROJECT = "opendatahackathon-503500"
REGION = "asia-northeast1"
SERVICE = "kosodate-graph-viewer-dev"
DATASET = "gov_knowledge_db_dev"

# ETL が作る正規のテーブル。これ以外は検証用とみなして消す。
CANONICAL_TABLES = {
    "benefits",
    "schemes",
    "statuses",
    "documents",
    "benefit_requires_status",
    "benefit_requires_doc",
    "benefit_in_scheme",
    "benefit_leads_to",
}


def run(args: list[str], check: bool = True) -> str:
    res = subprocess.run(args, capture_output=True, text=True)
    if check and res.returncode != 0:
        print(f"  失敗: {' '.join(args[:4])}...\n  {res.stderr.strip()[:200]}", file=sys.stderr)
    return res.stdout


def clean_revisions() -> None:
    """使われていない古いリビジョンを消す。トラフィックのあるものは残す。"""
    print(f"▶ Cloud Run のリビジョン ({SERVICE})")
    out = run(
        [
            "gcloud",
            "run",
            "revisions",
            "list",
            "--service",
            SERVICE,
            "--project",
            PROJECT,
            "--region",
            REGION,
            "--format",
            "json",
        ]
    )
    revisions = json.loads(out) if out.strip() else []
    if not revisions:
        print("  リビジョンなし")
        return

    # 作成が新しい順。先頭（現行）は必ず残す。
    revisions.sort(key=lambda r: r["metadata"]["creationTimestamp"], reverse=True)
    keep, drop = revisions[0], revisions[1:]
    print(f"  残す: {keep['metadata']['name']}")

    if not drop:
        print("  消すものはありません")
        return
    for rev in drop:
        name = rev["metadata"]["name"]
        run(
            [
                "gcloud",
                "run",
                "revisions",
                "delete",
                name,
                "--project",
                PROJECT,
                "--region",
                REGION,
                "--quiet",
            ],
            check=False,
        )
        print(f"  削除: {name}")


def clean_tables() -> None:
    """ETL が作る正規のテーブル以外を消す。"""
    print(f"▶ BigQuery の検証用テーブル ({DATASET})")
    out = run(["bq", "ls", "--project_id", PROJECT, "--max_results", "1000", "--format", "json", DATASET])
    tables = json.loads(out) if out.strip() else []
    extras = [
        t["tableReference"]["tableId"]
        for t in tables
        if t["tableReference"]["tableId"] not in CANONICAL_TABLES
    ]
    if not extras:
        print("  消すものはありません")
        return
    for name in extras:
        run(["bq", "rm", "-f", "-t", f"{PROJECT}:{DATASET}.{name}"], check=False)
        print(f"  削除: {name}")


clean_revisions()
clean_tables()

print()
print("✅ dev の後片付けが完了しました")
print()
print("   Artifact Registry のイメージは claude-dev の権限では消せません")
print("   （staging / prod のイメージと同じリポジトリにあり、消すと切り戻せなくなるため）。")
print("   溜まってきたら管理者が整理してください:")
print(f"     gcloud artifacts docker images list {REGION}-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy")
