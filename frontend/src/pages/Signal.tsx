import ChartCard from "../components/ChartCard";

export const meta = { title: "Signal", path: "/signal", order: 80 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Is Anthropic ahead of OpenAI?",
    note: "Developer demand, measured the same way for both. Anthropic can be shrinking and still ahead of a market that shrank more.",
    charts: ["signal.vs_openai"],
  },
  {
    title: "Do new models move developer demand?",
    note: "Each release drawn over SDK downloads, then averaged. If a model drives installs about a month later, the average would show it.",
    charts: ["signal.releases", "signal.around_release"],
  },
  {
    title: "Does one public signal move before another?",
    note: "Every pair of Anthropic's own signals, and of its growth relative to OpenAI's, tested for a lead of one to eight weeks.",
    charts: ["signal.lead_lag", "signal.tested"],
  },
  {
    title: "What moved when the valuation did?",
    note: "Four announced valuations: enough to put side by side, not enough to say what drives them.",
    charts: ["signal.valuation", "signal.valuation_steps"],
  },
];

export default function Signal() {
  return (
    <>
      <header className="page-head">
        <h1>Signal</h1>
        <p style={{ marginBottom: 10 }}>
          <strong>What you are reading.</strong> Whether Anthropic is gaining on OpenAI, and whether anything we collect moves before
          something else, such as a model release before downloads, or one public signal before another. Every chart draws both series
          whether or not a pattern was found, so you can judge the picture yourself.
        </p>
        <p>
          <strong>How to read it.</strong> A label says what held up: <em>Finding</em> survived the test and held in both halves of the
          history, <em>Hypothesis</em> survived the test only, <em>Not supported</em> did not. "Not supported" is a result, not a gap.
          A lead shows what moved first, never why.
        </p>
      </header>
      {SECTIONS.map((s) => (
        <section key={s.title}>
          <h2 className="section-title">{s.title}</h2>
          <p className="subtitle" style={{ marginBottom: 14 }}>
            {s.note}
          </p>
          <div className="grid">
            {s.charts.map((id) => (
              <ChartCard key={id} id={id} />
            ))}
          </div>
        </section>
      ))}
    </>
  );
}
