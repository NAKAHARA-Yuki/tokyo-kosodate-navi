# 開発環境のセットアップ

このプロジェクトは **Claude Code をコンテナ内で常駐させて開発する**構成です。
コンテナの中では sudo を含めて自由に操作でき、壊しても作り直せます。

> **手順を覚える必要はありません。** 迷ったらこれを打ってください。
> 今の状態を見て、次にやることだけを出します。
>
> ```bash
> make next
> ```

- サーバーに常駐させてスマホから指示を送る → [サーバーで常駐させる](#サーバーで常駐させる)
- 自分のマシンで普通に開発する → [手元で開発する](#手元で開発する)

## 前提

- GCP プロジェクトへのアクセス権
- 管理者に `scripts/grant_member.sh <あなたのメール>` を実行してもらっていること

## ホスト側に入れるもの

コンテナの中には依存が焼き込んであるので、**ホストに要るのはこれだけ**です。
Ubuntu / Debian 系を想定しています。

```bash
sudo apt update
sudo apt install -y git tmux curl
```

### Docker（Compose v2 込み）

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

**グループ追加後は一度ログインし直してください。** 反映されていないと
`make agent-up` が権限エラーで落ちます。

```bash
docker ps                  # エラーが出なければOK
docker compose version     # v2 系であること
```

### gcloud CLI

```bash
curl -fsSL https://sdk.cloud.google.com | bash
exec -l $SHELL             # PATH を反映
gcloud version
```

### Claude Code

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

`~/.local/share/claude/versions/` に本体が入り、`~/.local/bin/claude` から使えます。
`~/.local/bin` が PATH に無ければ通してください。

```bash
claude --version
claude doctor              # インストールの健全性を確認できる
```

> コンテナはこのホスト側の本体をそのまま使います（read-only でマウント）。
> **コンテナ側でのインストールは不要**で、バージョンも常にホストと一致します。
> 更新は `claude install stable` でホスト側だけ行えば、コンテナにも反映されます。

### 1台のサーバーを複数人で使う場合

コンテナ名・イメージ名・プロジェクト名は**ログインユーザーごとに自動で分かれます**
（`kosodate-agent-<ユーザー名>`）。同じサーバーで別のユーザーが同時に使っても衝突しません。

コンテナ内のユーザーはホストの uid / gid に合わせて作られます。合わせないと
bind mount したリポジトリや `~/.claude` に書き込めず、**uid 1000 の人以外が詰まります**。

ポートだけは自動で分けられないので、2人目以降は変えてください。

**ホスト側で、自分のクローンのリポジトリ直下**（`Makefile` があるディレクトリ）に
`.env` を作ります。コンテナの中ではありません。

```bash
cd tokyo-kosodate-navi        # リポジトリ直下
echo "DEV_PORT=8081" >> .env  # 8081 は空いている番号なら何でもよい
make agent-down && make agent-up   # 起動中なら作り直すと反映される
```

`.env` は git 管理外なので、他の人に影響しません。

確認するには `make next` を打ってください。コンテナの中で
`make dev アプリを起動 (http://localhost:8081)` のように出ます。

> 一時的に変えたいだけなら `make agent-up DEV_PORT=8081` でも構いません
> （コマンドラインの指定が `.env` より優先されます）。

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

### 3. GitHub の初期設定

コンテナの中から `git push` や PR 作成をするために必要です。

#### 3-1. 権限をもらう

管理者にリポジトリの **Collaborator** に追加してもらってください
（Settings → Collaborators）。招待メールが届くので承諾します。

#### 3-2. アクセストークン（PAT）を作る

GitHub はパスワードでの push を受け付けません。代わりにトークンを使います。

https://github.com/settings/tokens → **Generate new token (classic)**

チェックする権限は**2つ**です。

| スコープ | なぜ必要か |
|---|---|
| `repo` | クローン・push・PR の作成 |
| **`workflow`** | このリポジトリには `.github/workflows/` があるため |

> **`workflow` を忘れると push が拒否されます。** エラーはこう出ます。
> ```
> refusing to allow a Personal Access Token to create or update workflow
> `.github/workflows/ci.yml` without `workflow` scope
> ```
> 後から既存トークンに追加でき、トークンの値は変わりません。

有効期限は好みですが、切れたら push できなくなるので長めを推奨します。
**生成されたトークンは一度しか表示されません。** その場でコピーしてください。

#### 3-3. トークンを git に覚えさせる

`<あなたのGitHubユーザー名>` と `<トークン>` を置き換えて実行します。

```bash
printf 'https://<あなたのGitHubユーザー名>:<トークン>@github.com\n' > ~/.git-credentials
chmod 600 ~/.git-credentials
git config --global credential.helper store
```

> トークンはチャットやコミットに貼らないでください。このファイルの中だけに置きます。

#### 3-4. コミットに使う名前とメールを設定する

**メールアドレスは GitHub の noreply アドレスを使ってください。**
このリポジトリは public で、**コミット履歴は誰でも見られます**。
実アドレスを設定すると、それがそのまま公開されます。

noreply アドレスは https://github.com/settings/emails で確認できます
（「Keep my email addresses private」の項に `12345678+username@users.noreply.github.com`
という形式で書かれています）。

```bash
cd tokyo-kosodate-navi
git config user.name "あなたの名前"
git config user.email "<数字>+<ユーザー名>@users.noreply.github.com"
```

> `--global` を付けなければ、このリポジトリだけの設定になります。
> 他のリポジトリの設定に影響しません。
>
> 設定を忘れたまま数コミットしてしまった場合、後から履歴を書き換えるのは
> 面倒です（push 済みなら特に）。**最初に設定してください。**

#### 3-5. 確認

```bash
git ls-remote origin > /dev/null && echo "OK"
```

`OK` と出れば設定できています。

#### 補足: issue や PR の作成について

**`gh`（GitHub CLI）はコンテナの中にだけ入れてあります。ホストには入っていません。**
ホストで打つと `コマンド 'gh' が見つかりません` になりますが、正常です。

```bash
# ホスト側
gh issue list        # → command not found（これでよい）

make agent-shell     # コンテナに入ってから
gh issue list        # → 動く
gh pr create         # PR も作れる
```

ここで作ったトークンを `gh` が実行時に読むので、**コンテナ側で別途ログインする必要はありません**。

> **トークンをチャットに貼らないでください。** コンテナには
> `~/.git-credentials` がマウントされており、`gh` は実行時にそこから読みます。
> 「トークンを教えてください」と言われても渡す必要はありません。

> ホストでも `gh` を使いたい場合は各自で入れてください（このプロジェクトの手順では不要です）。

### 4. Claude Code にログイン

```bash
claude          # 初回はログインを求められる
```

コンテナはホストの Claude Code 本体と認証をそのまま使います。
インストールもログインもコンテナ側では不要です。

### 5. 承認について

Claude Code に作業を任せて離席するなら、起動時にこのフラグを付けます。
**設定ファイルは要りません。**

```bash
claude --dangerously-skip-permissions --remote-control kosodate
```

`--dangerously-skip-permissions` が承認を全て省きます。
`.claude/settings.local.json` に allow リストを書く必要はありません
（フラグを付けている限り、書いても何も変わりません）。

**必ずコンテナの中で使ってください。** 中でなら壊しても作り直せますし、
GCP は dev しか触れず、外向き通信も許可リストに限られています。
ホストで同じことをすると、ファイル全体と本人の GCP 権限が対象になります。

> フラグを付け忘れると、承認待ちで止まったまま進みません。
> スマホから見ていると気づきにくいので、`make agent-shell` の案内に出る
> コマンドをそのままコピーして使ってください。

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

# 承認なしで動かす。--remote-control を付けると Claude アプリから接続できる
claude --dangerously-skip-permissions --remote-control kosodate
```

`Ctrl-b` を押して離してから `d` を押すと tmux から抜けられます。
**抜けても Claude Code は動き続けます。** これが tmux を使う理由です。

> tmux が初めてなら [docs/tmux.md](tmux.md) を見てください。
> 最低限これだけ覚えれば足ります。
>
> ```
> tmux new -s claude       部屋を作る
> tmux attach -t claude    部屋に入る
> Ctrl-b  d                出る（作業は継続）
> Ctrl-b  [                さかのぼって読む（q で戻る）
> ```

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

詳細は [ADR 0008](adr/0008-scoped-credentials.md) と [ADR 0009](adr/0009-agent-container.md)。

## よくある詰まり

| 症状 | 原因と対処 |
|---|---|
| `make auth` が権限エラー | `serviceAccountTokenCreator` 未付与。管理者に依頼 |
| `make agent-up` が認証エラーで止まる | `make auth` か `claude` のログインが未実施 |
| コンテナ内から外部サイトに繋がらない | 許可リスト外。必要なら `docker/init-firewall.sh` に足して PR |
| 前は繋がったのに繋がらない | 許可リストは起動時に IP を解決している。`make agent-firewall` |
| E2E がブラウザのクラッシュで大量に落ちる | `/dev/shm` 不足。compose の `shm_size` を確認 |
| `make agent-up` で port is already allocated | 他ユーザーと衝突。`.env` に `DEV_PORT=8081` |
| コンテナ内でファイルを保存できない | uid のずれ。`make agent-down && make agent-up` で作り直す |
| `docker ps` が権限エラー | `sudo usermod -aG docker $USER` の後、**ログインし直す** |
| ホストで `gh: command not found` | **仕様**。`gh` はコンテナの中だけ。`make agent-shell` してから使う |
| `claude: command not found`（ホスト） | `~/.local/bin` が PATH に無い。通すか再ログイン |
| `gcloud: command not found` | インストール後に `exec -l $SHELL` で PATH を反映 |
| Claude Code を更新したい | ホストで `claude install stable`。コンテナにも反映される |
| tmux でキーを打っても反応しない | コピーモードのまま。`q` で抜ける → [tmux.md](tmux.md) |
| tmux でスクロールできない | 仕様。`Ctrl-b` `[` でコピーモードに入る → [tmux.md](tmux.md) |
| `make etl ENV=prod` が権限エラー | **仕様**。prod への書き込みは不可。ETL は dev で行う |
| `make deploy ENV=staging` が止まる | **仕様**。staging は main へのマージで自動デプロイ |

## メンバーを追加するとき（管理者向け）

```bash
./scripts/grant_member.sh newmember@example.com
```

本人の GCP 権限は変えません。付与するのは claude-dev になりすます権限だけです。
