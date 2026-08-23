import { describe, expect, it } from "vitest";

import { ageLabel } from "@/lib/age";

/**
 * 対象年齢の見せ方（issue #61 / #114 / ADR 0002）。
 *
 * **推定値を断定的に見せてはいけない**（CLAUDE.md）。
 * こちらが読み取った値を、自治体が定めた条件と同じ見た目で出さないこと。
 */
describe("explicit（元データに年齢が明示されている）", () => {
  it("断定してよい", () => {
    const label = ageLabel("explicit", 0, 71);
    expect(label.uncertain).toBe(false);
    expect(label.text).not.toContain("推定");
  });
});

describe("inferred（本文から推定した）", () => {
  it("推定であることを文言で示す", () => {
    const label = ageLabel("inferred", 0, 71);
    expect(label.text).toContain("推定");
    expect(label.uncertain).toBe(true);
  });
});

describe("unknown（読み取れなかった）", () => {
  it("範囲を出さない", () => {
    // **既定値を当てない。** 「0か月〜」と出すと読み取れなかったことが消える。
    const label = ageLabel("unknown", null, null);
    expect(label.text).toBe("対象年齢の記載なし");
    expect(label.uncertain).toBe(true);
  });
});

describe("corrected（元データの年齢欄が制度名と食い違っていた。issue #114）", () => {
  it("推定として見せる。断定しない", () => {
    const label = ageLabel("corrected", 3, 4);
    expect(label.text).toContain("推定");
    expect(label.uncertain).toBe(true);
  });

  it("なぜその範囲なのかを補足で伝える", () => {
    const label = ageLabel("corrected", 3, 4);
    expect(label.note).toContain("食い違");
    expect(label.note).toContain("窓口");
  });

  it("範囲そのものは表示する", () => {
    expect(ageLabel("corrected", 3, 4).text).toContain("3か月");
  });
});
