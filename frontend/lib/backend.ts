import { GoogleAuth, IdTokenClient } from "google-auth-library";

/**
 * backend (FastAPI, kosodate-graph-viewer-*) を呼ぶための共通ヘルパー。
 *
 * ADR 0013 により、backend への呼び出しはフロントエンド専用サービスアカウント
 * （kosodate-frontend@...）に限定されている。ブラウザにはメタデータサーバが無く
 * ID トークンを安全に持てないため、この関数は **サーバサイドからのみ**呼ぶこと
 * （Server Component から直接、または app/api/[...path]/route.ts 経由）。
 *
 * ローカル開発で `gcloud run services proxy` を使う場合など、backend 側が
 * 認証を要求しない場合は BACKEND_REQUIRES_AUTH=false を設定する。
 */

const BACKEND_URL = process.env.BACKEND_URL;
const REQUIRES_AUTH = process.env.BACKEND_REQUIRES_AUTH !== "false";

let idTokenClientPromise: Promise<IdTokenClient> | null = null;

function getIdTokenClient(targetAudience: string): Promise<IdTokenClient> {
  if (!idTokenClientPromise) {
    const auth = new GoogleAuth();
    idTokenClientPromise = auth.getIdTokenClient(targetAudience);
  }
  return idTokenClientPromise;
}

export function backendUrl(path: string): string {
  if (!BACKEND_URL) {
    throw new Error("環境変数 BACKEND_URL が設定されていません");
  }
  return new URL(path, BACKEND_URL).toString();
}

export async function fetchBackend(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const url = backendUrl(path);

  if (!REQUIRES_AUTH) {
    return fetch(url, { ...init, cache: "no-store" });
  }

  // audience は Cloud Run サービスの URL そのもの（BACKEND_URL）を使う。
  const client = await getIdTokenClient(new URL(BACKEND_URL!).origin);
  const authHeaders = await client.getRequestHeaders(url);

  const headers = new Headers(init?.headers);
  authHeaders.forEach((value, key) => headers.set(key, value));

  return fetch(url, { ...init, headers, cache: "no-store" });
}
