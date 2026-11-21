"use client";
import { useState } from "react";
import { postJson } from "@/lib/api";
import { fmt } from "@/lib/format";
import { track } from "@/lib/track";
import type { Plan } from "@/lib/types";

const FIELDS = [
  { key: "budget", label: "Production budget (EGP)", money: true },
  { key: "start_cash", label: "Cash in the bank today (EGP)", money: true },
  { key: "factory_moq", label: "Factory minimum per style", money: false },
  { key: "lead_weeks", label: "Weeks until stock arrives", money: false },
] as const;

const num = (s: string | undefined) => {
  const t = String(s ?? "").replace(/[,\s]/g, "");
  const n = Number(t);
  return t !== "" && Number.isFinite(n) ? n : null;
};

type AnySettings = Record<string, unknown>;

/** Change the plan's main numbers or costs and plan again, without uploading the file again. */
export default function Replan({ plan, onStarted }: { plan: Plan; onStarted: () => void }) {
  const st = (plan.settings ?? {}) as AnySettings;
  const styles = plan.checks?.stats?.style_list ?? [];
  const savedCosts = (st.costs ?? {}) as Record<string, number>;
  const savedFees = (st.courier_fees ?? {}) as Record<string, number>;
  const ordersMode = plan.checks?.stats?.mode === "orders";
  const couriers = plan.checks?.stats?.couriers ?? [];

  const [open, setOpen] = useState(false);
  const [vals, setVals] = useState<Record<string, string>>(() =>
    Object.fromEntries(FIELDS.map((f) => [f.key, st[f.key] == null ? "" : f.money ? fmt(Number(st[f.key])) : String(st[f.key])])),
  );
  const [costs, setCosts] = useState<Record<string, string>>(() =>
    Object.fromEntries(styles.map((s) => [s.id, String(savedCosts[s.id] ?? s.cost ?? "")])),
  );
  const [fees, setFees] = useState<Record<string, string>>(() =>
    Object.fromEntries(couriers.map((c) => [c, String(savedFees[c] ?? 60)])),
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    const settings: Record<string, unknown> = {};
    for (const f of FIELDS) {
      const n = num(vals[f.key]);
      if (n === null) return setErr(`Enter a number for "${f.label}".`);
      settings[f.key] = n;
    }
    const c: Record<string, number> = {};
    for (const s of styles) {
      const n = num(costs[s.id]);
      if (n === null || n <= 0) return setErr(`Enter the cost to make ${s.name}.`);
      c[s.id] = n;
    }
    settings.costs = c;
    if (ordersMode) {
      const fz: Record<string, number> = {};
      for (const k of couriers) {
        const n = num(fees[k]);
        if (n === null || n < 0) return setErr(`Enter the fee for ${k}.`);
        fz[k] = n;
      }
      settings.courier_fees = fz;
    }
    if (st.owner) settings.owner = st.owner;
    setBusy(true);
    try {
      await postJson(`/api/plans/${plan.id}/run`, { settings });
      onStarted();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't start the plan.");
      setBusy(false);
    }
  }

  if (!open)
    return (
      <button type="button" className="btn" onClick={() => { setOpen(true); track("replan_open"); }}>
        Change numbers &amp; re-plan
      </button>
    );

  const input = (value: string, onChange: (v: string) => void, label: string, width = 140) => (
    <input
      inputMode="decimal"
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{ width, height: 38, border: "1px solid var(--field)", borderRadius: 8, padding: "0 10px" }}
    />
  );

  return (
    <form className="card stack" style={{ gap: 16, width: "100%" }} onSubmit={submit} aria-labelledby="h-replan">
      <div className="card-head" style={{ marginBottom: 0 }}>
        <h2 id="h-replan">Change numbers &amp; re-plan</h2>
        <span className="small muted">Same orders file; takes 1–3 minutes</span>
      </div>
      <div className="fields">
        {FIELDS.map((f) => (
          <label key={f.key} className="field">
            {f.label}
            <input inputMode="decimal" value={vals[f.key]} onChange={(e) => setVals((v) => ({ ...v, [f.key]: e.target.value }))} />
          </label>
        ))}
      </div>
      {styles.length > 0 && (
        <details>
          <summary>Cost to make each style ({styles.length})</summary>
          <div className="tbl-wrap" style={{ marginTop: 10 }}>
            <table className="tbl" style={{ minWidth: 420 }}>
              <tbody>
                {styles.map((s) => (
                  <tr key={s.id}>
                    <td style={{ fontWeight: 600 }} dir="auto">
                      {s.name}
                    </td>
                    <td className="r">{input(costs[s.id] ?? "", (v) => setCosts((c) => ({ ...c, [s.id]: v })), `Cost to make ${s.name}`, 120)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      {ordersMode && couriers.length > 0 && (
        <div className="fields">
          {couriers.map((k) => (
            <label key={k} className="field">
              {couriers.length > 1 ? `${k}: fee per order` : "Courier fee per order (EGP)"}
              <input inputMode="decimal" value={fees[k]} onChange={(e) => setFees((f) => ({ ...f, [k]: e.target.value }))} />
            </label>
          ))}
        </div>
      )}
      {err && (
        <p className="note bad" role="alert" style={{ margin: 0 }}>
          {err}
        </p>
      )}
      <div className="actions">
        <button type="button" className="btn" onClick={() => setOpen(false)}>
          Cancel
        </button>
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? "Starting…" : "Re-plan with these numbers"}
        </button>
      </div>
    </form>
  );
}
