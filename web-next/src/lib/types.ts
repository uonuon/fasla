// Shapes returned by the FastAPI backend (see fasla/main.py and brandopt/view.py).

export type AppConfig = {
  authMode: "supabase" | "dev";
  supabaseUrl: string;
  supabaseAnonKey: string;
  googleLogin: boolean;
  maxUploadMb: number;
};

export type Brand = { id: string; name: string; created_at: string; plans: number };

export type PlanStatus = "draft" | "queued" | "running" | "done" | "failed";

export type PlanSummary = {
  id: string;
  brand_id: string;
  status: PlanStatus;
  input_name: string;
  summary: { profit: number; units: number; budget: number; leftover: number; cash_min: number } | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
};

export type Checks = {
  errors: string[];
  warnings: string[];
  stats: {
    orders: number;
    products: number;
    styles: number;
    seasons: string[];
    couriers: string[];
    zones: number;
    date_from: string;
    date_to: string;
    new_styles: string[];
    owner_estimates: number;
    mode?: "orders" | "template";
    style_list?: { id: string; name: string; price: number; cost: number | null; sold_last_year: number }[];
    columns?: Record<string, string>;
    ai_mapped?: boolean;
    personal_removed?: string[];
    dropped_columns?: number;
  };
};

export type Settings = Record<string, number>;

export type StyleResult = { id: string; planned: number; stock?: number; sold: number; left: number; sellout: number };

export type Variant = {
  mult: number;
  budget: number;
  profit: number;
  p10: number;
  p90: number;
  revenue: number;
  cash_used: number;
  units: number;
  leftover: number;
  leftover_cost: number;
  lost: number;
  fill: number;
  Q: number[];
  cash: number[];
  cash_min: number;
  cash_min_week: number;
  style: StyleResult[];
};

export type StyleMeta = {
  id: string;
  name: string;
  category: string;
  colors: string[];
  cost: number;
  price: number;
  source: string;
  f_mean: number;
  f_p10: number;
  f_p90: number;
  owner: number | null;
  size_share: number[];
};

export type CourierAlt = { courier: string; rate: number; fee: number; profit: number; seen: number };
export type Zone = { zone: string; orders: number; pick: string; rate: number; fee: number; profit: number; now: string; alts: CourierAlt[] };

export type PlanView = {
  brand: string;
  simulated: boolean;
  settings: Settings;
  sizes: string[];
  sku: { s: string; c: string; z: string }[];
  styles: StyleMeta[];
  variants: Variant[];
  base: number;
  usual: Variant;
  couriers: { zones: Zone[]; now: number; plan: number; orders: number };
  exchanges: {
    base: number;
    rows: { id: string; name: string; n: number; rate: number; low: number; high: number; flag: boolean; size: string; size_rate: number }[];
  };
  refusal: { overall: number; zones: { zone: string; rate: number; n: number }[] };
  cash_weeks: number;
  lead_weeks: number;
  problems: string[];
  history: {
    orders: number;
    seasons: number;
    soldout: number;
    skus: number;
    growth: number;
    soldout_inferred?: number;
    production_known?: boolean;
  };
  check?: { kind?: "window"; season: string; styles: number; tests?: number; units_per_style?: number; style_error: number; repeat_last_error: number; total_bias: number } | null;
};

export type Plan = PlanSummary & { brand_name: string; settings: Settings; checks: Checks | null; view?: PlanView };
