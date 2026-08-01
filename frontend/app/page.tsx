import { fetchBackend } from "@/lib/backend";
import { Button } from "@/components/dads/button";
import { Heading } from "@/components/dads/heading";
import { Link } from "@/components/dads/link";

// PR1〜2: ID トークン認証で backend を呼べることの動作確認 + デジタル庁デザインシステムの
// 導入確認用ページ。本物のトップページ（一覧ビュー）は後続PRで実装する。
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
    <main className="mx-auto max-w-2xl p-8">
      <Heading size="28" rule="4" className="mb-6">
        フロントエンド疎通確認（PR1〜2）
      </Heading>

      <p className="mb-4">
        このページは Server Component から ID トークン付きで backend の{" "}
        <code>/api/healthz</code> を呼び出しています（ADR 0013）。
        デジタル庁デザインシステム（<code>components/dads/</code>）の導入確認も兼ねています。
      </p>

      {error ? (
        <p className="mb-4 text-red-700">エラー: {error}</p>
      ) : (
        <pre className="mb-4 rounded-8 bg-solid-gray-50 p-4">
          {JSON.stringify(healthz, null, 2)}
        </pre>
      )}

      <p className="mb-4">
        同一オリジンのプロキシ経由でも確認できます: <code>/api/healthz</code>
        （JSONを直接返すエンドポイントなのでページ遷移ではなくブラウザで直接開いて確認してください）
      </p>

      <div className="flex gap-4">
        <Button size="md" variant="solid-fill">
          solid-fill
        </Button>
        <Button size="md" variant="outline">
          outline
        </Button>
        <Button size="md" variant="text">
          text
        </Button>
      </div>

      <p className="mt-4">
        <Link href="https://design.digital.go.jp/dads/react/" target="_blank">
          デジタル庁デザインシステム（React）
        </Link>
      </p>
    </main>
  );
}
