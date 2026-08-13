import { ChipLabel } from "@/components/dads/chip-label";
import { type AgeSource, ageLabel } from "@/lib/age";

/**
 * 対象年齢のチップ（issue #61）。
 *
 * **推定・不明のものを、明示されているものと同じ見た目で出さない。**
 * 文言（「（推定）」「記載なし」）と色の両方で区別する。色だけに頼らないのは、
 * 色覚特性のある人に伝わらないため（WCAG 1.4.1。ADR 0016）。
 */
export function AgeChip({
  source,
  minMonths,
  maxMonths,
}: {
  source: AgeSource;
  minMonths: number | null;
  maxMonths: number | null;
}) {
  const label = ageLabel(source, minMonths, maxMonths);
  return (
    <ChipLabel
      variant="outlined"
      color={label.uncertain ? "orange" : "gray"}
      title={label.note}
      data-testid="age-chip"
    >
      {label.text}
    </ChipLabel>
  );
}
