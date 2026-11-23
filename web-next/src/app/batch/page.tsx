"use client";
import { useState } from "react";
import { Loading, TopBar, useSession } from "@/components/shell";
import Landing from "@/components/Landing";
import { api, ApiError, download } from "@/lib/api";
import { fmt } from "@/lib/format";
import { track } from "@/lib/track";

type Row = {
  product: string;
  variant: string;
  pack: string;
  on_hand: number;
  make: number;
  next_30_days: number;
  make_every_days: number;
  then_per_batch: number;
  chance_no_stockout: number;
  months_of_stock: number;
  cost: number;
  note: string;
};
type Mat = { material: string; needed: number; on_hand: number; to_order: number; order_by: string | null; status: string; cost: number };
type Result = {
  start: string;
  total_cost: number;
  notes: string[];
  plan: Row[];
  batches: { product: string; variant: string; units: number; amount: number; unit: string; cost: number }[];
  materials: Mat[];
  history: { month: string; units: number }[];
};

export default function BatchPage() {
  const session = useSession();
  if (session === undefined) return <Loading />;
  if (session === null) return <Landing />;
  return <Batch email={session.email} />;
}

function Batch({ email }: { email: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [res, setRes] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function plan(f: File) {
    setBusy(true);
    setErr("");
    setRes(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await api("/api/batch/plan", { method: "POST", body: fd });
      const body = await r.json().catch(() => null);
      if (!r.ok) throw new ApiError((body && body.detail) || "Couldn't read this file.", r.status);
      setRes(body as Result);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't read this file.");
    } finally {
      setBusy(false);
    }
  }

  async function tryExample() {
    track("example_used", { kind: "batch" });
    const r = await fetch("/api/batch/templates/example");
    const blob = await r.blob();
    const f = new File([blob], "fasla_batch_example.xlsx");
    setFile(f);
    plan(f);
  }

  async function report() {
    if (!file) return;
    track("report_download", { kind: "batch" });
    const fd = new FormData();
    fd.append("file", file);
    const r = await api("/api/batch/report", { method: "POST", body: fd });
    if (!r.ok) return setErr("Couldn't make the report. Please try again.");
    const url = URL.createObjectURL(await r.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: "fasla_batch_plan.xlsx" });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  const maxH = res ? Math.max(...res.history.map((h) => h.units), 1) : 1;

  return (
    <>
      <TopBar email={email} />
      <main className="page narrow">
        <div>
          <p className="eyebrow">For food, oils and beauty brands</p>
          <h1 style={{ fontSize: 32 }}>Plan your next batch</h1>
          <p className="muted" style={{ margin: "6px 0 0" }}>
            How many jars or bottles of each product to make, from your past orders, stock, batch sizes and shelf life.
            Your file is read once and not saved.
          </p>
        </div>

        <section className="card">
          <h2 style={{ fontSize: 19, marginBottom: 10 }}>1. Fill the workbook</h2>
          <p className="small muted" style={{ marginTop: 0 }}>
            Products (with price, cost, stock, minimum batch, shelf life), your orders, and optionally the jars, labels and
            ingredients each product uses. Instructions are inside, in English and Arabic.
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            <button type="button" className="btn" onClick={() => download("/api/batch/templates/empty", "fasla_batch_template.xlsx")}>
              Download empty workbook
            </button>
            <button type="button" className="btn" onClick={() => download("/api/batch/templates/example", "fasla_batch_example.xlsx")}>
              Download example
            </button>
            <button type="button" className="btn link" onClick={tryExample} disabled={busy}>
              Or try it with the example brand
            </button>
          </div>
        </section>

        <section className="card">
          <h2 style={{ fontSize: 19, marginBottom: 10 }}>2. Upload it</h2>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (file) plan(file);
            }}
            style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}
          >
            <label className="field" style={{ flex: "1 1 280px" }}>
              Workbook (.xlsx)
              <input type="file" accept=".xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
            <button type="submit" className="btn primary" disabled={!file || busy}>
              {busy ? "Planning…" : "Plan my batch"}
            </button>
          </form>
          {err && (
            <p className="note bad" role="alert" style={{ marginBottom: 0 }}>
              {err}
            </p>
          )}
        </section>

        {res && (
          <>
            <div className="kpis">
              <div className="card kpi">
                <p className="kpi-label">Batch day</p>
                <p className="kpi-value">{res.start}</p>
              </div>
              <div className="card kpi">
                <p className="kpi-label">Units to make</p>
                <p className="kpi-value num">{fmt(res.plan.reduce((s, r) => s + r.make, 0))}</p>
              </div>
              <div className="card kpi">
                <p className="kpi-label">Production cost</p>
                <p className="kpi-value num">{fmt(res.total_cost)} EGP</p>
              </div>
            </div>

            <section className="card">
              <div className="card-head">
                <h2>Make this batch</h2>
                <button type="button" className="btn" onClick={report}>
                  Download Excel report
                </button>
              </div>
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Product</th>
                      <th className="r">In stock</th>
                      <th className="r">Make now</th>
                      <th className="r">Next 30 days</th>
                      <th className="r">Then every</th>
                      <th className="r">Won&apos;t run out</th>
                      <th className="r">Cost</th>
                      <th>Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {res.plan.map((r) => (
                      <tr key={`${r.product}|${r.variant}|${r.pack}`}>
                        <td>
                          <strong>{r.product}</strong>
                          <div className="tiny muted">
                            {[r.variant !== "-" ? r.variant : "", r.pack !== "-" ? r.pack : ""].filter(Boolean).join(" · ")}
                          </div>
                        </td>
                        <td className="r num">{fmt(r.on_hand)}</td>
                        <td className="r num">
                          <strong>{fmt(r.make)}</strong>
                        </td>
                        <td className="r num">{fmt(Math.round(r.next_30_days))}</td>
                        <td className="r num tiny">
                          ~{fmt(r.then_per_batch)} / {r.make_every_days} days
                        </td>
                        <td className="r num">{Math.round(r.chance_no_stockout * 100)}%</td>
                        <td className="r num">{fmt(Math.round(r.cost))}</td>
                        <td className="tiny muted">{r.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            {res.materials.length > 0 && (
              <section className="card">
                <h2 style={{ marginBottom: 12 }}>Materials to order</h2>
                <div className="tbl-wrap">
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Material</th>
                        <th className="r">Needed</th>
                        <th className="r">In stock</th>
                        <th className="r">Order</th>
                        <th>Order by</th>
                      </tr>
                    </thead>
                    <tbody>
                      {res.materials.map((m) => (
                        <tr key={m.material}>
                          <td>{m.material}</td>
                          <td className="r num">{fmt(m.needed)}</td>
                          <td className="r num">{fmt(m.on_hand)}</td>
                          <td className="r num">
                            <strong>{m.to_order ? fmt(m.to_order) : "—"}</strong>
                          </td>
                          <td className={m.status.startsWith("late") ? "small" : "small muted"} style={m.status.startsWith("late") ? { color: "var(--bad)" } : undefined}>
                            {m.order_by ? `${m.order_by}${m.status.startsWith("late") ? " (late: order today)" : ""}` : "Enough in stock"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            <section className="card">
              <h2 style={{ marginBottom: 12 }}>Units ordered per month</h2>
              <div className="stack">
                {res.history.map((h) => (
                  <div key={h.month} className="hbar">
                    <span className="lbl small">{h.month}</span>
                    <span className="trk">
                      <span className="fill" style={{ width: `${(100 * h.units) / maxH}%`, display: "block" }} />
                    </span>
                    <span className="val num small">{fmt(h.units)}</span>
                  </div>
                ))}
              </div>
              {res.notes.length > 0 && (
                <ul className="small muted" style={{ marginBottom: 0 }}>
                  {res.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </main>
    </>
  );
}
