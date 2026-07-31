# Claude Code を常駐させて開発作業をさせるためのコンテナ。
#
# 本番イメージと同じ Python にそろえる。ホストの Python がこれとズレていると、
# make lock で本番に入らないロックができるなど「手元では動くのに本番で落ちる」問題が起きる
# （実際にホストが 3.14、本番が 3.12 でズレていた）。
FROM python:3.12-slim

ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git make sudo less procps \
        tmux ripgrep jq \
        iptables ipset dnsutils \
    && rm -rf /var/lib/apt/lists/*

# gcloud / bq（ETL とデプロイの確認に使う）
RUN curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
        > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

# Claude Code 本体はイメージに入れない。ホストの ~/.local/share/claude を
# read-only でマウントして使う（compose.yaml 参照）。インストーラを叩かないぶん
# バージョンがホストと必ず一致し、認証もそのまま共有できる。
# ネイティブバイナリだが Debian 上でそのまま動くことを確認済み。

# docker CLI は入れない。ソケットを渡さない方針のため使えないし、
# make lock はこのイメージが本番と同じ 3.12 なのでそのまま解決できる。

RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m -s /bin/bash $USERNAME \
    && echo "$USERNAME ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME \
    && mkdir -p /home/$USERNAME/.local/bin
ENV PATH=/home/$USERNAME/.local/bin:$PATH

# claude の起動スクリプト。
# ホストの ~/.local/share/claude/versions/ を read-only でマウントして使うが、
# そこには versions/2.1.220 のようなバイナリが並ぶだけで current のような
# 固定の入口が無い。特定バージョンへの symlink にするとホスト側が更新された
# 時点で壊れるので、実行時に最新を選ぶ。
RUN printf '%s\n' \
    '#!/bin/sh' \
    'DIR="$HOME/.local/share/claude/versions"' \
    '[ -d "$DIR" ] || { echo "Claude Code がマウントされていません: $DIR" >&2; exit 1; }' \
    'BIN=$(ls -1 "$DIR" | sort -V | tail -1)' \
    '[ -n "$BIN" ] || { echo "$DIR にバイナリがありません" >&2; exit 1; }' \
    'exec "$DIR/$BIN" "$@"' \
    > /usr/local/bin/claude \
    && chmod +x /usr/local/bin/claude

# Playwright のブラウザと OS 依存をイメージに焼き込む。
# ここを実行時に任せると、手元で sudo が必要になって setup が止まる（実際に踏んだ）。
# root で入れて全ユーザーから読める場所に置く。
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
COPY requirements-dev.txt requirements.txt /tmp/deps/
COPY app/requirements.in /tmp/deps/app/
RUN pip install --no-cache-dir -r /tmp/deps/requirements-dev.txt \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright \
    && rm -rf /tmp/deps

USER $USERNAME
WORKDIR /workspace
# 常駐させて docker exec で入る使い方。中で何かを自動起動はしない。
CMD ["sleep", "infinity"]
