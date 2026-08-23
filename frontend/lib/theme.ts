/**
 * 配色の切り替え（issue #101）。
 *
 * **OS 追従だけにしない。** 「OS はダークだが、このサイトはライトで読みたい」を
 * 選べるようにする。行政情報は落ち着いて読みたい場面が多く、
 * 端末全体の設定と読みたい配色が一致するとは限らない。
 *
 * 保存先は localStorage。プロフィール（`lib/profile.ts`）と同じく
 * 端末に置くだけで、サーバには送らない。
 */

export const THEME_STORAGE_KEY = "kosodate-theme";

/** 利用者が選ぶ値。`system` は「OS に合わせる」。 */
export type ThemePreference = "light" | "dark" | "system";

/** 実際に適用する値。`system` はここで解決済み。 */
export type ResolvedTheme = "light" | "dark";

export const THEME_LABELS: Record<ThemePreference, string> = {
  light: "ライト",
  dark: "ダーク",
  system: "端末の設定に合わせる",
};

export function isThemePreference(value: unknown): value is ThemePreference {
  return value === "light" || value === "dark" || value === "system";
}

/**
 * 保存された選択を読む。**壊れた値は `system` に倒す。**
 * 手で書き換えられても落ちないようにする（localStorage は利用者が触れる）。
 */
export function readStoredPreference(storage: Pick<Storage, "getItem">): ThemePreference {
  try {
    const raw = storage.getItem(THEME_STORAGE_KEY);
    return isThemePreference(raw) ? raw : "system";
  } catch {
    // プライベートモード等で localStorage 自体が例外を投げることがある
    return "system";
  }
}

/** 選択と OS の設定から、実際に当てる配色を決める。 */
export function resolveTheme(preference: ThemePreference, prefersDark: boolean): ResolvedTheme {
  if (preference === "system") return prefersDark ? "dark" : "light";
  return preference;
}

/**
 * 初期表示でちらつかせないための先読みスクリプト（`layout.tsx` の `<head>` に置く）。
 *
 * **React が動く前に属性を付ける**必要がある。マウント後に当てると、
 * 一瞬ライトが見えてから暗くなる。
 */
export const THEME_INIT_SCRIPT = `
(function () {
  try {
    var pref = localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});
    if (pref !== "light" && pref !== "dark") {
      pref = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    document.documentElement.dataset.theme = pref;
  } catch (e) {
    document.documentElement.dataset.theme = "light";
  }
})();
`.trim();
