"use client";
// Sign-in: Supabase in production, the backend's dev accounts when AUTH_MODE=dev (local testing).
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import type { AppConfig } from "./types";

let cfgPromise: Promise<AppConfig> | null = null;
let sb: SupabaseClient | null = null;

export function getConfig(): Promise<AppConfig> {
  if (!cfgPromise) {
    cfgPromise = fetch("/api/config").then((r) => {
      if (!r.ok) throw new Error("Couldn't load the app settings. Refresh the page.");
      return r.json();
    });
    cfgPromise.catch(() => (cfgPromise = null));
  }
  return cfgPromise;
}

export async function supabase(): Promise<SupabaseClient | null> {
  const cfg = await getConfig();
  if (cfg.authMode !== "supabase") return null;
  if (!sb) {
    sb = createClient(cfg.supabaseUrl, cfg.supabaseAnonKey, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
  }
  return sb;
}

export type Session = { token: string; email: string };

const DEV_KEY = "fasla.dev";

function readDev(): Session | null {
  try {
    const s = JSON.parse(localStorage.getItem(DEV_KEY) || "null");
    return s && s.exp > Date.now() ? { token: s.token, email: s.email } : null;
  } catch {
    return null;
  }
}

export async function getSession(): Promise<Session | null> {
  const c = await supabase();
  if (c) {
    const { data } = await c.auth.getSession();
    const s = data.session;
    return s ? { token: s.access_token, email: s.user.email ?? "" } : null;
  }
  return readDev();
}

async function devAuth(kind: "login" | "signup", email: string, password: string) {
  const r = await fetch(`/api/dev/${kind}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "Couldn't sign in. Check your email and password.");
  localStorage.setItem(DEV_KEY, JSON.stringify({ token: j.access_token, email: j.email, exp: Date.now() + 11 * 3600e3 }));
}

/** Returns a message to show when the user must confirm their email first. */
export async function signUp(email: string, password: string): Promise<string | null> {
  const c = await supabase();
  if (!c) {
    await devAuth("signup", email, password);
    return null;
  }
  const { data, error } = await c.auth.signUp({ email, password, options: { emailRedirectTo: location.origin + "/" } });
  if (error) throw new Error(error.message);
  return data.session ? null : "Check your inbox and click the link we sent to confirm your email.";
}

export async function signIn(email: string, password: string) {
  const c = await supabase();
  if (!c) return devAuth("login", email, password);
  const { error } = await c.auth.signInWithPassword({ email, password });
  if (error) throw new Error(error.message);
}

export async function signInWithGoogle() {
  const c = await supabase();
  if (!c) throw new Error("Google sign-in isn't available here.");
  const { error } = await c.auth.signInWithOAuth({ provider: "google", options: { redirectTo: location.origin + "/" } });
  if (error) throw new Error(error.message);
}

export async function sendReset(email: string) {
  const c = await supabase();
  if (!c) throw new Error("Password reset isn't available in local mode.");
  const { error } = await c.auth.resetPasswordForEmail(email, { redirectTo: location.origin + "/reset/" });
  if (error) throw new Error(error.message);
}

export async function setNewPassword(password: string) {
  const c = await supabase();
  if (!c) throw new Error("Password reset isn't available in local mode.");
  const { error } = await c.auth.updateUser({ password });
  if (error) throw new Error(error.message);
}

export async function signOut() {
  const c = await supabase();
  if (c) await c.auth.signOut();
  try {
    localStorage.removeItem(DEV_KEY);
  } catch {}
}

export async function onAuthChange(cb: () => void): Promise<() => void> {
  const c = await supabase();
  if (!c) return () => {};
  const { data } = c.auth.onAuthStateChange(() => cb());
  return () => data.subscription.unsubscribe();
}
