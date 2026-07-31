#!/usr/bin/env bash
# 新しいメンバーが claude-dev を使えるようにする（管理者が1度だけ実行）。
#
# 使い方: ./scripts/grant_member.sh someone@example.com
#
# メンバー本人の GCP 権限は変えない。付けるのは「claude-dev になりすます権限」だけで、
# これによりツール経由のアクセスが dev 限定になる（→ docs/adr/0008）。
#
# メールアドレスをリポジトリに書かないよう、引数で受け取る形にしている
# （public リポジトリなので個人情報を置かない）。
set -euo pipefail

PROJECT=opendatahackathon-503500
SA="claude-dev@${PROJECT}.iam.gserviceaccount.com"

if [ $# -ne 1 ]; then
  echo "使い方: $0 <メンバーのメールアドレス>" >&2
  exit 1
fi
MEMBER="$1"

gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --project="$PROJECT" \
  --member="user:${MEMBER}" \
  --role=roles/iam.serviceAccountTokenCreator \
  --quiet > /dev/null

echo "✅ ${MEMBER} が claude-dev を使えるようになりました"
echo
echo "本人に伝えること:"
echo "  1. gcloud auth application-default login"
echo "  2. make auth"
echo "  3. VS Code / Cursor で「Reopen in Container」"
echo
echo "詳細: docs/onboarding.md"
