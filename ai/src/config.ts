// All settings come from the environment, so the same image runs locally, in tests and on AWS.

const num = (v: string | undefined, d: number) => {
  const n = Number(v);
  return v !== undefined && v !== "" && Number.isFinite(n) ? n : d;
};

export type ModelPrice = { input: number; output: number }; // USD per million tokens

export const config = () => ({
  port: num(process.env.AI_PORT, 3001),
  host: process.env.AI_HOST || "127.0.0.1", // sidecar: only the app in the same task can reach it
  internalKey: process.env.AI_INTERNAL_KEY || "",
  region: process.env.AWS_REGION || "eu-north-1",
  // "bedrock" (default) or "fake" (tests and local work without AWS)
  provider: (process.env.AI_PROVIDER || "bedrock") as "bedrock" | "fake",
  fastModel: process.env.AI_FAST_MODEL || "eu.anthropic.claude-haiku-5-5",
  smartModel: process.env.AI_SMART_MODEL || "eu.anthropic.claude-sonnet-5-5",
  databaseUrl: process.env.AI_DATABASE_URL || process.env.DATABASE_URL || "",
  // Spending: a hard monthly cap for the whole app, and allowances per user per month
  monthlyBudgetUsd: num(process.env.AI_MONTHLY_BUDGET_USD, 10),
  limits: {
    explain: num(process.env.AI_LIMIT_EXPLAIN, 3),
    ask: num(process.env.AI_LIMIT_ASK, 20),
    map: num(process.env.AI_LIMIT_MAP, 20),
  },
  perMinute: num(process.env.AI_RATE_PER_MINUTE, 5),
  alertTopicArn: process.env.AI_ALERT_TOPIC_ARN || "",
  // List prices, USD per million tokens. Override with AI_PRICES='{"model-id":{"input":..,"output":..}}'.
  prices: {
    "eu.anthropic.claude-haiku-5-5": { input: 0.1, output: 0.5 },
    "global.anthropic.claude-haiku-5-5": { input: 0.1, output: 0.5 },
    "eu.anthropic.claude-sonnet-5-5": { input: 2, output: 10 },
    "global.anthropic.claude-sonnet-5-5": { input: 2, output: 10 },
    ...(process.env.AI_PRICES ? (JSON.parse(process.env.AI_PRICES) as Record<string, ModelPrice>) : {}),
  } as Record<string, ModelPrice>,
});

export type Config = ReturnType<typeof config>;

/** Cost in USD; unknown models are priced like the most expensive one we know, never as free. */
export function costUsd(cfg: Config, model: string, inputTokens: number, outputTokens: number): number {
  const p = cfg.prices[model] ?? Object.values(cfg.prices).reduce((a, b) => (b.output > a.output ? b : a));
  return (inputTokens * p.input + outputTokens * p.output) / 1e6;
}
