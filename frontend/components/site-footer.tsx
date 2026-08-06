import { Link } from "@/components/dads/link";
import type { DataSource } from "@/lib/types";

/**
 * 全ページ共通のフッター。出典・免責・データの鮮度を出す。
 *
 * 行政情報を扱う以上、これは機能ではなく責任にあたる（#57）。
 * したがって **出典と免責は backend の応答に依存させない**。
 * `dataSource` が null（backend が落ちている等）でも、出典と免責は必ず表示され、
 * 件数と更新日の行だけが消える。
 */

// 出典の表記は backend（app/routers/meta.py）が返す値を使うが、
// 取得できなかったときのために既定値を持っておく。
const FALLBACK_SOURCE = {
  name: "東京デジタル2030ビジョン（こどもDX）子育て支援制度レジストリ",
  url: "https://portal.data.metro.tokyo.lg.jp/visualization/childcare-support-system-registry/",
  publisher: "東京都",
};

function formatDate(value: string): string {
  const [y, m, d] = value.split("-");
  return y && m && d ? `${y}年${Number(m)}月${Number(d)}日` : value;
}

export function SiteFooter({ dataSource }: { dataSource: DataSource | null }) {
  const source = dataSource?.source ?? FALLBACK_SOURCE;
  const count = dataSource?.benefit_count;
  const areas = dataSource?.area_count;
  const updated = dataSource?.latest_update_date;

  return (
    <footer className="mt-12 border-t border-solid-gray-300 bg-solid-gray-50">
      <div className="mx-auto flex max-w-3xl flex-col gap-3 p-6 text-dns-14N-130 text-solid-gray-800">
        <p>
          <span className="font-bold">出典:</span> {source.publisher}
          {"「"}
          <Link href={source.url} target="_blank" rel="noopener noreferrer">
            {source.name}
          </Link>
          {"」（東京都オープンデータカタログサイト）"}
        </p>

        {(count != null || updated) && (
          <p>
            <span className="font-bold">収録データ:</span>{" "}
            {count != null &&
              `${count.toLocaleString("ja-JP")}件${areas != null ? `（${areas}自治体）` : ""}`}
            {updated && `${count != null ? " / " : ""}制度情報の最終更新: ${formatDate(updated)}`}
          </p>
        )}

        {/* 日本語の文中に不要な空白が入らないよう、改行で区切らずに書く
            （JSX は要素間の改行＋インデントを半角スペース1つに畳む） */}
        <p>
          {"表示している制度情報は参考です。要件・金額・受付期間は変更される場合があります。"}
          <span className="font-bold">{"最終的な判断は各自治体の公式情報をご確認ください。"}</span>
        </p>

        <p className="text-solid-gray-700">
          {"本サイトは東京都および各自治体が運営する公式サービスではありません。オープンデータを利用して個人が制作したものです。"}
        </p>
      </div>
    </footer>
  );
}
