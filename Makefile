.DEFAULT_GOAL := help
SHELL := /bin/bash

VENV        := .venv
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
setup: ## 仮想環境と依存関係を用意する
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

.PHONY: lock
lock: ## 本番イメージの依存を再固定する (app/requirements.in を変えたら必ず実行)
	@# 本番と同じ python:3.12-slim の中で解決する。ローカルの Python で作ると
	@# バージョンが違うぶん本番で入らないロックができる（ローカルは 3.14）。
	docker run --rm -v "$(PWD)/app:/w" -w /w python:3.12-slim sh -c '\
		pip install -q pip-tools && \
		pip-compile --quiet --generate-hashes --output-file requirements.lock requirements.in'
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
deploy: ## Cloud Run にデプロイする (例: make deploy ENV=staging)
	@if [ "$(ENV)" = "prod" ]; then \
		echo "⚠ 本番環境へのデプロイです"; \
		read -p "続行しますか? [y/N] " ans && [ "$$ans" = "y" ] || exit 1; \
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
