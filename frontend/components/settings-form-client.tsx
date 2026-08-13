"use client";

import dynamic from "next/dynamic";

/**
 * 設定フォームをクライアント限定で描画する（issue #53）。
 *
 * フォームの初期値は localStorage から読む。サーバ側には存在しないので、
 * SSR すると「サーバは空・クライアントは復元済み」で hydration が食い違う。
 * 描画をクライアントに寄せて、初期値として素直に読めるようにしている。
 */
export const SettingsFormClient = dynamic(
  () => import("@/components/settings-form").then((m) => m.SettingsForm),
  { ssr: false, loading: () => <p>読み込んでいます…</p> },
);
