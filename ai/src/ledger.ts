import pg from "pg";

export type Kind = "explain" | "ask" | "map";
export type UsageRow = { userId: string; kind: Kind; model: string; inputTokens: number; outputTokens: number; costUsd: number };

/** Where usage, cached answers and sent alerts are kept. */
export interface Ledger {
  init(): Promise<void>;
  monthSpendUsd(): Promise<number>;
  userCount(userId: string, kind: Kind): Promise<number>;
  record(row: UsageRow): Promise<void>;
  getCached(key: string): Promise<string | null>;
  setCached(key: string, text: string): Promise<void>;
  /** Returns true only the first time for this month+level (so each alert is sent once). */
  claimAlert(level: string): Promise<boolean>;
  close(): Promise<void>;
}

export const monthKey = (d = new Date()) => d.toISOString().slice(0, 7); // UTC "2026-11"

export class MemoryLedger implements Ledger {
  rows: (UsageRow & { at: Date })[] = [];
  cache = new Map<string, string>();
  alerts = new Set<string>();
  async init() {}
  async monthSpendUsd() {
    const m = monthKey();
    return this.rows.filter((r) => monthKey(r.at) === m).reduce((t, r) => t + r.costUsd, 0);
  }
  async userCount(userId: string, kind: Kind) {
    const m = monthKey();
    return this.rows.filter((r) => r.userId === userId && r.kind === kind && monthKey(r.at) === m).length;
  }
  async record(row: UsageRow) {
    this.rows.push({ ...row, at: new Date() });
  }
  async getCached(key: string) {
    return this.cache.get(key) ?? null;
  }
  async setCached(key: string, text: string) {
    this.cache.set(key, text);
  }
  async claimAlert(level: string) {
    const k = `${monthKey()}:${level}`;
    if (this.alerts.has(k)) return false;
    this.alerts.add(k);
    return true;
  }
  async close() {}
}

/**
 * Postgres (Supabase). Tables live in their own schema, `fasla_ai`, which the public
 * Supabase API roles (anon, authenticated) cannot read: usage data is server-only.
 */
export class PgLedger implements Ledger {
  private pool: pg.Pool;
  constructor(url: string) {
    // Supabase accepts TLS; its pooler certificate chain isn't in Node's default store, so verify-full isn't possible here
    const ssl = process.env.AI_DB_SSL === "1" ? { rejectUnauthorized: false } : undefined;
    this.pool = new pg.Pool({ connectionString: url, ssl, max: 3, connectionTimeoutMillis: 10_000, idleTimeoutMillis: 30_000 });
  }

  async init() {
    await this.pool.query(`
      create schema if not exists fasla_ai;
      create table if not exists fasla_ai.usage (
        id bigserial primary key,
        user_id text not null,
        kind text not null,
        model text not null,
        input_tokens integer not null,
        output_tokens integer not null,
        cost_usd numeric(12, 6) not null,
        created_at timestamptz not null default now()
      );
      create index if not exists usage_user_month on fasla_ai.usage (user_id, kind, created_at);
      create index if not exists usage_month on fasla_ai.usage (created_at);
      create table if not exists fasla_ai.cache (
        key text primary key,
        text text not null,
        created_at timestamptz not null default now()
      );
      create table if not exists fasla_ai.alerts (
        key text primary key,
        created_at timestamptz not null default now()
      );
      do $$
      begin
        if exists (select 1 from pg_roles where rolname = 'anon') then
          execute 'revoke all on schema fasla_ai from anon, authenticated';
          execute 'revoke all on all tables in schema fasla_ai from anon, authenticated';
        end if;
      end $$;
    `);
  }

  private monthStart() {
    const d = new Date();
    return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1));
  }

  async monthSpendUsd() {
    const r = await this.pool.query("select coalesce(sum(cost_usd), 0)::float8 as s from fasla_ai.usage where created_at >= $1", [this.monthStart()]);
    return Number(r.rows[0].s);
  }

  async userCount(userId: string, kind: Kind) {
    const r = await this.pool.query(
      "select count(*)::int as n from fasla_ai.usage where user_id = $1 and kind = $2 and created_at >= $3",
      [userId, kind, this.monthStart()],
    );
    return Number(r.rows[0].n);
  }

  async record(row: UsageRow) {
    await this.pool.query(
      "insert into fasla_ai.usage (user_id, kind, model, input_tokens, output_tokens, cost_usd) values ($1, $2, $3, $4, $5, $6)",
      [row.userId, row.kind, row.model, row.inputTokens, row.outputTokens, row.costUsd],
    );
  }

  async getCached(key: string) {
    const r = await this.pool.query("select text from fasla_ai.cache where key = $1", [key]);
    return r.rows[0]?.text ?? null;
  }

  async setCached(key: string, text: string) {
    await this.pool.query(
      "insert into fasla_ai.cache (key, text) values ($1, $2) on conflict (key) do update set text = excluded.text, created_at = now()",
      [key, text],
    );
  }

  async claimAlert(level: string) {
    const r = await this.pool.query("insert into fasla_ai.alerts (key) values ($1) on conflict do nothing", [`${monthKey()}:${level}`]);
    return (r.rowCount ?? 0) > 0;
  }

  async close() {
    await this.pool.end();
  }
}

export function makeLedger(url: string): Ledger {
  return url.startsWith("postgres") ? new PgLedger(url) : new MemoryLedger();
}
