"use client";
import { useMemo, useState } from "react";
import AskFasla from "./AskFasla";
import CashChart from "./CashChart";
import Replan from "./Replan";
import { cap, fmt, pct, short } from "@/lib/format";
import type { Plan, PlanView, Variant } from "@/lib/types";

type Tone = "ok" | "warn" | "bad" | "plain";

/** Index of the budget we recommend: most expected profit that keeps a cash cushion. */
export function recommendedIndex(v: PlanView): number {
  const cushion = Math.max(0, 0.15 * (v.settings.start_cash ?? 0));
  const pick = (ok: (x: Variant) => boolean) =>
    v.variants.reduce<number>((best, x, i) => (ok(x) && (best < 0 || x.profit > v.variants[best].profit) ? i : best), -1);
  const a = pick((x) => x.cash_min >= cushion);
  if (a >= 0) return a;
  const b = pick((x) => x.cash_min >= 0);
  if (b >= 0) return b;
  // every option runs out of cash: point to the least risky one
  return v.variants.reduce((best, x, i) => (x.cash_min > v.variants[best].cash_min ? i : best), 0);
}

function advice(v: PlanView, i: number, rec: number): { tone: Tone; title: string; text: string } {
  const x = v.variants[i];
  const r = v.variants[rec];
  const cushion = Math.max(0, 0.15 * (v.settings.start_cash ?? 0));
  const vsRec = x.profit - r.profit;
  const vsRecText =
    i === rec ? "" : ` That's ${short(Math.abs(vsRec))} EGP ${vsRec >= 0 ? "more" : "less"} expected profit than our pick (${Math.round(r.mult * 100)}%).`;
  if (x.cash_min < 0 && r.cash_min < 0)
    return {
      tone: "bad",
      title: "Cash runs out at every budget level.",
      text: `At ${Math.round(x.mult * 100)}% you'd be ${short(-x.cash_min)} EGP short in week ${x.cash_min_week}. Lower your fixed costs, ask buyers for deposits, or add cash before producing. The least risky option is ${Math.round(r.mult * 100)}%.`,
    };
  if (x.cash_min < 0)
    return {
      tone: "bad",
      title: "Cash runs out.",
      text: `You'd be ${short(-x.cash_min)} EGP short in week ${x.cash_min_week}. You'd need a loan or bigger deposits from buyers.${vsRecText}`,
    };
  if (i === rec)
    return {
      tone: "ok",
      title: "Our pick for this season.",
      text: `The most expected profit while cash stays above ${short(x.cash_min)} EGP at its lowest (week ${x.cash_min_week}). You meet ${pct(x.fill)} of expected demand.`,
    };
  if (x.cash_min < cushion)
    return {
      tone: "warn",
      title: "Cash gets very tight.",
      text: `Cash falls to ${short(x.cash_min)} EGP in week ${x.cash_min_week}. One late courier payment and bills are at risk.${vsRecText}`,
    };
  if (x.mult < r.mult)
    return {
      tone: "plain",
      title: "Safe, but you leave sales on the table.",
      text: `Cash never drops below ${short(x.cash_min)} EGP, but you meet only ${pct(x.fill)} of expected demand.${vsRecText}`,
    };
  return { tone: "plain", title: "A comfortable option.", text: `Cash stays above ${short(x.cash_min)} EGP.${vsRecText}` };
}

