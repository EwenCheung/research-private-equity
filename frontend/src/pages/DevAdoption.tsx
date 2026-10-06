import ChartCard from "../components/ChartCard";

export const meta = { title: "Developer Adoption", path: "/developer-adoption", order: 30 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Are developers choosing Claude over rivals?",
    note: "Downloads of each company's official API SDK: the closest public proxy for API adoption.",
    charts: ["dev_adoption.python_sdk_share", "dev_adoption.js_sdk_monthly"],
  },
  {
    title: "How much is Claude Code being used?",
    note: "Downloads of Claude Code against OpenAI's Codex and Google's Gemini CLI, and the public commits Claude Code wrote.",
    charts: ["dev_adoption.coding_agent_cli", "dev_adoption.coauthored_commits"],
  },
];

export default function DevAdoption() {
  return (
    <>
      <header className="page-head">
        <h1>Developer Adoption</h1>
        <p style={{ marginBottom: 10 }}>
          <strong>What you are reading.</strong> Whether developers are choosing Claude. It counts downloads of each company's official software kit (SDK) and coding tool, and public code commits written with Claude Code. The sources are the public package registries developers install from (npm and PyPI) and GitHub.
        </p>
        <p>
          <strong>How to read it.</strong> Look at share and direction, not the absolute number. Downloads include automated installs from build servers and mirrors, so they show relative pull, not users.
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
