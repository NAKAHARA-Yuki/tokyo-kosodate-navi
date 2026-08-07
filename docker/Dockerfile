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
        dnsutils \
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

# Node.js。フロントエンドを Next.js の別サービスに分けるため（issue #33 / ADR 0013）。
#
# Debian bookworm の nodejs は 18 系で、現行の Next.js には古い。
# NodeSource から現行 LTS の 24.x を入れる。メジャーを固定しているのは、
# イメージのビルド時期でメジャーが変わると「手元では動くのに他の人の環境で落ちる」ため。
#
# 本番のフロント用イメージはこれとは別に作る。ここに入れるのは開発作業のためだけ。
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/nodesource.gpg \
    && chmod go+r /usr/share/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_24.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI。issue や PR の作成には API を叩く必要があり、git push だけでは足りない。
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# gh のラッパー。トークンは ~/.git-credentials にあるので実行時に読む。
#
# 環境変数として compose から渡す方法も取れるが、それだと docker inspect で
# 同じサーバーの他ユーザーからトークンが見えてしまう。ファイルは 600 なので読めない。
#
# bash の rc に書く方法も取れない。Claude Code はコマンドを非対話シェルで実行するため
# /etc/bash.bashrc も /etc/profile.d も読まれず、そこに書いても効かない。
# ラッパーならシェルの種類に関係なく必ず通る。
RUN printf '%s\n' \
    '#!/bin/sh' \
    'if [ -z "$GH_TOKEN" ] && [ -r "$HOME/.git-credentials" ]; then' \
    '  GH_TOKEN=$(sed -n "s#^https://[^:]*:\\([^@]*\\)@github\\.com.*#\\1#p" "$HOME/.git-credentials" | head -1)' \
    '  [ -n "$GH_TOKEN" ] && export GH_TOKEN' \
    'fi' \
    'exec /usr/bin/gh "$@"' \
    > /usr/local/bin/gh \
    && chmod +x /usr/local/bin/gh

# git の設定。ホストの ~/.gitconfig はマウントしないので、コンテナ側で持たせる。
#   credential.helper: これが無いと ~/.git-credentials をマウントしても
#     git が読みに行かず push できない（実際に踏んだ）
#   safe.directory   : uid はホストに合わせているので通常は不要だが、
#     ずれたときに出るエラーが分かりにくいので先に潰しておく
RUN git config --system credential.helper store \
    && git config --system --add safe.directory /workspace

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
# pip-tools は `make lock` が使う。実行時に入れようとすると /usr/local も
# /home/dev/.local も root 所有なので sudo が要り、そこで手が止まる（Playwright と同じ理由）。
RUN pip install --no-cache-dir -r /tmp/deps/requirements-dev.txt pip-tools \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright \
    && rm -rf /tmp/deps

USER $USERNAME
WORKDIR /workspace
# 常駐させて docker exec で入る使い方。中で何かを自動起動はしない。
CMD ["sleep", "infinity"]
