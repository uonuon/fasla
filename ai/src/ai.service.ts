import { HttpException, HttpStatus, Inject, Injectable, OnModuleDestroy, OnModuleInit } from "@nestjs/common";
import { PublishCommand, SNSClient } from "@aws-sdk/client-sns";
import { CONFIG, LEDGER, LLM } from "./tokens.js";
import { costUsd, type Config } from "./config.js";
import type { Kind, Ledger } from "./ledger.js";
import { LlmError, type Llm, type LlmRequest } from "./llm.js";
import * as P from "./prompts.js";

const MAX_CONTEXT_CHARS = 40_000; // ~10K tokens: keeps every call far under 100K-token pricing tiers
const MAX_QUESTION_CHARS = 500;

export type Usage = { kind: Kind; used: number; limit: number };

export function aiError(status: number, code: string, message: string): HttpException {
  return new HttpException({ code, message }, status);
}

/** Sliding one-minute window per user, in memory (one sidecar per app task). */
export class RateLimiter {
  private hits = new Map<string, number[]>();
  constructor(private perMinute: number) {}
  take(userId: string, now = Date.now()): boolean {
    const recent = (this.hits.get(userId) ?? []).filter((t) => now - t < 60_000);
    if (recent.length >= this.perMinute) {
      this.hits.set(userId, recent);
      return false;
    }
    recent.push(now);
    this.hits.set(userId, recent);
    if (this.hits.size > 10_000) this.hits.clear();
    return true;
  }
}

@Injectable()
export class AiService implements OnModuleInit, OnModuleDestroy {
  private limiter: RateLimiter;
  private sns?: SNSClient;

  constructor(
    @Inject(CONFIG) private cfg: Config,
    @Inject(LEDGER) private ledger: Ledger,
    @Inject(LLM) private llm: Llm,
  ) {
    this.limiter = new RateLimiter(cfg.perMinute);
    if (cfg.alertTopicArn) this.sns = new SNSClient({ region: cfg.region });
  }

  async onModuleInit() {
    await this.ledger.init();
  }

  async onModuleDestroy() {
    await this.ledger.close();
  }

  // ---------- limits ----------

  async usage(userId: string) {
    const spent = await this.ledger.monthSpendUsd();
    const kinds: Kind[] = ["explain", "ask", "map"];
    const counts = await Promise.all(kinds.map((k) => this.ledger.userCount(userId, k)));
    return {
      paused: spent >= this.cfg.monthlyBudgetUsd,
      explain: { used: counts[0], limit: this.cfg.limits.explain },
      ask: { used: counts[1], limit: this.cfg.limits.ask },
      map: { used: counts[2], limit: this.cfg.limits.map },
      resetsOn: nextMonthStart(),
    };
  }

  /** Checks rate, allowance and the monthly budget before spending anything. */
  private async guard(userId: string, kind: Kind, req: LlmRequest) {
    if (!userId || userId.length > 100) throw aiError(400, "bad_request", "Missing user.");
    if (!this.limiter.take(userId)) throw aiError(429, "rate", "Too many AI requests in a minute. Please wait a moment.");
    const used = await this.ledger.userCount(userId, kind);
    const limit = this.cfg.limits[kind];
    if (used >= limit) {
      const what = kind === "explain" ? "plan explanations" : kind === "ask" ? "questions" : "smart uploads";
      throw aiError(429, "limit", `You've used all ${limit} AI ${what} for this month. They reset on ${nextMonthStart()}.`);
    }
    const spent = await this.ledger.monthSpendUsd();
    const inputEstimate = Math.ceil((req.system.length + req.messages.reduce((t, m) => t + m.text.length, 0)) / 3);
    const worstCase = costUsd(this.cfg, req.model, inputEstimate, req.maxTokens);
    if (spent + worstCase > this.cfg.monthlyBudgetUsd) {
      await this.alert("100", spent);
      throw aiError(503, "paused", "Fasla's AI assistant is paused until the start of next month. Your plans still work as usual.");
    }
    return { used, limit };
  }

