import ChartCard from "../components/ChartCard";

export const meta = { title: "Licensed Alt-Data", path: "/licensed-data", order: 90 };

const VENDORS: { name: string; id: string }[] = [
  { name: "YipitData", id: "yipit_consumer" },
  { name: "M Science", id: "mscience_panel" },
];

export default function LicensedData() {
  return (
    <>
      <header className="page-head">
        <h1>Licensed Alt-Data</h1>
        <p>
          Panel data the team has paid for. There is no feed: figures are read from each vendor's report and entered by hand,
          so every number shows who entered it, when, and where in the report it came from. The charts stay empty until
          figures are entered, and the freshness badge turns amber if updates stop.
        </p>
      </header>
      {VENDORS.map((v) => (
        <section key={v.id}>
          <h2 className="section-title">{v.name}</h2>
          <p className="subtitle" style={{ marginBottom: 14 }}>
            To add figures, ask Claude Code to run <code>/add-manual-data</code> and paste the report.
          </p>
          <div className="grid">
            {["spend", "users", "growth"].map((m) => (
              <ChartCard key={m} id={`licensed_data.${v.id}_${m}`} />
            ))}
          </div>
        </section>
      ))}
    </>
  );
}
