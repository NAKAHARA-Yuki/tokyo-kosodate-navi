#!/usr/bin/env bash
# コンテナ作成後に1度だけ走る。何が足りていないかを最初に伝えるのが目的で、
# 勝手に認証を進めたりはしない（ブラウザ操作が要るため）。
set -uo pipefail

echo
echo "──────────────────────────────────────────────"
echo " tokyo-kosodate-navi devcontainer"
echo "──────────────────────────────────────────────"
python -V
echo "依存: イメージに焼き込み済み（make setup は不要）"

# docker ソケットはマウントしてあるが、ホスト側の group id と合わないと権限で弾かれる。
# make lock でしか使わないので、失敗しても致命的ではない。
if docker info >/dev/null 2>&1; then
  echo "docker: 利用可（make lock が使えます）"
else
  echo "docker: 利用不可（make lock のみ影響。sudo chmod 666 /var/run/docker.sock で回避可）"
fi

echo
if [ -f "$HOME/.config/gcloud/claude-dev-adc.json" ]; then
  echo "✅ GCP 認証は設定済みです。'make check' から始められます。"
else
  echo "▶ 残りの手順（初回のみ）:"
  echo
  echo "   1. gcloud auth application-default login"
  echo "   2. make auth      # claude-dev に切り替え（dev のみ書き込み可）"
  echo "   3. make check     # lint + テスト + E2E"
  echo
  echo "   2 で権限エラーが出たら、管理者に serviceAccountTokenCreator の付与を依頼してください。"
fi
echo
