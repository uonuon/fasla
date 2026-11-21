"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import SignIn from "@/components/SignIn";
import { ErrorBox, Loading, TopBar, useSession } from "@/components/shell";
import { api, apiJson, postJson } from "@/lib/api";
import { fmt } from "@/lib/format";
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

const STEPS = [
  ["Orders", "Your sales data"],
  ["Check", "What we found"],
  ["Season", "Budget, cash, factory"],
] as const;

function Setup({ brandId, email }: { brandId: string; email: string }) {
  const [brand, setBrand] = useState<Brand | null>(null);
  const [err, setErr] = useState("");
  const [step, setStep] = useState(0);
  const [up, setUp] = useState<Uploaded | null>(null);
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

  const go = (i: number) => setStep(i);

  return (
    <>
      <TopBar brandName={brand.name} email={email} />
      <main className="page narrow">
        <div>
          <h1 style={{ fontSize: 32, lineHeight: 1.15 }}>Plan your next season</h1>
          <p className="muted" style={{ margin: "8px 0 0" }}>
            Three short steps. Only the first needs your data; everything else has a sensible default.
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
              go(1);
            }}
          />
        )}
        {step === 1 && up && <StepCheck up={up} headRef={head} back={() => go(0)} next={() => go(2)} />}
        {step === 2 && up && <StepSeason up={up} headRef={head} back={() => go(1)} />}
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
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setErrors(["Please choose an Excel .xlsx file."]);
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
          Where are your orders?
        </h2>
        <p className="small muted" style={{ margin: "6px 0 0" }}>
          Fasla learns from past orders what sells, in which size and colour, and where.
        </p>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
        <button type="button" className="choice" disabled aria-describedby="shopify-soon">
          <span className="pill">Coming soon</span>
          <span className="t">Connect Shopify</span>
          <span className="d" id="shopify-soon">
            One click, read-only, kept up to date every day. We&apos;re building this next.
          </span>
        </button>
        <div className="choice" style={{ cursor: "default", border: "2px solid var(--brand)" }}>
          <span className="pill brand">Available now</span>
          <span className="t">Upload the Fasla workbook</span>
          <span className="d">
            Fill our Excel template with your products, orders and past production, then upload it here.{" "}
            <a href="/api/templates/empty">Download the empty template</a>.
          </span>
        </div>
      </div>

      <div
        className={"drop" + (over ? " over" : "")}
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
        <p style={{ margin: "8px 0 4px", fontWeight: 600 }}>{busy || "Drop your filled workbook here"}</p>
        <p className="small muted" style={{ margin: "0 0 14px" }}>
          Excel .xlsx file
        </p>
        <input
          ref={input}
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
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

      {errors.length > 0 && (
        <div className="note bad" role="alert">
          <b>Please fix these in your workbook and upload it again:</b>
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

function StepCheck({ up, headRef, back, next }: { up: Uploaded; headRef: HeadRef; back: () => void; next: () => void }) {
  const s = up.checks.stats;
  const items: [string, string][] = [
    ["Orders", fmt(s.orders)],
    ["Styles", fmt(s.styles)],
    ["Products (style × colour × size)", fmt(s.products)],
    ["Seasons", s.seasons.join(", ")],
    ["Dates", `${s.date_from} to ${s.date_to}`],
    ["Delivery zones", fmt(s.zones)],
    ["Couriers", s.couriers.join(", ")],
    ["Your own estimates", s.owner_estimates ? `${s.owner_estimates} styles` : "none"],
  ];
  return (
    <section className="card stack" style={{ gap: 18, padding: 24 }} aria-labelledby="s2">
      <div>
        <h2 id="s2" ref={headRef} tabIndex={-1}>
          Here&apos;s what we found
        </h2>
        <p className="small muted" style={{ margin: "6px 0 0" }}>
          From {up.file}. If something looks wrong, fix the workbook and upload it again.
        </p>
      </div>
      <dl style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12, margin: 0 }}>
        {items.map(([k, v]) => (
          <div key={k} style={{ padding: "12px 14px", background: "var(--bg)", borderRadius: 10 }}>
            <dt className="tiny muted" style={{ fontWeight: 600 }}>
              {k}
            </dt>
            <dd style={{ margin: "2px 0 0", fontWeight: 600 }}>{v}</dd>
          </div>
        ))}
      </dl>
      {s.new_styles.length > 0 && (
        <p className="note plain small" style={{ margin: 0 }}>
          <b>New styles:</b> {s.new_styles.join(", ")}. They have no sales yet, so Fasla estimates them from similar styles
          {s.owner_estimates ? " and your estimates" : ""}.
        </p>
      )}
      {up.checks.warnings.length > 0 && (
        <div className="note warn">
          <b>Worth knowing ({up.checks.warnings.length}):</b>
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
        <button type="button" className="btn dark" onClick={next}>
          Looks right, continue
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

function StepSeason({ up, headRef, back }: { up: Uploaded; headRef: HeadRef; back: () => void }) {
  const [vals, setVals] = useState<Record<string, string>>(() =>
    Object.fromEntries([...MAIN, ...MORE].map((f) => [f.key, toText(up.settings[f.key], f.kind)])),
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [bad, setBad] = useState<Record<string, string>>({});
  const router = useRouter();

  function parse(): Settings | null {
    const out: Settings = {};
    const b: Record<string, string> = {};
    for (const f of [...MAIN, ...MORE]) {
      const raw = (vals[f.key] ?? "").replace(/[,\s%]/g, "");
      const n = Number(raw);
      if (raw === "" || !Number.isFinite(n)) {
        b[f.key] = "Enter a number";
        continue;
      }
      out[f.key] = f.kind === "pct" ? n / 100 : n;
    }
    setBad(b);
    return Object.keys(b).length ? null : out;
  }

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    const settings = parse();
    if (!settings) return;
    setBusy(true);
    try {
      await postJson(`/api/plans/${up.plan_id}/run`, { settings });
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
      <input
        inputMode="decimal"
        value={vals[f.key]}
        aria-invalid={!!bad[f.key]}
        onChange={(e) => setVals((v) => ({ ...v, [f.key]: e.target.value }))}
      />
      {bad[f.key] ? <span className="err">{bad[f.key]}</span> : <span className="hint">{f.hint}</span>}
    </label>
  );

  return (
    <form className="card stack" style={{ gap: 22, padding: 24 }} aria-labelledby="s3" onSubmit={run} noValidate>
      <div>
        <h2 id="s3" ref={headRef} tabIndex={-1}>
          Your season
        </h2>
        <p className="small muted" style={{ margin: "6px 0 0" }}>
          Four numbers about money and the factory. We filled them from your workbook; change anything that&apos;s different this season.
        </p>
      </div>
      <div className="fields">{MAIN.map(field)}</div>
      <details>
        <summary>More settings (optional)</summary>
        <div className="fields" style={{ marginTop: 14 }}>
          {MORE.map(field)}
        </div>
      </details>
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
