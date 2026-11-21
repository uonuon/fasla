"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import SignIn from "@/components/SignIn";
import { ErrorBox, Loading, TopBar, useSession } from "@/components/shell";
import { api, apiJson, postJson } from "@/lib/api";
import { fmt, short } from "@/lib/format";
import type { Brand, Checks, Settings } from "@/lib/types";

export default function SetupPage() {
  return (
    <Suspense fallback={<Loading />}>
      <SetupRoute />
    </Suspense>
  );
}

function SetupRoute() {
  const session = useSession();
  const brand = useSearchParams().get("brand") ?? "";
  if (session === undefined) return <Loading />;
  if (session === null) return <SignIn />;
  if (!brand) return <ErrorBox message="This link is missing the brand. Open it from your brands." />;
  return <Setup brandId={brand} email={session.email} />;
}

type Uploaded = { plan_id: string; settings: Settings; checks: Checks; file: string };
type StyleInputs = { costs: Record<string, string>; owner: Record<string, string> };

const STEPS = [
  ["Orders", "Your sales file"],
  ["Styles", "Cost per piece"],
  ["Season", "Budget, cash, delivery"],
] as const;

const COLUMN_LABELS: Record<string, string> = {
  date: "Order date",
  style: "Product",
  size: "Size",
  color: "Colour",
  qty: "Quantity",
  price: "Price",
  zone: "City / zone",
  status: "Status",
  courier: "Courier",
  cost: "Cost",
  category: "Category",
  cancelled: "Cancelled",
  financial: "Payment status",
};

const num = (s: string | undefined) => {
  const t = String(s ?? "").replace(/[,\s]/g, "");
  const n = Number(t);
  return t !== "" && Number.isFinite(n) ? n : null;
};

function Setup({ brandId, email }: { brandId: string; email: string }) {
  const [brand, setBrand] = useState<Brand | null>(null);
  const [err, setErr] = useState("");
  const [step, setStep] = useState(0);
  const [up, setUp] = useState<Uploaded | null>(null);
  const [styles, setStyles] = useState<StyleInputs>({ costs: {}, owner: {} });
  const head = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    apiJson<Brand[]>("/api/brands")
      .then((bs) => {
        const b = bs.find((x) => x.id === brandId);
        if (!b) setErr("We couldn't find this brand in your account.");
        else setBrand(b);
      })
      .catch((e) => setErr(e.message));
  }, [brandId]);

  useEffect(() => {
    head.current?.focus();
  }, [step]);

  if (err) return <ErrorBox message={err} />;
  if (!brand) return <Loading />;

  return (
    <>
      <TopBar brandName={brand.name} email={email} />
      <main className="page narrow">
        <div>
          <h1 style={{ fontSize: 32, lineHeight: 1.15 }}>Plan your next season</h1>
          <p className="muted" style={{ margin: "8px 0 0" }}>
            Three short steps. All you need is your orders file and what each style costs you to make.
          </p>
        </div>

        <ol className="steps" aria-label="Progress">
          {STEPS.map(([t, s], i) => (
            <li key={t} aria-current={i === step ? "step" : undefined} className={i < step ? "on" : undefined}>
              <span className="dot" aria-hidden="true">
                {i < step ? "✓" : i + 1}
              </span>
              <span>
                <span style={{ display: "block", fontWeight: 600, fontSize: 14 }}>
                  {t}
                  <span className="sr">{i < step ? " (done)" : i === step ? " (current step)" : ""}</span>
                </span>
                <span className="tiny muted" style={{ display: "block" }}>
                  {s}
                </span>
              </span>
            </li>
          ))}
        </ol>

        {step === 0 && (
          <StepOrders
            brandId={brandId}
            headRef={head}
            onDone={(u) => {
              setUp(u);
              const list = u.checks.stats.style_list ?? [];
              setStyles({ costs: Object.fromEntries(list.map((s) => [s.id, s.cost == null ? "" : String(s.cost)])), owner: {} });
              setStep(1);
            }}
          />
        )}
        {step === 1 && up && (
          <StepStyles up={up} headRef={head} value={styles} onChange={setStyles} back={() => setStep(0)} next={() => setStep(2)} />
        )}
        {step === 2 && up && <StepSeason up={up} styles={styles} headRef={head} back={() => setStep(1)} />}
      </main>
    </>
  );
}

