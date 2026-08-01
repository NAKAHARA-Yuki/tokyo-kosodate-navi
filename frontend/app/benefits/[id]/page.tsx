import { Heading } from "@/components/dads/heading";
import { Link } from "@/components/dads/link";

// 詳細ビューは後続PR（issue #33 PR4）で実装する。ここでは一覧からのリンク先が
// 404にならないよう、最小限のプレースホルダーだけ置く。
export default async function BenefitDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <main className="mx-auto max-w-3xl p-6">
      <Heading size="24" rule="4" className="mb-4">
        制度の詳細（準備中）
      </Heading>
      <p className="mb-4">
        制度ID: <code>{id}</code>
      </p>
      <p className="mb-4">詳細ビューは準備中です。</p>
      <Link href="/">一覧に戻る</Link>
    </main>
  );
}