  private async call(userId: string, kind: Kind, req: LlmRequest) {
    const { used, limit } = await this.guard(userId, kind, req);
    let res;
    try {
      res = await this.llm.complete(req);
    } catch (e) {
      if (e instanceof LlmError) throw aiError(e.retryable ? 503 : 502, "ai_error", e.message);
      throw e;
    }
    const cost = costUsd(this.cfg, res.model, res.inputTokens, res.outputTokens);
    await this.ledger.record({ userId, kind, model: res.model, inputTokens: res.inputTokens, outputTokens: res.outputTokens, costUsd: cost });
    const spent = await this.ledger.monthSpendUsd();
    if (spent >= 0.8 * this.cfg.monthlyBudgetUsd) await this.alert(spent >= this.cfg.monthlyBudgetUsd ? "100" : "80", spent);
    console.log(JSON.stringify({ level: "info", msg: "ai call", kind, model: res.model, in: res.inputTokens, out: res.outputTokens, cost: +cost.toFixed(6), monthSpend: +spent.toFixed(4) }));
    return { res, usage: { kind, used: used + 1, limit } as Usage };
  }

  private async alert(level: "80" | "100", spent: number) {
    if (!(await this.ledger.claimAlert(level))) return;
    const subject = level === "100" ? "Fasla AI paused: monthly budget reached" : "Fasla AI: 80% of monthly budget used";
    const message =
      `Fasla's AI has spent $${spent.toFixed(2)} of its $${this.cfg.monthlyBudgetUsd} monthly budget (${new Date().toISOString().slice(0, 7)}).\n` +
      (level === "100" ? "AI features are paused for everyone until the 1st. Plans keep working." : "AI features pause automatically at 100%.") +
      "\nChange the cap with AI_MONTHLY_BUDGET_USD.";
    console.warn(JSON.stringify({ level: "warn", msg: subject, spent }));
    if (this.sns) {
      try {
        await this.sns.send(new PublishCommand({ TopicArn: this.cfg.alertTopicArn, Subject: subject, Message: message }));
      } catch (e) {
        console.error(JSON.stringify({ level: "error", msg: "alert publish failed", error: String(e) }));
      }
    }
  }

  // ---------- features ----------

  async mapColumns(userId: string, headers: string[], original: string[], sample: string[][], fields: string[]) {
    headers = (headers ?? []).map(String).slice(0, 80);
    fields = (fields ?? []).filter((f) => f in P.MAP_FIELDS);
    if (!headers.length || !fields.length) throw aiError(400, "bad_request", "Nothing to map.");
    const req: LlmRequest = {
      model: this.cfg.fastModel,
      system: P.MAP_SYSTEM,
      messages: [{ role: "user", text: P.mapPrompt(headers, (original ?? []).map(String), (sample ?? []).slice(0, 8), fields) }],
      maxTokens: 600,
      temperature: 0,
      tool: { name: "map_columns", description: "Report which header holds each field", schema: P.mapSchema(fields, headers) },
    };
    const { res, usage } = await this.call(userId, "map", req);
    // model output is untrusted: keep only real headers, each used once
    const out: Record<string, string> = {};
    const seen = new Set<string>();
    const raw = (res.json ?? {}) as Record<string, unknown>;
    for (const f of fields) {
      const h = raw[f];
      if (typeof h === "string" && headers.includes(h) && !seen.has(h)) {
        out[f] = h;
        seen.add(h);
      }
    }
    return { columns: out, usage };
  }

