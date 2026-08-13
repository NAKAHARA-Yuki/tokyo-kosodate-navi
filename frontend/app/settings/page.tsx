import { SettingsFormClient } from "@/components/settings-form-client";
import { Heading, HeadingTitle } from "@/components/dads/heading";
import { fetchBackend } from "@/lib/backend";
import { fromSearchParams } from "@/lib/profile";
import type { Area } from "@/lib/types";

// force-dynamic の理由は lib/backend.ts / app/page.tsx 参照。
export const dynamic = "force-dynamic";

async function getAreas(): Promise<Area[]> {
  try {
    const res = await fetchBackend("/api/areas");
    if (!res.ok) return [];
    return await res.json();
  } catch {
    // 自治体一覧が取れなくても、他の項目は入力できる方が良い
    return [];
  }
}

/**
 * 設定画面（issue #53 / #35）。
 *
 * **トップページに入力欄を置かず、ここに集約している。** 一覧を見るたびにフォームが
 * 挟まるのを避けるため。入力した属性は URL のクエリとしてトップページへ渡す。
 */
export default async function SettingsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[]>>;
}) {
  const params = await searchParams;
  const areas = await getAreas();
  const initial = fromSearchParams(params);
  const hasParams = Object.keys(params).length > 0;

  return (
    <main className="mx-auto max-w-3xl p-6">
      <Heading size="28" rule="4" className="mb-4">
        <HeadingTitle level="h1">条件を設定する</HeadingTitle>
      </Heading>
      <p className="mb-6 text-solid-gray-800">
        入力した内容はこの端末にだけ保存され、サーバーには送られません。
        絞り込みの結果は URL に反映されるので、そのまま共有できます。
      </p>
      <SettingsFormClient areas={areas} initial={initial} hasParams={hasParams} />
    </main>
  );
}
