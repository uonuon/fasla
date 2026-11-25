"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import LogoMark from "@/components/LogoMark";
import { track } from "@/lib/track";

type Size = { size: string; units: number };
type Style = {
  id: string;
  name: string;
  units: number;
  sizes: Size[];
  colors: { color: string; units: number }[];
  forecast: number | null;
  expected_sold: number;
  sellout: number | null;
};
type Result = { styles: Style[]; more_styles: number; units: number; costs_guessed: number; seconds: number };
type Status = { status: "running" | "done" | "error"; result?: Result; error?: string };

const n = (x: number) => Math.round(x).toLocaleString("en-US");

export default function TryPage() {
  const [file, setFile] = useState<File | null>(null);
  const [contact, setContact] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [res, setRes] = useState<Result | null>(null);
  const [example, setExample] = useState(false);
  const stop = useRef(false);

  useEffect(() => {
    track("try_page_view", {});
    return () => {
      stop.current = true;
    };
  }, []);

  async function poll(id: string) {
    const t0 = Date.now();
    while (!stop.current && Date.now() - t0 < 5 * 60 * 1000) {
      await new Promise((r) => setTimeout(r, 1500));
      const r = await fetch(`/api/try/${id}`);
      if (!r.ok) continue;
      const s = (await r.json()) as Status;
      if (s.status === "done" && s.result) return s.result;
      if (s.status === "error") throw new Error(s.error || "We couldn't plan this file.");
    }
    throw new Error("This is taking longer than usual. Please try again in a minute.");
  }

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setErr("");
    setRes(null);
    setExample(false);
    track("try_upload", {});
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("contact", contact);
      const r = await fetch("/api/try", { method: "POST", body: fd });
      const body = await r.json().catch(() => null);
      if (!r.ok) throw new Error((body && body.detail) || "We couldn't read this file.");
      setRes(await poll(body.id));
      track("try_result", {});
    } catch (e) {
      setErr(e instanceof Error ? e.message : "We couldn't read this file.");
    } finally {
      setBusy(false);
    }
  }

  async function showExample() {
    setBusy(true);
    setErr("");
    setRes(null);
    track("try_example", {});
    try {
      const r = await fetch("/api/try-example");
      const s = (await r.json()) as Status;
      if (!s.result) throw new Error("Couldn't load the example.");
      setRes(s.result);
      setExample(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't load the example.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <header className="topbar" style={{ position: "static" }}>
        <div className="topbar-in" style={{ maxWidth: 960 }}>
          <Link href="/" className="logo">
            <LogoMark />
            <span className="logo-word">Fasla</span>
          </Link>
          <div className="spacer" />
          <Link className="btn" href="/login/">
            Sign in
          </Link>
        </div>
      </header>

      <main className="page narrow" style={{ maxWidth: 960 }}>
        <div>
          <p className="eyebrow" style={{ color: "var(--brand)" }}>
            Free · no sign-up
          </p>
          <h1 style={{ fontSize: "clamp(28px, 4vw, 40px)", margin: "6px 0 8px" }}>
            How many of each style and size should you make?
          </h1>
          <p className="muted" style={{ margin: 0, fontSize: 17 }}>
            Upload your orders and see your plan in about a minute: pieces per style, size and colour, from your own sales.
          </p>
          <p className="muted" dir="rtl" style={{ margin: "6px 0 0", fontSize: 16 }}>
            ارفع ملف الأوردرات وشوف خطتك في دقيقة: كام قطعة من كل موديل ومقاس ولون، من مبيعاتك أنت.
          </p>
        </div>

        <section className="card">
          <form onSubmit={upload} className="stack" style={{ gap: 14 }}>
            <label className="field">
              Your orders file (.csv or .xlsx)
              <input type="file" accept=".csv,.xlsx,.xls" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
            <p className="small muted" style={{ margin: "-6px 0 0" }}>
              Shopify: Orders → Export → All orders (CSV). Any Excel sheet with date, product, size and price works too.
            </p>
            <label className="field">
              Your Instagram or email (optional, so we can send you tips for your plan)
              <input type="text" value={contact} maxLength={200} placeholder="@yourbrand or you@brand.com"
                onChange={(e) => setContact(e.target.value)} />
            </label>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <button type="submit" className="btn primary" disabled={!file || busy} style={{ height: 46, padding: "0 22px" }}>
                {busy && !example ? "Planning…" : "Show my plan"}
              </button>
              <button type="button" className="btn" onClick={showExample} disabled={busy}>
                Or see an example brand
              </button>
              {busy && <span className="spin" aria-label="Working" />}
            </div>
            <p className="tiny muted" style={{ margin: 0 }}>
              By uploading, you agree to our <Link href="/terms/">Terms</Link> and <Link href="/privacy/">Privacy Policy</Link>.
            </p>
          </form>
          {err && (
            <p className="note bad" role="alert" style={{ marginTop: 12 }}>
              {err}
            </p>
          )}
        </section>

        {res && <Plan res={res} example={example} />}
      </main>
    </div>
  );
}

function Plan({ res, example }: { res: Result; example: boolean }) {
  return (
    <section className="card stack" style={{ gap: 16 }}>
      <div>
        <p className="eyebrow">{example ? "Example brand" : "Your plan"}</p>
        <h2 style={{ fontSize: 24, margin: "4px 0" }}>Make {n(res.units)} pieces across {res.styles.length + res.more_styles} styles</h2>
        <p className="small muted" style={{ margin: 0 }}>
          Based on your past orders and how each style sells by size and colour.
          {res.costs_guessed > 0 && ` Your file had no making costs for ${res.costs_guessed} style(s), so we assumed 45% of the price.`}
        </p>
      </div>

      <div className="stack" style={{ gap: 12 }}>
        {res.styles.map((s) => {
          const max = Math.max(...s.sizes.map((z) => z.units), 1);
          return (
            <div key={s.id} className="card" style={{ padding: 14 }}>
              <p style={{ margin: "0 0 8px", fontWeight: 600 }}>
                {s.name}: make {n(s.units)}
              </p>
              {s.sizes.map((z) => (
                <div key={z.size} className="hbar" style={{ marginBottom: 4 }}>
                  <span className="lbl" style={{ width: 44, fontWeight: 600 }}>
                    {z.size || "—"}
                  </span>
                  <div className="trk">
                    <div className="fill" style={{ width: `${(z.units / max) * 100}%` }} />
                  </div>
                  <span className="num" style={{ width: 48, textAlign: "right" }}>
                    {n(z.units)}
                  </span>
                </div>
              ))}
              {s.colors.length > 1 && (
                <p className="small muted" style={{ margin: "8px 0 0" }}>
                  Colours: {s.colors.map((c) => `${c.color} ${n(c.units)}`).join(" · ")}
                </p>
              )}
            </div>
          );
        })}
        {res.more_styles > 0 && <p className="small muted">…and {res.more_styles} more styles in the full plan.</p>}
      </div>

      <div className="card" style={{ background: "var(--bg)", padding: 16 }}>
        <p style={{ margin: "0 0 6px", fontWeight: 600 }}>Make it exact for your brand</p>
        <p className="small muted" style={{ margin: "0 0 12px" }}>
          This quick plan guesses your budget and factory minimums. With a free account you set your real budget, costs,
          stock and lead time, see your cash week by week, and download the full plan.
        </p>
        <Link className="btn primary" href="/login/?mode=signup" onClick={() => track("try_signup_click", {})}>
          Create a free account
        </Link>
      </div>
    </section>
  );
}
