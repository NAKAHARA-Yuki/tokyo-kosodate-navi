#!/usr/bin/env bash
# コンテナの外向き通信を必要な宛先だけに絞る。
#
# なぜ要るか:
#   Claude Code の --dangerously-skip-permissions は「インターネットの無い
#   サンドボックス向け」とされている。承認なしで何でも実行できる状態では、
#   プロンプトインジェクションでデータを外部に送られても止められないため。
#   このプロジェクトは GCP と GitHub に繋がる必要があるので完全遮断はできない。
#   そこで宛先をホワイトリストにして、それ以外への送信を落とす。
#
# 限界:
#   コンテナ内の利用者は sudo を持つので、その気になれば iptables を消せる。
#   これは事故と自動化の暴走を防ぐ層であって、意図的な回避を防ぐものではない。
set -euo pipefail

log() { echo "  $*"; }

# ざっくり作り直せるよう毎回まっさらにする（コンテナ再起動のたびに走る）
iptables -F OUTPUT 2>/dev/null || true
ipset destroy allowed 2>/dev/null || true
ipset create allowed hash:net

add_cidrs_from_json() {
  local url="$1" jq_expr="$2" label="$3" count=0
  local json
  json=$(curl -fsSL --max-time 20 "$url" 2>/dev/null) || { log "$label: 取得失敗（スキップ）"; return 0; }
  while read -r cidr; do
    [ -n "$cidr" ] || continue
    ipset add allowed "$cidr" 2>/dev/null && count=$((count + 1))
  done < <(echo "$json" | python3 -c "$jq_expr")
  log "$label: $count 件"
}

# Google（BigQuery / Cloud Run / Vertex AI / Artifact Registry）
add_cidrs_from_json "https://www.gstatic.com/ipranges/goog.json" \
  'import json,sys
for p in json.load(sys.stdin)["prefixes"]:
    if "ipv4Prefix" in p: print(p["ipv4Prefix"])' \
  "Google"

# GitHub（git push / API / Actions の確認）
add_cidrs_from_json "https://api.github.com/meta" \
  'import json,sys
d=json.load(sys.stdin)
seen=set()
for k in ("git","api","web","packages"):
    for c in d.get(k,[]):
        if ":" not in c and c not in seen:
            seen.add(c); print(c)' \
  "GitHub"

# ホスト名でしか分からないもの。起動時に解決して入れる。
# IP が変わることがあるので、通信できなくなったらコンテナを作り直す。
for host in pypi.org files.pythonhosted.org api.anthropic.com registry.npmjs.org statsig.anthropic.com sentry.io; do
  ips=$(getent ahostsv4 "$host" 2>/dev/null | awk '{print $1}' | sort -u) || true
  for ip in $ips; do ipset add allowed "$ip/32" 2>/dev/null || true; done
  [ -n "$ips" ] && log "$host: $(echo "$ips" | wc -l) 件" || log "$host: 解決できず"
done

# DNS はコンテナのリゾルバに向くので許可しないと何も引けない
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
# devcontainer と VS Code の通信やホスト側との疎通のためプライベート帯は許可
for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
  iptables -A OUTPUT -d "$net" -j ACCEPT
done
iptables -A OUTPUT -m set --match-set allowed dst -j ACCEPT
iptables -P OUTPUT DROP

echo "✅ 外向き通信を許可リストに制限しました"
