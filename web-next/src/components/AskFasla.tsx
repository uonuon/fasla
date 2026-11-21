"use client";
import { Fragment, useEffect, useRef, useState } from "react";
import { ApiError, apiJson, postJson } from "@/lib/api";

type Allowance = { used: number; limit: number };
type AiUsage = { enabled: boolean; paused?: boolean; explain?: Allowance; ask?: Allowance; resetsOn?: string };
type Msg = { role: "user" | "assistant"; text: string };

/** Very small, safe Markdown: **bold**, "- " bullets, paragraphs. Never injects HTML. */
function Md({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  let list: string[] = [];
  const inline = (s: string, k: string) =>
    s.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
      part.startsWith("**") && part.endsWith("**") ? <b key={`${k}-${i}`}>{part.slice(2, -2)}</b> : <Fragment key={`${k}-${i}`}>{part}</Fragment>,
    );
  const flush = () => {
    if (list.length) {
      const items = list;
      blocks.push(
        <ul key={`ul-${blocks.length}`} className="clean">
          {items.map((li, i) => (
            <li key={i}>{inline(li, `li${blocks.length}-${i}`)}</li>
          ))}
        </ul>,
      );
      list = [];
    }
  };
  text.split("\n").forEach((raw, i) => {
    const line = raw.trim();
    if (/^[-*•]\s+/.test(line)) list.push(line.replace(/^[-*•]\s+/, ""));
    else {
      flush();
      if (line) blocks.push(<p key={`p-${i}`} style={{ margin: "6px 0" }}>{inline(line.replace(/^#+\s*/, ""), `p${i}`)}</p>);
    }
  });
  flush();
  return <>{blocks}</>;
}

function left(a?: Allowance) {
  return a ? Math.max(0, a.limit - a.used) : 0;
}

export default function AskFasla({ planId, suggestions }: { planId: string; suggestions: string[] }) {
  const [usage, setUsage] = useState<AiUsage | null>(null);
  const [explain, setExplain] = useState("");
  const [explainBusy, setExplainBusy] = useState(false);
  const [explainErr, setExplainErr] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const [askBusy, setAskBusy] = useState(false);
  const [askErr, setAskErr] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    apiJson<AiUsage>("/api/ai/usage")
      .then((u) => alive && setUsage(u))
      .catch(() => alive && setUsage({ enabled: false }));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [msgs]);

  if (!usage || !usage.enabled) return null;

  const bump = (kind: "explain" | "ask", u?: Allowance) => u && setUsage((x) => (x ? { ...x, [kind]: { used: u.used, limit: u.limit } } : x));

  async function doExplain() {
    setExplainErr("");
    setExplainBusy(true);
    try {
      const r = await postJson<{ text: string; cached: boolean; usage?: Allowance }>(`/api/plans/${planId}/explain`, {});
      setExplain(r.text);
      bump("explain", r.usage);
    } catch (e) {
      setExplainErr(e instanceof ApiError || e instanceof Error ? e.message : "The assistant couldn't answer.");
    } finally {
      setExplainBusy(false);
    }
  }

  async function ask(question: string) {
    const text = question.trim();
    if (!text || askBusy) return;
    setAskErr("");
    setAskBusy(true);
    const history = msgs.slice(-6);
    setMsgs((m) => [...m, { role: "user", text }]);
    setQ("");
    try {
      const r = await postJson<{ answer: string; usage?: Allowance }>(`/api/plans/${planId}/ask`, { question: text, history });
      setMsgs((m) => [...m, { role: "assistant", text: r.answer }]);
      bump("ask", r.usage);
    } catch (e) {
      setMsgs((m) => m.slice(0, -1));
      setQ(text);
      setAskErr(e instanceof Error ? e.message : "The assistant couldn't answer.");
    } finally {
      setAskBusy(false);
    }
  }

  const explainLeft = left(usage.explain);
  const askLeft = left(usage.ask);

  return (
    <section className="card" aria-labelledby="h-ai">
      <div className="card-head">
        <h2 id="h-ai">Ask Fasla</h2>
        <span className="pill brand">AI assistant</span>
      </div>
      {usage.paused && (
        <p className="note warn" style={{ marginTop: 0 }}>
          The AI assistant is paused until {usage.resetsOn}. Your plan and every number above still work.
        </p>
      )}

      <div className="row" style={{ gap: 20 }}>
        <div className="grow stack" style={{ gap: 10, flexBasis: 420 }}>
          <h3 style={{ margin: 0 }}>Why this plan?</h3>
          {explain ? (
            <div className="note plain" aria-live="polite">
              <Md text={explain} />
            </div>
          ) : (
            <p className="small muted" style={{ margin: 0 }}>
              A short, plain-language summary of what to produce, the cash risk and what to do this week.
            </p>
          )}
          {!explain && (
            <div>
              <button type="button" className="btn primary" onClick={doExplain} disabled={explainBusy || usage.paused || explainLeft === 0}>
                {explainBusy ? "Writing…" : "Explain this plan"}
              </button>
            </div>
          )}
          {explainErr && (
            <p className="note bad small" role="alert" style={{ margin: 0 }}>
              {explainErr}
            </p>
          )}
          <p className="tiny muted" style={{ margin: 0 }}>
            {explainLeft} of {usage.explain?.limit} explanations left this month · opening the same plan again is free
          </p>
        </div>

        <div className="side stack" style={{ gap: 10, flexBasis: 380 }}>
          <h3 style={{ margin: 0 }}>Ask about your plan</h3>
          {msgs.length === 0 && (
            <div className="chips">
              {suggestions.map((s) => (
                <button key={s} type="button" className="chip" style={{ background: "var(--card)", cursor: "pointer" }} onClick={() => ask(s)} disabled={askBusy || usage.paused || askLeft === 0}>
                  {s}
                </button>
              ))}
            </div>
          )}
          {msgs.length > 0 && (
            <div className="stack" style={{ gap: 8, maxHeight: 360, overflowY: "auto" }} aria-live="polite">
              {msgs.map((m, i) => (
                <div
                  key={i}
                  className={"note " + (m.role === "user" ? "info" : "plain")}
                  style={{ alignSelf: m.role === "user" ? "flex-end" : "stretch", maxWidth: m.role === "user" ? "85%" : "100%" }}
                >
                  <span className="sr">{m.role === "user" ? "You: " : "Fasla: "}</span>
                  {m.role === "assistant" ? <Md text={m.text} /> : m.text}
                </div>
              ))}
              {askBusy && <p className="small muted" style={{ margin: 0 }}>Thinking…</p>}
              <div ref={endRef} />
            </div>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              ask(q);
            }}
            style={{ display: "flex", gap: 8 }}
          >
            <label className="sr" htmlFor="ask-input">
              Your question
            </label>
            <input
              id="ask-input"
              value={q}
              maxLength={500}
              onChange={(e) => setQ(e.target.value)}
              placeholder="e.g. What if I only have 1.5M to spend?"
              disabled={usage.paused || askLeft === 0}
              style={{ flex: 1, minWidth: 0, height: 42, border: "1px solid var(--field)", borderRadius: 8, padding: "0 12px" }}
            />
            <button type="submit" className="btn dark" disabled={askBusy || !q.trim() || usage.paused || askLeft === 0}>
              Ask
            </button>
          </form>
          {askErr && (
            <p className="note bad small" role="alert" style={{ margin: 0 }}>
              {askErr}
            </p>
          )}
          <p className="tiny muted" style={{ margin: 0 }}>
            {askLeft} of {usage.ask?.limit} questions left this month. Answers come from your plan&apos;s numbers; check anything important.
          </p>
        </div>
      </div>
    </section>
  );
}
