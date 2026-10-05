# Signal Monitor

An alt-data monitor for private companies, built for the deal team. First company: **Anthropic**, compared against its AI peers
(OpenAI, Google DeepMind, xAI, Mistral, Cohere).

- **Provenance on every number.** Automated numbers show their source link, the date the data describes, and when it was retrieved.
  Manual numbers show who entered them, when, and from what evidence.
- **Comparable over time and across companies.** Shared metric definitions, plus frozen weekly editions.
- **Easy to refresh.** Daily and weekly GitHub Actions, or ask Claude for `/refresh-data`.
- **AI Analysis.** Bull, Bear and Neutral reporters that cite the charts they rely on.

Outputs are drafts for deal-team review, not investment advice. Only public sources and team-supplied data are used.

Status: see [docs/ROADMAP.md](docs/ROADMAP.md). Design: [docs/superpowers/specs](docs/superpowers/specs/2026-10-05-signal-monitor-design.md).

## Setup for colleagues
1. Clone the repo, then open it in Claude Code:
   ```bash
   gh repo clone EwenCheung/research-private-equity
   ```
2. **Trust the folder** when Claude asks. Then accept the prompt to install the team marketplaces and plugins,
   which are listed in `.claude/settings.json`:

   | Marketplace | Plugins |
   |---|---|
   | `anthropics/financial-services` | financial-analysis, investment-banking, private-equity, pitch-agent, market-researcher, equity-research |
   | `anthropics/knowledge-work-plugins` | finance, claude-for-financial-advisors, data |
   | `anthropics/claude-plugins-official` | playwright |

3. Copy `.env.example` to `.env` and fill in only the keys for the phases you work on.
4. The Python and frontend setup commands are added here as Phase 1 lands.

## Working on an implementation
Each roadmap item gets its own branch and worktree, so several can run in parallel without conflicts:
```bash
git worktree add .worktrees/p2-hiring -b p2/hiring
```
Read `CLAUDE.md` for the ownership rule, ports, review checkpoints and commit conventions.
