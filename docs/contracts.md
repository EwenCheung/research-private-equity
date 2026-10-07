# Contracts

These are the interfaces every implementation builds against. The machine-checked versions live in `contracts/*.schema.json`.
`contracts.validate(kind, obj)` enforces each schema, plus the chart rules JSON Schema can't express.
`tests/fixtures/` holds valid examples of everything.

Changing a contract is a core change, made in its own `fix(core)` PR, never inside a page branch.

## 1. Observation: one stored data point
Every number in the system is one observation row. The schema is `contracts/observation.schema.json`.

| Field | Type | Meaning |
|---|---|---|
| `source` | `snake_case` | Registry id, e.g. `greenhouse_jobs` |
| `source_url` | URI | Exact URL fetched, or `urn:` when there's no URL (e.g. `urn:analyst-note:2026-10-05`) |
| `method` | enum | `api` · `scrape` · `manual` · `ledger` |
| `as_of` | `YYYY-MM-DD` | The date the value **describes** |
| `retrieved_at` | RFC 3339 UTC | When we fetched it, or when it was entered |
| `tier` | enum | `company-stated` · `filing` · `platform` · `press` · `vendor` · `derived` |
| `entity` | slug | Company slug, e.g. `anthropic`, `openai` |
| `metric` | `snake_case` | Defined in `config/metrics/<page>.yaml` |
| `value` | number | Always numeric. Text facts go in `dims`, with `value: 1` so they can be counted |
| `dims` | flat object | e.g. `{"function": "research", "location": "London"}` |
| `entered_by`, `evidence` | string | **Required** when `method` is `manual` or `ledger` |

Tiers, from most to least direct:
- **company-stated:** the company's own channel (its job board, newsroom, status page, pricing page).
- **filing:** SEC, DOL or other government records.
- **platform:** third-party telemetry (npm, PyPI, GitHub, App Store, Wikimedia, Trends).
- **press:** media reports.
- **vendor:** licensed alt-data (YipitData, M Science).
- **derived:** computed by us.

**Raw storage is immutable.**
- Each collector run writes `data/raw/<source>/<YYYYMMDDTHHMMSSZ>.jsonl.gz`: gzip-compressed, one observation per line.
- Never edit or delete a raw file. Corrections are new rows.
- Large corpora keep only the rows we use. For example, the hiring-for-Claude corpus keeps matching postings plus per-company counts.
  This keeps repo growth to tens of MB a year.

**Manual and ledger input is a CSV.**
- Location: `data/manual/<source>.csv` (licensed or vendor data) or `data/ledgers/<page>_<name>.csv` (hand-curated public facts).
- Header: `as_of,entity,metric,value,dims,source_url,entered_by,retrieved_at,evidence`
  - `dims` is a JSON object string, or empty for `{}`.
  - `retrieved_at` is the time the row was entered.
- The core turns each line into an observation, filling in `source`, `method` and `tier` from the source declaration.

## 2. Source: metadata declared next to the collector
The schema is `contracts/source.schema.json`. Each source declares:

| Field | Meaning |
|---|---|
| `id`, `page`, `label` | Registry id, owning page, and the name shown on charts |
| `url` | Endpoint or page. May hold `{placeholders}` that are filled from `config/identifiers/<page>.yaml` |
| `method`, `tier` | Defaults stamped onto every row |
| `cadence` | `daily` · `weekly` · `monthly` · `quarterly`: how often it's collected (or expected, for manual sources) |
| `sla_days` | The freshness SLA on `retrieved_at` |
| `backfillable` | `false` = snapshot-only. History is lost for any day we don't collect |
| `caveats` | What a reader must know, shown on the Data & Methods page |

## 3. Freshness rule
`contracts.freshness(retrieved_at, sla_days, now)` is the single definition. The age is `now − latest retrieved_at` for the source:

| State | Condition | Badge |
|---|---|---|
| `fresh` | age ≤ `sla_days` | green |
| `aging` | age ≤ 2 × `sla_days` | amber |
| `stale` | age > 2 × `sla_days` | red |
| `never` | nothing retrieved yet | grey, "awaiting data" |

Freshness measures **our collection**, not the data date. A quarterly SEC filing can be fresh (we checked yesterday) while its `as_of` is three months old.
The chart shows both dates.

**Freshness is live.** `build` stores freshness in each mart, but the API recomputes it on every request from `data/registry.json`,
using the current time. If the pipeline stops, badges turn amber and then red on their own, with no rebuild needed.
(With fixtures the clock is pinned to the fixture build time, so every freshness state stays visible.)

