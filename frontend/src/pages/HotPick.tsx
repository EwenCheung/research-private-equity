import { useEffect, useState } from "react";
import { api } from "../api";
import ChartCard from "../components/ChartCard";

export const meta = { title: "Hot Pick", path: "/hot-pick", order: 5 };

export default function HotPick() {
  const [weeks, setWeeks] = useState<string[] | null>(null);

  useEffect(() => {
    api<{ id: string }[]>("/api/marts")
      .then((marts) => setWeeks(marts.map((m) => m.id).filter((id) => id.startsWith("hot_pick.week_")).sort().reverse()))
      .catch(() => setWeeks([]));
  }, []);

  return (
    <>
      <header className="page-head">
        <h1>Hot Pick</h1>
        <p style={{ marginBottom: 10 }}>
          <strong>What you are reading.</strong> The few things about Anthropic that stood out in the last seven days: the news that
          several outlets ran, the most-discussed Hacker News story, new filings that name Anthropic, and the biggest moves in our own
          signals. Each pick says why it was picked and links to its source.
        </p>
        <p>
          <strong>How to read it.</strong> Each kind is scored in its own unit (outlets, points, filers, size of the move) and never
          against another kind, so read a score within its kind. A quiet week shows fewer picks, not filler. The picks are rules
          applied to public feeds, not a judgement of importance.
        </p>
      </header>
      <section>
        <h2 className="section-title">What stood out this week?</h2>
        <p className="subtitle" style={{ marginBottom: 14 }}>
          Rebuilt every time the data is refreshed.
        </p>
        <div className="grid">
          <ChartCard id="hot_pick.this_week" />
        </div>
      </section>
      <section>
        <h2 className="section-title">What stood out in earlier weeks?</h2>
        <p className="subtitle" style={{ marginBottom: 14 }}>
          Each week is saved with the Hot Pick command, and a saved week never changes.
        </p>
        {weeks === null ? <div className="skeleton" style={{ height: 120 }} /> : null}
        {weeks && weeks.length === 0 ? <p>No week has been saved yet.</p> : null}
        <div className="grid">
          {(weeks ?? []).map((id) => (
            <ChartCard key={id} id={id} />
          ))}
        </div>
      </section>
    </>
  );
}
