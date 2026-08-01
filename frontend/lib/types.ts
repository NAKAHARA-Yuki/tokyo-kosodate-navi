// backend (app/routers/*.py) のレスポンス形に対応する型。
// バックエンド側の変更に追従させる必要がある（今のところ自動生成はしていない）。

// GET /api/benefits のレスポンス1件分（app/routers/benefits.py: search_benefits）
export type Benefit = {
  benefit_id: string;
  title: string;
  category: string;
  summary: string;
  min_age_months: number | null;
  max_age_months: number | null;
  age_source: "explicit" | "inferred" | "unknown";
  area_name: string | null;
  has_free_text_conditions: boolean;
  is_free: boolean;
  has_amount_info: boolean;
  electronic_submission: boolean;
};
