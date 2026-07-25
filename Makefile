.DEFAULT_GOAL := help
SHELL := /bin/bash

VENV        := .venv
PY          := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
PROJECT_ID  ?= opendatahackathon-503500
REGION      := asia-northeast1
SERVICE     := kosodate-graph-viewer

export GCP_PROJECT_ID := $(PROJECT_ID)

.PHONY: help
help: ## コマンド一覧を表示
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- 環境

.PHONY: setup
setup: ## 仮想環境と依存関係を用意する
	python3 -m venv $(VENV)
	$(PY) -m ensurepip --upgrade
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements-dev.txt
	@echo "✅ setup 完了。GCP未認証なら: gcloud auth application-default login"

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
test: ## テストを実行する（GCP不要）
	$(VENV)/bin/pytest

.PHONY: check
check: lint test ## lint とテストをまとめて実行する

# ---------------------------------------------------------------- アプリ

.PHONY: dev
dev: ## ローカルでアプリを起動する (http://localhost:8080)
	cd app && ../$(VENV)/bin/uvicorn main:app --reload --host 0.0.0.0 --port 8080

.PHONY: deploy
deploy: ## Cloud Run にデプロイする
	gcloud run deploy $(SERVICE) \
		--source ./app \
		--project $(PROJECT_ID) \
		--region $(REGION) \
		--allow-unauthenticated \
		--set-env-vars GCP_PROJECT_ID=$(PROJECT_ID) \
		--memory 512Mi --cpu 1 --min-instances 0 --max-instances 3 --timeout 120 \
		--quiet

# ---------------------------------------------------------------- データパイプライン

.PHONY: etl
etl: ## レジストリを取得して BigQuery にロードする（⚠ 本番データを上書き）
	@echo "⚠ 本番の BigQuery を WRITE_TRUNCATE で上書きします (project=$(PROJECT_ID))"
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
