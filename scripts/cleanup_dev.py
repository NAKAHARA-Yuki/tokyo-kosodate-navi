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


def try_run(args: list[str]) -> tuple[bool, str]:
    """成否と最初のエラー行を返す。成功したと嘘をつかないために使う。"""
    res = subprocess.run(args, capture_output=True, text=True)
    if res.returncode == 0:
        return True, ""
    lines = [ln for ln in res.stderr.strip().splitlines() if ln.strip()]
    return False, (lines[-1] if lines else "原因不明")


def clean_revisions() -> None:
    """誰も使っていないリビジョンだけを消す。

    「最新以外を消す」ではいけない。dev の Cloud Run はチームで1つを共有しており、
    各自が自分のタグ付きリビジョンを持っている（make deploy ENV=dev）。
    最新以外を消すと他の人の確認用 URL を壊す。

    残すのは次の2種類:
      - トラフィックが流れているもの（消そうとしても Cloud Run に拒否される）
      - タグが付いているもの（誰かの確認用 URL）
    """
    print(f"▶ Cloud Run のリビジョン ({SERVICE})")

    out = run(
        [
            "gcloud",
            "run",
            "services",
            "describe",
            SERVICE,
            "--project",
            PROJECT,
            "--region",
            REGION,
            "--format",
            "json",
        ]
    )
    if not out.strip():
        print("  サービスが見つかりません")
        return
    traffic = json.loads(out).get("status", {}).get("traffic", [])

    protected: dict[str, str] = {}
    for t in traffic:
        name = t.get("revisionName")
        if not name:
            continue
        if t.get("percent", 0) > 0:
            protected[name] = f"トラフィック {t['percent']}%"
        elif t.get("tag"):
            protected[name] = f"タグ {t['tag']}"

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

    for name, why in protected.items():
        print(f"  残す: {name} ({why})")

    targets = [r["metadata"]["name"] for r in revisions if r["metadata"]["name"] not in protected]
    if not targets:
        print("  消すものはありません")
        return

    for name in targets:
        ok, err = try_run(
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
            ]
        )
        print(f"  削除: {name}" if ok else f"  削除できず: {name} ({err[:120]})")


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
        ok, err = try_run(["bq", "rm", "-f", "-t", f"{PROJECT}:{DATASET}.{name}"])
        print(f"  削除: {name}" if ok else f"  削除できず: {name} ({err[:120]})")


clean_revisions()
clean_tables()

print()
print("✅ dev の後片付けが完了しました")
print()
print("   Artifact Registry のイメージは claude-dev の権限では消せません")
print("   （staging / prod のイメージと同じリポジトリにあり、消すと切り戻せなくなるため）。")
print("   溜まってきたら管理者が整理してください:")
print(f"     gcloud artifacts docker images list {REGION}-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy")
