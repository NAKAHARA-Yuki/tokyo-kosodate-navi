import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * backend 呼び出しヘルパーの検証（issue #64）。
 *
 * **この分岐は E2E では片側しか通っていなかった。** E2E は
 * `BACKEND_REQUIRES_AUTH=false` でスタブに向けて動かすため、
 * ID トークンを付ける経路（本番で必ず通る方）が一度も実行されていない。
 * 認証が外れても E2E は緑のままなので、ここで直接押さえる。
 *
 * `BACKEND_URL` と `BACKEND_REQUIRES_AUTH` は**モジュール読み込み時**に
 * 定数へ束縛される。テストごとに環境を変えるには `resetModules()` してから
 * 動的 import する必要がある（先に import すると最初の値のまま固定される）。
 */

async function loadBackend(env: Record<string, string | undefined>) {
  vi.resetModules();
  for (const [key, value] of Object.entries(env)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  return import("./backend");
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("backendUrl", () => {
  it("BACKEND_URL が無ければ、何を渡す前に落ちる", async () => {
    const { backendUrl } = await loadBackend({ BACKEND_URL: undefined });
    expect(() => backendUrl("/api/healthz")).toThrow("BACKEND_URL");
  });

  it("パスを BACKEND_URL に結合する", async () => {
    const { backendUrl } = await loadBackend({ BACKEND_URL: "https://backend.example.com" });
    expect(backendUrl("/api/benefits")).toBe("https://backend.example.com/api/benefits");
  });

  it("クエリ文字列を落とさない", async () => {
    const { backendUrl } = await loadBackend({ BACKEND_URL: "https://backend.example.com" });
    expect(backendUrl("/api/benefits?area_code=131067&limit=40")).toBe(
      "https://backend.example.com/api/benefits?area_code=131067&limit=40",
    );
  });

  it("benefit_id のエンコードを保つ", async () => {
    // 実データの benefit_id は `+` を含む。二重エンコードで backend が 404 を返す
    // 不具合が実際に出ているため、ここを素通りさせないことを固定する（issue #64）。
    const { backendUrl } = await loadBackend({ BACKEND_URL: "https://backend.example.com" });
    expect(backendUrl("/api/subgraph?benefit_id=psid3.0%2B3sai%2B1%2BUM1")).toBe(
      "https://backend.example.com/api/subgraph?benefit_id=psid3.0%2B3sai%2B1%2BUM1",
    );
  });

  it("BACKEND_URL 側のパスは引き継がない（先頭 / の絶対パス指定のため）", async () => {
    // 現状の挙動を明示しておく。将来 backend をサブパスに置くなら、
    // ここが落ちることで気づける。
    const { backendUrl } = await loadBackend({ BACKEND_URL: "https://backend.example.com/base/" });
    expect(backendUrl("/api/healthz")).toBe("https://backend.example.com/api/healthz");
  });
});

describe("fetchBackend（BACKEND_REQUIRES_AUTH=false）", () => {
  it("ID トークンを付けず、そのまま取りに行く", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);

    const { fetchBackend } = await loadBackend({
      BACKEND_URL: "https://backend.example.com",
      BACKEND_REQUIRES_AUTH: "false",
    });
    await fetchBackend("/api/healthz");

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://backend.example.com/api/healthz");
    expect(init?.headers).toBeUndefined();
    expect(init?.cache).toBe("no-store");
  });
});

describe("fetchBackend（既定＝認証あり）", () => {
  /** google-auth-library を差し替え、audience と付与ヘッダを覗く。 */
  function stubGoogleAuth() {
    const seen: { audience?: string; url?: string } = {};
    vi.doMock("google-auth-library", () => ({
      GoogleAuth: class {
        async getIdTokenClient(audience: string) {
          seen.audience = audience;
          return {
            async getRequestHeaders(url: string) {
              seen.url = url;
              return new Headers({ authorization: "Bearer dummy-id-token" });
            },
          };
        }
      },
    }));
    return seen;
  }

  it("BACKEND_REQUIRES_AUTH が未設定なら認証する（既定で認証あり）", async () => {
    const seen = stubGoogleAuth();
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);

    const { fetchBackend } = await loadBackend({
      BACKEND_URL: "https://backend.example.com",
      BACKEND_REQUIRES_AUTH: undefined,
    });
    await fetchBackend("/api/benefits");

    const [, init] = fetchMock.mock.calls[0];
    expect(new Headers(init.headers).get("authorization")).toBe("Bearer dummy-id-token");
    expect(seen.audience).toBe("https://backend.example.com");
  });

  it('"false" 以外の値では認証を外さない', async () => {
    // `!== "false"` なので "0" や "no" では外れない。切りたい人が
    // 別の綴りを書いて**黙って認証付きのまま**になる、を固定しておく。
    stubGoogleAuth();
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);

    const { fetchBackend } = await loadBackend({
      BACKEND_URL: "https://backend.example.com",
      BACKEND_REQUIRES_AUTH: "0",
    });
    await fetchBackend("/api/benefits");

    const [, init] = fetchMock.mock.calls[0];
    expect(new Headers(init.headers).get("authorization")).toBe("Bearer dummy-id-token");
  });

  it("audience はオリジンのみ（パスを含めない）", async () => {
    // Cloud Run の ID トークンは audience がサービスの URL と一致する必要がある。
    // ここにパスが混ざるとトークンが弾かれる。
    const seen = stubGoogleAuth();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}")));

    const { fetchBackend } = await loadBackend({
      BACKEND_URL: "https://backend.example.com/base/",
      BACKEND_REQUIRES_AUTH: undefined,
    });
    await fetchBackend("/api/benefits");

    expect(seen.audience).toBe("https://backend.example.com");
  });

  it("呼び出し側のヘッダを消さない", async () => {
    stubGoogleAuth();
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);

    const { fetchBackend } = await loadBackend({
      BACKEND_URL: "https://backend.example.com",
      BACKEND_REQUIRES_AUTH: undefined,
    });
    await fetchBackend("/api/support/draft-review", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}",
    });

    const [, init] = fetchMock.mock.calls[0];
    const headers = new Headers(init.headers);
    expect(headers.get("content-type")).toBe("application/json");
    expect(headers.get("authorization")).toBe("Bearer dummy-id-token");
    expect(init.method).toBe("POST");
  });

  it("キャッシュしない（no-store）", async () => {
    // 制度データは backend 側で更新される。Next.js の既定でキャッシュされると
    // ETL を回しても画面が古いままになる。
    stubGoogleAuth();
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);

    const { fetchBackend } = await loadBackend({
      BACKEND_URL: "https://backend.example.com",
      BACKEND_REQUIRES_AUTH: undefined,
    });
    await fetchBackend("/api/benefits");

    expect(fetchMock.mock.calls[0][1].cache).toBe("no-store");
  });
});
