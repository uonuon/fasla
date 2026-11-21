import { BedrockRuntimeClient, ConverseCommand, type Message, type Tool } from "@aws-sdk/client-bedrock-runtime";
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

/** Amazon Bedrock, Converse API. maxTokens is always explicit (it reserves quota). */
export class BedrockLlm implements Llm {
  private client: BedrockRuntimeClient;
  constructor(cfg: Config) {
    this.client = new BedrockRuntimeClient({ region: cfg.region, maxAttempts: 4, retryMode: "adaptive" });
  }

  async complete(req: LlmRequest): Promise<LlmResult> {
    const messages: Message[] = req.messages.map((m) => ({ role: m.role, content: [{ text: m.text }] }));
    const tools: Tool[] | undefined = req.tool
      ? [{ toolSpec: { name: req.tool.name, description: req.tool.description, inputSchema: { json: req.tool.schema as never } } }]
      : undefined;
    try {
      const r = await this.client.send(
        new ConverseCommand({
          modelId: req.model,
          system: [{ text: req.system }],
          messages,
          inferenceConfig: { maxTokens: req.maxTokens, temperature: req.temperature ?? 0.2 },
          ...(tools ? { toolConfig: { tools, toolChoice: { tool: { name: req.tool!.name } } } } : {}),
        }),
      );
      const content = r.output?.message?.content ?? [];
      const text = content.map((c) => c.text ?? "").join("").trim();
      const tool = content.find((c) => c.toolUse)?.toolUse;
      return {
        text,
        json: tool?.input,
        inputTokens: r.usage?.inputTokens ?? 0,
        outputTokens: r.usage?.outputTokens ?? 0,
        model: req.model,
      };
    } catch (e) {
      const name = (e as { name?: string }).name ?? "";
      const retryable = ["ThrottlingException", "ModelTimeoutException", "ServiceUnavailableException", "InternalServerException"].includes(name);
      console.error(JSON.stringify({ level: "error", msg: "bedrock call failed", model: req.model, error: name, detail: String((e as Error).message).slice(0, 300) }));
      throw new LlmError(
        name === "AccessDeniedException"
          ? "The AI model isn't enabled for this AWS project yet."
          : retryable
            ? "The AI is busy right now. Please try again in a minute."
            : "The AI couldn't answer this time. Please try again.",
        retryable,
      );
    }
  }
}

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
