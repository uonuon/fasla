"use client";
import { getSession, signOut } from "./session";

export class ApiError extends Error {
  constructor(message: string, public status: number, public body: unknown = null) {
    super(message);
  }
}

function messageFrom(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  if (status === 401) return "Your session ended. Please sign in again.";
  if (status >= 500) return "The server had a problem. Please try again in a minute.";
  return "Something went wrong. Please try again.";
}

export async function api(path: string, init: RequestInit = {}): Promise<Response> {
  const s = await getSession();
  if (!s) throw new ApiError("Please sign in.", 401);
  const r = await fetch(path, { ...init, headers: { ...(init.headers || {}), Authorization: `Bearer ${s.token}` } });
  if (r.status === 401) {
    await signOut();
    window.location.assign(window.location.origin + "/");
  }
  return r;
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await api(path, init);
  const body = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) throw new ApiError(messageFrom(body, r.status), r.status, body);
  return body as T;
}

export function postJson<T>(path: string, data: unknown, method = "POST"): Promise<T> {
  return apiJson<T>(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
}

export async function download(path: string, fallbackName: string) {
  const r = await api(path);
  if (!r.ok) throw new ApiError(messageFrom(await r.json().catch(() => null), r.status), r.status);
  const cd = r.headers.get("Content-Disposition") || "";
  const name = /filename="([^"]+)"/.exec(cd)?.[1] || fallbackName;
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
