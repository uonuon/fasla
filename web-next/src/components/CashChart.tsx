"use client";
import { useState } from "react";
import { fmt, short } from "@/lib/format";

/** Weekly bank balance, one series. Hover or focus a week to read its value. */
export default function CashChart({ cash, label }: { cash: number[]; label: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 1000,
    H = 280,
    padL = 56,
    padR = 12,
    padT = 16,
    padB = 30;
  const n = cash.length;
  const hi = Math.max(...cash, 0);
  const lo = Math.min(...cash, 0);
  const span = hi - lo || 1;
  const top = hi + span * 0.08;
  const bot = lo - (lo < 0 ? span * 0.05 : 0);
  const X = (i: number) => padL + (i / Math.max(1, n - 1)) * (W - padL - padR);
  const Y = (v: number) => padT + ((top - v) / (top - bot)) * (H - padT - padB);
  const line = cash.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(" ");
  const base = Y(Math.max(bot, 0));
  const area = `${line} L${X(n - 1).toFixed(1)} ${base.toFixed(1)} L${X(0).toFixed(1)} ${base.toFixed(1)} Z`;
  const lowI = cash.indexOf(Math.min(...cash));
  const ticks = niceTicks(bot, top, 4);
  const weeks = Array.from(new Set([0, Math.round((n - 1) / 4), Math.round((n - 1) / 2), Math.round((3 * (n - 1)) / 4), n - 1]));
  const low = cash[lowI];
  const lowColor = low < 0 ? "var(--bad)" : "var(--warn)";
  const step = (W - padL - padR) / Math.max(1, n - 1);

  return (
    <div className="chart-scroll">
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={Y(t)} y2={Y(t)} stroke={t === 0 ? "#94a3b8" : "#eef1f5"} strokeWidth={1} strokeDasharray={t === 0 ? "4 4" : undefined} />
            <text x={padL - 8} y={Y(t) + 4} textAnchor="end" fontSize={12} fill="var(--muted)">
              {short(t)}
            </text>
          </g>
        ))}
        {weeks.map((w) => (
          <text key={w} x={X(w)} y={H - 8} textAnchor="middle" fontSize={12} fill="var(--muted)">
            Week {w}
          </text>
        ))}
        <path d={area} fill="var(--brand-soft)" />
        <path d={line} fill="none" stroke="var(--brand)" strokeWidth={2} strokeLinejoin="round" />
        {hover !== null && <line x1={X(hover)} x2={X(hover)} y1={padT} y2={H - padB} stroke="#94a3b8" strokeWidth={1} />}
        {hover !== null && <circle cx={X(hover)} cy={Y(cash[hover])} r={5} fill="var(--brand)" stroke="#fff" strokeWidth={2} />}
        <circle cx={X(lowI)} cy={Y(low)} r={7} fill={lowColor} stroke="#fff" strokeWidth={2} />
        {cash.map((v, i) => (
          <rect
            key={i}
            x={X(i) - step / 2}
            y={padT}
            width={step}
            height={H - padT - padB}
            fill="transparent"
            tabIndex={-1}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          >
            <title>{`Week ${i}: ${fmt(v)} EGP`}</title>
          </rect>
        ))}
      </svg>
      <div
        className="tip"
        style={{
          left: `${(X(hover ?? lowI) / W) * 100}%`,
          top: `${(Y(cash[hover ?? lowI]) / H) * 100}%`,
          background: hover === null ? (low < 0 ? "var(--bad)" : "var(--warn)") : undefined,
        }}
      >
        {hover === null ? `Lowest: week ${lowI}, ${short(low)} EGP` : `Week ${hover}: ${fmt(cash[hover])} EGP`}
      </div>
    </div>
    </div>
  );
}

function niceTicks(lo: number, hi: number, count: number): number[] {
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) out.push(Math.round(t));
  if (!out.includes(0) && lo < 0 && hi > 0) out.push(0);
  return out.sort((a, b) => a - b);
}
