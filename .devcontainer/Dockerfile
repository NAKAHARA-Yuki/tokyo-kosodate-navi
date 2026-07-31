# 本番イメージと同じ Python にそろえる。
# ローカルの Python がこれとズレていると、make lock で本番に入らないロックができるなど、
# 「手元では動くのに本番で落ちる」類の問題が起きる（実際に 3.14 と 3.12 でズレていた）。
FROM python:3.12-slim

ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git make sudo less procps \
    && rm -rf /var/lib/apt/lists/*

# gcloud / bq（ETL とデプロイの確認に使う）
RUN curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
        > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

# docker CLI。make lock がホストの docker を使うため（ソケットは devcontainer.json でマウント）
RUN curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /usr/share/keyrings/docker.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m -s /bin/bash $USERNAME \
    && echo "$USERNAME ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME

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
