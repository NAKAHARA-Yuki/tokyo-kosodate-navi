# kosodate-frontend

backend（`app/`, FastAPI）とは別の Cloud Run サービスとして動く Next.js フロントエンド。
`ADR 0013`（`docs/adr/0013-backend-sa-only-access.md`）により、backend への呼び出しは
サーバサイド（Route Handler / Server Component）から ID トークン付きで行う必要があり、
ブラウザから backend を直接叩くことはできない。

## Node のバージョン

**実行時の Node と `@types/node` のメジャーを揃える**（issue #93）。

| | 版 |
|---|---|
| `frontend/Dockerfile` | `node:24-slim` |
| `.github/workflows/ci.yml` | `node-version: "24"` |
| `docker/Dockerfile`（開発コンテナ） | NodeSource `node_24.x` |
| `package.json` の `@types/node` | **`^24`** |

`@types/node` は「どの Node で動かすか」の宣言なので、実行環境より**新しい**メジャーを
指していると、**その版で入った API を型が許してしまう**（実行時に落ちるまで気づけない）。
逆に古いと、使える API が型で弾かれる。

Dependabot は `@types/node` の新メジャーを個別 PR で提案してくる。
**上げるのは実行時の Node を上げるときだけ**で、そのときは上の4つを同時に動かす。
単独で上げた PR は閉じてよい。

## ローカル開発

backend への認証を通すため、`gcloud run services proxy` で backend をローカルにプロキシする。

```bash
# 別ターミナルで、dev の backend をプロキシする（トークン付与を肩代わりしてくれる）
gcloud run services proxy kosodate-graph-viewer-dev \
  --project opendatahackathon-503500 --region asia-northeast1 --port 8080

# frontend 側は認証不要な localhost の backend を見るだけでよい
cd frontend
BACKEND_URL=http://localhost:8080 BACKEND_REQUIRES_AUTH=false npm run dev
```

`BACKEND_REQUIRES_AUTH=false` は `gcloud run services proxy` が認証を肩代わりしている
ときだけ使う。実際の Cloud Run 環境（dev/staging/prod）では設定しない
（既定で ID トークン付き呼び出しになる）。

## デプロイ

`make deploy-frontend ENV=dev`（backend の `make deploy` と同じ運用。dev のみ手元から可能。
staging は main マージ、prod は `v*.*.*` タグ push）。

## 環境変数

| 変数 | 用途 |
|---|---|
| `BACKEND_URL` | backend (FastAPI) の Cloud Run URL。ID トークンの audience にも使う |
| `BACKEND_REQUIRES_AUTH` | `false` にすると ID トークンを付けない（ローカルで `services proxy` を使うときのみ） |

## フォント

日本語は **Noto Sans JP**（デジタル庁デザインシステムの指定。`@digital-go-jp/tailwind-theme-plugin`
の `--font-sans` も先頭がこれ）。`app/layout.tsx` の `next/font/google` で読み込む。

- **ブラウザから Google へはリクエストが飛ばない。** `next/font` がビルド時に取得して
  `.next/static/media/` に置き、そこから自前で配る（[ADR 0010](../docs/adr/0010-no-runtime-cdn.md)
  の「実行時に外部 CDN から取らない」を満たす）。ただし **ビルド時には外へ出る**ので、
  ネットワークの無い環境ではビルドできない
- `subsets: ["latin"]` を指定しているが、**日本語のグリフはこれで入る**。
  `next/font` のフォントメタデータに `"japanese"` という subset 名は存在せず、
  日本語は unicode-range で分割されたチャンクとして落ちてくる（実測: 125ファイル / 5.3MB）。
  ブラウザは表示に必要なチャンクだけを取るので、トップページで実際に転送されるのは
  18ファイル / 約365KB（実測）
- Docker イメージには `.next/static` の COPY で入る（`Dockerfile` 側の追加対応は不要）

## デザインシステム（`components/dads/`）

[デジタル庁デザインシステム](https://design.digital.go.jp/dads/react/)を使う。
色・タイポグラフィ等のトークンは `@digital-go-jp/tailwind-theme-plugin`（`app/globals.css` で
`@import` 済み）で Tailwind のユーティリティクラスとして使える（例: `bg-key-900`,
`text-std-24B-150`, `rounded-8`）。

コンポーネント本体（Button, Heading, Link 等）は
[design-system-example-components-react](https://github.com/digital-go-jp/design-system-example-components-react)
の React 実装をコピーしたもの（MIT License）。**npm パッケージとしては公開されていない**
（`package.json` に `publishConfig` はあるが実際には npm に無い。2026-08-01 時点でサンプル
スニペット集という位置づけ）ため、`npm install` ではなく必要なコンポーネントだけを
`components/dads/` に個別コピーしている。

コンポーネントを追加するときは:
1. [`src/components/<Name>/<Name>.tsx`](https://github.com/digital-go-jp/design-system-example-components-react/tree/main/src/components) を取得する
2. `components/dads/<name>.tsx` としてコピーし、ファイル冒頭に出典コミットを記録する
3. 相対importを `./slot` 等（このディレクトリ内）に直す
4. 元がTailwind v3 / React 18向けなので、`strict: true` のTypeScriptで型エラーが出た場合は調整する
   （Slot/Button/Heading/Link で実施済み。詳細は各ファイルの差分参照）
