import { fetchBackend } from "@/lib/backend";
import type { Benefit } from "@/lib/types";
import { Heading, HeadingTitle } from "@/components/dads/heading";
import { ChipLabel } from "@/components/dads/chip-label";
import { Link } from "@/components/dads/link";

// トップページ = 一覧ビュー。グラフ表示はせず、項目とサマリーだけのカード一覧にする
// （issue #33 のヒアリングで決めたUX方針）。属性による絞り込み（居住地・月齢）は
// まだUIから渡せないため、現時点では backend の既定の並び（title順、上限30件）をそのまま表示する。
//
// force-dynamic の理由は lib/backend.ts 参照（BACKEND_URL未設定時にビルド時の
// エラーが静的にプリレンダーされてしまう問題への対処）。
export const dynamic = "force-dynamic";

async function getBenefits(): Promise<Benefit[]> {
  const res = await fetchBackend("/api/benefits");
  if (!res.ok) {
    throw new Error(`backend が ${res.status} を返しました`);
  }
  return res.json();
}

export default async function Home() {
  let benefits: Benefit[] = [];
  let error: string | null = null;

  try {
    benefits = await getBenefits();
  } catch (reason) {
    error = reason instanceof Error ? reason.message : "不明なエラー";
  }

  return (
    <main className="mx-auto max-w-3xl p-6">
      <Heading size="28" rule="4" className="mb-6">
        <HeadingTitle level="h1">今受けられる子育て支援制度</HeadingTitle>
      </Heading>

      {error ? (
        <p className="text-red-700">読み込みに失敗しました: {error}</p>
      ) : benefits.length === 0 ? (
        <p>制度が見つかりませんでした。</p>
      ) : (
        <ul className="flex flex-col gap-4">
          {benefits.map((b) => (
            <li key={b.benefit_id} className="rounded-8 border border-solid-gray-300 p-4">
              {/* benefit_id には `+` が含まれる（例: psid3.0+1000020132152+1+UM5036）。
                  エンコードせずにURLに載せると、詳細ページ側がクエリに載せ直したときに
                  `+` がスペースとして解釈されて backend が 404 を返す。 */}
              <Link
                href={`/benefits/${encodeURIComponent(b.benefit_id)}`}
                className="text-std-18B-160"
              >
                {b.title}
              </Link>

              <div className="mt-2 flex flex-wrap gap-2">
                <ChipLabel variant="outlined" color="blue">
                  {b.category}
                </ChipLabel>
                {b.area_name && <ChipLabel variant="text">{b.area_name}</ChipLabel>}
                {b.is_free && (
                  <ChipLabel variant="filled-1" color="green">
                    無料
                  </ChipLabel>
                )}
                {b.has_free_text_conditions && (
                  <ChipLabel variant="filled-1" color="yellow">
                    要確認の条件あり
                  </ChipLabel>
                )}
              </div>

              <p className="mt-2 text-solid-gray-800">{b.summary}</p>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
