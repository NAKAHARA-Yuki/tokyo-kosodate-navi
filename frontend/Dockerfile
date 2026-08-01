# backend (app/Dockerfile) と同様、公式の Cloud Run 向けサンプル
# (github.com/nextjs/deploy-google-cloud-run, github.com/vercel/next.js
# examples/with-docker) に倣ったマルチステージビルド。
# next.config.ts の output: "standalone" が前提。

FROM node:24-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:24-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NODE_ENV=production
RUN npm run build

FROM node:24-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=8080
ENV HOSTNAME="0.0.0.0"

COPY --from=builder --chown=node:node /app/public ./public
RUN mkdir .next && chown node:node .next
# standalone 出力に実行に必要な node_modules がトレースされて含まれるため、
# 通常の npm install は不要（イメージサイズを抑えられる）。
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static

USER node
EXPOSE 8080
CMD ["node", "server.js"]
