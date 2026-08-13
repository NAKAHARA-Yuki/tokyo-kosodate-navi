import { notFound } from "next/navigation";
import { fetchBackend } from "@/lib/backend";
import type { BenefitLink, Subgraph, SubgraphNode } from "@/lib/types";
import { AgeChip } from "@/components/age-chip";
import { Heading } from "@/components/dads/heading";
import { ChipLabel } from "@/components/dads/chip-label";
import { Link } from "@/components/dads/link";

// force-dynamic の理由は lib/backend.ts / app/page.tsx 参照。
export const dynamic = "force-dynamic";

async function getSubgraph(id: string): Promise<Subgraph | null> {
  const res = await fetchBackend(`/api/subgraph?benefit_id=${id}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    // 握りつぶさず投げる。app/error.tsx が受け取って利用者向けの画面を出し、
    // このメッセージ自体はサーバのログにだけ残る（本番ビルドではクライアントに渡らない）。
    throw new Error(`backend が ${res.status} を返しました`);
  }
  return res.json();
}

/** 同じ URI のリンクが related / form / embedded に重複して入っていることがあるのでまとめる。 */
function dedupeLinks(...groups: (BenefitLink[] | undefined)[]): BenefitLink[] {
  const seen = new Set<string>();
  const result: BenefitLink[] = [];
  for (const link of groups.flatMap((g) => g ?? [])) {
    if (seen.has(link.uri)) continue;
    seen.add(link.uri);
    result.push(link);
  }
  return result;
}

function LinkList({ title, links }: { title: string; links: BenefitLink[] }) {
  if (links.length === 0) return null;
  return (
    <section className="mt-6" data-testid="link-list">
      <Heading size="18" hasChip className="mb-2">
        {title}
      </Heading>
      <ul className="list-disc pl-6">
        {links.map((l) => (
          <li key={l.uri}>
            <Link href={l.uri} target="_blank" rel="noopener noreferrer">
              {l.title}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
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

  const subgraph = await getSubgraph(id);
  if (!subgraph) notFound();

  const benefit = subgraph.nodes.find((n) => n.data.type === "Benefit")?.data;
  if (!benefit) notFound();

  const pick = (type: SubgraphNode["data"]["type"]) =>
    subgraph.nodes.filter((n) => n.data.type === type).map((n) => n.data);
  const statuses = pick("Status");
  const documents = pick("Document");

  // 対象者と条件が同じ文言になっている制度が 1,359 件ある（実データ）。同じ段落を2度出さない。
  const conditionTexts: [string, string][] = [];
  if (benefit.target_persons_text) {
    conditionTexts.push(["対象になる方", benefit.target_persons_text]);
  }
  if (benefit.conditions_text && benefit.conditions_text !== benefit.target_persons_text) {
    conditionTexts.push(["その他の条件", benefit.conditions_text]);
  }

  // 申請書式（form_links）は導線として重要なので独立させ、残りは「関連リンク」にまとめる。
  const formLinks = dedupeLinks(benefit.form_links);
  const formUris = new Set(formLinks.map((l) => l.uri));
  const relatedLinks = dedupeLinks(benefit.related_links, benefit.embedded_links).filter(
    (l) => !formUris.has(l.uri),
  );

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
        <AgeChip
          source={benefit.age_source ?? "unknown"}
          minMonths={benefit.min_age_months ?? null}
          maxMonths={benefit.max_age_months ?? null}
        />
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
        title="制度の内容"
        rows={[
          ["詳しい説明", benefit.description],
          ["利用のしかた", benefit.utilization],
        ]}
      />

      <TextSection
        title="費用・助成額"
        rows={[
          ["助成額・支給額", benefit.monetary_support_text],
          ["現物給付", benefit.materially_support_text],
          ["費用", benefit.cost_text],
          ["費用の条件", benefit.cost_conditions_text],
        ]}
      />

      {(statuses.length > 0 || conditionTexts.length > 0) && (
        <section className="mt-6">
          <Heading size="18" hasChip className="mb-2">
            対象の条件
          </Heading>

          {statuses.length > 0 && (
            <ul className="mb-4 flex flex-wrap gap-2">
              {statuses.map((s) => (
                <li key={s.id}>
                  <ChipLabel variant="filled-1" color="cyan">
                    {s.label}
                  </ChipLabel>
                </li>
              ))}
            </ul>
          )}

          {/* 条件の原文。チップは機械的に構造化できた条件だけで、所得制限や世帯要件などは
              ここにしか書かれていない。要約や言い換えはせず原文のまま出す（ADR 0001）。 */}
          <dl>
            {conditionTexts.map(([label, value]) => (
              <div key={label} className="mb-3">
                <dt className="font-bold">{label}</dt>
                <dd className="whitespace-pre-line">{value}</dd>
              </div>
            ))}
          </dl>

          {benefit.has_free_text_conditions && (
            // 全体の約半数（3,808件）が該当する。チップだけを見て「自分は対象だ」と
            // 判断させないための注意書き。
            <p className="rounded-8 border border-yellow-600 bg-yellow-200 p-3">
              この制度には、機械的に判定しきれない条件が残っています。上の条件だけで対象かどうかは決まりません。
              必ず条件の原文と自治体の公式ページをご確認ください。
            </p>
          )}
        </section>
      )}

      {documents.length > 0 && (
        <section className="mt-6">
          <Heading size="18" hasChip className="mb-2">
            必要書類
          </Heading>
          <ul className="list-disc pl-6">
            {documents.map((d) => (
              <li key={d.id}>
                {/* 様式のURLを持つ書類は 462/4,919 件だけ。あるときはそのまま導線にする */}
                {d.doc_url ? (
                  <Link href={d.doc_url} target="_blank" rel="noopener noreferrer">
                    {d.label}
                  </Link>
                ) : (
                  d.label
                )}
              </li>
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

      <LinkList title="申請書式・電子申請" links={formLinks} />

      <TextSection
        title="問い合わせ先"
        rows={[
          ["担当部署", benefit.department || benefit.contact_name],
          ["電話", benefit.contact_phone],
          ["メール", benefit.contact_email],
          ["所在地", benefit.contact_address],
        ]}
      />

      <LinkList title="関連リンク" links={relatedLinks} />

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
