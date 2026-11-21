import Link from "next/link";
import { CONTACT_EMAIL, LAST_UPDATED, OPERATOR } from "@/lib/legal";

export const metadata = { title: "Privacy policy · Fasla" };

export default function Privacy() {
  return (
    <main className="page narrow" style={{ maxWidth: 760 }}>
      <Link href="/" className="small">
        ← Fasla
      </Link>
      <article className="card stack" style={{ gap: 14, padding: 28, lineHeight: 1.65 }}>
        <h1 style={{ fontSize: 30 }}>Privacy policy</h1>
        <p className="small muted" style={{ margin: 0 }}>
          Last updated {LAST_UPDATED}. Fasla is run by {OPERATOR}. Questions: <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
        </p>

        <h2 style={{ fontSize: 20 }}>The short version</h2>
        <ul className="clean">
          <li>We use your sales data only to make your plans, and never sell it or show it to other brands.</li>
          <li>We don&apos;t keep your customers&apos; personal details. Names, phone numbers, emails and addresses are removed from your file before it is stored.</li>
          <li>You can delete a plan or a whole brand at any time, and its files are deleted with it.</li>
          <li>We only use your data to improve Fasla if you say yes, and only anonymised and combined with other brands.</li>
        </ul>

        <h2 style={{ fontSize: 20 }}>What we collect</h2>
        <ul className="clean">
          <li>
            <b>Your account:</b> your email address and password (passwords are stored hashed by our login provider, Supabase).
          </li>
          <li>
            <b>Your sales file, minus customer details:</b> only the columns Fasla reads: order number, date, product, size, colour,
            quantity, price, city or zone, order status, courier, cost and category. Every other column is dropped before we save
            the file.
          </li>
          <li>
            <b>What you enter:</b> brand name, costs, budget, cash, factory and courier settings, and your sales estimates.
          </li>
          <li>
            <b>Your plans:</b> the results Fasla calculates and the Excel reports.
          </li>
          <li>
            <b>How you use Fasla:</b> simple events such as &ldquo;uploaded a file&rdquo; or &ldquo;opened a plan&rdquo;, stored in our own database
            to improve the product. We don&apos;t use advertising trackers or third-party analytics.
          </li>
          <li>
            <b>AI assistant:</b> if you use it, your plan&apos;s numbers are sent to the AI model to answer you (Anthropic Claude on
            Amazon Bedrock, processed in the EU). We count how much you use it, and we keep plan explanations so we don&apos;t
            pay for them twice. Your questions and answers are not stored.
          </li>
        </ul>

        <h2 style={{ fontSize: 20 }}>Where it is kept</h2>
        <p>
          Your files and account are stored with Supabase (EU, Frankfurt) and Fasla runs on Amazon Web Services (EU, Stockholm).
          Data is encrypted in transit (HTTPS). Only you can see your brands and plans; our team accesses them only to fix a problem
          you reported or when the law requires it.
        </p>

        <h2 style={{ fontSize: 20 }}>Improving Fasla</h2>
        <p>
          If you allow it, we may study anonymised sales patterns across brands (for example how often sizes sell out) to make
          forecasts more accurate. Nothing that identifies your brand is ever shared. You can change this choice by emailing us.
        </p>

        <h2 style={{ fontSize: 20 }}>Keeping and deleting</h2>
        <p>
          We keep your data while your account is active. Deleting a plan or brand in Fasla deletes its files. To delete your
          account and everything in it, email <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a> and we will do it within 30 days.
        </p>

        <h2 style={{ fontSize: 20 }}>Your rights</h2>
        <p>
          You can ask for a copy of your data, ask us to correct or delete it, or withdraw your consent, by emailing{" "}
          <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
        </p>
      </article>
    </main>
  );
}
