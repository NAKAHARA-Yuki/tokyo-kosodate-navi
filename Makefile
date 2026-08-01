.DEFAULT_GOAL := help
SHELL := /bin/bash

# 各自の設定を .env から読む（git 管理外。1台を複数人で使うときのポート指定など）。
# compose にも .env を読む仕組みはあるが、それだと効かない。
#   - compose のプロジェクトディレクトリは compose ファイルのある docker/ になるため、
#     リポジトリ直下の .env は読まれない
#   - Makefile が DEV_PORT を export しており、環境変数は compose の .env より優先される
# ここで読めば make 経由の全てに効く。書式は KEY=VALUE のみ。
-include .env

# 開発用コンテナでは依存がイメージに焼き込み済みなので VENV=/usr/local で上書きする
# （そちらの bin/ に pytest や ruff が入っている）。素の環境では .venv を作って使う。
VENV        ?= .venv
PY          := $(VENV)/bin/python
# .venv がまだ無い段階（make auth）でも動かせるようにフォールバックする
AUTH_PY     := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
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

# コンテナの中か外かで案内する内容が変わるので見分ける（イメージ側で作っている目印）
IN_CONTAINER := $(wildcard /.dockerenv)

.PHONY: help
help: ## コマンド一覧を表示
	@echo "使い方: make <target> [ENV=dev|staging|prod]   (現在: ENV=$(ENV))"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "───────────────────────────────────────────────"
	@echo " はじめての人はこの順で（詳細: docs/onboarding.md）"
	@echo "───────────────────────────────────────────────"
	@$(MAKE) --no-print-directory next

.PHONY: next
next: ## 今の状態を見て「次にやること」を表示する
	@# 手順を覚えていなくても、これを打てば進められる状態にしておく。
	@# コンテナの中を先に判定する。ホスト側の前提（gcloud ログインなど）は
	@# コンテナ内には存在しないので、順番を逆にすると誤った案内が出る。
	@if [ -n "$(IN_CONTAINER)" ]; then \
		echo "  コンテナの中にいます。そのまま作業できます。"; \
		echo ""; \
		echo "      claude --dangerously-skip-permissions --remote-control kosodate"; \
		echo "      make check     lint + テスト + E2E"; \
		echo "      make dev       アプリを起動 (http://localhost:$(DEV_PORT))"; \
	elif [ ! -f "$(HOME)/.git-credentials" ]; then \
		echo "  ▶ GitHub の認証を設定する"; \
		echo "      docs/onboarding.md「GitHub の初期設定」を参照"; \
	elif [ ! -f "$(HOME)/.config/gcloud/application_default_credentials.json" ]; then \
		echo "  ▶ GCP にログインする"; \
		echo "      gcloud auth application-default login"; \
	elif [ ! -f "$(SCOPED_ADC)" ]; then \
		echo "  ▶ GCP の権限を dev だけに絞る"; \
		echo "      make auth"; \
	elif [ ! -f "$(HOME)/.claude.json" ]; then \
		echo "  ▶ Claude Code にログインする"; \
		echo "      claude"; \
	elif ! $(COMPOSE) ps --status running 2>/dev/null | grep -q agent; then \
		echo "  ▶ コンテナを起動する"; \
		echo "      make agent-up"; \
	elif [ -z "$$TMUX" ]; then \
		echo "  ▶ tmux を起動してからコンテナに入る"; \
		echo "      tmux new -A -s claude     # 無ければ作る / あれば入る"; \
		echo "      make agent-shell"; \
	else \
		echo "  ▶ コンテナに入る"; \
		echo "      make agent-shell"; \
	fi

.PHONY: env
env: ## 現在の環境設定を表示
	@$(PY) -c "import sys; sys.path.insert(0,'app'); import config; print(config.describe()); print('service =', config.SERVICE_NAME)"

# ---------------------------------------------------------------- 環境構築

.PHONY: setup
setup: ## 仮想環境と依存関係を用意する（コンテナ内では不要）
	@# 全体を1つのシェルにまとめている。make はレシピを行ごとに別シェルで動かすため、
	@# if の中で exit しても次の行は実行されてしまう。分けて書くと、コンテナ内で
	@# 「不要です」と表示した直後に /usr/local を venv 化しようとして壊す（実際に踏んだ）。
	@set -e; \
	if [ "$(VENV)" != ".venv" ]; then \
		echo "✅ コンテナでは依存がイメージに入っているため setup は不要です"; \
	else \
		python3 -m venv $(VENV); \
		$(PY) -m ensurepip --upgrade; \
		$(PIP) install -q --upgrade pip; \
		$(PIP) install -q -r requirements-dev.txt; \
		: '--with-deps は OS パッケージを入れるため sudo が要る。CI では通るが手元では'; \
		: 'パスワード入力できずに失敗することがある。ブラウザ本体さえ入れば E2E は動くので'; \
		: '失敗しても setup 全体は止めず、必要なときの対処だけ案内する。'; \
		$(VENV)/bin/playwright install --with-deps chromium \
			|| ($(VENV)/bin/playwright install chromium \
			    && echo "⚠️  OS依存パッケージの導入をスキップしました（sudo が必要）。" \
			    && echo "    E2E がブラウザ起動で失敗する場合は手動で実行してください:" \
			    && echo "    sudo $(VENV)/bin/playwright install-deps chromium"); \
		echo ""; \
		echo "✅ setup 完了"; \
	fi
	@echo ""
	@$(MAKE) --no-print-directory next

