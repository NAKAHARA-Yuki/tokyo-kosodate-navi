"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/dads/button";
import { Heading, HeadingTitle } from "@/components/dads/heading";
import {
  type Child,
  EMPTY_PROFILE,
  MODEL_USERS,
  type Profile,
  loadProfile,
  saveProfile,
  toSearchParams,
} from "@/lib/profile";
import type { Area } from "@/lib/types";

/**
 * 属性の入力フォーム（issue #53 / #35）。
 *
 * **トップページには置かない。** 一覧を見るたびに入力欄が挟まるのを避け、
 * 設定画面に集約している。結果は URL のクエリとして共有できる。
 *
 * 入力値は保存すると localStorage に入るが、**判定に使うのは URL の値**。
 * ここで組み立てた URL でトップページへ遷移し、サーバ側の確定クエリが絞り込む。
 */
export function SettingsForm({
  areas,
  initial,
  hasParams,
}: {
  areas: Area[];
  initial: Profile;
  /** URL にクエリがあったか。**オブジェクトの参照比較で判定してはいけない**（下記） */
  hasParams: boolean;
}) {
  const router = useRouter();
  // localStorage はブラウザにしか無い。**このフォームはクライアント限定で描画している**ので
  // （settings-form-client.tsx）、初期値としてそのまま読める。
  // useEffect で読んで setState すると、描画のたびに一瞬空のフォームが出るうえ、
  // React の「effect 内で直接 setState しない」規則にも触れる。
  //
  // URL で属性が渡っていればそちらを優先する（**URL が正**）。
  //
  // **その判定に `initial === EMPTY_PROFILE` を使ってはいけない。**
  // Server Component から渡る props は RSC のシリアライズを経由するため、
  // サーバ側の EMPTY_PROFILE とクライアント側が import した EMPTY_PROFILE は
  // 中身が同じでも別インスタンスになり、参照比較は**常に false** になる。
  // 実際にそう書いていて、localStorage からの復元が一度も動いていなかった
  // （レビューで指摘。プリミティブの boolean なら境界をまたいでも比較できる）。
  const [profile, setProfile] = useState<Profile>(() => (hasParams ? initial : loadProfile()));

  const update = (patch: Partial<Profile>) => setProfile((p) => ({ ...p, ...patch }));
  const updateChild = (index: number, patch: Partial<Child>) =>
    setProfile((p) => ({
      ...p,
      children: p.children.map((c, i) => (i === index ? { ...c, ...patch } : c)),
    }));

  const submit = () => {
    saveProfile(profile);
    router.push(`/?${toSearchParams(profile).toString()}`);
  };

  return (
    <div className="flex flex-col gap-8">
      <section>
        <Heading size="20" hasChip className="mb-2">
          <HeadingTitle level="h2">例から選ぶ</HeadingTitle>
        </Heading>
        <p className="mb-3 text-solid-gray-800">
          どんな人にどんな制度が出るかを見るための例です。選ぶと下のフォームに入ります。
        </p>
        <ul className="flex flex-col gap-2">
          {MODEL_USERS.map((user) => (
            <li key={user.id} className="rounded-8 border border-solid-gray-300 p-3">
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid={`model-user-${user.id}`}
                onClick={() => setProfile(user.profile)}
              >
                {user.label}
              </Button>
              <p className="mt-2 text-dns-14N-130 text-solid-gray-800">{user.note}</p>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <Heading size="20" hasChip className="mb-2">
          <HeadingTitle level="h2">自分で入力する</HeadingTitle>
        </Heading>

        <div className="flex flex-col gap-4">
          <div>
            <label htmlFor="area" className="block font-bold">
              お住まいの市区町村
            </label>
            <select
              id="area"
              className="mt-1 w-full rounded-8 border border-solid-gray-500 p-2"
              value={profile.areaCode ?? ""}
              onChange={(e) => update({ areaCode: e.target.value || undefined })}
            >
              <option value="">指定しない</option>
              {areas.map((a) => (
                <option key={a.area_code} value={a.area_code}>
                  {a.area_name}
                </option>
              ))}
            </select>
          </div>

          <fieldset>
            <legend className="font-bold">お子さんの生年月日</legend>
            <p className="text-dns-14N-130 text-solid-gray-800">
              月齢はその時点で変わるため、生年月日で持ちます。分からない場合は空のままで構いません。
            </p>
            {profile.children.map((child, i) => (
              <div key={i} className="mt-2 flex items-center gap-2">
                <label htmlFor={`child-${i}`} className="sr-only">
                  {i + 1}人目のお子さんの生年月日
                </label>
                <input
                  id={`child-${i}`}
                  type="date"
                  className="rounded-8 border border-solid-gray-500 p-2"
                  value={child.birthDate ?? ""}
                  onChange={(e) => updateChild(i, { birthDate: e.target.value || undefined })}
                />
                {child.birthDate == null && child.ageMonths != null && (
                  <span className="text-dns-14N-130 text-solid-gray-800">
                    （月齢 {child.ageMonths} で指定中）
                  </span>
                )}
                <Button
                  type="button"
                  variant="text"
                  size="sm"
                  onClick={() =>
                    update({ children: profile.children.filter((_, index) => index !== i) })
                  }
                >
                  削除
                </Button>
              </div>
            ))}
            {profile.children.length < 10 && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="mt-2"
                data-testid="add-child"
                onClick={() => update({ children: [...profile.children, {}] })}
              >
                お子さんを追加
              </Button>
            )}
          </fieldset>

          <fieldset className="flex flex-col gap-2">
            <legend className="font-bold">あてはまるもの</legend>
            {(
              [
                ["isPregnant", "妊娠中である"],
                ["isSingleParent", "ひとり親世帯である"],
                ["hasDisability", "障がいのあるお子さんがいる"],
              ] as const
            ).map(([key, label]) => (
              <label key={key} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={profile[key]}
                  onChange={(e) => update({ [key]: e.target.checked } as Partial<Profile>)}
                />
                {label}
              </label>
            ))}
          </fieldset>
        </div>
      </section>

      <div className="flex gap-3">
        <Button type="button" size="md" onClick={submit} data-testid="apply-profile">
          この条件で制度を見る
        </Button>
        <Button type="button" variant="text" size="md" onClick={() => setProfile(EMPTY_PROFILE)}>
          入力をクリア
        </Button>
      </div>
    </div>
  );
}
