"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import SignIn from "@/components/SignIn";
import { ErrorBox, Loading, TopBar, useSession } from "@/components/shell";
import { apiJson, postJson } from "@/lib/api";
import { fmt, short } from "@/lib/format";
import type { Brand, PlanSummary } from "@/lib/types";

type BrandRow = Brand & { list: PlanSummary[] };

const STATUS: Record<string, string> = {
  done: "Ready",
  running: "Planning…",
  queued: "Waiting to start",
  failed: "Didn't finish",
  draft: "Data uploaded",
};

export default function Home() {
  const session = useSession();
  if (session === undefined) return <Loading />;
  if (session === null) return <SignIn />;
  return <Brands email={session.email} />;
}

function Brands({ email }: { email: string }) {
  const [rows, setRows] = useState<BrandRow[] | null>(null);
  const [err, setErr] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [formErr, setFormErr] = useState("");
  const router = useRouter();

  const [nonce, setNonce] = useState(0); // bump to load again

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const bs = await apiJson<Brand[]>("/api/brands");
        const lists = await Promise.all(bs.map((b) => apiJson<PlanSummary[]>(`/api/brands/${b.id}/plans`)));
        if (!alive) return;
        setRows(bs.map((b, i) => ({ ...b, list: lists[i] })));
        setErr("");
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : "Couldn't load your brands.");
      }
    })();
    return () => {
      alive = false;
    };
  }, [nonce]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setFormErr("");
    setBusy(true);
    try {
      const b = await postJson<{ id: string }>("/api/brands", { name: name.trim() });
      router.push(`/setup/?brand=${b.id}`);
    } catch (e) {
      setFormErr(e instanceof Error ? e.message : "Couldn't create the brand.");
      setBusy(false);
    }
  }

  if (err) return <ErrorBox message={err} retry={() => { setErr(""); setNonce((n) => n + 1); }} />;
  if (!rows) return <Loading label="Loading your brands…" />;

  return (
    <>
      <TopBar current="brands" email={email} />
      <main className="page narrow">
        <div>
          <h1 style={{ fontSize: 32 }}>Your brands</h1>
          <p className="muted" style={{ margin: "6px 0 0" }}>
            Open a brand&apos;s latest plan, or start a new one for next season.
          </p>
        </div>

        {rows.length === 0 && (
          <div className="note info">
            You don&apos;t have a brand yet. Add one below. You can try Fasla with our example brand&apos;s data before using
            your own.
          </div>
        )}

        <div className="stack" style={{ gap: 16 }}>
          {rows.map((b) => {
            const latest = b.list[0];
            const done = b.list.find((p) => p.status === "done");
            return (
              <section key={b.id} className="card" aria-labelledby={`b-${b.id}`}>
                <div className="card-head" style={{ marginBottom: 6 }}>
                  <h2 id={`b-${b.id}`}>{b.name}</h2>
                  {latest && <span className={"pill" + (latest.status === "failed" ? " warn" : latest.status === "done" ? " brand" : "")}>{STATUS[latest.status] ?? latest.status}</span>}
                </div>
                {done?.summary ? (
                  <p className="muted small" style={{ margin: "0 0 14px" }}>
                    Latest plan: {fmt(done.summary.units)} pieces · expected profit {short(done.summary.profit)} EGP · from{" "}
                    {done.input_name}
                  </p>
                ) : (
                  <p className="muted small" style={{ margin: "0 0 14px" }}>
                    No plan yet.
                  </p>
                )}
                {latest?.status === "failed" && latest.error && <p className="note warn">{latest.error}</p>}
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {done && (
                    <Link className="btn primary" href={`/plan/?id=${done.id}`}>
                      Open plan
                    </Link>
                  )}
                  {latest && (latest.status === "running" || latest.status === "queued") && (
                    <Link className="btn primary" href={`/plan/?id=${latest.id}`}>
                      See progress
                    </Link>
                  )}
                  <Link className={"btn" + (done ? "" : " primary")} href={`/setup/?brand=${b.id}`}>
                    {done ? "New plan" : "Start planning"}
                  </Link>
                </div>
              </section>
            );
          })}
        </div>

        <form className="card" onSubmit={create} aria-labelledby="new-brand">
          <h2 id="new-brand" style={{ fontSize: 19, marginBottom: 12 }}>
            Add a brand
          </h2>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
            <label className="field" style={{ flex: "1 1 280px" }}>
              Brand name
              <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} placeholder="e.g. Nile Thread" />
            </label>
            <button type="submit" className="btn dark" disabled={busy || !name.trim()}>
              {busy ? "Adding…" : "Add brand"}
            </button>
          </div>
          {formErr && (
            <p className="note bad" role="alert" style={{ marginBottom: 0 }}>
              {formErr}
            </p>
          )}
        </form>
      </main>
    </>
  );
}
