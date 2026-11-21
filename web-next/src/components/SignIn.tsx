"use client";
import { useEffect, useState } from "react";
import { getConfig, sendReset, signIn, signInWithGoogle, signUp } from "@/lib/session";

type Mode = "in" | "up" | "reset";

export default function SignIn() {
  const [mode, setMode] = useState<Mode>("in");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [google, setGoogle] = useState(false);

  useEffect(() => {
    getConfig()
      .then((c) => setGoogle(c.authMode === "supabase" && c.googleLogin))
      .catch(() => {});
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setMsg("");
    setBusy(true);
    try {
      if (mode === "reset") {
        await sendReset(email.trim());
        setMsg("If that email has an account, a reset link is on its way.");
      } else if (mode === "up") {
        const note = await signUp(email.trim(), pw);
        if (note) setMsg(note);
        else location.reload();
      } else {
        await signIn(email.trim(), pw);
        location.reload();
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't sign in.");
    } finally {
      setBusy(false);
    }
  }

  const title = mode === "up" ? "Create your account" : mode === "reset" ? "Reset your password" : "Sign in to Fasla";

  return (
    <main className="auth">
      <div className="logo" style={{ justifyContent: "center", marginBottom: 20 }}>
        <span className="logo-mark" aria-hidden="true">
          F
        </span>
        <span className="logo-word">Fasla</span>
      </div>
      <div className="card" style={{ padding: 28 }}>
        <h1 style={{ fontSize: 26, marginBottom: 6 }}>{title}</h1>
        <p className="muted small" style={{ marginTop: 0, marginBottom: 20 }}>
          Know what to produce next season, how much cash you&apos;ll need, and which courier to use.
        </p>
        {google && mode !== "reset" && (
          <>
            <button type="button" className="btn" style={{ width: "100%" }} onClick={() => signInWithGoogle().catch((e) => setErr(e.message))}>
              Continue with Google
            </button>
            <div className="divider" style={{ margin: "16px 0" }}>
              or with email
            </div>
          </>
        )}
        <form onSubmit={submit}>
          <label className="field">
            Email
            <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          {mode !== "reset" && (
            <label className="field">
              Password
              <input
                type="password"
                autoComplete={mode === "up" ? "new-password" : "current-password"}
                required
                minLength={8}
                value={pw}
                onChange={(e) => setPw(e.target.value)}
              />
              {mode === "up" && <span className="hint">At least 8 characters</span>}
            </label>
          )}
          {err && (
            <p className="note bad" role="alert" style={{ margin: 0 }}>
              {err}
            </p>
          )}
          {msg && (
            <p className="note ok" role="status" style={{ margin: 0 }}>
              {msg}
            </p>
          )}
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? "One moment…" : mode === "up" ? "Create account" : mode === "reset" ? "Send reset link" : "Sign in"}
          </button>
        </form>
        <p className="small" style={{ marginBottom: 0, display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
          {mode === "in" ? (
            <>
              <button type="button" className="btn link" onClick={() => setMode("up")}>
                New here? Create an account
              </button>
              <button type="button" className="btn link" onClick={() => setMode("reset")}>
                Forgot password?
              </button>
            </>
          ) : (
            <button type="button" className="btn link" onClick={() => setMode("in")}>
              Back to sign in
            </button>
          )}
        </p>
      </div>
    </main>
  );
}