export default function Dashboard({ plan, view, onDownload, onReplan }: { plan: Plan; view: PlanView; onDownload: () => void; onReplan: () => void }) {
  const rec = useMemo(() => recommendedIndex(view), [view]);
  const [bi, setBi] = useState(view.base);
  const V = view.variants[bi];
  const meta = useMemo(() => Object.fromEntries(view.styles.map((s) => [s.id, s])), [view]);
  const rows = useMemo(() => [...V.style].sort((a, b) => b.planned - a.planned), [V]);
  const [sel, setSel] = useState<string>(() => [...view.variants[view.base].style].sort((a, b) => b.planned - a.planned)[0]?.id ?? "");
  const ad = advice(view, bi, rec);
  const leftoverShare = view.settings.leftover_share ?? 0.3;
  const cushion = Math.max(0, 0.15 * (view.settings.start_cash ?? 0));
  const cashTone = V.cash_min < 0 ? "out" : V.cash_min < cushion ? "tight" : "";
  const isExample = /example/i.test(plan.input_name || "");

  return (
    <main className="page">
      {isExample && (
        <div className="note info" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
          <span style={{ flex: "1 1 320px" }}>
            This plan uses the <b>example brand&apos;s</b> made-up sales, so you can explore. Upload your own orders to plan with your
            numbers.
          </span>
          <a href={`/setup/?brand=${plan.brand_id}`} style={{ fontWeight: 600, color: "var(--info-ink)" }}>
            Use my data →
          </a>
        </div>
      )}

      <section aria-labelledby="h-plan" style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: "16px 32px" }}>
        <div style={{ flex: "1 1 420px" }}>
          <p className="eyebrow" style={{ color: "var(--brand)" }}>
            {view.brand} · plan ready
          </p>
          <h1 id="h-plan" style={{ fontSize: 32, lineHeight: 1.15, marginTop: 4 }}>
            Your season, in one page
          </h1>
          <p className="muted" style={{ margin: "8px 0 0" }}>
            Built from {fmt(view.history.orders)} past orders over {view.history.seasons} season{view.history.seasons === 1 ? "" : "s"},{" "}
            {view.styles.length} styles and {fmt(view.history.skus)} size-and-colour combinations.
          </p>
        </div>
        <div style={{ flex: "0 1 560px", width: "100%" }}>
          <p id="budget-label" className="small" style={{ margin: "0 0 8px", fontWeight: 600, color: "var(--ink-2)" }}>
            Production budget
          </p>
          <div className="seg" role="group" aria-labelledby="budget-label">
            {view.variants.map((x, i) => (
              <button key={x.mult} type="button" aria-pressed={i === bi} onClick={() => setBi(i)}>
                <b>
                  {Math.round(x.mult * 100)}%{i === rec ? " ★" : ""}
                </b>
                <span>{short(x.budget)} EGP</span>
              </button>
            ))}
          </div>
          <p className="tiny muted" style={{ margin: "6px 0 0" }}>
            100% is the budget you entered. ★ is our pick. Different budget or costs?{" "}
            <a href="#replan-bottom">Change numbers &amp; re-plan</a>
          </p>
        </div>
      </section>

      <div className={`note ${ad.tone}`} role="status" aria-live="polite" style={{ fontSize: 15 }}>
        <b>{ad.title}</b> {ad.text}
      </div>

      <section aria-label="Key numbers" className="kpis">
        <div className="card kpi">
          <p className="kpi-label">Pieces to produce</p>
          <p className="kpi-value num">{fmt(V.units)}</p>
          <p className="kpi-sub">
            Costs {short(V.cash_used)} EGP · meets {pct(V.fill)} of expected demand
          </p>
        </div>
        <div className="card kpi">
          <p className="kpi-label">Expected profit this season</p>
          <p className="kpi-value num">
            {short(V.profit)} <span className="kpi-unit">EGP</span>
          </p>
          <p className="kpi-sub">
            8 seasons in 10: {short(V.p10)} – {short(V.p90)}
          </p>
          {bi === view.base && V.profit > view.usual.profit && (
            <p className="kpi-sub good" style={{ marginTop: 8, fontWeight: 600 }}>
              +{short(V.profit - view.usual.profit)} EGP vs producing the usual way
            </p>
          )}
        </div>
        <div className={`card kpi ${cashTone}`}>
          <p className="kpi-label">Lowest cash point</p>
          <p className="kpi-value num">
            {short(V.cash_min)} <span className="kpi-unit">EGP</span>
          </p>
          <p className="kpi-sub">
            Week {V.cash_min_week} · {V.cash_min < 0 ? "runs out" : V.cash_min < cushion ? "very tight" : "comfortable"}
          </p>
        </div>
        <div className="card kpi">
          <p className="kpi-label">Left unsold at season end</p>
          <p className="kpi-value num">
            {fmt(V.leftover)} <span className="kpi-unit">pieces</span>
          </p>
          <p className="kpi-sub">Expected on average; sold off at {pct(leftoverShare)} of price</p>
        </div>
      </section>

      <AskFasla
        planId={plan.id}
        suggestions={[
          `Why make ${fmt(rows[0]?.planned ?? 0)} of ${meta[rows[0]?.id]?.name ?? "my top style"}?`,
          "What if I can only spend 80% of the budget?",
          "What's my biggest risk this season?",
        ]}
      />

      <section className="row" aria-labelledby="h-produce">
        <div className="card grow">
          <div className="card-head">
            <h2 id="h-produce">What to produce</h2>
            <span className="small muted">At {Math.round(V.mult * 100)}% budget · pick a style for sizes and colours</span>
          </div>
          <div className="tbl-wrap">
            <table className="tbl" style={{ minWidth: 640 }}>
              <thead>
                <tr>
                  <th scope="col">Style</th>
                  <th scope="col" className="r">
                    Make
                  </th>
                  <th scope="col" style={{ width: "38%" }}>
                    Likely demand vs plan
                  </th>
                  <th scope="col" className="r">
                    Sells out
                  </th>
                  <th scope="col" className="r">
                    Left over
                  </th>
                </tr>
              </thead>
              <tbody>
                <StyleRows rows={rows} meta={meta} sel={sel} onSel={setSel} />
              </tbody>
            </table>
          </div>
          <p className="tiny muted" style={{ margin: "12px 0 0" }}>
            <span style={{ display: "inline-block", width: 14, height: 8, borderRadius: 4, background: "var(--brand-range)", verticalAlign: "middle" }} /> likely demand (8 seasons in 10) &nbsp;
            <span style={{ display: "inline-block", width: 3, height: 12, background: "var(--brand)", verticalAlign: "middle" }} /> pieces planned · &quot;Sells out&quot; is the chance this style runs out before the season ends
          </p>
        </div>
        <StyleDetail view={view} V={V} id={sel} />
      </section>

      <section className="card" aria-labelledby="h-cash">
        <div className="card-head">
          <h2 id="h-cash">Cash in the bank, week by week</h2>
          <span className="small muted">
            At {Math.round(V.mult * 100)}% budget · you start with {short(view.settings.start_cash ?? V.cash[0])} EGP; the factory deposit goes out in week 0
          </span>
        </div>
        <CashChart
          cash={V.cash}
          label={`Cash starts at ${short(V.cash[0])} EGP, is lowest at ${short(V.cash_min)} EGP in week ${V.cash_min_week}, and ends at ${short(V.cash[V.cash.length - 1])} EGP.`}
        />
        <p className="small muted" style={{ margin: "12px 0 0" }}>
          The dip is the factory deposit and the rest of the production bill. Customer cash comes back after couriers deliver and pay you.
        </p>
      </section>

      <section className="row">
        <Couriers view={view} />
        <Returns view={view} />
      </section>

      <Trust view={view} />

      <div id="replan-bottom" className="actions" style={{ alignItems: "flex-start" }}>
        <Replan plan={plan} onStarted={onReplan} />
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span className="small muted">Every number, size by size:</span>
          <button type="button" className="btn" onClick={onDownload}>
            Download Excel report
          </button>
        </div>
      </div>
    </main>
  );
}

