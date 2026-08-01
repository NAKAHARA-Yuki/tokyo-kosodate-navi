import { fetchBackend } from "@/lib/backend";

// PR1: ID トークン認証で backend を呼べることの動作確認用ページ。
// 本物のトップページ（一覧ビュー）は後続PRで実装する。
//
// fetchBackend() の失敗をこの中で try/catch しているため、Next.js のビルド時
// 動的判定（ネットワークI/Oを検知して動的にする仕組み）が働かず、ビルド時に
// BACKEND_URL が無いとエラーメッセージごと静的にプリレンダーされてしまう
// （デプロイ後に BACKEND_URL を設定しても古い結果が返り続ける）。
// 常にリクエスト時に評価させるため明示的に force-dynamic にする。
export const dynamic = "force-dynamic";

export default async function Home() {
  let healthz: unknown;
  let error: string | null = null;

  try {
    const res = await fetchBackend("/api/healthz");
    if (!res.ok) {
      throw new Error(`backend が ${res.status} を返しました`);
    }
    healthz = await res.json();
  } catch (reason) {
    error = reason instanceof Error ? reason.message : "不明なエラー";
  }

  return (
    <main style={{ fontFamily: "sans-serif", padding: "2rem" }}>
      <h1>フロントエンド疎通確認（PR1）</h1>
      <p>
        このページは Server Component から ID トークン付きで backend の{" "}
        <code>/api/healthz</code> を呼び出しています（ADR 0013）。
      </p>
      {error ? (
        <p style={{ color: "red" }}>エラー: {error}</p>
      ) : (
        <pre>{JSON.stringify(healthz, null, 2)}</pre>
      )}
      <p>
        同一オリジンのプロキシ経由でも確認できます: <code>/api/healthz</code>
        （JSONを直接返すエンドポイントなのでページ遷移ではなくブラウザで直接開いて確認してください）
      </p>
    </main>
  );
}
