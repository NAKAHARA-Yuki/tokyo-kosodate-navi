/**
 * 利用者のプロフィール（issue #53 / #35）。
 *
 * **サーバーには永続化しない。** 保持先は URL のクエリパラメータと localStorage の2つで、
 * **URL を正とする**（CLAUDE.md: 個人情報を扱わない設計を維持する）。
 *
 * - URL が正 … 共有・リロードで同じ結果が再現される。判定はサーバ側の確定クエリのまま
 * - localStorage は補助 … 設定画面を開き直したときに前回の入力を復元するだけ
 *
 * 入力はチャットではなく**選択式フォーム**（ADR 0001 / CLAUDE.md）。
 * 将来のマイナポータル連携を見越して、生年月日を正とする（#75）。
 */

export type Child = {
  /** 生年月日。分かるならこちらが正（月齢は時間で古くなる） */
  birthDate?: string;
  /** 生年月日が分からないとき用 */
  ageMonths?: number;
};

export type Profile = {
  areaCode?: string;
  children: Child[];
  isPregnant: boolean;
  isSingleParent: boolean;
  hasDisability: boolean;
};

export const EMPTY_PROFILE: Profile = {
  children: [],
  isPregnant: false,
  isSingleParent: false,
  hasDisability: false,
};

/**
 * 例示用のモデルユーザー（issue #35）。
 *
 * **本物の認証は作らない。** デモとして「こういう人ならこう出る」を見せるための
 * 事前定義プロフィールで、切り替えるとフォームに流し込まれる。
 *
 * 属性の組み合わせは、判定の分岐が実際に効くものを選んでいる。
 * 妊娠期（`is_prenatal` は月齢で表せないため別軸）、きょうだい（片方だけ対象が実データで
 * 3分の1ある）、ひとり親・障がい（並び順と理由付けに効く）をそれぞれ通す。
 */
export type ModelUser = {
  id: string;
  label: string;
  /** なぜこの人を用意しているか。画面にも出す */
  note: string;
  profile: Profile;
};

export const MODEL_USERS: ModelUser[] = [
  {
    id: "pregnant",
    label: "妊娠中の方（世田谷区）",
    note: "妊娠期の制度は子どもの月齢で表せないため、別の軸で判定されます。",
    profile: {
      areaCode: "131121",
      children: [],
      isPregnant: true,
      isSingleParent: false,
      hasDisability: false,
    },
  },
  {
    id: "baby",
    label: "0歳児を育てている方（台東区）",
    note: "健診・予防接種など、月齢の刻みが細かい時期です。",
    profile: {
      areaCode: "131067",
      children: [{ ageMonths: 4 }],
      isPregnant: false,
      isSingleParent: false,
      hasDisability: false,
    },
  },
  {
    id: "siblings",
    label: "きょうだいがいる方（八王子市）",
    note: "上の子だけが対象になる制度があります。どの子が該当したかが理由に出ます。",
    profile: {
      areaCode: "132012",
      children: [{ ageMonths: 30 }, { ageMonths: 96 }],
      isPregnant: false,
      isSingleParent: false,
      hasDisability: false,
    },
  },
  {
    id: "single-parent",
    label: "ひとり親の方（足立区）",
    note: "ひとり親向けの制度が上位に来ます。該当しない制度も隠しません（対象なのに出ない、を作らないため）。",
    profile: {
      areaCode: "131211",
      children: [{ ageMonths: 60 }],
      isPregnant: false,
      isSingleParent: true,
      hasDisability: false,
    },
  },
  {
    id: "disability",
    label: "障がいのあるお子さんがいる方（練馬区）",
    note: "障がい児向けに分類された制度が上位に来ます。分類は統計的な推定なので断定はしません。",
    profile: {
      areaCode: "131202",
      children: [{ ageMonths: 84 }],
      isPregnant: false,
      isSingleParent: false,
      hasDisability: true,
    },
  },
];

/** プロフィールを `/api/benefits/match` のクエリ文字列にする。 */
export function toSearchParams(profile: Profile): URLSearchParams {
  const params = new URLSearchParams();
  if (profile.areaCode) params.set("area_code", profile.areaCode);
  for (const child of profile.children) {
    if (child.birthDate) params.append("child_birth_date", child.birthDate);
    else if (child.ageMonths != null) params.append("child_age_months", String(child.ageMonths));
  }
  if (profile.isPregnant) params.set("is_pregnant", "true");
  if (profile.isSingleParent) params.set("is_single_parent", "true");
  if (profile.hasDisability) params.set("has_disability", "true");
  return params;
}

/**
 * URL のクエリからプロフィールを復元する。
 *
 * **壊れた値は落とす。** 共有された URL は誰でも書き換えられるので、
 * ここで通すと backend が 422 を返して画面がエラーになる。
 */
export function fromSearchParams(params: URLSearchParams | Record<string, string | string[]>): Profile {
  const get = (key: string): string[] => {
    if (params instanceof URLSearchParams) return params.getAll(key);
    const value = params[key];
    if (value == null) return [];
    return Array.isArray(value) ? value : [value];
  };
  const first = (key: string): string | undefined => get(key)[0];

  const children: Child[] = [];
  for (const value of get("child_birth_date")) {
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) children.push({ birthDate: value });
  }
  for (const value of get("child_age_months")) {
    const months = Number(value);
    // backend 側の制約（ge=0, le=300）と揃える。ここで落とさないと 422 になる
    if (Number.isInteger(months) && months >= 0 && months <= 300) children.push({ ageMonths: months });
  }

  return {
    areaCode: /^\d{6}$/.test(first("area_code") ?? "") ? first("area_code") : undefined,
    children: children.slice(0, 10), // backend の max_length=10 と揃える
    isPregnant: first("is_pregnant") === "true",
    isSingleParent: first("is_single_parent") === "true",
    hasDisability: first("has_disability") === "true",
  };
}

/** 属性が1つでも指定されているか（未指定なら絞り込みをせず全件を出す）。 */
export function hasAnyAttribute(profile: Profile): boolean {
  return Boolean(
    profile.areaCode ||
      profile.children.length > 0 ||
      profile.isPregnant ||
      profile.isSingleParent ||
      profile.hasDisability,
  );
}

const STORAGE_KEY = "kosodate.profile";

/** localStorage から復元する。壊れていたら空のプロフィールを返す（例外を投げない）。 */
export function loadProfile(): Profile {
  if (typeof window === "undefined") return EMPTY_PROFILE;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY_PROFILE;
    const parsed = JSON.parse(raw);
    return { ...EMPTY_PROFILE, ...parsed, children: Array.isArray(parsed.children) ? parsed.children : [] };
  } catch {
    return EMPTY_PROFILE;
  }
}

/** localStorage に保存する。**失敗しても例外を投げない**（保存できなくても URL で動く）。 */
export function saveProfile(profile: Profile): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(profile));
  } catch {
    /* プライベートモード等で保存できないことがある。URL が正なので致命ではない */
  }
}