function StyleRows({
  rows,
  meta,
  sel,
  onSel,
}: {
  rows: Variant["style"];
  meta: Record<string, PlanView["styles"][number]>;
  sel: string;
  onSel: (id: string) => void;
}) {
  const maxD = Math.max(1, ...rows.map((r) => Math.max(meta[r.id]?.f_p90 ?? 0, r.planned))) * 1.05;
  return (
    <>
      {rows.map((r) => {
        const m = meta[r.id];
        if (!m) return null;
        const isNew = /new/i.test(m.source);
        return (
          <tr key={r.id} className={r.id === sel ? "sel" : undefined}>
            <td>
              <button type="button" className="rowbtn" aria-pressed={r.id === sel} onClick={() => onSel(r.id)}>
                {m.name}
              </button>
              <span className="tiny muted" style={{ display: "block" }}>
                {isNew ? "New style · estimated from similar ones" : m.category === "Other" ? "" : cap(m.category)}
              </span>
            </td>
            <td className="r num" style={{ fontWeight: 700 }}>
              {fmt(r.planned)}
            </td>
            <td>
              <div className="range" role="img" aria-label={`Likely demand ${fmt(m.f_p10)} to ${fmt(m.f_p90)}, planned ${fmt(r.planned)}`}>
                <div className="track" />
                <div className="band" style={{ left: `${(m.f_p10 / maxD) * 100}%`, width: `${((m.f_p90 - m.f_p10) / maxD) * 100}%` }} />
                <div className="mark" style={{ left: `${(r.planned / maxD) * 100}%` }} />
              </div>
            </td>
            <td className="r num">{pct(r.sellout)}</td>
            <td className="r num muted">{fmt(r.left)}</td>
          </tr>
        );
      })}
    </>
  );
}

