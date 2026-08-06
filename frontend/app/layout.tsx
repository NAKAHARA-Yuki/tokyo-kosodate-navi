import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { SiteHeader } from "@/components/site-header";
import { SiteFooter } from "@/components/site-footer";
import { fetchBackend } from "@/lib/backend";
import type { DataSource } from "@/lib/types";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
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
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <SiteHeader />
        {/* フッターを最下部に押し下げる。中身が短いページでも footer が浮かないようにする */}
        <div className="flex-1">{children}</div>
        <SiteFooter dataSource={dataSource} />
      </body>
    </html>
  );
}