**`data/registry.json`** is written by every `build`. It is `{generated_at, sources: [...]}`, where each source is its declared metadata
(section 2) plus `retrieved_at` (latest), `as_of` (latest), `row_count`, and `readable`. `readable` is false when stored rows failed validation.

## 4. Chart spec (mart): what build writes and ChartCard renders
The schema is `contracts/chart_spec.schema.json`. Files are written to `data/marts/<page>.<chart>.json`.

| Field | Who fills it | Meaning |
|---|---|---|
| `id`, `page` | core | `<page>.<chart>`, e.g. `hiring.open_roles` |
| `title`, `subtitle` | mart | |
| `kind` | mart | `line` · `area` · `bar` · `stacked_bar` · `scatter` · `table` · `stat` · `timeline` · `combo` |
| `layers` | mart | Only for `combo`, and required there: the marks drawn over one shared x axis (below) |
| `panels` | mart | Only for `combo`, optional: extra panels stacked under the main one on the same x axis, each `{label, format}` with its own y axis (below) |
| `encoding` | mart | `x`, `y`, `color`, `facet` → `{field, type, label, format}`. Every field must be a column |
| `columns` | mart | Order and format of the table view and CSV. Formats: `int` `float` `pct` `usd` `usd_compact` `multiple` `date` `text` `url` |
| `rows` | mart | Objects. Each row must have every column field |
| `takeaway` | mart | At most 2 sentences, **computed from the rows** and never hand-written |
| `assumptions` | mart | Shown under "Assumptions & detail" |
| `badges` | mart | `arithmetic` (we computed it) · `extrapolation` (it projects past observed data) |
| `as_of` | core | Latest data date in the rows; `null` when awaiting data |
| `sources` | core | One provenance line per source; see below |
| `status` | core | `ok`, or `awaiting_data` exactly when `rows` is empty |
| `generated_at` | core | Build time |

Each entry in `sources` holds:
- `source`, `label`, `url`, `method`, `tier`, `cadence`
- `as_of`: the latest data date this source contributed
- `retrieved_at`: the latest retrieval time
- `freshness`
- `manual`: `{entered_by, entered_at, evidence}`. It is required exactly for manual and ledger sources that have rows, and is `null` otherwise.

**Combined charts (`kind: combo`).** Put two aspects on one frame, such as events over a series or bars with a line. `encoding.x` is the shared axis and `encoding.y` names the axis label and number format. Each layer is `{mark, name, ...}`, and every field it names must be a column:

