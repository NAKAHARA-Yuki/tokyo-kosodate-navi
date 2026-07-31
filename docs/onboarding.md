# 開発環境のセットアップ

このプロジェクトは **Claude Code をコンテナ内で常駐させて開発する**構成です。
コンテナの中では sudo を含めて自由に操作でき、壊しても作り直せます。

- サーバーに常駐させてスマホから指示を送る → [サーバーで常駐させる](#サーバーで常駐させる)
- 自分のマシンで普通に開発する → [手元で開発する](#手元で開発する)

## 前提

- Docker（Compose v2）
- GCP プロジェクトへのアクセス権
- 管理者に `scripts/grant_member.sh <あなたのメール>` を実行してもらっていること

## 共通の初期設定（ホスト側で1度だけ）

### 1. 取得

```bash
git clone https://github.com/NAKAHARA-Yuki/tokyo-kosodate-navi
cd tokyo-kosodate-navi
```

### 2. GCP の権限を絞る

```bash
gcloud auth application-default login
make auth
```

`make auth` は **dev だけ書き込めるサービスアカウントの認証**を作ります。
コンテナにはこの1ファイルだけを渡すので、**コンテナ内から staging と prod は書き換えられません**。

> 失敗する場合は `serviceAccountTokenCreator` が付いていません。
> 管理者に `scripts/grant_member.sh <あなたのメール>` の実行を依頼してください。

### 3. Claude Code にログイン

```bash
claude          # 初回はログインを求められる
```

コンテナはホストの Claude Code 本体と認証をそのまま使います。
インストールもログインもコンテナ側では不要です。

## サーバーで常駐させる

Claude Code を立ち上げっぱなしにしておくと、スマホの Claude アプリからそのセッションに
指示を送れます。パソコンを開かずに開発を進められます。

### 起動

```bash
make agent-up        # ビルド + 起動 + 通信制限の適用
```

`restart: unless-stopped` なので、**サーバーを再起動しても自動で上がります**。

### 入って Claude Code を起動する

tmux は**ホスト側**で回します。コンテナを作り直しても tmux セッションが生き残るためです。

```bash
tmux new -s claude                      # 初回
# 以降は tmux attach -t claude

make agent-shell                        # コンテナに入る
claude --dangerously-skip-permissions   # 承認なしで動かす
```

`Ctrl-b d` で tmux から抜けても Claude Code は動き続けます。

```
ssh server
 └─ tmux attach -t claude          ← 常駐。コンテナ再作成でも生存
      └─ make agent-shell
           └─ claude --dangerously-skip-permissions
```

### 止める・作り直す

```bash
make agent-down      # 止める
make agent-up        # 作り直す（イメージも更新される）
make agent-firewall  # 通信の許可リストを入れ直す（繋がらなくなったとき）
```

## 手元で開発する

コンテナに入って普通に使うこともできます。

```bash
make agent-up
make agent-shell
make check        # lint + ユニット/API テスト + E2E
make dev          # http://localhost:8080
```

### コンテナを使わない場合

動きますが推奨しません。Python が 3.12 以外だと本番と挙動がズレます。

```bash
make setup
gcloud auth application-default login
make auth
make check
```

## この環境について

### 何が保証されるか

- **本番と同じ Python 3.12**、依存もブラウザもイメージに焼き込み済み（`make setup` 不要）
- **GCP アクセスは dev 限定**（staging / prod は読み取りのみ）
- **ホストマシンから隔離**（docker ソケットを渡していない）
- **外向き通信は許可リストのみ**（Google / GitHub / PyPI / Anthropic）

### 何が保証されないか

コンテナ内の利用者は sudo を持つので、その気になれば firewall も消せます。
**事故と自動化の暴走を防ぐ層であって、意図的な回避を防ぐものではありません。**

またホスト側であなた自身の GCP 認証を直接使えば、権限どおりのことができます。

詳細は [ADR 0008](adr/0008-scoped-credentials.md) と [ADR 0009](adr/0009-sandboxed-devcontainer.md)。

## よくある詰まり

| 症状 | 原因と対処 |
|---|---|
| `make auth` が権限エラー | `serviceAccountTokenCreator` 未付与。管理者に依頼 |
| `make agent-up` が認証エラーで止まる | `make auth` か `claude` のログインが未実施 |
| コンテナ内から外部サイトに繋がらない | 許可リスト外。必要なら `docker/init-firewall.sh` に足して PR |
| 前は繋がったのに繋がらない | 許可リストは起動時に IP を解決している。`make agent-firewall` |
| E2E がブラウザのクラッシュで大量に落ちる | `/dev/shm` 不足。compose の `shm_size` を確認 |
| `make etl ENV=prod` が権限エラー | **仕様**。prod への書き込みは不可。ETL は dev で行う |
| `make deploy ENV=staging` が止まる | **仕様**。staging は main へのマージで自動デプロイ |

## メンバーを追加するとき（管理者向け）

```bash
./scripts/grant_member.sh newmember@example.com
```

本人の GCP 権限は変えません。付与するのは claude-dev になりすます権限だけです。