type HeadRef = React.RefObject<HTMLHeadingElement | null>;

function StepOrders({ brandId, headRef, onDone }: { brandId: string; headRef: HeadRef; onDone: (u: Uploaded) => void }) {
  const [busy, setBusy] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function send(file: File) {
    setErrors([]);
    if (!/\.(xlsx|csv)$/i.test(file.name)) {
      setErrors(["Please choose an Excel (.xlsx) or CSV file."]);
      return;
    }
    setBusy(`Reading ${file.name}…`);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api(`/api/brands/${brandId}/uploads`, { method: "POST", body: fd });
      const j = await r.json().catch(() => null);
      if (r.status === 422 && j?.errors) setErrors(j.errors);
      else if (!r.ok) setErrors([typeof j?.detail === "string" ? j.detail : "We couldn't read this file. Please try again."]);
      else onDone({ plan_id: j.plan_id, settings: j.settings, checks: j.checks, file: file.name });
    } catch (e) {
      setErrors([e instanceof Error ? e.message : "Upload failed. Please try again."]);
    } finally {
      setBusy("");
    }
  }

  async function useExample() {
    setErrors([]);
    setBusy("Loading the example brand…");
    try {
      const r = await fetch("/api/templates/example");
      if (!r.ok) throw new Error("Couldn't load the example data.");
      const blob = await r.blob();
      await send(new File([blob], "fasla_example_brand.xlsx", { type: blob.type }));
    } catch (e) {
      setErrors([e instanceof Error ? e.message : "Couldn't load the example data."]);
      setBusy("");
    }
  }

  return (
    <section className="card stack" style={{ gap: 20, padding: 24 }} aria-labelledby="s1">
      <div>
        <h2 id="s1" ref={headRef} tabIndex={-1}>
          Upload your orders
        </h2>
        <p className="small muted" style={{ margin: "6px 0 0" }}>
          Fasla learns from past orders what sells, in which size and colour, and where. 12 months or more works best.
        </p>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 20 }}>
        <div
          className={"drop" + (over ? " over" : "")}
          style={{ flex: "1 1 360px" }}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            const f = e.dataTransfer.files?.[0];
            if (f) send(f);
          }}
        >
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--brand)" strokeWidth="2" aria-hidden="true">
            <path d="M12 16V4M7 9l5-5 5 5M4 20h16" />
          </svg>
          <p style={{ margin: "8px 0 4px", fontWeight: 600 }} aria-live="polite">
            {busy || "Drop your orders file here"}
          </p>
          <p className="small muted" style={{ margin: "0 0 14px" }}>
            Shopify orders export (CSV), or any Excel or CSV file
          </p>
          <input
            ref={input}
            type="file"
            accept=".xlsx,.csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            className="sr"
            tabIndex={-1}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) send(f);
              e.target.value = "";
            }}
          />
          <div style={{ display: "flex", gap: 8, justifyContent: "center", flexWrap: "wrap" }}>
            <button type="button" className="btn primary" disabled={!!busy} onClick={() => input.current?.click()}>
              Choose file
            </button>
            <button type="button" className="btn" disabled={!!busy} onClick={useExample}>
              Try with example data
            </button>
          </div>
        </div>
        <div style={{ flex: "1 1 320px" }}>
          <h3 style={{ fontSize: 15, marginBottom: 8 }}>One row per item sold</h3>
          <ul className="small" style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
            {[
              ["Needed", "Order date, product, size, price"],
              ["Helpful", "Quantity (else 1), colour"],
              ["Helpful", "City or zone, order status, courier"],
              ["Optional", "Cost to make (or type it in the next step)"],
            ].map(([need, what]) => (
              <li key={what} style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
                <span className={"pill" + (need === "Needed" ? " brand" : "")} style={{ width: 72, textAlign: "center", flex: "none" }}>
                  {need}
                </span>
                {what}
              </li>
            ))}
          </ul>
          <p className="small muted">
            In Shopify: <b>Orders → Export → All orders → CSV</b>. Size and colour are read from names like &ldquo;Classic Tee - Black / M&rdquo;.
          </p>
          <p className="tiny muted" style={{ marginBottom: 0 }}>
            Have production history and courier prices per zone? The <a href="/api/templates/empty">full Fasla template</a> makes the plan
            sharper.
          </p>
        </div>
      </div>

      <div className="note plain small" style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <span className="pill">Coming soon</span>
        <span>
          <b>Connect Shopify</b>: one click, read-only, kept up to date every day.
        </span>
      </div>

      {errors.length > 0 && (
        <div className="note bad" role="alert">
          <b>We couldn&apos;t use this file:</b>
          <ul className="clean">
            {errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function StepStyles({
  up,
  headRef,
  value,
  onChange,
  back,
  next,
}: {
  up: Uploaded;
  headRef: HeadRef;
  value: StyleInputs;
  onChange: (v: StyleInputs) => void;
  back: () => void;
  next: () => void;
}) {
  const s = up.checks.stats;
  const list = s.style_list ?? [];
  const [tried, setTried] = useState(false);
  const missing = list.filter((x) => !((num(value.costs[x.id]) ?? 0) > 0));
  const cols = Object.entries(s.columns ?? {});
  const summary: [string, string][] = [
    ["Orders", fmt(s.orders)],
    ["Styles", fmt(s.styles)],
    ["Dates", `${s.date_from} to ${s.date_to}`],
    ["Seasons", s.seasons.join(", ")],
    ["Zones", fmt(s.zones)],
  ];

  function go() {
    setTried(true);
    if (missing.length === 0) next();
  }

  return (
    <section className="card stack" style={{ gap: 18, padding: 24 }} aria-labelledby="s2">
      <div>
        <h2 id="s2" ref={headRef} tabIndex={-1}>
          Check your styles
        </h2>
        <p className="small muted" style={{ margin: "6px 0 0" }}>
          From {up.file}. Add what one piece of each style costs you to make. Your own guess for next season is optional.
        </p>
      </div>

      <dl style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10, margin: 0 }}>
        {summary.map(([k, v]) => (
          <div key={k} style={{ padding: "10px 12px", background: "var(--bg)", borderRadius: 10 }}>
            <dt className="tiny muted" style={{ fontWeight: 600 }}>
              {k}
            </dt>
            <dd style={{ margin: "2px 0 0", fontWeight: 600 }}>{v}</dd>
          </div>
        ))}
      </dl>

      {cols.length > 0 && (
        <details>
          <summary>How we read your file</summary>
          <ul className="clean small" style={{ marginTop: 8 }}>
            {cols.map(([k, c]) => (
              <li key={k}>
                {COLUMN_LABELS[k] ?? k} ← column <code>{c}</code>
              </li>
            ))}
            {!s.columns?.size && <li>Size and colour ← read from the product name</li>}
          </ul>
        </details>
      )}

      <div className="tbl-wrap">
        <table className="tbl" style={{ minWidth: 640 }}>
          <thead>
            <tr>
              <th scope="col">Style</th>
              <th scope="col" className="r">
                Price
              </th>
              <th scope="col" className="r">
                Sold, last 12 months
              </th>
              <th scope="col">Cost to make (EGP)</th>
              <th scope="col">Your guess next season</th>
            </tr>
          </thead>
          <tbody>
            {list.map((x) => {
              const bad = tried && missing.includes(x);
              return (
                <tr key={x.id}>
                  <td style={{ fontWeight: 600 }}>{x.name}</td>
                  <td className="r num muted">{fmt(x.price)}</td>
                  <td className="r num muted">{fmt(x.sold_last_year)}</td>
                  <td>
                    <input
                      inputMode="decimal"
                      aria-label={`Cost to make ${x.name}`}
                      aria-invalid={bad}
                      value={value.costs[x.id] ?? ""}
                      placeholder="e.g. 250"
                      onChange={(e) => onChange({ ...value, costs: { ...value.costs, [x.id]: e.target.value } })}
                      style={{ width: 120, height: 36, border: `1px solid ${bad ? "var(--bad)" : "var(--field)"}`, borderRadius: 8, padding: "0 10px" }}
                    />
                  </td>
                  <td>
                    <input
                      inputMode="numeric"
                      aria-label={`Your sales guess for ${x.name}, in pieces`}
                      value={value.owner[x.id] ?? ""}
                      placeholder="pieces"
                      onChange={(e) => onChange({ ...value, owner: { ...value.owner, [x.id]: e.target.value } })}
                      style={{ width: 110, height: 36, border: "1px solid var(--field)", borderRadius: 8, padding: "0 10px" }}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {tried && missing.length > 0 && (
        <p className="note bad" role="alert" style={{ margin: 0 }}>
          Add the cost for {missing.length === 1 ? missing[0].name : `${missing.length} styles`} to continue.
        </p>
      )}

      {up.checks.warnings.length > 0 && (
        <div className="note warn small">
          <b>Worth knowing:</b>
          <ul className="clean">
            {up.checks.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="actions">
        <button type="button" className="btn" onClick={back}>
          Upload a different file
        </button>
        <button type="button" className="btn dark" onClick={go}>
          Continue
        </button>
      </div>
    </section>
  );
}

type FieldDef = { key: string; label: string; hint: string; kind: "money" | "int" | "pct" };
const MAIN: FieldDef[] = [
  { key: "budget", label: "Production budget", hint: "EGP you plan to spend making stock", kind: "money" },
  { key: "start_cash", label: "Cash in the bank today", hint: "So we can warn you before it runs out (EGP)", kind: "money" },
  { key: "factory_moq", label: "Factory minimum per style", hint: "Pieces; 0 if there's no minimum", kind: "int" },
  { key: "lead_weeks", label: "Weeks until stock arrives", hint: "From placing the order to your warehouse", kind: "int" },
];
const MORE: FieldDef[] = [
  { key: "deposit_share", label: "Paid to the factory up front", hint: "The rest is paid when stock arrives", kind: "pct" },
  { key: "min_per_color", label: "Minimum pieces per colour", hint: "Smallest colour run the factory accepts", kind: "int" },
  { key: "leftover_share", label: "Value of an unsold piece", hint: "At season end, as a share of its price", kind: "pct" },
  { key: "remit_weeks", label: "Weeks until couriers pay you", hint: "Cash-on-delivery money coming back", kind: "int" },
  { key: "ads_share", label: "Ad spend", hint: "As a share of revenue", kind: "pct" },
  { key: "fixed_weekly", label: "Fixed costs per week", hint: "Rent, salaries (EGP)", kind: "money" },
];

const toText = (v: number | undefined, kind: FieldDef["kind"]) =>
  v === undefined || v === null ? "" : kind === "pct" ? String(Math.round(v * 1000) / 10) : kind === "money" ? fmt(v) : String(Math.round(v));

function StepSeason({ up, styles, headRef, back }: { up: Uploaded; styles: StyleInputs; headRef: HeadRef; back: () => void }) {
  const ordersMode = up.checks.stats.mode === "orders";
  const couriers = up.checks.stats.couriers ?? [];
  // Last 12 months of sales at the owner's costs: a sensible starting budget for an orders-only upload.
  const lastYearAtCost = useMemo(
    () => (up.checks.stats.style_list ?? []).reduce((t, x) => t + x.sold_last_year * (num(styles.costs[x.id]) ?? 0), 0),
    [up, styles],
  );
  const [vals, setVals] = useState<Record<string, string>>(() => {
    const v = Object.fromEntries([...MAIN, ...MORE].map((f) => [f.key, toText(up.settings[f.key], f.kind)]));
    if (ordersMode) {
      v.budget = lastYearAtCost > 0 ? fmt(Math.round(lastYearAtCost / 1000) * 1000) : "";
      v.start_cash = "";
    }
    return v;
  });
  const [fees, setFees] = useState<Record<string, string>>(() => Object.fromEntries(couriers.map((c) => [c, "60"])));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [bad, setBad] = useState<Record<string, string>>({});
  const router = useRouter();

  function parse(): Settings | null {
    const out: Settings = {};
    const b: Record<string, string> = {};
    for (const f of [...MAIN, ...MORE]) {
      const n = num(String(vals[f.key] ?? "").replace("%", ""));
      if (n === null) {
        b[f.key] = "Enter a number";
        continue;
      }
      out[f.key] = f.kind === "pct" ? n / 100 : n;
    }
    if (ordersMode)
      for (const c of couriers) {
        const n = num(fees[c]);
        if (n === null || n < 0) b["fee:" + c] = "Enter the fee in EGP";
      }
    setBad(b);
    return Object.keys(b).length ? null : out;
  }

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    const settings = parse();
    if (!settings) return;
    const mapNums = (m: Record<string, string>) =>
      Object.fromEntries(Object.entries(m).flatMap(([k, v]) => (num(v) !== null ? [[k, num(v)]] : [])));
    const body: Record<string, unknown> = { ...settings, costs: mapNums(styles.costs), owner: mapNums(styles.owner) };
    if (ordersMode) body.courier_fees = mapNums(fees);
    setBusy(true);
    try {
      await postJson(`/api/plans/${up.plan_id}/run`, { settings: body });
      router.push(`/plan/?id=${up.plan_id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't start the plan.");
      setBusy(false);
    }
  }

  const field = (f: FieldDef) => (
    <label key={f.key} className="field">
      {f.label}
      {f.kind === "pct" ? " (%)" : ""}
      <input inputMode="decimal" value={vals[f.key]} aria-invalid={!!bad[f.key]} onChange={(e) => setVals((v) => ({ ...v, [f.key]: e.target.value }))} />
      {bad[f.key] ? (
        <span className="err">{bad[f.key]}</span>
      ) : (
        <span className="hint">
          {f.key === "budget" && lastYearAtCost > 0 ? `Your last 12 months of sales cost about ${short(lastYearAtCost)} EGP to make` : f.hint}
        </span>
      )}
    </label>
  );

  return (
    <form className="card stack" style={{ gap: 22, padding: 24 }} aria-labelledby="s3" onSubmit={run} noValidate>
      <div>
        <h2 id="s3" ref={headRef} tabIndex={-1}>
          Your season
        </h2>
        <p className="small muted" style={{ margin: "6px 0 0" }}>
          A few numbers about money, the factory and delivery.
        </p>
      </div>
      <div className="fields">{MAIN.map(field)}</div>

      {ordersMode && (
        <div className="stack" style={{ gap: 12, borderTop: "1px solid var(--line)", paddingTop: 20 }}>
          <div>
            <h3 style={{ fontSize: 16, margin: 0 }}>Delivery</h3>
            <p className="small muted" style={{ margin: "4px 0 0" }}>
              {couriers.length > 1
                ? "What each courier charges per order. Fasla will show which one is best in each zone."
                : "What your courier charges per order. Returned parcels are counted at half the fee."}
            </p>
          </div>
          <div className="fields">
            {couriers.map((c) => (
              <label key={c} className="field">
                {couriers.length > 1 ? `${c}: fee per order` : "Courier fee per order"}
                <input
                  inputMode="decimal"
                  value={fees[c] ?? ""}
                  aria-invalid={!!bad["fee:" + c]}
                  onChange={(e) => setFees((f) => ({ ...f, [c]: e.target.value }))}
                />
                {bad["fee:" + c] ? <span className="err">{bad["fee:" + c]}</span> : <span className="hint">EGP; an average across zones is fine</span>}
              </label>
            ))}
          </div>
        </div>
      )}

      <details open={Object.keys(bad).some((k) => MORE.some((f) => f.key === k)) || undefined}>
        <summary>More settings (optional)</summary>
        <div className="fields" style={{ marginTop: 14 }}>
          {MORE.map(field)}
        </div>
      </details>
      {Object.keys(bad).length > 0 && (
        <p className="note bad" role="alert" style={{ margin: 0 }}>
          Some fields need a number.
        </p>
      )}
      {err && (
        <p className="note bad" role="alert" style={{ margin: 0 }}>
          {err}
        </p>
      )}
      <div className="actions">
        <button type="button" className="btn" onClick={back}>
          Back
        </button>
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? "Starting…" : "Build my plan"}
        </button>
      </div>
      <p className="tiny muted" style={{ margin: 0 }}>
        Planning takes about a minute. <Link href="/">Back to your brands</Link>
      </p>
    </form>
  );
}
