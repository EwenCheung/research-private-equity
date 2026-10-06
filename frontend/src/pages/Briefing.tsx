import ChartCard from "../components/ChartCard";

export const meta = { title: "Briefing", path: "/briefing", order: 0 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Where does Anthropic stand?",
    note: "One verdict per family of signals, from the latest three months against the three before.",
    charts: ["briefing.verdicts"],
  },
  {
    title: "What would change the view?",
    note: "Plain rules checked every time the Briefing is built. A rule that fires is a prompt to look, not a forecast.",
    charts: ["briefing.tripwires"],
  },
  {
    title: "What is underneath the verdicts?",
    note: "Each signal's latest window, with how much it moved and how much it moved the period before.",
    charts: ["briefing.signals"],
  },
];

export default function Briefing() {
  return (
    <>
      <header className="page-head">
        <h1>Briefing</h1>
        <p style={{ marginBottom: 10 }}>
          <strong>What you are reading.</strong> A one-page read of Anthropic. For each area of public evidence (developer usage,
          enterprise adoption, consumer attention, build-out, reliability, fund marks) it says whether the signals are rising,
          falling, flat or mixed. Every verdict is arithmetic on numbers charted on the other pages: no model, no hand-entered data.
        </p>
        <p>
          <strong>How to read it.</strong> Read the verdict, then the numbers behind it. Each shows its change on the previous period
          and, in brackets, the change the period before, so a rise that is cooling shows. Verdicts judge each signal against its own
          history, not against other companies, and say what moved, not why.
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
