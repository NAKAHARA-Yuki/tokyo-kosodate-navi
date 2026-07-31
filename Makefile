.DEFAULT_GOAL := help
SHELL := /bin/bash

# devcontainer では依存がイメージに焼き込み済みなので VENV=/usr/local で上書きする
# （そちらの bin/ に pytest や ruff が入っている）。素の環境では .venv を作って使う。
VENV        ?= .venv
PY          := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
PROJECT_ID  ?= opendatahackathon-503500
REGION      := asia-northeast1

# 環境。指定しなければ dev（誤って本番を触らないため）
# 使い方: make etl ENV=staging / make deploy ENV=prod
ENV ?= dev

ifeq ($(ENV),prod)
  SERVICE := kosodate-graph-viewer
else
  SERVICE := kosodate-graph-viewer-$(ENV)
endif

export APP_ENV       := $(ENV)
export GCP_PROJECT_ID := $(PROJECT_ID)

# 権限を絞った認証情報があればそれを使う（詳細は docs/adr/0008）。
# claude-dev は dev だけ書き込み可・staging/prod は読み取りのみのサービスアカウント。
# 存在しない環境（CI や他のメンバー）では既定の認証のままになる。
SCOPED_ADC := $(HOME)/.config/gcloud/claude-dev-adc.json
ifneq ($(wildcard $(SCOPED_ADC)),)
  export GOOGLE_APPLICATION_CREDENTIALS := $(SCOPED_ADC)
endif

.PHONY: help
help: ## コマンド一覧を表示
	@echo "使い方: make <target> [ENV=dev|staging|prod]   (現在: ENV=$(ENV))"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: env
env: ## 現在の環境設定を表示
	@$(PY) -c "import sys; sys.path.insert(0,'app'); import config; print(config.describe()); print('service =', config.SERVICE_NAME)"

# ---------------------------------------------------------------- 環境構築

.PHONY: setup
setup: ## 仮想環境と依存関係を用意する（devcontainer では不要）
	@if [ "$(VENV)" != ".venv" ]; then \
		echo "✅ devcontainer では依存がイメージに入っているため setup は不要です"; \
		exit 0; \
	fi
	python3 -m venv $(VENV)
	$(PY) -m ensurepip --upgrade
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements-dev.txt
	@# --with-deps は OS パッケージを入れるため sudo が要る。CI では通るが手元では
	@# パスワード入力できずに失敗することがある。ブラウザ本体さえ入れば E2E は動くので、
	@# 失敗しても setup 全体は止めず、必要なときの対処だけ案内する。
	$(VENV)/bin/playwright install --with-deps chromium \
		|| ($(VENV)/bin/playwright install chromium \
		    && echo "⚠️  OS依存パッケージの導入をスキップしました（sudo が必要）。" \
		    && echo "    E2E がブラウザ起動で失敗する場合は手動で実行してください:" \
		    && echo "    sudo $(VENV)/bin/playwright install-deps chromium")
	@echo "✅ setup 完了。GCP未認証なら: gcloud auth application-default login"

.PHONY: auth
auth: ## Claude Code 経由の GCP アクセスを claude-dev に切り替える（初回に1度）
	@# 認証だけは個人ごとなのでコンテナに焼き込めない。ここを1コマンドにしている。
	@if [ ! -f "$(HOME)/.config/gcloud/application_default_credentials.json" ]; then \
		echo "先に gcloud の認証が要ります:"; \
		echo "   gcloud auth application-default login"; \
		exit 1; \
	fi
	$(PY) scripts/setup_scoped_adc.py

# ---------------------------------------------------------------- 常駐エージェント

# 1台のサーバーを複数人で使うため、コンテナ名・プロジェクト名・ポートを
# ユーザーごとに分ける。分けないと 2人目が「name already in use」で起動できない。
export AGENT_USER := $(shell id -un)
# コンテナ内のユーザーをホストの uid/gid に合わせる。ずれると bind mount した
# リポジトリや ~/.claude に書き込めない（uid 1000 の人だけ動いて他は詰まる）。
export HOST_UID   := $(shell id -u)
export HOST_GID   := $(shell id -g)
# 同じサーバーで複数人が make dev すると 8080 を取り合う。各自 .env で変える。
DEV_PORT          ?= 8080
export DEV_PORT

