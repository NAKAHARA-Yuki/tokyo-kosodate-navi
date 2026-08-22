# 開発の進め方

チームで安全に速く進めるための約束事。迷ったらこのファイルの方針に寄せてください。

## 大原則

1. **判定ロジックに LLM を持ち込まない。** 制度の適用判定は BigQuery Graph の確定クエリのみ。
   詳細は [docs/adr/0001](docs/adr/0001-judgment-vs-llm-separation.md)。
2. **誤って「対象外を対象と見せる」変更を最も警戒する。** ユーザーの不利益に直結します。
   年齢・地域の絞り込みを変えるときは必ずテストと実データでの確認をセットにしてください。
3. **推測でコードや文書を書かない。** モデル名・API仕様・データの中身は動かして確かめる。

## ブランチ戦略

**GitHub Flow + タグでの本番リリース**を採用しています。
`develop` や `release` ブランチは作りません（リリース列が1本しかなく、二重管理になるだけのため）。

```
main ← 常にデプロイ可能。直接コミットしない
 ├─ feat/timeline-view      新機能
 ├─ fix/age-filter-leak     バグ修正
 ├─ docs/adr-graph-schema   ドキュメント
 ├─ refactor/etl-split      挙動を変えないリファクタ
 ├─ chore/bump-deps         雑務・依存更新
 └─ hotfix/xxx              本番の緊急修正（後述）
```

ブランチ名は `<種別>/<英小文字とハイフンの短い説明>`。トピックブランチは短命に保ち、
長生きさせるとコンフリクトと巨大 PR の原因になります。

### main へのマージは Squash merge

PR を1コミットにまとめて `main` に入れます。

- `main` の履歴が「1行1変更」で読める
- 切り戻しが `git revert <commit>` 1発で済む
- PR 内の試行錯誤のコミットが `main` を汚さない

GitHub のリポジトリ設定で **Squash merge のみ有効**にしてください
（Merge commit / Rebase merge は無効化）。

### 環境への反映

```
PR                    → CI（lint / test / E2E(スタブ) / Docker build）
main へ Squash merge  → staging へ自動デプロイ → E2E(staging 実データ)
v*.*.* タグを push    → 承認 → 本番へデプロイ（backend → frontend）→ スモーク
```

**本番は「main の HEAD」ではなく「タグを打ったコミット」を出します。**
こうしないと本番に何が入っているか特定できず、切り戻し先も分かりません。

### リリース手順

staging での確認が済んだら、`main` でタグを打ちます。

```bash
git checkout main && git pull
git tag -a v1.2.0 -m "タイムラインビューを追加"
git push origin v1.2.0
```

タグ push で本番デプロイのワークフローが起動し、
GitHub Environments（`production`）の承認待ちになります。

**承認は1リリースにつき1回です。** backend（`deploy-prod`）にだけ承認ゲートを置き、
frontend（`deploy-frontend-prod`）は承認後に続けて走ります。
frontend 側にもゲートを置くと、1回目だけ承認して2回目を忘れたときに
「backend だけ新しく frontend が古い」状態を作れてしまうため、あえて分けていません。