function StyleDetail({ view, V, id }: { view: PlanView; V: Variant; id: string }) {
  const m = view.styles.find((s) => s.id === id);
  const r = V.style.find((s) => s.id === id);
  const { sizes, colors } = useMemo(() => {
    const sz: Record<string, number> = {};
    const co: Record<string, number> = {};
    view.sku.forEach((k, i) => {
      if (k.s !== id) return;
      sz[k.z] = (sz[k.z] ?? 0) + (V.Q[i] ?? 0);
      co[k.c] = (co[k.c] ?? 0) + (V.Q[i] ?? 0);
    });
    return { sizes: sz, colors: co };
  }, [view, V, id]);
  if (!m || !r) return null;
  const order = view.sizes.filter((z) => z in sizes).concat(Object.keys(sizes).filter((z) => !view.sizes.includes(z)));
  const maxZ = Math.max(1, ...Object.values(sizes));
  return (
    <aside className="card side stack" style={{ gap: 16 }} aria-labelledby="h-style">
      <div>
        {m.category !== "Other" && <p className="eyebrow">{cap(m.category)}</p>}
        <h2 id="h-style" style={{ marginTop: 4 }}>
          {m.name}
        </h2>
        <p className="small muted" style={{ margin: "6px 0 0" }}>
          Make <b style={{ color: "var(--ink)" }}>{fmt(r.planned)}</b> · cost {fmt(m.cost)} · price {fmt(m.price)} EGP
        </p>
      </div>
      <div>
        <h3>By size</h3>
        <div className="stack">
          {order.map((z) => (
            <div key={z} className="hbar">
              <span className="lbl" style={{ width: 40, fontWeight: 600 }}>
                {z}
              </span>
              <div className="trk">
                <div className="fill" style={{ width: `${(sizes[z] / maxZ) * 100}%` }} />
              </div>
              <span className="val num">{fmt(sizes[z])}</span>
            </div>
          ))}
        </div>
      </div>
      <div>
        <h3>By colour</h3>
        <div className="chips">
          {Object.entries(colors)
            .sort((a, b) => b[1] - a[1])
            .map(([c, n]) => (
              <span key={c} className="chip">
                {c} <b className="num">{fmt(n)}</b>
              </span>
            ))}
        </div>
      </div>
      <div className="note plain small">
        <b>Why this number:</b> demand is likely {fmt(m.f_p10)}–{fmt(m.f_p90)} pieces
        {m.owner ? ` (your estimate: ${fmt(m.owner)})` : ""}. Fasla stops where one more piece is more likely to be left over than sold. Based on{" "}
        {m.source}.
      </div>
    </aside>
  );
}

