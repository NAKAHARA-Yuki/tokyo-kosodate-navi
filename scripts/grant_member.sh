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
echo "─────────────────────────────────────────────"
echo " サーバー管理者の作業（まだの場合）"
echo "─────────────────────────────────────────────"
echo "  sudo adduser <ユーザー名>              # Linux ユーザーを作る"
echo "  sudo usermod -aG docker <ユーザー名>   # 無いと make agent-up が権限エラー"
echo "  （追加後、本人が一度ログインし直す必要があります）"
echo
echo "─────────────────────────────────────────────"
echo " 本人に伝えること"
echo "─────────────────────────────────────────────"
echo "  0. ホストに git / tmux / Docker / gcloud / Claude Code を入れる"
echo "       docs/onboarding.md「ホスト側に入れるもの」に手順があります"
echo "  1. GitHub の PAT を作って ~/.git-credentials に置く"
echo "       スコープは repo と workflow の2つ（workflow を忘れると push が拒否されます）"
echo "  2. gcloud auth application-default login"
echo "  3. make auth      # GCP アクセスを dev だけに絞る"
echo "  4. claude         # Claude Code にログイン"
echo "  5. make agent-up  # コンテナを起動"
echo
echo "  以降は make next を打てば、その時々の次の一手が出ます。"
echo
echo "詳細: docs/onboarding.md"