.PHONY: auth
auth: ## Claude Code 経由の GCP アクセスを claude-dev に切り替える（初回に1度）
	@# 認証だけは個人ごとなのでコンテナに焼き込めない。ここを1コマンドにしている。
	@if [ ! -f "$(HOME)/.config/gcloud/application_default_credentials.json" ]; then \
		echo "先に gcloud の認証が要ります:"; \
		echo "   gcloud auth application-default login"; \
		exit 1; \
	fi
	@# make auth は make setup より前に実行される手順なので、.venv があるとは限らない。
	@# スクリプト本体は標準ライブラリだけで動くため、無ければ system の python3 を使う。
	$(AUTH_PY) scripts/setup_scoped_adc.py
	@echo ""
	@$(MAKE) --no-print-directory next

# ---------------------------------------------------------------- 常駐エージェント

# 1台のサーバーを複数人で使うため、コンテナ名・プロジェクト名・ポートを
# ユーザーごとに分ける。分けないと 2人目が「name already in use」で起動できない。
export AGENT_USER := $(shell id -un)
# コンテナ内のユーザーをホストの uid/gid に合わせる。ずれると bind mount した
# リポジトリや ~/.claude に書き込めない（uid 1000 の人だけ動いて他は詰まる）。
export HOST_UID   := $(shell id -u)
export HOST_GID   := $(shell id -g)
# 同じサーバーで複数人が使うと 8080 を取り合うので、空いている番号を自動で選ぶ。
#
# 既に自分のコンテナが動いていれば、その番号をそのまま使う。
# 毎回選び直すと URL が変わるうえ、ポートが変わるとコンテナも作り直しになるため。
#
# .env に DEV_PORT を書けばそちらが優先される（?= のため）。
# コマンドラインの指定（make agent-up DEV_PORT=9000）はさらに優先される。
AUTO_DEV_PORT := $(shell \
	mine=$$(docker port kosodate-agent-$$(id -un) 8080 2>/dev/null | head -1 | sed 's/.*://'); \
	if [ -n "$$mine" ]; then echo "$$mine"; else \
		for p in $$(seq 8080 8099); do \
			if ! ss -ltn 2>/dev/null | grep -qE ":$$p[[:space:]]"; then echo "$$p"; break; fi; \
		done; \
	fi)
DEV_PORT          ?= $(if $(AUTO_DEV_PORT),$(AUTO_DEV_PORT),8080)
export DEV_PORT

# dev の Cloud Run に出すときのリビジョンタグ。ユーザーごとに専用 URL を得るため。
# タグに使えるのは英小文字・数字・ハイフンだけなので、それ以外は落とす。
DEPLOY_TAG := $(shell id -un | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | sed 's/-*$$//')

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
	@# 失敗したときに何をすればいいかまで出す。1台を複数人で使うと必ずポートが衝突し、
	@# docker の "port is already allocated" だけでは対処が分からない。
	@if ! $(COMPOSE) up -d --build; then \
		echo ""; \
		if ss -ltn 2>/dev/null | grep -qE ":$(DEV_PORT)\s"; then \
			echo "───────────────────────────────────────────────"; \
			echo " ポート $(DEV_PORT) は既に使われています"; \
			echo "───────────────────────────────────────────────"; \
			echo "  同じサーバーの他の人が使っている可能性があります。"; \
			echo "  空いている番号に変えてください（リポジトリ直下で実行）:"; \
			echo ""; \
			echo "      echo \"DEV_PORT=8081\" >> .env"; \
			echo "      make agent-up"; \
			echo ""; \
			echo "  .env は git 管理外なので、他の人には影響しません。"; \
		fi; \
		exit 1; \
	fi
	@echo ""
	@echo "✅ コンテナを起動しました（サーバー再起動後も自動で上がります）"
	@echo ""
	@echo "───────────────────────────────────────────────"
	@echo " 次にやること"
	@echo "───────────────────────────────────────────────"
	@if [ -z "$$TMUX" ]; then \
		echo "  1. tmux を起動する（接続が切れても作業が生き残ります）"; \
		echo ""; \
		echo "       tmux new -A -s claude"; \
		echo ""; \
		echo "  2. コンテナに入る"; \
		echo ""; \
		echo "       make agent-shell"; \
	else \
		echo "  tmux の中にいます。そのままコンテナに入れます。"; \
		echo ""; \
		echo "       make agent-shell"; \
	fi
	@echo ""
	@echo "  tmux の使い方: docs/tmux.md"

