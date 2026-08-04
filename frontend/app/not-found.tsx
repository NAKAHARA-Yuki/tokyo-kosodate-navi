import { Heading, HeadingTitle } from "@/components/dads/heading";
import { Link } from "@/components/dads/link";

/**
 * 404 の画面。`notFound()` を呼んだとき（例: 存在しない benefit_id）と、
 * どのルートにも一致しない URL の両方でこれが出る。
 *
 * **意図的に静的のままにしている（`force-dynamic` を付けない）。**
 * このページはビルド時に `/_not-found` として事前生成される唯一のルートで、
 * そのぶんフッターの収録件数と最終更新日（#57）は出ない（ビルド時に backend が居ないため）。
 * 出典と免責は `FALLBACK_SOURCE` から出るので要件は満たしている。
 *
 * 404 は最も backend に依存させたくないページで、**backend が落ちているときこそ
 * 404 や誤った URL へのアクセスは増える**。鮮度を揃えるためだけに毎回 backend を
 * 叩きに行くのは筋が悪い（#69 のレビューでの議論）。
 */
export default function NotFound() {
  return (
    <main className="mx-auto max-w-3xl p-6">
      <Heading size="28" rule="4" className="mb-6">
        <HeadingTitle level="h1">お探しのページは見つかりませんでした</HeadingTitle>
      </Heading>

      <p className="text-std-16N-170">
        URL が変わったか、その制度の掲載が終了した可能性があります。
      </p>

      <p className="mt-6">
        <Link href="/">制度の一覧から探す</Link>
      </p>
    </main>
  );
}
