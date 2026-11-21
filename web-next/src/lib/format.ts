export const fmt = (n: number) => Math.round(n).toLocaleString("en-US");

/** 2093000 -> "2.09M", 28461 -> "28K", -291621 -> "−292K" */
export function short(n: number): string {
  const sign = n < 0 ? "−" : "";
  const a = Math.abs(n);
  if (a >= 1e6) return sign + (a / 1e6).toFixed(2).replace(/\.?0+$/, "") + "M";
  if (a >= 1e3) return sign + Math.round(a / 1e3) + "K";
  return sign + Math.round(a);
}

export const pct = (x: number, digits = 0) => (x * 100).toFixed(digits) + "%";

export const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
