/**
 * 対象年齢の表示（issue #61 / ADR 0002）。
 *
 * `age_source` は「その年齢範囲をどれだけ信用してよいか」を表す。
 *
 * - `explicit` : 元データに年齢が明示されている
 * - `inferred` : 本文から `src/age_rules.py` の正規表現で推定した
 * - `unknown`  : どちらでもない（範囲は NULL）
 *
 * **推定値を断定的に見せてはいけない**（CLAUDE.md）。
 * 実データでは inferred が 2,346件（30.0%）、unknown が 2,672件（34.2%）で、
 * 合わせて**6割超が「元データに年齢が書かれていない」制度**にあたる。
 * ここを黙って explicit と同じ見た目で出すと、こちらの推定を
 * 自治体が定めた条件のように見せることになる。
 */
import type { Benefit } from "@/lib/types";

export type AgeSource = Benefit["age_source"];

/** 月齢を「1歳6か月」のような表示にする。 */
export function formatMonths(months: number): string {
  const years = Math.floor(months / 12);
  const rest = months % 12;
  if (years === 0) return `${rest}か月`;
  return rest === 0 ? `${years}歳` : `${years}歳${rest}か月`;
}

export type AgeLabel = {
  text: string;
  /** 推定・不明であることを色でも示すか。true なら注意色にする。 */
  uncertain: boolean;
  /** ラベルだけでは伝わらない補足（title 属性やスクリーンリーダー向け）。 */
  note: string;
};

/**
 * 一覧カード・詳細ページに出す対象年齢のラベルを作る。
 *
 * **`unknown` のときに範囲を出さない**のが要点。範囲は NULL なので
 * 「0か月〜」のような既定値を当てると、読み取れなかったことが消えてしまう。
 */
export function ageLabel(
  source: AgeSource,
  minMonths: number | null,
  maxMonths: number | null,
): AgeLabel {
  if (source === "unknown" || (minMonths === null && maxMonths === null)) {
    return {
      text: "対象年齢の記載なし",
      uncertain: true,
      note: "元データに年齢の条件が書かれておらず、本文からも読み取れませんでした。年齢の条件があるかどうかは本文と窓口でご確認ください。",
    };
  }

  const range =
    minMonths !== null && maxMonths !== null
      ? `${formatMonths(minMonths)}〜${formatMonths(maxMonths)}`
      : minMonths !== null
        ? `${formatMonths(minMonths)}〜`
        : `〜${formatMonths(maxMonths as number)}`;

  if (source === "inferred") {
    return {
      text: `対象 ${range}（推定）`,
      uncertain: true,
      note: "元データに年齢の記載が無いため、制度の本文から推定した範囲です。正確な条件は本文と窓口でご確認ください。",
    };
  }
  return { text: `対象 ${range}`, uncertain: false, note: "元データに記載されている年齢の条件です。" };
}
