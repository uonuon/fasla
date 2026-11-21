"use client";
import { useEffect, useState } from "react";
import SignIn from "@/components/SignIn";
import { ErrorBox, Loading, TopBar, useSession } from "@/components/shell";
import { apiJson } from "@/lib/api";
import { fmt } from "@/lib/format";

type Metrics = {
  days: number;
  funnel_users: [string, number][];
  event_totals: Record<string, number>;
  daily: Record<string, Record<string, number>>;
  recent_failures: { at: string; error?: string; kind?: string; plan?: string }[];
  improve_data_opt_in: Record<string, number>;
};

const LABELS: Record<string, string> = {
  signup: "Signed up",
  brand_created: "Added a brand",
  upload_ok: "Uploaded a file",
  plan_started: "Started a plan",
  plan_done: "Got a plan",
  page_view: "Came back (page views)",
  replan: "Re-planned",
  report_download: "Downloaded report",
  ai_explain: "Asked AI to explain",
  ai_ask: "Asked AI a question",
};

const DAILY_COLS = ["signup", "upload_ok", "upload_failed", "plan_done", "replan", "page_view"];

export default function AdminPage() {
  const session = useSession();
  if (session === undefined) return <Loading />;
  if (session === null) return <SignIn />;
  return <Admin email={session.email} />;
}

function Admin({ email }: { email: string }) {
  const [days, setDays] = useState(14);
  const [m, setM] = useState<Metrics | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    apiJson<Metrics>(`/api/admin/metrics?days=${days}`)
      .then((x) => alive && setM(x))
      .catch((e) => alive && setErr(e.status === 404 ? "This page is only for Fasla admins." : e.message));
    return () => {
      alive = false;
    };
  }, [days]);

  if (err) return <ErrorBox message={err} />;
  if (!m) return <Loading label="Loading numbers…" />;
  const top = Math.max(1, ...m.funnel_users.map(([, n]) => n));

  return (
    <>
      <TopBar email={email} />
      <main className="page">
        <div className="card-head">
          <h1 style={{ fontSize: 30 }}>Fasla numbers</h1>
          <label className="small">
            Last{" "}
            <select value={days} onChange={(e) => setDays(Number(e.target.value))} style={{ height: 34, borderRadius: 8, border: "1px solid var(--field)" }}>
              {[1, 3, 7, 14, 30, 90].map((d) => (
                <option key={d} value={d}>
                  {d} day{d > 1 ? "s" : ""}
                </option>
              ))}
            </select>
          </label>
        </div>

        <section className="card" aria-labelledby="h-funnel">
          <h2 id="h-funnel" style={{ marginBottom: 12 }}>
            Funnel (people)
          </h2>
          <div className="stack">
            {m.funnel_users.map(([k, n]) => (
              <div key={k} className="hbar">
                <span className="lbl" style={{ width: 190 }}>
                  {LABELS[k] ?? k}
                </span>
                <div className="trk">
                  <div className="fill" style={{ width: `${(n / top) * 100}%` }} />
                </div>
                <span className="val num" style={{ width: 60 }}>
                  {fmt(n)}
                </span>
              </div>
            ))}
          </div>
          <p className="tiny muted" style={{ marginBottom: 0 }}>
            Opted in to anonymised data use: {fmt(m.improve_data_opt_in["true"] ?? 0)} · declined: {fmt(m.improve_data_opt_in["false"] ?? 0)}
          </p>
        </section>

        <section className="card" aria-labelledby="h-daily">
          <h2 id="h-daily" style={{ marginBottom: 12 }}>
            By day
          </h2>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th scope="col">Day</th>
                  {DAILY_COLS.map((c) => (
                    <th key={c} scope="col" className="r">
                      {c.replace("_", " ")}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(m.daily).map(([d, row]) => (
                  <tr key={d}>
                    <td>{d}</td>
                    {DAILY_COLS.map((c) => (
                      <td key={c} className="r num">
                        {row[c] ?? 0}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="card" aria-labelledby="h-fail">
          <h2 id="h-fail" style={{ marginBottom: 12 }}>
            Recent problems
          </h2>
          {m.recent_failures.length === 0 ? (
            <p className="muted small">No failed uploads or plans.</p>
          ) : (
            <ul className="clean small">
              {m.recent_failures.map((f, i) => (
                <li key={i}>
                  <span className="muted">{f.at.slice(0, 16).replace("T", " ")}</span> {f.kind ? `(${f.kind}) ` : ""}
                  {f.error}
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </>
  );
}
