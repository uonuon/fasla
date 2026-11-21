"use client";
// First-party product analytics: events go to our own database (no third-party trackers, no cookies).
import { api } from "./api";

export function track(name: string, props: Record<string, string | number | boolean> = {}) {
  api("/api/events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, props }),
    keepalive: true,
  }).catch(() => {});
}
