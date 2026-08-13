"""Claude Code 経由の GCP アクセスを claude-dev サービスアカウントに切り替える。

claude-dev は dev だけ書き込み可、staging と prod は読み取りのみ
（→ docs/adr/0008-scoped-credentials.md）。

自分の gcloud 認証を「起点」にして、そこから claude-dev になりすます設定ファイルを作る。
サービスアカウントキー(JSON)は発行しない。鍵ファイルが存在しなければ流出しようがなく、
自分の認証を失効させれば連鎖して止まるため。

`make auth` から呼ばれる。
"""

import json
import os
import subprocess
import sys

PROJECT = "opendatahackathon-503500"
SA = f"claude-dev@{PROJECT}.iam.gserviceaccount.com"
SOURCE = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
OUT = os.path.expanduser("~/.config/gcloud/claude-dev-adc.json")


def fail(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)


# 実際に dev を読んでみて確かめる。設定ファイルを置くだけでは権限が付いているか分からない。
# なりすましのトークン取得はスコープ指定が必須（無指定だと 400 になる）。
PROBE = f"""
from google.cloud import bigquery
c = bigquery.Client(project="{PROJECT}", location="asia-northeast1")
list(c.query("SELECT COUNT(*) FROM `gov_knowledge_db_dev.benefits`").result())
print("ok")
"""


def probe_adc():
    """OUT の設定で実際に dev を読めるか試す。"""
    return subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env={**os.environ, "GOOGLE_APPLICATION_CREDENTIALS": OUT},
    )


if not os.path.exists(SOURCE):
    # 起点の ADC が無くても、**生成済みの設定が有効なら作り直す必要は無い。**
    # claude-dev-adc.json は refresh token を内包していて起点ファイルを参照しないため、
    # 起点を消しても（あるいは書けない場所にあっても）そのまま使い続けられる。
    # ここで一律に落としていたため、動く認証があるのに `make auth` が通らなかった。
    if os.path.exists(OUT) and probe_adc().returncode == 0:
        print(f"✅ {OUT} は既に有効です（作り直しはしていません）")
        print(f"   make 経由の GCP アクセスは {SA} として動きます。")
        sys.exit(0)
    fail("gcloud の認証がありません。先に実行してください:\n   gcloud auth application-default login")

with open(SOURCE) as f:
    source_creds = json.load(f)

# 既に切り替え済みの状態で再実行されても二重にラップしない
if source_creds.get("type") == "impersonated_service_account":
    source_creds = source_creds["source_credentials"]

config = {
    "type": "impersonated_service_account",
    "service_account_impersonation_url": (
        f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{SA}:generateAccessToken"
    ),
    "delegates": [],
    "source_credentials": source_creds,
}

with open(OUT, "w") as f:
    json.dump(config, f, indent=2)
os.chmod(OUT, 0o600)

probe = probe_adc()

# make auth は make setup より前に実行される手順なので、依存がまだ入っていないことがある。
# その場合は権限の問題ではないので、設定は残したうえで確認だけ諦める。
if "ModuleNotFoundError" in probe.stderr and "google" in probe.stderr:
    print(f"✅ {OUT} を作成しました")
    print("   （依存がまだ入っていないため、実際に使えるかの確認は省略しました）")
    print("   コンテナを起動すれば確認できます: make agent-up")
    sys.exit(0)

if probe.returncode != 0:
    stderr = probe.stderr.strip()
    detail = stderr.splitlines()[-1] if stderr else "(詳細なし)"
    hint = ""
    if "denied" in stderr or "PERMISSION_DENIED" in stderr or "403" in stderr:
        hint = (
            "\n   プロジェクト管理者に、あなたのアカウントへ以下の付与を依頼してください:\n"
            f"   gcloud iam service-accounts add-iam-policy-binding {SA} \\\n"
            f"     --project={PROJECT} \\\n"
            '     --member="user:<あなたのメールアドレス>" \\\n'
            "     --role=roles/iam.serviceAccountTokenCreator"
        )
    os.remove(OUT)  # 使えない設定を残すと以降の make が全部おかしくなる
    fail(f"{SA} として dev を読めませんでした。\n   {detail}{hint}")

print(f"✅ {OUT} を作成しました")
print(f"   これ以降、make 経由の GCP アクセスは {SA} として動きます。")
print("   dev は読み書き可 / staging と prod は読み取りのみです。")