| `mark` | Fields | Draws |
|---|---|---|
| `bar` | `y` | a bar per row on a category axis (the rows' order); on a date or number axis a thick stick, since dates are unevenly spaced |
| `line` | `y` | a line through the rows |
| `point` | `y` | a dot per row |
| `band` | `y_low`, `y_high` | a neutral shaded area, for a noise ceiling or confidence range |
| `rule` | `label` | a vertical line at each row's x, captioned by `label` (a release, a funding round) |

- A layer draws the rows whose value field (`y`, `y_low` or `label`) is not null, so one tidy table can feed several layers.
- `panel` (a layer's number, 0 by default) puts a layer in the main panel or in `panels[panel - 1]`. `encoding.y` describes panel 0. The panels share the x axis, so a `rule` in panel 0 also runs through every other panel, and a measure on a different scale (a score under a growth line) is read at the same moment as the event. A panel whose layers are all unticked is left out.
- A panel may name its own x axis (`panels[].x`, a channel like `encoding.x`), for example one column per model in release order under a timeline. It then does not line up with the main axis, and a `rule` is not drawn in it. Panels on the same x field share one domain, so their columns line up whichever layers are ticked.
- `series` (any mark but `band`) splits a layer into one colour per value of that field. Without it, `name` is the legend entry. A series or layer named for a company takes that company's fixed colour. A `band`, and a `rule` without `series`, are neutral and not in the legend.
- All layers share one y scale and unit, so two aspects with different units are rebased or expressed as the same unit by the mart. There is no second axis.
- The table view and CSV list the columns as for any chart; the plot never hides a layer that fails a test, so the mart draws what it tested and says so in its subtitle and takeaway.

From this, ChartCard renders the provenance line:
- **Automated:** `Greenhouse job board API ↗ · data as of 2026-10-05 · retrieved 2026-10-05 06:02 UTC · ● fresh`
- **Manual:** `MANUAL · updated 2026-09-24 by EwenCheung · Fixture: weekly report p.3 · ● aging`

## 5. AI report
The schema is `contracts/ai_report.schema.json`. There are two shapes:
- **`case_report`** (bull, bear) holds a headline, 4–8 `arguments`, `valuation` and `catalysts`.
  Each argument has a `claim`, its `evidence`, a `strength` and what it is `invalidated_by`.
- **`neutral_report`** holds `debates` (topic, bull, bear, verdict, evidence quality, evidence), `net_read`, `confidence`, `watch_next` and `data_gaps`.
- **Every report carries** `edition` (`YYYY-Www`), `reporter`, `model`, `generated_at`, `evidence_as_of` and `headline`.
- **Each evidence item** is `{chart_id, metric, value, as_of, source}`.
  The Phase 4 gates check that the `chart_id` exists and that the `value` appears in that chart's rows.

## 6. Module interfaces (auto-discovered; no shared registry to edit)

### Collector: `pipeline/sources/<page>.py`
```python
from pipeline.core import source

@source(id="greenhouse_jobs", page="hiring", label="Greenhouse job board API",
        url="https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
        method="api", tier="company-stated", cadence="daily", sla_days=2,
        backfillable=False, caveats="Live postings only.")
def greenhouse_jobs(company):          # called once per company in scope
    board = company.ids("hiring").get("greenhouse")
    if not board:
        return                         # the source doesn't apply to this company
    yield {"source_url": ..., "as_of": "2026-10-05", "entity": company.slug,
           "metric": "open_roles", "value": 640, "dims": {}}
```
- The core stamps `source`, `method`, `tier` and `retrieved_at`, then validates every row before writing.
- A collector that raises is reported, and the other sources still run.
- **Shared HTTP** (`pipeline.core.http`): `get`/`post` retry 429/5xx with backoff and send our user agent.
  - `github_get` uses `GITHUB_TOKEN`, or else your local `gh auth` login.
  - `sec_get` sends `SEC_USER_AGENT` and keeps to SEC's 10 requests a second.
  - `env_key(name, why)` returns an API key.
- **Missing keys skip, they don't fail.** Raise `SourceUnavailable` (e.g. from `env_key`) when a source can't run here.
  `collect` reports it once as `SKIP`, not as an error, so a missing optional key never fails the daily job.
- **Incremental collection.** `pipeline.core.store.latest_as_of(company.root, source_id, entity)` gives the newest stored date.
  A backfillable source fetches everything the first time, then only what is new; re-fetching a short overlap is fine.

### Mart: `pipeline/marts/<page>.py`
```python
from pipeline.core import mart

@mart(id="hiring.open_roles", sources=["greenhouse_jobs", "ashby_jobs"])
def open_roles(ctx):                   # ctx.obs(...) -> pandas DataFrame of observations
    df = ctx.obs(metric="open_roles")
    return {"title": ..., "kind": "line", "encoding": {...}, "columns": [...],
            "rows": [...], "takeaway": [...], "assumptions": [...], "badges": []}
```
- The core adds `id`, `page`, `as_of`, `sources` (with freshness), `status` and `generated_at`, then validates and writes the file.
- **Shared helpers** (`pipeline.core.frames`):
  - `latest()`: one row per key, the newest retrieval. Use it on every snapshot source.
  - `dims()`: dims as columns.
  - `roll()`: totals or averages per week, month or quarter.
  - `drop_partial()`: drops the still-running period.
  - `num`, `usd`, `pct`, `change`: number formats for takeaways.
- NaN in rows is written as `null`. The file is always valid JSON.
- `ctx.names` maps each slug to its display name from `config/companies/`. Label series with it ("Anthropic", not "anthropic"),
  so every chart names companies the same way.

### Page: `frontend/src/pages/<Page>.tsx`
```tsx
import ChartCard from "../components/ChartCard";
export const meta = { title: "Hiring & Talent", path: "/hiring", order: 20 };
export default function Hiring() {
  return <ChartCard id="hiring.open_roles" />;
}
```
The router loads `import.meta.glob("./pages/*.tsx")`, and the nav is sorted by `meta.order`. Order slots:

| Order | Page |
|---|---|
| 0 | Briefing |
| 5 | Hot Pick |
| 10 | AI Analysis |
| 20 | Hiring |
| 30 | Developer Adoption |
| 40 | Consumer & Attention |
| 50 | Product |
| 70 | Capital |
| 80 | Signal |
| 100 | Data & Methods |

## 7. Config files (one file per owner, so nothing is shared)
| File | Owner | Content |
|---|---|---|
| `config/companies/<slug>.yaml` | 1.1 | `slug`, `name`, `role` (`target` \| `peer`), `peers` (target only) |
| `config/identifiers/<page>.yaml` | that page | How to find each company in this page's sources, e.g. `anthropic: {greenhouse: anthropic}`, `openai: {ashby: openai}` |
| `config/metrics/<page>.yaml` | that page | `<metric_id>: {label, unit, definition, aggregation: last\|sum\|mean, higher_is: good\|bad\|neutral}`. Metric ids are unique across all files, and the core fails on duplicates |

## 8. Commands, API, environment and ports
**CLI** (run from the repo root):
```bash
uv run python -m pipeline.collect [--cadence daily|weekly] [--source ID ...] [--company SLUG]
uv run python -m pipeline.build                 # raw + manual + ledgers -> data/marts/*.json + data/registry.json
uv run python -m pipeline.hot_pick freeze       # Phase 3: data/marts/hot_pick.month_YYYY_MM.json
uv run python -m pipeline.analysis              # Phase 4: bull, bear, neutral
uv run pytest
```

**API** (FastAPI). Everything under `/api/*`, except the login and session routes, needs the session cookie; otherwise it returns `401`.

| Route | Returns |
|---|---|
| `POST /api/login` `{password}` | Sets a signed cookie (`DASHBOARD_PASSWORD`, `SESSION_SECRET`). `429` after 10 wrong passwords from one IP in 15 minutes |
| `POST /api/logout` | Clears the cookie |
| `GET /api/session` | `{authenticated: bool}` |
| `GET /api/marts` | `[{id, page, title, status, as_of}]` |
| `GET /api/marts/{id}` | One chart spec, with each source's `freshness` recomputed live |
| `GET /api/companies` | `[{slug, name, role}]`: targets first, then their peers in config order. Charts give each company a fixed colour |
| `GET /api/registry` | `data/registry.json` sources, with `freshness` recomputed live |
| `GET /api/freshness` | `{source: {freshness, retrieved_at, as_of}}`, live |
| `GET /api/marts` | Also lists the Phase 3 weekly picks, `hot_pick.month_2026_10`, as ordinary chart specs |
| `GET /api/analysis?week=` | Phase 4: `{bull, bear, neutral}` |
| `GET /api/refresh` | Only with `DATA_DB` (else `404`): `{running, started_at, data: {packed_at, observations, charts}, last}`. `last` is the previous refresh's report: `{state, swapped, message, started_at, finished_at, ...}` with `state` one of `ok`, `partial`, `unchanged`, `offline`, `failed` |
| `POST /api/refresh` | Only with `DATA_DB`: starts a refresh in the background (`202`; `409` when one is running). It fetches every source again and replaces the file only when the new one is sound, so the dashboard never shows half a refresh |

**Environment:** see `.env.example`. `DATA_DIR` points the API at the pipeline's `data/` (Render sets `data`). If it's unset, the API serves `tests/fixtures/`. `DATA_DB` points it at the offline SQLite file written by `python -m pipeline.offline` instead: the charts and the registry are read from the file, so a machine with no internet needs no `data/` folder and no build. The data is then a snapshot until someone presses **Refresh data** in the sidebar.

**Ports:** worktree `n` uses API `8000+n` and Vite `5173+n`.
- `main` is `n = 0`.
- Implementation `P.k` uses `n = k`.
- The variables are `API_PORT` and `WEB_PORT`.

## 9. Fixtures
`tests/fixtures/` contains everything below. All of it is labelled as fixture data, so it can't be mistaken for real figures.

| File | What it covers |
|---|---|
| `marts/*.json` | Seven sample marts covering every UI state: fresh, aging, stale and never; `ok` and `awaiting_data`; API, manual and ledger sources; the `arithmetic` badge; and the line, stacked-bar, bar, table and stat kinds |
| `observations.jsonl` | API, manual and ledger rows |
| `sources.json` | Seven source declarations |
| `registry.json` | The registry `build` would write for those sources; the fixture API reads it with a pinned clock |
| `ai/bull.json`, `ai/neutral.json` | Reports whose citations match the sample marts |