COMPOSE := docker compose -f docker/compose.yaml

.PHONY: agent-up
agent-up: ## Claude Code 用のコンテナを起動する（サーバー常駐）
	@# 前提を先に確かめる。起動してから「認証が無い」と気づくと原因が分かりにくい。
	@test -f "$(HOME)/.config/gcloud/claude-dev-adc.json" \
		|| { echo "❌ 先に 'make auth' を実行してください（dev 用の認証が要ります）"; exit 1; }
	@test -f "$(HOME)/.claude.json" \
		|| { echo "❌ ホスト側で claude にログインしてください"; exit 1; }
	@# 無いまま起動すると docker が同名のディレクトリを勝手に作り、
	@# 以降 git が壊れて原因が分かりにくくなる。先に止める。
	@test -f "$(HOME)/.git-credentials" \
		|| { echo "❌ GitHub の認証情報がありません（~/.git-credentials）"; \
		     echo "   docs/onboarding.md「GitHub の初期設定」を参照してください"; exit 1; }
	$(COMPOSE) up -d --build
	@$(COMPOSE) exec -u root agent bash /workspace/docker/init-firewall.sh
	@echo ""
	@echo "✅ 起動しました。入るには: make agent-shell"

.PHONY: agent-shell
agent-shell: ## コンテナに入る（この中で claude を起動する）
	@echo "Claude Code は次で起動します: claude --dangerously-skip-permissions"
	$(COMPOSE) exec agent bash

.PHONY: agent-down
agent-down: ## コンテナを止める
	$(COMPOSE) down

.PHONY: agent-firewall
agent-firewall: ## 外向き通信の許可リストを入れ直す（宛先のIPが変わったとき）
	$(COMPOSE) exec -u root agent bash /workspace/docker/init-firewall.sh

.PHONY: lock
lock: ## 本番イメージの依存を再固定する (app/requirements.in を変えたら必ず実行)
	@# ロックは解決した Python のバージョンに紐づく。本番イメージは 3.12 なので、
	@# 3.12 以外で作ると本番で入らないロックになる。
	@# devcontainer は本番と同じ 3.12 なのでそのまま実行できる。
	@# それ以外の環境では docker で 3.12 を用意する（そのために docker が要る）。
	@if [ "$$($(PY) -c 'import sys; print("%d.%d" % sys.version_info[:2])')" = "3.12" ]; then \
		echo "Python 3.12 のためそのまま解決します"; \
		$(PIP) install -q pip-tools && \
		cd app && ../$(VENV)/bin/pip-compile --quiet --generate-hashes \
			--output-file requirements.lock requirements.in; \
	else \
		echo "Python が 3.12 ではないため docker で解決します"; \
		docker run --rm -v "$(PWD)/app:/w" -w /w python:3.12-slim sh -c '\
			pip install -q pip-tools && \
			pip-compile --quiet --generate-hashes --output-file requirements.lock requirements.in'; \
	fi
	@echo "✅ app/requirements.lock を更新しました。差分を確認してコミットしてください"

# ---------------------------------------------------------------- 品質

.PHONY: lint
lint: ## ruff でチェックする（変更はしない）
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

.PHONY: fmt
fmt: ## ruff で自動整形する
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

.PHONY: test
test: ## 単体・結合テストを実行する（GCP不要）
	$(VENV)/bin/pytest tests

.PHONY: e2e
e2e: ## E2Eテストを実行する（ブラウザ操作。GCP不要）
	$(VENV)/bin/pytest e2e

