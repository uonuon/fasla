"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { apiJson, postJson } from "@/lib/api";
import { TERMS_VERSION } from "@/lib/legal";
import { getSession, onAuthChange, signOut, type Session } from "@/lib/session";
import { track } from "@/lib/track";

type Me = { email: string; admin: boolean; terms_version: string; consent: { terms_version: string; improve_data: boolean } | null };

/** Asks once for agreement to the terms (and optionally to anonymised data use); records page views. */
function ConsentGate() {
  const [me, setMe] = useState<Me | null>(null);
  const [agree, setAgree] = useState(false);
  const [improve, setImprove] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const path = usePathname();

  useEffect(() => {
    apiJson<Me>("/api/me").then(setMe).catch(() => {});
  }, []);
  useEffect(() => {
    track("page_view", { page: path || "/" });
  }, [path]);

  if (!me || (me.consent && me.consent.terms_version === TERMS_VERSION)) return null;

  async function save() {
    setBusy(true);
    setErr("");
    try {
      await postJson("/api/consent", { terms_version: TERMS_VERSION, improve_data: improve });
      setMe((m) => (m ? { ...m, consent: { terms_version: TERMS_VERSION, improve_data: improve } } : m));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't save. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="consent-h" style={{ position: "fixed", inset: 0, zIndex: 100, background: "rgba(15,23,42,.45)", display: "grid", placeItems: "center", padding: 16 }}>
      <div className="card stack" style={{ maxWidth: 520, gap: 14, padding: 26 }}>
        <h2 id="consent-h" style={{ fontSize: 22 }}>Before you start</h2>
        <p className="small" style={{ margin: 0 }}>
          Your sales data is used only to make your plans. Customer names, phones and addresses are removed from your files before
          they&apos;re stored.
        </p>
        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14 }}>
          <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} style={{ marginTop: 3 }} />
          <span>
            I agree to the{" "}
            <Link href="/terms" target="_blank">
              Terms of use
            </Link>{" "}
            and{" "}
            <Link href="/privacy" target="_blank">
              Privacy policy
            </Link>
            .
          </span>
        </label>
        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14 }}>
          <input type="checkbox" checked={improve} onChange={(e) => setImprove(e.target.checked)} style={{ marginTop: 3 }} />
          <span>
            Help make Fasla more accurate: allow anonymised use of my sales patterns (never shared, never linked to my brand).
            <span className="muted"> Optional.</span>
          </span>
        </label>
        {err && (
          <p className="note bad small" role="alert" style={{ margin: 0 }}>
            {err}
          </p>
        )}
        <button type="button" className="btn primary" disabled={!agree || busy} onClick={save}>
          {busy ? "Saving…" : "Continue"}
        </button>
      </div>
    </div>
  );
}

/** undefined while checking, null when signed out. */
export function useSession(): Session | null | undefined {
  const [s, setS] = useState<Session | null | undefined>(undefined);
  useEffect(() => {
    let off = () => {};
    let alive = true;
    const load = () => getSession().then((x) => alive && setS(x)).catch(() => alive && setS(null));
    load();
    onAuthChange(load).then((f) => (off = f));
    return () => {
      alive = false;
      off();
    };
  }, []);
  return s;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="center" role="status" aria-live="polite">
      <div>
        <div className="spin" aria-hidden="true" />
        <p className="muted">{label}</p>
      </div>
    </div>
  );
}

export function ErrorBox({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="center">
      <div className="card" style={{ maxWidth: 460 }}>
        <h2 style={{ fontSize: 20 }}>Something didn&apos;t load</h2>
        <p className="muted">{message}</p>
        <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
          {retry && (
            <button type="button" className="btn primary" onClick={retry}>
              Try again
            </button>
          )}
          <Link className="btn" href="/">
            Go to your brands
          </Link>
        </div>
      </div>
    </div>
  );
}

type Current = "plan" | "setup" | "brands";

export function TopBar({
  current,
  brandName,
  email,
  children,
}: {
  current?: Current;
  brandName?: string;
  email?: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="topbar">
      <ConsentGate />
      <div className="topbar-in">
        <Link href="/" className="logo" aria-label="Fasla home">
          <span className="logo-mark" aria-hidden="true">
            F
          </span>
          <span className="logo-word">Fasla</span>
        </Link>
        {brandName && (
          <span className="pill" title="Brand">
            {brandName}
          </span>
        )}
        <nav className="nav" aria-label="Main">
          <Link href="/" aria-current={current === "brands" ? "page" : undefined}>
            Brands
          </Link>
        </nav>
        <div className="spacer" />
        {children}
        {email && (
          <button
            type="button"
            className="btn"
            onClick={async () => {
              await signOut();
              window.location.assign(window.location.origin + "/");
            }}
            title={email}
          >
            Sign out
          </button>
        )}
      </div>
    </header>
  );
}
