import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // 外部ライブラリのミニファイ済みビルド（docs/adr/0010 で取り込んでいるもの）。
    // 自分で書いたコードではないので lint の対象にしない。
    "public/cytoscape.min.js",
  ]),
]);

export default eslintConfig;