  async mapStatuses(userId: string, values: string[]) {
    values = Array.from(new Set((values ?? []).map((v) => String(v).trim().slice(0, 60)).filter(Boolean))).slice(0, 40);
    if (!values.length) return { status: {}, usage: null };
    const req: LlmRequest = {
      model: this.cfg.fastModel,
      system: P.STATUS_SYSTEM,
      messages: [{ role: "user", text: P.statusPrompt(values) }],
      maxTokens: 1200,
      temperature: 0,
      tool: { name: "map_statuses", description: "Classify each status value", schema: P.statusSchema(values) },
    };
    const { res, usage } = await this.call(userId, "map", req);
    const status: Record<string, string> = {};
    const list = ((res.json as { statuses?: unknown[] })?.statuses ?? []) as { value?: unknown; status?: unknown }[];
    for (const s of list) {
      if (typeof s.value === "string" && values.includes(s.value) && ["delivered", "refused", "size_exchange"].includes(String(s.status)))
        status[s.value] = String(s.status);
    }
    return { status, usage };
  }

  async explain(userId: string, planKey: string, context: unknown) {
    const key = `explain:${planKey}`;
    const cached = await this.ledger.getCached(key);
    if (cached) return { text: cached, cached: true, usage: await this.usageOf(userId, "explain") };
    const ctx = clip(context);
    const req: LlmRequest = {
      model: this.cfg.smartModel,
      system: P.EXPLAIN_SYSTEM,
      messages: [{ role: "user", text: `${P.contextBlock(ctx)}\n\nExplain this plan to the owner.` }],
      maxTokens: 900,
      temperature: 0.3,
    };
    const { res, usage } = await this.call(userId, "explain", req);
    if (!res.text) throw aiError(502, "ai_error", "The AI returned an empty answer. Please try again.");
    await this.ledger.setCached(key, res.text);
    return { text: res.text, cached: false, usage };
  }

  async ask(userId: string, planKey: string, context: unknown, question: string, history: { role: string; text: string }[]) {
    question = String(question ?? "").trim();
    if (!question) throw aiError(400, "bad_request", "Type a question first.");
    if (question.length > MAX_QUESTION_CHARS) throw aiError(400, "bad_request", `Keep questions under ${MAX_QUESTION_CHARS} characters.`);
    const past = (Array.isArray(history) ? history : [])
      .filter((m) => (m.role === "user" || m.role === "assistant") && typeof m.text === "string")
      .slice(-6)
      .map((m) => ({ role: m.role as "user" | "assistant", text: m.text.slice(0, 1500) }));
    // conversation must start with the user turn that carries the plan data
    const messages: LlmRequest["messages"] = [
      { role: "user", text: `${P.contextBlock(clip(context))}\n\nI'll ask questions about this plan.` },
      { role: "assistant", text: "Understood. Ask me anything about this plan." },
      ...past,
      { role: "user", text: question },
    ];
    const req: LlmRequest = { model: this.cfg.fastModel, system: P.ASK_SYSTEM, messages: fixAlternation(messages), maxTokens: 600, temperature: 0.2 };
    const { res, usage } = await this.call(userId, "ask", req);
    return { answer: res.text || "I couldn't find an answer to that in your plan.", usage };
  }

  private async usageOf(userId: string, kind: Kind): Promise<Usage> {
    return { kind, used: await this.ledger.userCount(userId, kind), limit: this.cfg.limits[kind] };
  }
}

function clip(context: unknown): unknown {
  const s = JSON.stringify(context ?? {});
  if (s.length <= MAX_CONTEXT_CHARS) return context;
  throw aiError(413, "too_large", "This plan is too large for the AI assistant.");
}

/** Bedrock requires user/assistant turns to alternate. */
function fixAlternation(msgs: LlmRequest["messages"]): LlmRequest["messages"] {
  const out: LlmRequest["messages"] = [];
  for (const m of msgs) {
    if (out.length && out[out.length - 1].role === m.role) out[out.length - 1] = { role: m.role, text: `${out[out.length - 1].text}\n\n${m.text}` };
    else out.push(m);
  }
  return out;
}

function nextMonthStart(): string {
  const d = new Date();
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1)).toISOString().slice(0, 10);
}

export { HttpStatus };
