"use client";
import Link from "next/link";
import { useState } from "react";
import { setNewPassword } from "@/lib/session";

export default function ResetPage() {
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await setNewPassword(pw);
      setDone(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't change the password. Open the link from your email again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth">
      <div className="card" style={{ padding: 28 }}>
        <h1 style={{ fontSize: 26, marginBottom: 16 }}>Choose a new password</h1>
        {done ? (
          <>
            <p className="note ok">Your password is changed.</p>
            <Link className="btn primary" href="/">
              Go to your brands
            </Link>
          </>
        ) : (
          <form onSubmit={submit} className="stack" style={{ gap: 14 }}>
            <label className="field">
              New password
              <input type="password" autoComplete="new-password" minLength={8} required value={pw} onChange={(e) => setPw(e.target.value)} />
              <span className="hint">At least 8 characters</span>
            </label>
            {err && (
              <p className="note bad" role="alert" style={{ margin: 0 }}>
                {err}
              </p>
            )}
            <button type="submit" className="btn primary" disabled={busy}>
              {busy ? "Saving…" : "Save password"}
            </button>
          </form>
        )}
      </div>
    </main>
  );
}
