import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Cloud Run 用に自己完結ビルドにする（Dockerfile のマルチステージ構成が前提）。
  output: "standalone",

  // 既存画面（cytoscape.js の単一ファイル）を静的ファイルとして public/debug.html に
  // 置いている。/debug（拡張子なし）でアクセスできるようにリライトする。
  // 中身のJSは相対パス /api/... を叩くので、app/api/[...path]/route.ts の
  // プロキシ経由で backend に届く（クライアントJSを書き換える必要がない）。
  async rewrites() {
    return [{ source: "/debug", destination: "/debug.html" }];
  },
};

export default nextConfig;
