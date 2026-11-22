import type { Config } from "./config.js";

export type LlmRequest = {
  model: string;
  system: string;
  messages: { role: "user" | "assistant"; text: string }[];
  maxTokens: number;
  temperature?: number;
  /** When set, the model must answer by calling this tool; its JSON input is returned as `json`. */
  tool?: { name: string; description: string; schema: Record<string, unknown> };
};

export type LlmResult = { text: string; json?: unknown; inputTokens: number; outputTokens: number; model: string };

export interface Llm {
  complete(req: LlmRequest): Promise<LlmResult>;
}

export class LlmError extends Error {
  constructor(message: string, public readonly retryable = false) {
    super(message);
  }
}

const ANTHROPIC_URL = "https://api.anthropic.com/v1/messages";

/** Anthropic Messages API. max_tokens is always explicit; transient errors are retried with backoff. */
export class AnthropicLlm implements Llm {
  constructor(
    private readonly cfg: Config,
    private readonly fetchFn: typeof fetch = fetch,
  ) {}

  async complete(req: LlmRequest): Promise<LlmResult> {
    const key = this.cfg.anthropicApiKey;
    if (!key || key === "-") throw new LlmError("The AI isn't set up yet.");
    const body = {
      model: req.model,
      max_tokens: req.maxTokens,
      temperature: req.temperature ?? 0.2,
      system: req.system,
      messages: req.messages.map((m) => ({ role: m.role, content: m.text })),
      ...(req.tool
        ? {
            tools: [{ name: req.tool.name, description: req.tool.description, input_schema: req.tool.schema }],
            tool_choice: { type: "tool", name: req.tool.name },
          }
        : {}),
    };
    let lastStatus = 0;
    for (let attempt = 0; attempt < 3; attempt++) {
      let res: Response;
      try {
        res = await this.fetchFn(ANTHROPIC_URL, {
          method: "POST",
          headers: { "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01" },
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(60_000),
        });
      } catch (e) {
        lastStatus = 0;
        console.error(JSON.stringify({ level: "error", msg: "anthropic call failed", model: req.model, error: String((e as Error).message).slice(0, 200) }));
        await sleep(1000 * 2 ** attempt);
        continue;
      }
      if (res.ok) {
        const r = (await res.json()) as {
          content?: { type: string; text?: string; input?: unknown }[];
          usage?: { input_tokens?: number; output_tokens?: number; cache_read_input_tokens?: number; cache_creation_input_tokens?: number };
        };
        const content = r.content ?? [];
        const u = r.usage ?? {};
        return {
          text: content.filter((c) => c.type === "text").map((c) => c.text ?? "").join("").trim(),
          json: content.find((c) => c.type === "tool_use")?.input,
          inputTokens: (u.input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0),
          outputTokens: u.output_tokens ?? 0,
          model: req.model,
        };
      }
      lastStatus = res.status;
      const detail = (await res.text().catch(() => "")).slice(0, 300);
      console.error(JSON.stringify({ level: "error", msg: "anthropic call failed", model: req.model, status: res.status, detail }));
      if (![429, 500, 502, 503, 504, 529].includes(res.status)) {
        throw new LlmError(
          res.status === 401 || res.status === 403
            ? "The AI isn't set up yet."
            : /credit balance/i.test(detail)
              ? "The AI is paused for now. Please try again later."
              : "The AI couldn't answer this time. Please try again.",
        );
      }
      const wait = Number(res.headers.get("retry-after"));
      await sleep(Number.isFinite(wait) && wait > 0 ? Math.min(wait, 10) * 1000 : 1000 * 2 ** attempt);
    }
    throw new LlmError(
      lastStatus === 429 || lastStatus === 529 ? "The AI is busy right now. Please try again in a minute." : "The AI couldn't answer this time. Please try again.",
      true,
    );
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Deterministic stand-in for tests and local work without AWS. */
export class FakeLlm implements Llm {
  public calls: LlmRequest[] = [];
  async complete(req: LlmRequest): Promise<LlmResult> {
    this.calls.push(req);
    const prompt = req.messages.map((m) => m.text).join("\n");
    let json: unknown;
    if (req.tool?.name === "map_columns") {
      const headers: string[] = JSON.parse(prompt.split("HEADERS:")[1].split("\n")[0]);
      const pick = (...words: string[]) => headers.find((h) => words.some((w) => h.includes(w))) ?? null;
      json = {
        date: pick("تاريخ", "date", "day"),
        style: pick("منتج", "product", "item", "model"),
        size: pick("مقاس", "size"),
        color: pick("لون", "colo"),
        qty: pick("كمية", "qty", "quant"),
        price: pick("سعر", "price"),
        zone: pick("محافظة", "مدينة", "city", "gov"),
        status: pick("حالة", "status"),
        courier: pick("شركة", "courier"),
        cost: null,
        category: null,
      };
    } else if (req.tool?.name === "map_statuses") {
      const values: string[] = JSON.parse(prompt.split("VALUES:")[1].split("\n")[0]);
      json = {
        statuses: values.map((v) => ({
          value: v,
          status: /مرتجع|رفض|ملغ/.test(v) ? "refused" : /استبدال/.test(v) ? "size_exchange" : "delivered",
        })),
      };
    }
    const text = req.tool ? "" : `FAKE ANSWER (${req.model}): ${req.messages[req.messages.length - 1].text.slice(0, 80)}`;
    return { text, json, inputTokens: Math.ceil(prompt.length / 4), outputTokens: 120, model: req.model };
  }
}