.PHONY: agent-shell
agent-shell: ## コンテナに入る（この中で claude を起動する）
	@# tmux の外から入ると、接続が切れた時点で中の作業も死ぬ。止めはしないが必ず伝える。
	@if [ -z "$$TMUX" ]; then \
		echo ""; \
		echo "⚠️  tmux の外にいます"; \
		echo ""; \
		echo "   このまま入ると、接続が切れた時点で作業中の処理も止まります。"; \
		echo "   一度抜けて、tmux の中から入り直すことを勧めます。"; \
		echo ""; \
		echo "       tmux new -A -s claude"; \
		echo "       make agent-shell"; \
		echo ""; \
	fi
	@echo "───────────────────────────────────────────────"
	@echo " コンテナに入ります。Claude Code の起動コマンド:"
	@echo ""
	@echo "   claude --dangerously-skip-permissions --remote-control kosodate"
	@echo ""
	@echo " 迷ったら make next / make help"
	@echo "───────────────────────────────────────────────"
	$(COMPOSE) exec agent bash

.PHONY: agent-down
agent-down: ## コンテナを止める
	$(COMPOSE) down

.PHONY: cleanup
cleanup: ## dev に溜まった動作確認の跡を片付ける（リビジョン・検証用テーブル）
	$(PY) scripts/cleanup_dev.py

.PHONY: lock
lock: ## 本番イメージの依存を再固定する (app/requirements.in を変えたら必ず実行)
	@# ロックは解決した Python のバージョンに紐づく。本番イメージは 3.12 なので、
	@# 3.12 以外で作ると本番で入らないロックになる。
	@# 開発用コンテナは本番と同じ 3.12 なのでそのまま実行できる。
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
	@echo ""
	@echo "✅ app/requirements.lock を更新しました"
	@echo ""
	@echo "   次にやること:"
	@echo "     1. git diff app/requirements.lock   意図しない巻き添え更新が無いか確認"
	@echo "     2. make check                       テストが通るか確認"

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
	@# dev は複数人が同じサービスを使うので、自分のタグが付いた URL を返す。
	@# タグが無ければ（まだデプロイしていなければ）サービス既定の URL を返す。
	@if [ "$(ENV)" = "dev" ]; then \
		TAGGED=$$(gcloud run services describe $(SERVICE) --project $(PROJECT_ID) --region $(REGION) \
			--format="value(status.traffic.filter(tag:$(DEPLOY_TAG)).extract(url))" 2>/dev/null | tr -d '[]'); \
		if [ -n "$$TAGGED" ]; then \
			echo "$$TAGGED"; \
		else \
			echo "（あなたのタグはまだありません。make deploy ENV=dev で作られます）"; \
			gcloud run services describe $(SERVICE) --project $(PROJECT_ID) --region $(REGION) --format='value(status.url)'; \
		fi; \
	else \
		gcloud run services describe $(SERVICE) --project $(PROJECT_ID) --region $(REGION) --format='value(status.url)'; \
	fi

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
	@# ユーザーごとにリビジョンタグを付ける。dev の Cloud Run サービスは1つしかなく、
	@# そのままデプロイすると後から出した人が上書きしてしまい、相手の URL に
	@# 自分のコードが出る。タグを付ければ同じサービスのまま専用 URL が生える。
	@# --no-traffic なので、既定の URL は他の人のデプロイに影響されない。
	gcloud run deploy $(SERVICE) \
		--source ./app \
		--project $(PROJECT_ID) \
		--region $(REGION) \
		--allow-unauthenticated \
		--set-env-vars GCP_PROJECT_ID=$(PROJECT_ID),APP_ENV=$(ENV) \
		--memory 512Mi --cpu 1 --min-instances 0 --max-instances 3 --timeout 120 \
		--tag $(DEPLOY_TAG) --no-traffic \
		--quiet
	@echo ""
	@echo "✅ あなた専用の URL に出しました（他の人のデプロイに上書きされません）"
	@echo ""
	@$(MAKE) --no-print-directory url ENV=$(ENV)

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
	@echo ""
	@echo "   次にやること: make verify ENV=$(ENV)   グラフが引けるか確認"
