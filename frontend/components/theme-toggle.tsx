"use client";

import { useSyncExternalStore } from "react";

import {
  THEME_LABELS,
  THEME_STORAGE_KEY,
  type ThemePreference,
  readStoredPreference,
  resolveTheme,
} from "@/lib/theme";

const OPTIONS: ThemePreference[] = ["light", "dark", "system"];

// 保存された選択を「外部の状態」として読む。
// **effect の中で setState しない**ようにするため useSyncExternalStore を使う
// （React 19 / eslint-plugin-react-hooks の set-state-in-effect）。
const listeners = new Set<() => void>();

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  // 別タブで切り替えたときも追従する
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

function notify() {
  for (const listener of listeners) listener();
}

function getSnapshot(): ThemePreference {
  return readStoredPreference(window.localStorage);
}

/** サーバ描画時は「端末の設定に合わせる」。実際の配色は先読みスクリプトが当てている。 */
function getServerSnapshot(): ThemePreference {
  return "system";
}

/**
 * 配色の切り替え（issue #101）。
 *
 * **選択式にする。** 明暗を1つのボタンで往復させる形だと、いまが「OS 追従」なのか
 * 「自分で選んだ結果たまたま同じ」なのかが区別できない。
 *
 * `<select>` を使うのは、キーボード操作とスクリーンリーダーの読み上げが
 * ブラウザ側で担保されるため（ADR 0016）。
 */
export function ThemeToggle() {
  const preference = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  function apply(next: ThemePreference) {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // 保存できなくても表示は切り替える（プライベートモード等）
    }
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = resolveTheme(next, prefersDark);
    notify();
  }

  return (
    <label className="flex items-center gap-2 text-std-16N-170 text-solid-gray-800">
      <span>配色</span>
      <select
        className="rounded-8 border border-solid-gray-500 bg-[var(--background)] px-2 py-1 text-solid-gray-900"
        value={preference}
        onChange={(e) => apply(e.target.value as ThemePreference)}
        data-testid="theme-toggle"
      >
        {OPTIONS.map((option) => (
          <option key={option} value={option}>
            {THEME_LABELS[option]}
          </option>
        ))}
      </select>
    </label>
  );
}