function Couriers({ view }: { view: PlanView }) {
  const c = view.couriers;
  const names = new Set(c.zones.flatMap((z) => z.alts.map((a) => a.courier)));
  const gain = c.plan - c.now;
  if (names.size <= 1) {
    return (
      <div id="couriers" className="card grow">
        <h2>Delivery</h2>
        <p className="muted">
          You use one courier{names.size ? ` (${[...names][0]})` : ""}, so there&apos;s nothing to compare yet. Profit after delivery is about{" "}
          {fmt(c.now)} EGP per order. Add a second courier&apos;s fees in your data and Fasla will show which one is best in each zone.
        </p>
      </div>
    );
  }
  return (
    <div id="couriers" className="card grow">
      <div className="card-head">
        <h2>Couriers by zone</h2>
        {gain > 0.5 ? (
          <span className="small good" style={{ fontWeight: 600 }}>
            +{fmt(gain)} EGP per order · about +{short(gain * c.orders)} EGP a season
          </span>
        ) : (
          <span className="small muted">You&apos;re already using the best courier in each zone</span>
        )}
      </div>
      <div className="tbl-wrap">
        <table className="tbl" style={{ minWidth: 560 }}>
          <thead>
            <tr>
              <th scope="col">Zone</th>
              <th scope="col">Today → Use</th>
              <th scope="col" className="r">
                Delivered
              </th>
              <th scope="col" className="r">
                Profit / order
              </th>
            </tr>
          </thead>
          <tbody>
            {c.zones.map((z) => {
              const nowAlt = z.alts.find((a) => a.courier === z.now);
              const same = !z.now || z.pick === z.now;
              const d = nowAlt ? z.profit - nowAlt.profit : 0;
              return (
                <tr key={z.zone}>
                  <td style={{ fontWeight: 600 }}>
                    {z.zone}
                    <span className="tiny muted" style={{ display: "block", fontWeight: 400 }}>
                      {fmt(z.orders)} orders
                    </span>
                  </td>
                  <td>
                    {z.now || "—"} → {same ? <span className="muted">keep</span> : <b style={{ color: "var(--brand)" }}>{z.pick}</b>}
                  </td>
                  <td className="r num">{pct(z.rate)}</td>
                  <td className="r num">
                    {fmt(z.profit)} EGP
                    <span className={"tiny " + (same || d < 0.5 ? "muted" : "good")} style={{ display: "block", fontWeight: same ? 400 : 600 }}>
                      {same || d < 0.5 ? "no change" : `+${fmt(d)} vs today`}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="tiny muted" style={{ margin: "10px 0 0" }}>
        &quot;Delivered&quot; is the share of orders customers accept at the door with that courier.
      </p>
    </div>
  );
}

function Returns({ view }: { view: PlanView }) {
  const r = view.refusal;
  const zones = [...r.zones].sort((a, b) => b.rate - a.rate);
  const flagged = view.exchanges.rows.filter((x) => x.flag && x.rate > 0);
  const worst = zones[0];
  const high = (x: number) => x >= Math.max(0.3, r.overall * 1.25);
  const maxR = Math.max(0.5, ...zones.map((z) => z.rate));
  return (
    <div id="returns" className="card side stack" style={{ gap: 16 }}>
      <h2>Returns to watch</h2>
      {flagged.map((x) => (
        <div key={x.id} className="note warn">
          <b>
            {x.name}
            {x.size ? `, size ${x.size}` : ""}:
          </b>{" "}
          {x.size ? `${pct(x.size_rate)} of pieces are exchanged for another size` : `${pct(x.rate)} are exchanged`} (brand average{" "}
          {pct(view.exchanges.base, 1)}). Check the size chart before producing.
        </div>
      ))}
      {flagged.length === 0 && <p className="small muted" style={{ margin: 0 }}>No style stands out for exchanges.</p>}
      {zones.length > 0 && (
        <div>
          <h3>Orders refused at the door, by zone</h3>
          <div className="stack">
            {zones.map((z) => (
              <div key={z.zone} className="hbar">
                <span className="lbl">{z.zone}</span>
                <div className="trk">
                  <div className={"fill " + (high(z.rate) ? "warn" : "grey")} style={{ width: `${(z.rate / maxR) * 100}%` }} />
                </div>
                <span className="val num">{pct(z.rate)}</span>
              </div>
            ))}
          </div>
          <p className="small muted" style={{ margin: "8px 0 0" }}>
            Overall {pct(r.overall, 1)}.
            {worst && high(worst.rate) ? ` ${worst.zone} refuses ${pct(worst.rate)}: consider asking for a deposit there.` : ""}
          </p>
        </div>
      )}
    </div>
  );
}

/** How much to trust these numbers: what the forecast is built on and how accurate it has been. */
function Trust({ view }: { view: PlanView }) {
  const h = view.history;
  const growth = h.growth - 1;
  const soldOut = h.production_known
    ? h.soldout > 0
      ? `${fmt(h.soldout)} sizes sold out in past seasons, so their recorded sales understate demand. Fasla estimated how much more they would have sold and planned for that.`
      : "No size sold out in past seasons, so recorded sales are real demand."
    : (h.soldout_inferred ?? 0) > 0
      ? `Your file has no production numbers, so Fasla looked at when each size stopped selling: ${fmt(h.soldout_inferred ?? 0)} sizes most likely sold out early, and their demand was adjusted up.`
      : "Your file has no production numbers; none of your sizes looked like they sold out early.";
  return (
    <section className="card" aria-labelledby="h-trust">
      <div className="card-head">
        <h2 id="h-trust">How much to trust these numbers</h2>
      </div>
      <ul className="clean small" style={{ margin: 0 }}>
        <li>
          <b>Built from</b> {fmt(h.orders)} orders over {h.seasons} season{h.seasons === 1 ? "" : "s"}.{" "}
          {h.seasons < 2 ? "With one season Fasla can't see whether you're growing, so it assumes you'll sell about the same." : `Fasla expects about ${pct(Math.abs(growth))} ${growth >= 0 ? "more" : "fewer"} sales than last season, measured on sizes that stayed in stock in both seasons and kept cautious.`}
        </li>
        <li>
          <b>Sell-outs:</b> {soldOut}
        </li>
        <li>
          <b>Accuracy:</b> on test brands where the true demand is known, Fasla&apos;s forecast per style was about 40% closer than
          repeating last season&apos;s sales (about 20% off vs 30% off).
          {view.check
            ? ` On your own data, planning ${view.check.season} from the seasons before it: ${pct(view.check.style_error)} off per style, vs ${pct(view.check.repeat_last_error)} for repeating last season.`
            : " The ranges in the table (8 seasons in 10) show how far one season can swing."}
        </li>
        {view.problems.map((p, i) => (
          <li key={i} className="muted">
            {p}
          </li>
        ))}
      </ul>
    </section>
  );
}
