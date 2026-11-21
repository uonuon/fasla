import Link from "next/link";
import { CONTACT_EMAIL, LAST_UPDATED, OPERATOR } from "@/lib/legal";

export const metadata = { title: "Terms of use · Fasla" };

export default function Terms() {
  return (
    <main className="page narrow" style={{ maxWidth: 760 }}>
      <Link href="/" className="small">
        ← Fasla
      </Link>
      <article className="card stack" style={{ gap: 14, padding: 28, lineHeight: 1.65 }}>
        <h1 style={{ fontSize: 30 }}>Terms of use</h1>
        <p className="small muted" style={{ margin: 0 }}>
          Last updated {LAST_UPDATED}. Fasla is run by {OPERATOR}. Contact: <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
        </p>

        <h2 style={{ fontSize: 20 }}>1. What Fasla is</h2>
        <p>
          Fasla is a planning tool. From your past orders and the numbers you enter, it suggests how much of each style, size and
          colour to produce, estimates your cash over the season and compares couriers. It is currently offered free as an early
          version, and features may change.
        </p>

        <h2 style={{ fontSize: 20 }}>2. Suggestions, not guarantees</h2>
        <p>
          Plans are estimates based on your data and statistical methods. Real sales depend on many things Fasla can&apos;t see. You
          decide what to produce and spend; we are not responsible for business decisions made using Fasla, or for losses from
          them. Check important numbers before acting, especially if your data is incomplete.
        </p>

        <h2 style={{ fontSize: 20 }}>3. Your data</h2>
        <p>
          Your data stays yours. You give us permission to store and process it to provide Fasla, as described in the{" "}
          <Link href="/privacy">Privacy policy</Link>. Only upload data you are allowed to share. Don&apos;t upload files containing other
          people&apos;s sensitive information beyond normal order details.
        </p>

        <h2 style={{ fontSize: 20 }}>4. Fair use</h2>
        <p>
          Don&apos;t misuse Fasla: no attempts to access other accounts, overload the service, or reverse-engineer it. AI features have
          monthly limits and may be paused.
        </p>

        <h2 style={{ fontSize: 20 }}>5. Availability and changes</h2>
        <p>
          We try to keep Fasla running but can&apos;t promise it will always be available or error-free. We may update these terms; if
          we make important changes we&apos;ll ask you to agree again. We may introduce paid plans; you won&apos;t be charged unless you
          choose a paid plan.
        </p>

        <h2 style={{ fontSize: 20 }}>6. Ending</h2>
        <p>
          You can stop using Fasla and ask us to delete your account at any time. We may suspend accounts that break these terms.
        </p>
      </article>
    </main>
  );
}