バージョンは [セマンティックバージョニング](https://semver.org/lang/ja/)に準じます。

| 上げる桁 | 例 |
|---|---|
| MAJOR | 互換性のない変更（API の破壊的変更、データモデルの非互換変更） |
| MINOR | 後方互換のある機能追加 |
| PATCH | バグ修正のみ |

### 切り戻し

前のタグを `workflow_dispatch` の `prod` で再デプロイします。
Cloud Run のラベルに `release` と `commit` を入れているので、
今の本番がどのタグかは `make url` / GCP コンソールから確認できます。

### 本番の緊急修正（hotfix）

`main` に未リリースの変更が溜まっている状態で本番だけ直したいときは、
**タグから枝を切ります**（main から切ると未リリース分を巻き込むため）。

```bash
git checkout -b hotfix/fix-age-filter v1.2.0
# 修正してコミット
git tag -a v1.2.1 -m "年齢絞り込みの不具合を修正"
git push origin v1.2.1          # → 本番へ
```

修正内容は忘れずに `main` にも入れてください（PR を出すか cherry-pick）。

## コミットメッセージ

[Conventional Commits](https://www.conventionalcommits.org/ja/) に準拠します。

```
<種別>(<スコープ>): <日本語で要約>

<なぜこの変更が必要かの説明（任意だが推奨）>
```

種別は `feat` / `fix` / `docs` / `refactor` / `test` / `chore` / `perf`。
スコープは `etl` / `api` / `ui` / `graph` / `ci` など。

```
feat(api): マッチ結果に判定理由を含める
fix(api): 年齢絞り込みで推定値を使わず素通りしていた問題を修正
docs(adr): 年齢推定の設計判断を記録
```

**書く内容**: 何をしたかだけでなく、なぜ必要だったかを本文に残す。
半年後の自分とチームメイトが読む前提で書いてください。

## Issue

- **着手する issue には必ず自分をアサインする。** アサインが「今それを誰が持っているか」を
  表す唯一の印です。付けずに始めると、他の人が同じものに手を出します
- **すでに誰かがアサインされている issue には触らない。** 手が空いていても、
  横から進めると作業が重複し、コンフリクトと無駄なやり直しになります。
  引き取りたいときは issue にコメントして、アサインされている人の返事を待ってください

## Pull Request

- 1 PR = 1 つの関心事。レビューできる大きさに保つ（目安 400 行以内）
- テンプレート（`.github/pull_request_template.md`）の項目を埋める
- CI が green であること（`main` の ruleset で必須化済み）
- レビューコメントは解決してからマージする
- **必ず誰かをアサインする**（レビュー依頼は複数人でよいが、アサインは1人に絞る）
- **レビューを依頼されたら、Approve するか指摘事項を PR のコメントに書く。**
  口頭やチャットで済ませない。後から経緯を追えるのは PR に残っている記録だけ
- **approve が最低1件必要**（2026-08-04 に 0 から変更）。
  GitHub は自己承認を許可しないので、**自分の PR は必ず誰かに見てもらう**ことになる。
  ひとり体制の間は 0 にしていたが、メンバーが増えたので本来の運用に戻した

### レビュー依頼は「出したか」ではなく「いま飛んでいるか」で見る

**アサインとレビュー依頼は別物。** アサインは「誰が持っているか」の印で、
**それだけでは相手の「Review requested」の一覧に載らない。**

```bash
# PR を出したら必ず両方やる
gh api -X POST repos/:owner/:repo/issues/<n>/assignees   -f 'assignees[]=<相手>'
gh api -X POST repos/:owner/:repo/pulls/<n>/requested_reviewers -f 'reviewers[]=<相手>'
```

**`gh pr edit --add-reviewer` は使えない。** `gh` のトークンが `repo` と `workflow` しか
持っておらず、`login` / `name` / `slug` の解決に `read:org` を要求されて落ちる。

```
GraphQL: Your token has not been granted the required scopes to execute this query.
The 'login' field requires one of the following scopes: ['read:org']
```

**`gh pr list --search "review-requested:@me"` のほうは、同じトークンでも動く。**
検索は別系統のクエリで、`read:org` を要求しない。ただし**依頼が消えれば結果も消える**ので、
0件は「依頼が無い」だけを意味する。**「自分がもう見た PR」は最初から出てこない。**

- **指摘を直して push したら、その場でレビューを依頼し直す**
  （ruleset の `dismiss_stale_reviews_on_push` で approve が外れる）。
  **コメントを書くだけでは足りない。** コメントは「Review requested」の一覧に載らないので、
  相手から見ると対応済みだと分からない
- **レビューが提出されると、GitHub はその人への依頼を自動で消す。**
  つまり `CHANGES_REQUESTED` を受けて直した PR は、**必ず依頼が空になっている**

**溜まっていないかは、出した記憶ではなくこれで確かめる。**

```bash
for n in $(gh pr list --state open --json number -q '.[].number'); do
  echo "#$n 依頼: $(gh api repos/:owner/:repo/pulls/$n -q '[.requested_reviewers[].login]|join(",")')"
done
```

> **2回とも実害が出ている。** #127 は作成時に依頼を出しておらず**7日間レビューが0件**、
> #142 / #128 は指摘対応後に依頼し直しておらず、**直したのに止まったまま**だった。
> どちらも「アサインしてあるから伝わっているはず」と思い込んでいたのが原因。
- スクリーンショットを貼る（UI 変更時は必須）

### レビューで必ず見る点

- [ ] 判定ロジックに LLM が混入していないか
- [ ] 年齢・地域の絞り込みが `effective_*` カラムを使っているか
- [ ] 推定値（`age_source='inferred'`）をユーザーに断定的に見せていないか
- [ ] 「なぜ」がコメント/コミットに残っているか
- [ ] データモデルを変えたなら `docs/data-model.md` と `CLAUDE.md` も更新されているか
- [ ] 本番 BigQuery を壊す操作が意図せず入っていないか
- [ ] インフラ設定を手で変えた PR なら、それを設定している `deploy.yml` / `Makefile` も
      直っているか（直っていないと次のデプロイで元に戻る）

## テスト

```bash
make test       # ユニット・API結合（GCP不要）
make e2e        # E2E（ブラウザ操作。スタブ版アプリを自動起動。GCP不要）
make check      # 全部
make cov        # 一度も実行されていない経路を探す（数値目標ではない）
make mutations  # わざとバグを入れて、落ちるべきテストが落ちるか確かめる
```

- **純粋ロジック（`src/age_rules.py`、ETL の変換関数）は必ずテストを書く。**
  ここが壊れるとマッチ精度が静かに劣化して気づけません。
- API は BigQuery をモックした結合テストで、レスポンス形状と絞り込み条件を担保します。
- **画面の挙動は E2E で守る。** レイアウト崩れ・ラベルのはみ出し・タブ切り替えの不具合は
  ユニットテストをすり抜けて本番に出た実績があります。
- **画面を足したら `e2e/test_accessibility.py` に追加する。** axe で WCAG 2.1 AA 相当の
  違反ゼロを必須にしています（[ADR 0016](docs/adr/0016-accessibility-baseline.md)）。
  デザインシステムの `Heading` はスタイル用の `<div>` で、見出し要素は `HeadingTitle` です。
  取り違えると**見た目は見出しなのにアクセシビリティツリーには何も無い**状態になります。
- **重要な不変条件を守るテストを足したら、`scripts/check_mutations.py` に変異を1つ足す。**
  「通っていること」と「壊れたときに落ちること」は別で、このリポジトリでは
  テストが緑のままバグが本番に出た実績があります
  （→ [docs/test-effectiveness.md](docs/test-effectiveness.md)）。

### E2E は2段構え

同じテストコードを、スタブ版と実データの両方に対して実行します（→ [ADR 0005](docs/adr/0005-e2e-strategy.md)）。

| タイミング | 対象 |
|---|---|
| PR | スタブ版（`e2e/server.py` が BigQuery と Gemini を差し替え） |
| main マージ後 | **staging の実データ**（`E2E_BASE_URL` を指定） |

**staging の実データでも通る書き方にしてください。**

- ❌ 「制度が3件表示される」のような件数依存
- ⭕ 「1件以上表示される」「制度ノードは1つだけになる」のような構造依存
- 特定の制度名に依存しない（自治体によってデータが変わるため）

## Lint / フォーマット

`ruff` に統一しています。PR 前に:

```bash
make fmt && make lint
```

## データパイプラインを変更するとき

`make etl` は**対象環境の BigQuery を上書きします**（`WRITE_TRUNCATE`）。

手元から書き込めるのは **dev だけ**です。staging と prod は読み取りのみに絞った
サービスアカウントで動いており、`make etl ENV=prod` は権限エラーで落ちます
（→ [ADR 0008](docs/adr/0008-scoped-credentials.md)）。これは仕様です。

1. まずローカルのキャッシュ JSON で `transform()` の出力を確認する
2. 件数が想定通りか（`benefits=7812` など）ログで確認
3. その上で `make etl ENV=dev && make graph ENV=dev && make verify ENV=dev`
4. アプリの表示まで確認してから PR

staging / prod のデータ更新が必要な場合は、権限を持つメンバーが実施します。
勝手に権限を広げないでください。

スキーマを変えた場合は `make graph` の再実行が必須です（PROPERTY GRAPH は列を参照しているため）。

## 依存関係を変更するとき

本番イメージに入るものだけロックしています。ETL・開発・CI 側はロックせず、
**メジャー版の上限だけ**付けています（[ADR 0007](docs/adr/0007-dependency-locking.md)）。

| ファイル | 役割 |
|---|---|
| `app/requirements.in` | **人が編集する。** 本番アプリの直接依存だけを書く |
| `app/requirements.lock` | 自動生成。推移依存まで含めてハッシュ付きで固定。**手で編集しない** |
| `requirements.txt` / `requirements-dev.txt` | ETL・開発・CI 用。上限だけ付ける（`pandas>=2.2.0,<4` など） |

### ロックする側（`app/`）

`app/requirements.in` を変えたら**必ず**ロックを再生成してください。

```bash
make lock          # docker で python:3.12-slim を使って再生成
git diff app/requirements.lock   # 意図しない巻き添え更新が無いか確認
```

Dockerfile は `--require-hashes` 付きで入れるため、ロックを更新し忘れるとビルドが失敗します
（気づかないまま別物が本番に出るよりは良い、という判断です）。

**なぜロックするか**: 緩い指定のままだと、ビルドした日によって中身が変わります。
実際に prod と staging で `google-genai` のバージョンがずれ、staging だけ Gemini 呼び出しが
503 になる事故が起きました。同じコードなのに環境によって壊れるため、原因の特定に時間がかかります。

**なぜ docker 経由か**: ロックは解決した Python バージョンに紐づきます。
ローカル（3.14）で作ると本番（3.12）で入らないロックができます。

### ロックしない側（`requirements.txt` / `requirements-dev.txt`）

上限を上げるのは**意図的な変更**として扱ってください。「テストが落ちたので上限を外す」ではなく、
新しいメジャー版に合わせてコードを直したうえで上限を上げます。

追従は Dependabot（`.github/dependabot.yml`）が週1でまとめて PR にします。
**まとめるのはマイナー・パッチだけで、メジャーは1件ずつ来ます。**
まだ周辺が追いついていないメジャーが1つ混ざるだけで、他の更新まで巻き添えで止まるためです
（#88 で実際に起きました）。

**Dependabot の PR が `app/requirements.in` を触っていたら、マージ前に `make lock` を回して
ロックも同じ PR に含めてください。** Dependabot はロックを再生成できません
（`python:3.12-slim` の中で作る必要があるため）。

### メジャー更新の PR が落ちたとき

**「テストが落ちたので上限を外す」の逆で、通らないなら上げないのが既定**です。
落ちた理由が「まだ周辺のライブラリが対応していない」なら、その PR は閉じて構いません。

ただし **閉じただけで放置しない。閉じた版が再提案されるとは限りません。**
待っているものが「その依存の新しい版」ではなく**別のパッケージの追従**であるとき、
その依存が動かない限り提案は来ず、塞がったままになります。

実例が #99 です。eslint 10 と TypeScript 7 が入らない原因は eslint 側ではなく、
`eslint-config-next` が同梱しているプラグインでした。eslint が 10.8.1 のまま
`eslint-config-next` だけが直っても、Dependabot からは何も来ません。

閉じるときは **何を待っているのかと、どう試し直すのかを issue に残してください。**
数分で判定できる手順まで書いてあれば、次に触る人が測り直せます。

恒久的に上げないと決めたときだけ `ignore` を足してください（理由をコメントで残すこと）。

## セキュリティ

- **コミットのメールアドレスは GitHub の noreply を使う。**
  このリポジトリは public で、コミット履歴は誰でも見られる。実アドレスを設定すると公開される。
  設定方法は [docs/onboarding.md](docs/onboarding.md) を参照
- サービスアカウントキーや認証情報を**絶対にコミットしない**（`.gitignore` 済みだが目視でも確認）
- 個人情報は扱わない設計を維持する。プロフィールはクライアント側の localStorage に保持し、
  サーバーには永続化しない
- 公開 URL は認証不要のため、管理系のエンドポイントを足さない

## 困ったら

- 設計の背景 → `docs/adr/`
- データの意味 → `docs/data-model.md`
- 過去に踏んだ落とし穴 → `CLAUDE.md` の「落とし穴」節
- テストが何を守れていて何を守れていないか → `docs/test-effectiveness.md`
