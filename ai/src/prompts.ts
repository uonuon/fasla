// Prompts and tool schemas. Everything the model sees about a brand is that brand's own data.

export const MAP_FIELDS: Record<string, string> = {
  date: "date the order was placed",
  style: "product / style / item name (not the order number, not the SKU code if a name exists)",
  size: "size of the item (S, M, L, 38, 40, …); null if size is only inside the product name",
  color: "colour of the item; null if only inside the product name",
  qty: "quantity / number of pieces in the line",
  price: "selling price of one piece (not the order total, not shipping)",
  zone: "city, governorate or delivery zone",
  status: "order or delivery status (delivered, returned, cancelled…)",
  courier: "shipping / courier company",
  cost: "cost to make one piece",
  category: "product type / category",
};

export const MAP_SYSTEM = `You match the columns of a fashion brand's orders spreadsheet to the fields a planning tool needs.
The headers may be in Arabic, English, Franco-Arabic or abbreviations; use the sample rows to decide.
Answer only with the tool. For each field give the exact header from the list, or null if no column fits.
Never use the same header for two fields. Prefer null to a weak guess.`;

export function mapPrompt(headers: string[], original: string[], sample: string[][], fields: string[]): string {
  const lines = sample.slice(0, 8).map((r) => JSON.stringify(r.map((v) => String(v).slice(0, 40))));
  return [
    `HEADERS: ${JSON.stringify(headers)}`,
    `ORIGINAL HEADER TEXT: ${JSON.stringify(original)}`,
    `SAMPLE ROWS (same order as headers):`,
    ...lines,
    ``,
    `FIELDS TO FILL:`,
    ...fields.map((f) => `- ${f}: ${MAP_FIELDS[f] ?? f}`),
  ].join("\n");
}

export function mapSchema(fields: string[], headers: string[]) {
  const prop = { anyOf: [{ type: "string", enum: headers }, { type: "null" }] };
  return { type: "object", properties: Object.fromEntries(fields.map((f) => [f, prop])), required: fields };
}

export const STATUS_SYSTEM = `You classify order status values from a fashion e-commerce brand (Egypt, cash on delivery) into exactly one of:
- delivered: the customer received and kept the order (also paid, completed, shipped and not returned)
- refused: refused at the door, returned, cancelled, failed delivery, not reachable
- size_exchange: exchanged for another size or item
Values may be Arabic, English or Franco-Arabic. Answer only with the tool.`;

export const statusPrompt = (values: string[]) => `VALUES: ${JSON.stringify(values)}\nClassify every value.`;

export const statusSchema = (values: string[]) => ({
  type: "object",
  properties: {
    statuses: {
      type: "array",
      items: {
        type: "object",
        properties: {
          value: { type: "string", enum: values },
          status: { type: "string", enum: ["delivered", "refused", "size_exchange"] },
        },
        required: ["value", "status"],
      },
    },
  },
  required: ["statuses"],
});

export const EXPLAIN_SYSTEM = `You are Fasla's planning assistant. You explain a production plan to the owner of a small fashion brand in Egypt.
The owner is busy and not a data person. Write in plain, warm, direct English. Money is in EGP.
Use ONLY the numbers in the plan data. Never invent figures. Round sensibly (e.g. 2.34M, 352K).
Structure (Markdown, about 220 words, no tables):
**In one line:** the main recommendation (which budget level and why).
Then 4 to 6 short bullets, the most useful first, covering: what to produce more or less of and why;
the cash risk (lowest point and week) at the budget they entered and at the recommended one; delivery/courier savings if any;
returns or size problems if flagged; anything in data_notes that limits how much to trust the plan.
End with **Next steps:** 2 or 3 concrete actions for this week.`;

export const ASK_SYSTEM = `You are Fasla's planning assistant answering a fashion brand owner's question about THEIR plan.
Use ONLY the plan data provided; it is the brand's own data. If the answer isn't in the data, say so and suggest what would help.
Be brief: 2 to 6 sentences or a short list. Plain English, EGP, rounded numbers. No tables.
If asked to change the plan, explain the trade-off using the budget options and say they can re-run the plan with new settings.
Politely decline questions unrelated to their brand, plan, production, cash, delivery or returns.`;

export const contextBlock = (ctx: unknown) => `PLAN DATA (JSON):\n${JSON.stringify(ctx)}`;
