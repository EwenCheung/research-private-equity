import { useEffect, useState } from "react";
import { api } from "../api";
import ChartCard from "../components/ChartCard";

export const meta = { title: "Hot Pick", path: "/hot-pick", order: 5 };

type Period = "week" | "month";
const NAMES: Record<Period, { now: string; past: string; one: string }> = {
  week: { now: "What stood out this week?", past: "What stood out in earlier weeks?", one: "week" },
  month: { now: "What stood out this month?", past: "What stood out in earlier months?", one: "month" },
};

export default function HotPick() {
  const [period, setPeriod] = useState<Period>("month");
  const [ids, setIds] = useState<string[] | null>(null);

  useEffect(() => {
    api<{ id: string }[]>("/api/marts")
      .then((marts) => setIds(marts.map((m) => m.id)))
      .catch(() => setIds([]));
  }, []);

  const saved = ids === null ? null : ids.filter((id) => id.startsWith(`hot_pick.${period}_`)).sort().reverse();

  return (
    <>
      <header className="page-head">
        <h1>Hot Pick</h1>
        <p style={{ marginBottom: 10 }}>
          <strong>What you are reading.</strong> The few things about Anthropic that stood out: the news several outlets ran, the
          blogs and newsletters writing about it, the most-discussed Hacker News stories, popular developer posts, new GitHub
          repositories people are building around Claude, new filings that name Anthropic, and the biggest moves in our own
          signals. Each pick says why it was picked and links to its source.
        </p>
        <p>
          <strong>How to read it.</strong> Each kind is scored in its own unit (outlets, points, reactions, stars, filers, size of
          the move) and never against another kind, so read a score within its kind. A quiet period shows fewer picks, not filler.
          The picks are rules applied to public feeds, not a judgement of importance.
        </p>
        <div className="seg" role="group" aria-label="Period">
          {(["week", "month"] as Period[]).map((p) => (
            <button key={p} type="button" aria-pressed={p === period} onClick={() => setPeriod(p)}>
              {p === "week" ? "Week" : "Month"}
            </button>
          ))}
        </div>
      </header>
      <section>
        <h2 className="section-title">{NAMES[period].now}</h2>
        <p className="subtitle" style={{ marginBottom: 14 }}>
          Rebuilt every time the data is refreshed. The {NAMES[period].one} covers the {period === "week" ? "last 7" : "last 30"} days.
        </p>
        <div className="grid">
          <ChartCard key={period} id={`hot_pick.this_${period}`} />
        </div>
      </section>
      <section>
        <h2 className="section-title">{NAMES[period].past}</h2>
        <p className="subtitle" style={{ marginBottom: 14 }}>
          Each {NAMES[period].one} is saved with the Hot Pick command, and a saved {NAMES[period].one} never changes.
        </p>
        {saved === null ? <div className="skeleton" style={{ height: 120 }} /> : null}
        {saved && saved.length === 0 ? <p>No {NAMES[period].one} has been saved yet.</p> : null}
        <div className="grid">
          {(saved ?? []).map((id) => (
            <ChartCard key={id} id={id} />
          ))}
        </div>
      </section>
    </>
  );
}
