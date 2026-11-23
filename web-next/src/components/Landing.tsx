import Link from "next/link";
import { CONTACT_EMAIL } from "@/lib/legal";
import LogoMark from "@/components/LogoMark";

const pains = [
  ["Money stuck in leftovers", "Sizes and colours that didn't sell sit in the warehouse, discounted at the end of the season."],
  ["Best sellers sold out", "Your M and L in black ran out in week 3, while customers kept asking for weeks."],
  ["Cash runs dry mid-season", "The factory wants its money before couriers pay you back for COD orders."],
  ["Refused at the door", "Some zones and couriers return far more parcels than others, and each return costs you."],
];

const steps = [
  ["Upload your orders", "Export orders from Shopify (or any Excel/CSV). Arabic column names are fine."],
  ["Add your costs", "What each style costs you to make, your budget, cash and factory minimum. About 5 minutes."],
  ["Get your season plan", "How many of each style, size and colour to make, your cash week by week, and the best courier per zone."],
];

const faqs = [
  ["Is it free?", "Yes, during early access. If we add paid plans later, nothing changes unless you choose one."],
  ["Do I need Shopify?", "No. Any orders export works: WooCommerce, Zid, Salla, EasyOrders or your own Excel sheet."],
  [
    "Is my data safe?",
    "Your customers' names, phones and addresses are removed before your file is stored. Your sales data is only used for your plans, never shared with other brands.",
  ],
  ["Does AI decide what I produce?", "No. Quantities come from statistics and optimisation you can check. AI only helps read messy files and explain the plan in plain words."],
  ["How much history do I need?", "At least one season of orders; two or more makes the plan sharper. Around 300+ orders works best."],
];

const kpi = (label: string, value: string, sub: string, tone?: "warn") => (
  <div className="card kpi" style={{ padding: 16, ...(tone === "warn" ? { border: "2px solid var(--warn)" } : {}) }}>
    <p className="kpi-label">{label}</p>
    <p className="kpi-value num" style={{ fontSize: 26, ...(tone === "warn" ? { color: "var(--warn)" } : {}) }}>
      {value}
    </p>
    <p className="kpi-sub">{sub}</p>
  </div>
);

