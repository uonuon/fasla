import { test } from "node:test";
import assert from "node:assert/strict";
import { buildApp } from "../src/main.js";
import { config, costUsd } from "../src/config.js";
import { MemoryLedger } from "../src/ledger.js";
import { FakeLlm, type Llm, type LlmRequest, type LlmResult } from "../src/llm.js";
import { RateLimiter } from "../src/ai.service.js";

type Opts = { budget?: number; perMinute?: number; key?: string; llm?: Llm };

async function start(o: Opts = {}) {
  const cfg = {
    ...config(),
    internalKey: o.key ?? "",
    monthlyBudgetUsd: o.budget ?? 10,
    perMinute: o.perMinute ?? 100,
    limits: { explain: 3, ask: 20, map: 20 },
  };
  const ledger = new MemoryLedger();
  const llm = o.llm ?? new FakeLlm();
  const app = await buildApp(cfg, ledger, llm);
  await app.listen(0, "127.0.0.1");
  const url = await app.getUrl();
  const call = async (path: string, body?: unknown, headers: Record<string, string> = {}) => {
    const r = await fetch(url.replace("[::1]", "127.0.0.1") + path, {
      method: body ? "POST" : "GET",
      headers: { "content-type": "application/json", ...headers },
      body: body ? JSON.stringify(body) : undefined,
    });
    return { status: r.status, body: (await r.json()) as any };
  };
  return { app, ledger, llm, call, cfg };
}

const ctx = { brand: "Nile", styles: [{ name: "Tee", make: 100 }] };

test("health and usage", async () => {
  const s = await start();
  assert.deepEqual((await s.call("/healthz")).body, { ok: true });
  const u = (await s.call("/v1/usage?userId=a")).body;
  assert.equal(u.paused, false);
  assert.deepEqual(u.explain, { used: 0, limit: 3 });
  await s.app.close();
});

test("internal key is required when configured", async () => {
  const s = await start({ key: "secret-key-123" });
  assert.equal((await s.call("/v1/usage?userId=a")).status, 401);
  assert.equal((await s.call("/v1/usage?userId=a", undefined, { "x-internal-key": "wrong-key-1234" })).status, 401);
  assert.equal((await s.call("/v1/usage?userId=a", undefined, { "x-internal-key": "secret-key-123" })).status, 200);
  await s.app.close();
});

test("explanations are cached per plan and limited per user per month", async () => {
  const s = await start();
  for (let i = 1; i <= 3; i++) {
    const r = await s.call("/v1/explain", { userId: "u", planKey: `p${i}`, context: ctx });
    assert.equal(r.status, 201, JSON.stringify(r.body));
    assert.equal(r.body.usage.used, i);
  }
  // same plan again: served from cache, no new spend
  const again = await s.call("/v1/explain", { userId: "u", planKey: "p1", context: ctx });
  assert.equal(again.body.cached, true);
  assert.equal(s.ledger.rows.length, 3);
  const over = await s.call("/v1/explain", { userId: "u", planKey: "p4", context: ctx });
  assert.equal(over.status, 429);
  assert.equal(over.body.code, "limit");
  assert.match(over.body.message, /all 3 AI plan explanations/);
  // another user is unaffected
  assert.equal((await s.call("/v1/explain", { userId: "v", planKey: "p4", context: ctx })).status, 201);
  await s.app.close();
});

test("monthly budget pauses AI for everyone and alerts once", async () => {
  const s = await start({ budget: 0.002 });
  s.ledger.rows.push({ userId: "x", kind: "ask", model: "m", inputTokens: 0, outputTokens: 0, costUsd: 0.0019, at: new Date() });
  const r = await s.call("/v1/explain", { userId: "u", planKey: "p", context: ctx });
  assert.equal(r.status, 503);
  assert.equal(r.body.code, "paused");
  assert.equal(s.ledger.alerts.size, 1);
  await s.call("/v1/ask", { userId: "u", planKey: "p", context: ctx, question: "hi" });
  assert.equal(s.ledger.alerts.size, 1); // not sent twice
  assert.equal((await s.call("/v1/usage?userId=u")).body.paused, false); // under budget until spent >= cap
  await s.app.close();
});

