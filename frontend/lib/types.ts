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

// GET /api/subgraph のレスポンス（app/routers/benefits.py: get_subgraph）。
// cytoscape 用に nodes/edges の形で返ってくる。詳細ビューでは制度ノードの属性と、
// 条件(Status)・書類(Document)ノードを一覧として使う。
export type SubgraphNode = {
  data: {
    id: string;
    type: "Benefit" | "Status" | "Document";
    label: string;
    // Benefit のときだけ入る属性
    benefit_id?: string;
    category?: string;
    summary?: string;
    area_name?: string;
    min_age_months?: number | null;
    max_age_months?: number | null;
    cost_text?: string | null;
    cost_conditions_text?: string | null;
    monetary_support_text?: string | null;
    materially_support_text?: string | null;
    is_free?: boolean;
    department?: string | null;
    contact_name?: string | null;
    contact_phone?: string | null;
    contact_email?: string | null;
    contact_address?: string | null;
    official_url?: string | null;
    official_title?: string | null;
    procedure_method?: string | null;
    procedure_counter?: string | null;
    electronic_submission?: boolean;
    regulation_name?: string | null;
    update_date?: string | null;
    // Status のときだけ入る
    status_type?: string;
  };
};

// GET /api/data-source のレスポンス（app/routers/meta.py: get_data_source）。
// フッターに出す出典とデータの鮮度。鮮度の取得に失敗した場合は各値が null になり、
// 出典（source）だけが返る。
export type DataSource = {
  source: { name: string; url: string; publisher: string };
  benefit_count: number | null;
  area_count: number | null;
  latest_update_date: string | null;
};

export type Subgraph = {
  nodes: SubgraphNode[];
  edges: { data: { id: string; source: string; target: string; label: string } }[];
};
