"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getSession, onAuthChange, signOut, type Session } from "@/lib/session";

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
