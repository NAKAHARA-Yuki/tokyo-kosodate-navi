import { notFound } from "next/navigation";
import { fetchBackend } from "@/lib/backend";
import type { Subgraph, SubgraphNode } from "@/lib/types";
import { Heading } from "@/components/dads/heading";
import { ChipLabel } from "@/components/dads/chip-label";
import { Link } from "@/components/dads/link";

// force-dynamic の理由は lib/backend.ts / app/page.tsx 参照。
export const dynamic = "force-dynamic";

async function getSubgraph(id: string): Promise<Subgraph | null> {
  const res = await fetchBackend(`/api/subgraph?benefit_id=${id}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`backend が ${res.status} を返しました`);
  }
  return res.json();
}

/** 補助額・費用は制度ごとに書式が大きく異なるため数値化はせず、原文をそのまま出す。 */
function TextSection({ title, rows }: { title: string; rows: [string, string | null | undefined][] }) {
  const present = rows.filter(([, v]) => v);
  if (present.length === 0) return null;
  return (
    <section className="mt-6">
      <Heading size="18" hasChip className="mb-2">
        {title}
      </Heading>
      <dl>
        {present.map(([label, value]) => (
          <div key={label} className="mb-3">
            <dt className="font-bold">{label}</dt>
            <dd className="whitespace-pre-line">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export default async function BenefitDetail({ params }: { params: Promise<{ id: string }> }) {
  // Next.js の動的セグメントは **URLエンコードされたまま**渡ってくる。
  // benefit_id には `+` が含まれる（例: psid3.0+1000020132152+1+UM5036）ため、
  // ここで encodeURIComponent すると二重エンコードになり backend が 404 を返す。
  // クエリ文字列にはそのまま載せること。
  const { id } = await params;

  let subgraph: Subgraph | null;
  try {
    subgraph = await getSubgraph(id);
  } catch (reason) {
    const message = reason instanceof Error ? reason.message : "不明なエラー";
    return (
      <main className="mx-auto max-w-3xl p-6">
        <p className="text-red-700">読み込みに失敗しました: {message}</p>
        <p className="mt-4">
          <Link href="/">一覧に戻る</Link>
        </p>
      </main>
    );
  }

  if (!subgraph) notFound();

  const benefit = subgraph.nodes.find((n) => n.data.type === "Benefit")?.data;
  if (!benefit) notFound();

  const pick = (type: SubgraphNode["data"]["type"]) =>
    subgraph.nodes.filter((n) => n.data.type === type).map((n) => n.data);
  const statuses = pick("Status");
  const documents = pick("Document");

  return (
    <main className="mx-auto max-w-3xl p-6">
      <p className="mb-4">
        <Link href="/">← 一覧に戻る</Link>
      </p>

      <Heading size="28" rule="4" className="mb-4">
        {benefit.label}
      </Heading>

      <div className="mb-4 flex flex-wrap gap-2">
        {benefit.category && (
          <ChipLabel variant="outlined" color="blue">
            {benefit.category}
          </ChipLabel>
        )}
        {benefit.area_name && <ChipLabel variant="text">{benefit.area_name}</ChipLabel>}
        {benefit.is_free && (
          <ChipLabel variant="filled-1" color="green">
            無料
          </ChipLabel>
        )}
        {benefit.electronic_submission && (
          <ChipLabel variant="filled-1" color="blue">
            オンライン申請可
          </ChipLabel>
        )}
      </div>

      {benefit.summary && <p className="whitespace-pre-line">{benefit.summary}</p>}

      <TextSection
        title="費用・助成額"
        rows={[
          ["助成額・支給額", benefit.monetary_support_text],
          ["現物給付", benefit.materially_support_text],
          ["費用", benefit.cost_text],
          ["費用の条件", benefit.cost_conditions_text],
        ]}
      />

      {statuses.length > 0 && (
        <section className="mt-6">
          <Heading size="18" hasChip className="mb-2">
            対象の条件
          </Heading>
          <ul className="flex flex-wrap gap-2">
            {statuses.map((s) => (
              <li key={s.id}>
                <ChipLabel variant="filled-1" color="cyan">
                  {s.label}
                </ChipLabel>
              </li>
            ))}
          </ul>
        </section>
      )}

      {documents.length > 0 && (
        <section className="mt-6">
          <Heading size="18" hasChip className="mb-2">
            必要書類
          </Heading>
          <ul className="list-disc pl-6">
            {documents.map((d) => (
              <li key={d.id}>{d.label}</li>
            ))}
          </ul>
          <p className="mt-2 text-solid-gray-700">
            機械的に抽出したものです。実際に必要な書類は窓口や公式ページでご確認ください。
          </p>
        </section>
      )}

      <TextSection
        title="手続き"
        rows={[
          ["申請方法", benefit.procedure_method],
          ["申請窓口", benefit.procedure_counter],
          ["根拠法令", benefit.regulation_name],
        ]}
      />

      <TextSection
        title="問い合わせ先"
        rows={[
          ["担当部署", benefit.department || benefit.contact_name],
          ["電話", benefit.contact_phone],
          ["メール", benefit.contact_email],
          ["所在地", benefit.contact_address],
        ]}
      />

      {benefit.official_url && (
        <p className="mt-6">
          <Link href={benefit.official_url} target="_blank" rel="noopener">
            {benefit.official_title || "自治体の公式ページを開く"}
          </Link>
        </p>
      )}

      {benefit.update_date && (
        <p className="mt-6 text-solid-gray-700">最終更新: {benefit.update_date}</p>
      )}
    </main>
  );
}
