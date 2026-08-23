import type { Metadata } from "next";
import { Noto_Sans_JP } from "next/font/google";
import "./globals.css";
import { SiteHeader } from "@/components/site-header";
import { SiteFooter } from "@/components/site-footer";
import { fetchBackend } from "@/lib/backend";
import type { DataSource } from "@/lib/types";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

// デジタル庁デザインシステムのタイポグラフィは Noto Sans JP を前提にしている
// （@digital-go-jp/tailwind-theme-plugin の --font-sans も 'Noto Sans JP' を先頭に置く）。
// next/font はビルド時にフォントを取得して自前配信するため、ブラウザから Google へは
// 一切リクエストが飛ばない（ADR 0010 の「実行時に外部CDNから取らない」と矛盾しない）。
const notoSansJP = Noto_Sans_JP({
  variable: "--font-noto-sans-jp",
  // 日本語グリフは subsets では指定できない（next/font のメタデータに "japanese" が無い）。
  // latin を指定したうえで、日本語は unicode-range 分割された残りのチャンクとして入る。
  subsets: ["latin"],
  display: "swap",
});

const SITE_NAME = "子育て支援制度ナレッジグラフ";
const DESCRIPTION =
  "東京都の子育て支援制度レジストリをもとに、居住地と子どもの年齢から対象になる制度を探せます。制度の適用判定はオープンデータの構造化された条件だけで行っています。";

export const metadata: Metadata = {
  title: { default: SITE_NAME, template: `%s | ${SITE_NAME}` },
  description: DESCRIPTION,
  applicationName: SITE_NAME,
  openGraph: {
    type: "website",
    siteName: SITE_NAME,
    title: SITE_NAME,
    description: DESCRIPTION,
    locale: "ja_JP",
  },
};

/** フッターの出典・鮮度を取る。失敗しても画面は出す（フッターは既定値で描画される）。 */
async function getDataSource(): Promise<DataSource | null> {
  try {
    const res = await fetchBackend("/api/data-source");
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const dataSource = await getDataSource();

  return (
    <html
      lang="ja"
      // **既定を JSX 側にも書く。** Strict Mode の再マウントで React は
      // <html> の属性を JSX が持つものに戻すので、ここが無いと属性ごと消える。
      data-theme="light"
      className={`${notoSansJP.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        {/* **React が動く前に配色を決める**（issue #101）。マウント後に当てると
            一瞬ライトが見えてから暗くなる。html の属性を直接書き換えるので、
            サーバの出力と食い違う。suppressHydrationWarning はそのための指定。 */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-full flex flex-col">
        <SiteHeader />
        {/* フッターを最下部に押し下げる。中身が短いページでも footer が浮かないようにする */}
        <div className="flex-1">{children}</div>
        <SiteFooter dataSource={dataSource} />
      </body>
    </html>
  );
}
