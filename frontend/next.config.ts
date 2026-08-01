import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Cloud Run 用に自己完結ビルドにする（Dockerfile のマルチステージ構成が前提）。
  output: "standalone",
};

export default nextConfig;
