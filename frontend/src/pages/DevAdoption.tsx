import ChartCard from "../components/ChartCard";

export const meta = { title: "Developer Adoption", path: "/developer-adoption", order: 30 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Who developers build on",
    note: "Official API SDK downloads: the closest public proxy for API adoption.",
    charts: ["dev_adoption.python_sdk_share", "dev_adoption.python_sdk_monthly", "dev_adoption.js_sdk_monthly"],
  },
  {
    title: "Coding agents",
    note: "Claude Code against OpenAI's Codex and Google's Gemini CLI, plus the commits it leaves in public code.",
    charts: ["dev_adoption.coding_agent_cli", "dev_adoption.vscode_installs", "dev_adoption.coauthored_commits"],
  },
  {
    title: "Ecosystem",
    note: "The Model Context Protocol Anthropic open-sourced, and each company's open-source pull.",
    charts: ["dev_adoption.mcp_sdk_downloads", "dev_adoption.mcp_server_repos", "dev_adoption.github_stars"],
  },
];

export default function DevAdoption() {
  return (
    <>
      <header className="page-head">
        <h1>Developer Adoption</h1>
        <p>
          How much developers pull Anthropic's SDKs, coding agent and protocol, against OpenAI, Google DeepMind, xAI, Mistral
          and Cohere. Downloads include CI and mirrors, so read them as relative pull, not users.
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
