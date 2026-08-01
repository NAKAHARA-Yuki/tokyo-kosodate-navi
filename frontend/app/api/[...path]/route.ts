import { fetchBackend } from "@/lib/backend";
import type { NextRequest } from "next/server";

// Edge runtime では google-auth-library（メタデータサーバへのアクセス）が動かないため明示する。
export const runtime = "nodejs";

/**
 * backend (`/api/...`) への catch-all プロキシ。
 *
 * ブラウザは同一オリジンの `/api/...` を叩き、ここが ID トークン付きで実体の
 * backend サービスに転送する（ADR 0013）。既存の cytoscape.js 画面が相対パス
 * `/api/...` を叩く形をそのまま踏襲できるようにするための橋渡し。
 */
async function proxy(request: NextRequest, path: string[]): Promise<Response> {
  const backendPath = `/api/${path.join("/")}${request.nextUrl.search}`;

  const init: RequestInit = {
    method: request.method,
    headers: { "content-type": request.headers.get("content-type") ?? "application/json" },
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.text();
  }

  try {
    const res = await fetchBackend(backendPath, init);
    const body = await res.text();
    return new Response(body, {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") ?? "application/json" },
    });
  } catch (reason) {
    const message = reason instanceof Error ? reason.message : "backend呼び出しに失敗しました";
    return new Response(JSON.stringify({ detail: message }), {
      status: 502,
      headers: { "content-type": "application/json" },
    });
  }
}

export async function GET(request: NextRequest, ctx: RouteContext<"/api/[...path]">) {
  const { path } = await ctx.params;
  return proxy(request, path);
}

export async function POST(request: NextRequest, ctx: RouteContext<"/api/[...path]">) {
  const { path } = await ctx.params;
  return proxy(request, path);
}
