"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import Dashboard from "@/components/Dashboard";
import SignIn from "@/components/SignIn";
import { ErrorBox, Loading, TopBar, useSession } from "@/components/shell";
import { apiJson, download, postJson } from "@/lib/api";
import type { Plan } from "@/lib/types";

export default function PlanPage() {
  return (
    <Suspense fallback={<Loading />}>
      <PlanRoute />
    </Suspense>
  );
}

function PlanRoute() {
  const session = useSession();
  const id = useSearchParams().get("id") ?? "";
  if (session === undefined) return <Loading />;
  if (session === null) return <SignIn />;
  if (!id) return <ErrorBox message="This link is missing the plan. Open it from your brands." />;
  return <PlanScreen id={id} email={session.email} />;
}

function PlanScreen({ id, email }: { id: string; email: string }) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [nonce, setNonce] = useState(0); // bump to load again

  // Load the plan, and keep polling while it's being planned.
  useEffect(() => {
    let alive = true;
    let t: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const p = await apiJson<Plan>(`/api/plans/${id}`);
        if (!alive) return;
        setPlan(p);
        setErr("");
        if (p.status === "queued" || p.status === "running") t = setTimeout(tick, 2500);
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : "Couldn't load this plan.");
      }
    };
    tick();
    return () => {
      alive = false;
      if (t) clearTimeout(t);
    };
  }, [id, nonce]);

  async function rerun() {
    setNote("");
    try {
      await postJson(`/api/plans/${id}/run`, { settings: {} });
      setPlan((p) => (p ? { ...p, status: "queued" } : p));
      setNonce((n) => n + 1);
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Couldn't start the plan.");
    }
  }

  if (err) return <ErrorBox message={err} retry={() => { setErr(""); setNonce((n) => n + 1); }} />;
  if (!plan) return <Loading label="Loading your plan…" />;

  const bar = (
    <TopBar brandName={plan.brand_name} email={email}>
      <Link className="btn" href={`/setup/?brand=${plan.brand_id}`}>
        New plan
      </Link>
    </TopBar>
  );

  if (plan.status === "done" && plan.view) {
    return (
      <>
        {bar}
        <Dashboard
          plan={plan}
          view={plan.view}
          onDownload={() => download(`/api/plans/${id}/report`, "fasla_report.xlsx").catch((e) => alertLater(e))}
          onReplan={() => {
            setPlan((p) => (p ? { ...p, status: "queued" } : p));
            setNonce((n) => n + 1);
            window.scrollTo({ top: 0 });
          }}
        />
      </>
    );
  }

  if (plan.status === "queued" || plan.status === "running") {
    return (
      <>
        {bar}
        <div className="center" role="status" aria-live="polite">
          <div style={{ maxWidth: 460 }}>
            <div className="spin" aria-hidden="true" />
            <h1 style={{ fontSize: 24 }}>Building your plan…</h1>
            <p className="muted">
              Fasla is testing hundreds of possible seasons to find the best quantities. This usually takes 1–3 minutes (a little longer
              if nobody has planned for a while, while a planner starts up). You can leave this page and come back.
            </p>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      {bar}
      <main className="page narrow">
        <div className="card stack" style={{ gap: 14 }}>
          <h1 style={{ fontSize: 26 }}>{plan.status === "failed" ? "This plan didn't finish" : "This plan hasn't run yet"}</h1>
          {plan.error && <p className="note warn">{plan.error}</p>}
          {note && (
            <p className="note bad" role="alert">
              {note}
            </p>
          )}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" className="btn primary" onClick={rerun}>
              {plan.status === "failed" ? "Run it again" : "Run the plan"}
            </button>
            <Link className="btn" href={`/setup/?brand=${plan.brand_id}`}>
              Upload different data
            </Link>
          </div>
        </div>
      </main>
    </>
  );
}

function alertLater(e: unknown) {
  // Not a blocking dialog: a short-lived message at the top of the page.
  const el = Object.assign(document.createElement("div"), {
    className: "note bad",
    role: "alert",
    textContent: e instanceof Error ? e.message : "Couldn't download the report.",
  });
  Object.assign(el.style, { position: "fixed", top: "12px", left: "50%", transform: "translateX(-50%)", zIndex: "50" });
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}