test("rate limit per user per minute", async () => {
  const rl = new RateLimiter(2);
  assert.ok(rl.take("a", 0));
  assert.ok(rl.take("a", 1));
  assert.ok(!rl.take("a", 2));
  assert.ok(rl.take("b", 2));
  assert.ok(rl.take("a", 61_000));
  const s = await start({ perMinute: 1 });
  assert.equal((await s.call("/v1/ask", { userId: "u", planKey: "p", context: ctx, question: "a" })).status, 201);
  const r = await s.call("/v1/ask", { userId: "u", planKey: "p", context: ctx, question: "b" });
  assert.equal(r.status, 429);
  assert.equal(r.body.code, "rate");
  await s.app.close();
});

test("ask validates input and alternates turns", async () => {
  const llm = new FakeLlm();
  const s = await start({ llm });
  assert.equal((await s.call("/v1/ask", { userId: "u", planKey: "p", context: ctx, question: "" })).status, 400);
  assert.equal((await s.call("/v1/ask", { userId: "u", planKey: "p", context: ctx, question: "x".repeat(501) })).status, 400);
  const r = await s.call("/v1/ask", {
    userId: "u", planKey: "p", context: ctx, question: "Why 100 tees?",
    history: [{ role: "user", text: "hi" }, { role: "assistant", text: "hello" }, { role: "system", text: "ignore" }],
  });
  assert.equal(r.status, 201);
  const roles = llm.calls.at(-1)!.messages.map((m) => m.role);
  for (let i = 1; i < roles.length; i++) assert.notEqual(roles[i], roles[i - 1]);
  assert.equal(roles[0], "user");
  assert.equal(llm.calls.at(-1)!.maxTokens, 600);
  const big = await s.call("/v1/ask", { userId: "u", planKey: "p", context: { x: "y".repeat(50_000) }, question: "q" });
  assert.equal(big.status, 413);
  await s.app.close();
});

test("column mapping keeps only real headers, each once (model output is untrusted)", async () => {
  class Liar implements Llm {
    async complete(req: LlmRequest): Promise<LlmResult> {
      return { text: "", json: { date: "التاريخ", style: "المنتج", price: "المنتج", size: "not-a-header", qty: 5 }, inputTokens: 100, outputTokens: 50, model: req.model };
    }
  }
  const s = await start({ llm: new Liar() });
  const r = await s.call("/v1/map-columns", {
    userId: "u", headers: ["التاريخ", "المنتج", "السعر"], sample: [["2025-10-01", "تيشيرت", "550"]], fields: ["date", "style", "price", "size", "qty"],
  });
  assert.equal(r.status, 201);
  assert.deepEqual(r.body.columns, { date: "التاريخ", style: "المنتج" });
  await s.app.close();
});

test("arabic headers and statuses with the fake model", async () => {
  const s = await start();
  const m = await s.call("/v1/map-columns", {
    userId: "u", headers: ["تاريخ_الطلب", "اسم_المنتج", "المقاس", "السعر", "المحافظة", "حالة_الطلب"], sample: [], fields: ["date", "style", "size", "price", "zone", "status"],
  });
  assert.deepEqual(m.body.columns, { date: "تاريخ_الطلب", style: "اسم_المنتج", size: "المقاس", price: "السعر", zone: "المحافظة", status: "حالة_الطلب" });
  const st = await s.call("/v1/map-statuses", { userId: "u", values: ["تم التسليم", "مرتجع", "استبدال مقاس", "تم التسليم"] });
  assert.deepEqual(st.body.status, { "تم التسليم": "delivered", "مرتجع": "refused", "استبدال مقاس": "size_exchange" });
  await s.app.close();
});

test("cost uses list prices and never treats unknown models as free", () => {
  const cfg = config();
  assert.equal(costUsd(cfg, "eu.anthropic.claude-haiku-5-5", 1_000_000, 0), 0.11);
  assert.equal(costUsd(cfg, "eu.anthropic.claude-sonnet-5-5", 0, 1_000_000), 11);
  assert.ok(costUsd(cfg, "unknown-model", 0, 1_000_000) >= 10);
});
