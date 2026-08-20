import { AgeChip } from "@/components/age-chip";
import { fetchBackend } from "@/lib/backend";
import {
  fromSearchParams,
  hasAnyAttribute,
  toSearchParams,
} from "@/lib/profile";
import type { Benefit, MatchedBenefit, MatchResponse } from "@/lib/types";
import { Heading, HeadingTitle } from "@/components/dads/heading";
import { ChipLabel } from "@/components/dads/chip-label";
import { Link } from "@/components/dads/link";

// トップページ = 一覧ビュー。グラフ表示はせず、項目とサマリーだけのカード一覧にする
// （issue #33 のヒアリングで決めたUX方針）。
//
// **属性は URL のクエリで受ける**（issue #53）。入力欄はここには置かず /settings に集約し、
// あちらが組み立てた URL でここへ来る。URL を正にしているので、共有・リロードで同じ結果になる。
// 判定は `/api/benefits/match` のサーバ側確定クエリで、LLM は挟まない（ADR 0001）。
// 属性が無ければ従来どおり `/api/benefits` の既定の並び（title順）を出す。
//
// force-dynamic の理由は lib/backend.ts 参照（BACKEND_URL未設定時にビルド時の
// エラーが静的にプリレンダーされてしまう問題への対処）。
export const dynamic = "force-dynamic";

async function getBenefits(path: string): Promise<{
  benefits: Benefit[] | MatchedBenefit[];
  labels: Record<string, string>;
}> {
  const res = await fetchBackend(path);
  if (!res.ok) {
    // 握りつぶさず投げる。app/error.tsx が受け取って利用者向けの画面を出し、
    // このメッセージ自体はサーバのログにだけ残る（本番ビルドではクライアントに渡らない）。
    throw new Error(`backend が ${res.status} を返しました`);
  }
  const body = await res.json();
  // /api/benefits は配列、/api/benefits/match は { count, benefits } を返す（#66 で統一予定）
  if (Array.isArray(body)) return { benefits: body, labels: {} };
  const matched = body as MatchResponse;
  return { benefits: matched.benefits, labels: matched.attribute_labels ?? {} };
}

/**
 * 属性ごとに束ねる（issue #53）。
 *
 * **該当しないものを落とさない。** 分類コードと本文の一致率は90%で、
 * 隠すと「対象なのに出ない」を作る（CLAUDE.md）。見出しでまとめるだけにして、
 * どの見出しにも入らないものは「そのほか」に残す。
 *
 * 1つの制度が複数の属性に該当することがあるので、**最初の1つにだけ入れる**
 * （同じ制度が2箇所に出ると件数が二重に見える）。
 */
function groupByAttribute(
  benefits: MatchedBenefit[],
  labels: Record<string, string>,
): { key: string | null; label: string; items: MatchedBenefit[] }[] {
  const groups = Object.keys(labels).map((key) => ({
    key,
    label: `${labels[key]}向けに分類されている制度`,
    items: [] as MatchedBenefit[],
  }));
  const rest: MatchedBenefit[] = [];
  for (const b of benefits) {
    const group = groups.find((g) => b.matched_attributes?.includes(g.key));
    if (group) group.items.push(b);
    else rest.push(b);
  }
  return [
    ...groups.filter((g) => g.items.length > 0),
    { key: null, label: "そのほかの制度", items: rest },
  ];
}

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[]>>;
}) {
  const profile = fromSearchParams(await searchParams);
  const filtered = hasAnyAttribute(profile);
  const { benefits, labels } = await getBenefits(
    filtered
      ? `/api/benefits/match?${toSearchParams(profile).toString()}&include_skill_tree=false`
      : "/api/benefits",
  );
  // 属性の見出しが付くのは match のときだけ。属性未指定なら1つの束にまとめる。
  const groups =
    filtered && Object.keys(labels).length > 0
      ? groupByAttribute(benefits as MatchedBenefit[], labels)
      : [{ key: null, label: "", items: benefits }];

  return (
    <main className="mx-auto max-w-3xl p-6">
      <Heading size="28" rule="4" className="mb-4">
        <HeadingTitle level="h1">今受けられる子育て支援制度</HeadingTitle>
      </Heading>

      <p className="mb-6" data-testid="filter-status">
        {filtered ? (
          <>
            <span className="font-bold">条件で絞り込んでいます</span>（
            {benefits.length}件）。 <Link href="/settings">条件を変える</Link>
          </>
        ) : (
          <>
            すべての制度を表示しています。{" "}
            <Link href="/settings">お住まいやお子さんの年齢を設定する</Link>と、
            対象になる制度だけに絞り込めます。
          </>
        )}
      </p>

      {benefits.length === 0 ? (
        <p>制度が見つかりませんでした。</p>
      ) : (
        groups.map((group) => (
          <section key={group.key ?? "rest"} className="mb-8">
            {group.label && (
              <Heading size="20" hasChip className="mb-3">
                <HeadingTitle level="h2">
                  {group.label}（{group.items.length}件）
                </HeadingTitle>
              </Heading>
            )}
            <ul className="flex flex-col gap-4">
              {group.items.map((b) => (
                <li
                  key={b.benefit_id}
                  data-testid="benefit-card"
                  className="rounded-8 border border-solid-gray-300 p-4"
                >
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
                    {b.area_name && (
                      <ChipLabel variant="text">{b.area_name}</ChipLabel>
                    )}
                    <AgeChip
                      source={b.age_source}
                      minMonths={b.min_age_months}
                      maxMonths={b.max_age_months}
                    />
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

                  {"match_reasons" in b && b.match_reasons.length > 0 && (
                    /* なぜ当たったかを必ず添える。「対象です」とだけ言われても根拠が分からない。
                     判定を LLM に任せない設計の要（ADR 0001）。 */
                    <ul className="mt-2 list-disc pl-5 text-dns-14N-130 text-solid-gray-800">
                      {b.match_reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  )}

                  <p className="mt-2 text-solid-gray-800">{b.summary}</p>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
    </main>
  );
}
