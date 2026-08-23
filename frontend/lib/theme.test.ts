import { describe, expect, it } from "vitest";

import {
  THEME_INIT_SCRIPT,
  THEME_STORAGE_KEY,
  isThemePreference,
  readStoredPreference,
  resolveTheme,
} from "@/lib/theme";

/** localStorage の最小の代役。値と、例外を投げる状態を作れるようにする。 */
function fakeStorage(value: string | null, throws = false): Pick<Storage, "getItem"> {
  return {
    getItem() {
      if (throws) throw new Error("localStorage is not available");
      return value;
    },
  };
}

describe("readStoredPreference", () => {
  it("保存された選択を読む", () => {
    expect(readStoredPreference(fakeStorage("dark"))).toBe("dark");
    expect(readStoredPreference(fakeStorage("light"))).toBe("light");
  });

  it("未設定なら端末の設定に合わせる", () => {
    expect(readStoredPreference(fakeStorage(null))).toBe("system");
  });

  it("壊れた値は system に倒す", () => {
    // localStorage は利用者が手で書き換えられる。落ちないこと
    expect(readStoredPreference(fakeStorage("<script>"))).toBe("system");
  });

  it("localStorage 自体が使えなくても落ちない", () => {
    // プライベートモード等では getItem が例外を投げることがある
    expect(readStoredPreference(fakeStorage(null, true))).toBe("system");
  });
});

describe("resolveTheme", () => {
  it("選んだ配色は端末の設定より優先する", () => {
    // **OS 追従だけにしない**（issue #101）。
    // 「OS はダークだが、このサイトはライトで読みたい」を選べること
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("system は端末の設定に従う", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
  });
});

describe("isThemePreference", () => {
  it("3つの値だけを受け付ける", () => {
    expect(isThemePreference("system")).toBe(true);
    expect(isThemePreference("blue")).toBe(false);
    expect(isThemePreference(null)).toBe(false);
  });
});

describe("先読みスクリプト", () => {
  it("保存キーを埋め込んでいる", () => {
    // スクリプトは文字列なので、キーがずれても型では気づけない
    expect(THEME_INIT_SCRIPT).toContain(JSON.stringify(THEME_STORAGE_KEY));
  });

  it("data-theme を必ず決める", () => {
    // **ちらつきを防ぐのが目的**。どの経路でも属性が付くこと
    expect(THEME_INIT_SCRIPT).toContain("prefers-color-scheme");
    expect(THEME_INIT_SCRIPT.match(/dataset\.theme/g)?.length).toBeGreaterThanOrEqual(2);
  });

  it("例外が出てもライトに倒す", () => {
    expect(THEME_INIT_SCRIPT).toContain("catch");
  });
});
