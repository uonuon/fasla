"use client";
import { useEffect, useState } from "react";
import SignIn from "@/components/SignIn";
import { ErrorBox, Loading, TopBar, useSession } from "@/components/shell";
import { apiJson, download } from "@/lib/api";
import { fmt } from "@/lib/format";

type Metrics = {
  days: number;
  funnel_users: [string, number][];
  event_totals: Record<string, number>;
  daily: Record<string, Record<string, number>>;
  recent_failures: { at: string; error?: string; kind?: string; plan?: string }[];
  improve_data_opt_in: Record<string, number>;
};

type UploadLog = {
  type: "upload_failed" | "upload_crashed" | "upload_hard";
  at: string;
  report_id: string;
  ext?: string;
  error?: string;
  where?: string;
  shared: boolean;
  contact?: string;
  mapping?: Record<string, unknown>;
  shape?: {
    bytes?: number;
    kind?: string;
    sheets?: Record<string, number>;
    delimiter?: string;
    raw_headers?: string[];
    header_row_found?: boolean;
    rows_sampled?: number;
    shape_error?: string;
    columns?: { name: string; filled: number; personal?: boolean; examples?: string[] }[];
  };
};

const UPLOAD_TYPE: Record<UploadLog["type"], string> = {
  upload_failed: "Couldn't read",
  upload_crashed: "Crashed (our bug)",
  upload_hard: "Read with AI or warnings",
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
  const [uploads, setUploads] = useState<UploadLog[]>([]);

  useEffect(() => {
    apiJson<UploadLog[]>(`/api/admin/uploads?days=${Math.max(days, 30)}`)
      .then(setUploads)
      .catch(() => setUploads([]));
  }, [days]);

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

        <section className="card" aria-labelledby="h-uploads">
          <h2 id="h-uploads" style={{ marginBottom: 4 }}>
            Files Fasla struggled with
          </h2>
          <p className="tiny muted">
            Headers and example values only; customer columns show their name, never their values. Files appear here
            to download only if the owner pressed &quot;Send this file&quot;.
          </p>
          {uploads.length === 0 ? (
            <p className="muted small">No problem files.</p>
          ) : (
            <div className="stack">
              {uploads.map((u) => (
                <details key={u.report_id} className="note">
                  <summary>
                    <span className="muted">{u.at.slice(0, 16).replace("T", " ")}</span> <b>{UPLOAD_TYPE[u.type]}</b>{" "}
                    ({u.ext || u.shape?.kind}) {u.error?.slice(0, 140)}
                    {u.shared && " · file shared"}
                  </summary>
                  <div className="small" style={{ marginTop: 8 }}>
                    {u.where && <p>Where: <code>{u.where}</code></p>}
                    {u.shared && (
                      <p>
                        <button type="button" className="btn" onClick={() => download(`/api/admin/upload-reports/${u.report_id}`, `report_${u.report_id}.${u.ext || "xlsx"}`)}>
                          Download the scrubbed file
                        </button>
                        {u.contact ? ` · owner: ${u.contact}` : ""}
                      </p>
                    )}
                    <p className="muted">
                      {u.shape?.sheets ? `Sheets: ${Object.entries(u.shape.sheets).map(([k, n]) => `${k} (${n})`).join(", ")} · ` : ""}
                      {u.shape?.delimiter ? `Delimiter "${u.shape.delimiter}" · ` : ""}
                      {u.shape?.header_row_found ? "Headers found below title rows · " : ""}
                      {u.shape?.rows_sampled ?? 0} rows looked at
                      {u.shape?.shape_error ? ` · ${u.shape.shape_error}` : ""}
                    </p>
                    {u.mapping && Object.keys(u.mapping).length > 0 && (
                      <p>AI mapping: <code>{JSON.stringify(u.mapping)}</code></p>
                    )}
                    <div className="tbl-wrap">
                      <table className="tbl">
                        <thead>
                          <tr>
                            <th scope="col">Column</th>
                            <th scope="col" className="r">Filled</th>
                            <th scope="col">Examples</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(u.shape?.columns ?? []).map((c, i) => (
                            <tr key={i}>
                              <td>{c.name}</td>
                              <td className="r num">{Math.round(c.filled * 100)}%</td>
                              <td>{c.personal ? <span className="muted">customer data, hidden</span> : (c.examples ?? []).join(" · ")}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </details>
              ))}
            </div>
          )}
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
