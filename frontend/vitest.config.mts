import { defineConfig } from "vitest/config";

// frontend のユニットテスト（issue #64）。
//
// **environment は node。** いま対象にしている lib/backend.ts はサーバ側だけで動く
// モジュールで、DOM も React も使わない。jsdom や @testing-library を入れるのは
// 最初のコンポーネントのテストを書くときでよい（Next.js の vitest ガイドが挙げている
// 依存一式は、その時点で足す）。
//
// `@/lib/backend` のようなエイリアスは Vite が tsconfig から解決する。
// ガイドは vite-tsconfig-paths プラグインを使っているが、Vite 8 からは
// resolve.tsconfigPaths が組み込みになっており、プラグインを入れると
// 「不要になった」と警告が出る。
//
// E2E（Playwright）は e2e/ にあり pytest から動かす。こちらは分離しておく。
export default defineConfig({
  resolve: { tsconfigPaths: true },
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts", "components/**/*.test.ts", "app/**/*.test.ts"],
  },
});