.PHONY: e2e-smoke
e2e-smoke: ## デプロイ先に対してスモークテストを実行する (例: make e2e-smoke ENV=staging)
	E2E_BASE_URL=$$($(MAKE) --no-print-directory url ENV=$(ENV)) $(VENV)/bin/pytest e2e -m smoke

.PHONY: check
check: lint test e2e ## lint・テスト・E2E をまとめて実行する

# ---------------------------------------------------------------- アプリ

.PHONY: dev
dev: ## ローカルでアプリを起動する (http://localhost:8080)
	cd app && APP_ENV=$(ENV) ../$(VENV)/bin/uvicorn main:app --reload --host 0.0.0.0 --port 8080

.PHONY: url
url: ## デプロイ済みサービスのURLを表示する
	@gcloud run services describe $(SERVICE) --project $(PROJECT_ID) --region $(REGION) --format='value(status.url)'

.PHONY: deploy
deploy: ## Cloud Run の dev にデプロイする (staging/prod は GitHub Actions 経由)
	@# staging と prod は手元からデプロイしない。誰がいつ何を出したか追えなくなり、
	@# CI を通っていないコードが本番に出る経路にもなるため（docs/adr/0008）。
	@if [ "$(ENV)" != "dev" ]; then \
		echo "❌ $(ENV) へは手元からデプロイできません。"; \
		echo ""; \
		echo "   staging: main へマージすると自動でデプロイされます"; \
		echo "   prod   : v*.*.* タグを push してください"; \
		echo "            git tag -a v1.2.3 -m '説明' && git push origin v1.2.3"; \
		echo ""; \
		echo "   詳細: CONTRIBUTING.md「リリース手順」"; \
		exit 1; \
	fi
	gcloud run deploy $(SERVICE) \
		--source ./app \
		--project $(PROJECT_ID) \
		--region $(REGION) \
		--allow-unauthenticated \
		--set-env-vars GCP_PROJECT_ID=$(PROJECT_ID),APP_ENV=$(ENV) \
		--memory 512Mi --cpu 1 --min-instances 0 --max-instances 3 --timeout 120 \
		--quiet

# ---------------------------------------------------------------- データパイプライン

.PHONY: etl
etl: ## レジストリを取得して BigQuery にロードする（⚠ 対象環境のデータを上書き）
	@echo "⚠ ENV=$(ENV) のデータセットを WRITE_TRUNCATE で上書きします"
	@$(MAKE) --no-print-directory env
	@read -p "続行しますか? [y/N] " ans && [ "$$ans" = "y" ]
	$(PY) src/etl_to_bq.py

.PHONY: graph
graph: ## PROPERTY GRAPH を再作成する（スキーマ変更後は必須）
	$(PY) src/create_graph.py

.PHONY: verify
verify: ## グラフの動作検証クエリを実行する
	$(PY) src/verify_graph.py

.PHONY: pipeline
pipeline: etl graph verify ## ETL → グラフ再作成 → 検証 を通しで実行する

.PHONY: clone-data
clone-data: ## 本番データを別環境にコピーする (例: make clone-data ENV=dev)
	@if [ "$(ENV)" = "prod" ]; then echo "❌ prod へのコピーはできません"; exit 1; fi
	@echo "prod のデータを $(ENV) にコピーします（ETLを回すより速く、費用もかかりません）"
	@bq --project_id=$(PROJECT_ID) mk --force --location=$(REGION) --dataset \
		$(PROJECT_ID):gov_knowledge_db_$(ENV) >/dev/null 2>&1 || true
	@for t in benefits schemes statuses documents \
	          benefit_requires_status benefit_requires_doc benefit_in_scheme benefit_leads_to; do \
		echo "  copying $$t ..."; \
		bq --project_id=$(PROJECT_ID) cp -f \
			$(PROJECT_ID):gov_knowledge_db.$$t \
			$(PROJECT_ID):gov_knowledge_db_$(ENV).$$t >/dev/null; \
	done
	@$(MAKE) --no-print-directory graph ENV=$(ENV)
	@echo "✅ $(ENV) へのコピー完了"
