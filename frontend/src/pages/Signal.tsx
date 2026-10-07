import ChartCard from "../components/ChartCard";

export const meta = { title: "Signal", path: "/signal", order: 80 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Is Anthropic ahead of OpenAI?",
    note: "Developer demand, measured the same way for both. OpenAI stands in for the market: what both share is market growth, and the gap is Anthropic's own.",
    charts: ["signal.vs_openai"],
  },
  {
    title: "Do new models move developer demand?",
    note: "Each company's releases drawn over both companies' SDK growth, then averaged. If both rise after a release it is the market; if only the releasing company rises it is its own.",
    charts: ["signal.releases", "signal.around_release", "signal.around_openai_release"],
  },
  {
    title: "Does one public signal move before another?",
    note: "A lead means one signal moves first and another follows some weeks later, for example Wikipedia views jumping before SDK downloads do. If one did, it would work as an early warning. We tested every pair of Anthropic's own signals, and of its growth beyond OpenAI's.",
    charts: ["signal.lead_lag", "signal.lead_lag_edge", "signal.tested"],
  },
  {
    title: "What moved when the valuation did?",
    note: "Four announced valuations for Anthropic, with OpenAI's usage alongside: enough to put side by side, not enough to say what drives them.",
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
          something else, such as a model release before downloads. Every chart sets Anthropic beside OpenAI so a rise reads as either
          Anthropic's own or the whole market's, and draws both series whether or not a pattern was found.
        </p>
        <p>
          <strong>How to read it.</strong> A label says what held up: <em>Holds up</em> passed the test and held in both halves of the
          history, <em>Possible</em> passed the test only, <em>Could be luck</em> did not. "Could be luck" is a result, not a gap.
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