export default function Landing() {
  return (
    <div>
      <header className="topbar" style={{ position: "static" }}>
        <div className="topbar-in" style={{ maxWidth: 1120 }}>
          <span className="logo">
            <LogoMark />
            <span className="logo-word">Fasla</span>
          </span>
          <div className="spacer" />
          <Link className="btn" href="/login/">
            Sign in
          </Link>
          <Link className="btn primary" href="/login/?mode=signup">
            Start free
          </Link>
        </div>
      </header>

      <main style={{ maxWidth: 1120, margin: "0 auto", padding: "0 24px 64px" }}>
        {/* hero */}
        <section style={{ display: "flex", flexWrap: "wrap", gap: 40, alignItems: "center", padding: "56px 0 40px" }}>
          <div style={{ flex: "1 1 440px" }}>
            <p className="eyebrow" style={{ color: "var(--brand)" }}>
              For fashion brands selling online in Egypt
            </p>
            <h1 style={{ fontSize: "clamp(34px, 5vw, 52px)", lineHeight: 1.08, margin: "10px 0 16px" }}>
              Know exactly what to produce next season.
            </h1>
            <p style={{ fontSize: 18, color: "var(--ink-2)", margin: "0 0 24px", maxWidth: 560 }}>
              Fasla turns your past orders into a production plan: how many pieces of each style, size and colour, how low your
              cash will get, and which courier to use in each zone. It uses statistics you can check, not AI guesses.
            </p>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <Link className="btn primary" href="/login/?mode=signup" style={{ height: 48, padding: "0 22px", fontSize: 16 }}>
                Plan your season free
              </Link>
              <a className="btn" href="#how" style={{ height: 48, padding: "0 22px", fontSize: 16 }}>
                How it works
              </a>
            </div>
            <p className="small muted" style={{ marginTop: 12 }}>
              Free during early access · no card needed · try it with our example brand first
            </p>
          </div>
          {/* product preview built from real example numbers */}
          <div style={{ flex: "1 1 420px" }} aria-label="Example of a Fasla plan" role="img">
            <div className="card stack" style={{ gap: 12, background: "var(--bg)", padding: 18 }}>
              <p className="eyebrow">Example brand · winter plan</p>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                {kpi("Pieces to produce", "4,812", "meets 87% of demand")}
                {kpi("Expected profit", "2.34M EGP", "+207K vs the usual way")}
                {kpi("Lowest cash point", "28K EGP", "week 4: very tight", "warn")}
                {kpi("Left over", "711 pcs", "expected at season end")}
              </div>
              <div className="card" style={{ padding: 14 }}>
                <p className="small" style={{ margin: "0 0 8px", fontWeight: 600 }}>
                  Classic Oversized Tee: make 821
                </p>
                {[
                  ["S", 102],
                  ["M", 228],
                  ["L", 255],
                  ["XL", 163],
                  ["XXL", 73],
                ].map(([z, n]) => (
                  <div key={z} className="hbar" style={{ marginBottom: 4 }}>
                    <span className="lbl" style={{ width: 34, fontWeight: 600 }}>
                      {z}
                    </span>
                    <div className="trk">
                      <div className="fill" style={{ width: `${(Number(n) / 255) * 100}%` }} />
                    </div>
                    <span className="val num">{n}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* pains */}
        <section aria-labelledby="pains" style={{ padding: "24px 0" }}>
          <h2 id="pains" style={{ fontSize: 28, marginBottom: 16 }}>
            Every season, the same four problems
          </h2>
          <div className="kpis">
            {pains.map(([t, d]) => (
              <div key={t} className="card">
                <h3 style={{ fontSize: 17, marginBottom: 6 }}>{t}</h3>
                <p className="small muted" style={{ margin: 0 }}>
                  {d}
                </p>
              </div>
            ))}
          </div>
        </section>

        {/* how */}
        <section id="how" aria-labelledby="how-h" style={{ padding: "32px 0" }}>
          <h2 id="how-h" style={{ fontSize: 28, marginBottom: 16 }}>
            How it works
          </h2>
          <ol className="steps" style={{ alignItems: "stretch" }}>
            {steps.map(([t, d], i) => (
              <li key={t} style={{ alignItems: "flex-start", padding: 18 }}>
                <span className="dot" style={{ background: "var(--brand)", color: "#fff" }} aria-hidden="true">
                  {i + 1}
                </span>
                <span>
                  <b style={{ display: "block", marginBottom: 4 }}>{t}</b>
                  <span className="small muted">{d}</span>
                </span>
              </li>
            ))}
          </ol>
        </section>

        {/* trust */}
        <section aria-labelledby="trust-h" style={{ padding: "32px 0" }}>
          <div className="card" style={{ padding: 28 }}>
            <h2 id="trust-h" style={{ fontSize: 28, marginBottom: 8 }}>
              Not another &ldquo;AI predicts your sales&rdquo; tool
            </h2>
            <p className="muted" style={{ marginTop: 0, maxWidth: 760 }}>
              Fasla uses the same inventory maths big retailers use, applied to your own orders. Every number comes with its reason.
            </p>
            <div className="kpis" style={{ marginTop: 16 }}>
              <div>
                <h3 style={{ fontSize: 16, marginBottom: 6 }}>Shows its reasoning</h3>
                <p className="small muted" style={{ margin: 0 }}>
                  &ldquo;Make 821: likely demand is 560–1,067. Past that, an extra piece is more likely to be left over than sold.&rdquo;
                </p>
              </div>
              <div>
                <h3 style={{ fontSize: 16, marginBottom: 6 }}>Fixes sold-out history</h3>
                <p className="small muted" style={{ margin: 0 }}>
                  A size that sold out made last season look smaller than real demand. Fasla spots it and corrects it.
                </p>
              </div>
              <div>
                <h3 style={{ fontSize: 16, marginBottom: 6 }}>Measured, not promised</h3>
                <p className="small muted" style={{ margin: 0 }}>
                  On test brands with known demand, Fasla&apos;s forecasts were about 40% closer than repeating last season&apos;s sales.
                </p>
              </div>
              <div>
                <h3 style={{ fontSize: 16, marginBottom: 6 }}>Shows the risk</h3>
                <p className="small muted" style={{ margin: 0 }}>
                  Ranges instead of one magic number, and a warning before your cash runs out, not after.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* made for */}
        <section aria-labelledby="eg-h" style={{ padding: "16px 0 32px" }}>
          <h2 id="eg-h" style={{ fontSize: 28, marginBottom: 12 }}>
            Made for how you actually sell
          </h2>
          <ul className="clean" style={{ columns: "2 300px", gap: 32 }}>
            <li>Cash on delivery and refused parcels are built in</li>
            <li>Compares your couriers zone by zone (Cairo, Delta, Upper Egypt…)</li>
            <li>Factory minimums and the deposit you pay up front</li>
            <li>Reads Shopify exports and Arabic Excel sheets</li>
            <li>Flags styles and sizes customers exchange too often</li>
            <li>Your customers&apos; personal details are never stored</li>
          </ul>
        </section>

        {/* faq */}
        <section aria-labelledby="faq-h" style={{ padding: "16px 0 32px" }}>
          <h2 id="faq-h" style={{ fontSize: 28, marginBottom: 12 }}>
            Questions
          </h2>
          <div className="stack" style={{ gap: 8 }}>
            {faqs.map(([q, a]) => (
              <details key={q} className="card" style={{ padding: "14px 18px" }}>
                <summary>{q}</summary>
                <p className="small muted" style={{ margin: "8px 0 0" }}>
                  {a}
                </p>
              </details>
            ))}
          </div>
        </section>

        {/* cta */}
        <section className="card" style={{ padding: 32, textAlign: "center", background: "var(--brand-soft)", border: 0 }}>
          <h2 style={{ fontSize: 28, marginBottom: 8 }}>Plan your next season in 10 minutes</h2>
          <p className="muted" style={{ marginTop: 0 }}>
            Start with our example brand, then upload your own orders.
          </p>
          <Link className="btn primary" href="/login/?mode=signup" style={{ height: 48, padding: "0 24px", fontSize: 16 }}>
            Start free
          </Link>
        </section>
      </main>

      <footer style={{ borderTop: "1px solid var(--line)", padding: "20px 24px", textAlign: "center" }} className="small muted">
        <Link href="/terms/">Terms</Link> · <Link href="/privacy/">Privacy</Link> ·{" "}
        <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
      </footer>
    </div>
  );
}
