import Link from "next/link";

/**
 * 全ページ共通のヘッダー。
 *
 * サイト名は見出し要素にしていない。各ページが自分の `<h1>` を持っているため、
 * ここにも `<h1>` を置くと1ページに2つ並び、見出しの階層が壊れる（#58）。
 *
 * 自治体名や「東京都」といったラベルをサイト名に添えない。
 * 公式サービスだと受け取られかねないため（フッターで明示的に否定している）。
 */
export function SiteHeader() {
  return (
    <header className="border-b border-solid-gray-300 bg-white">
      <div className="mx-auto max-w-3xl p-4">
        <Link href="/" className="text-std-18B-160 text-solid-gray-900 no-underline">
          子育て支援制度ナレッジグラフ
        </Link>
      </div>
    </header>
  );
}
