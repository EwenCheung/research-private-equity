---
name: add-source
description: Use when the user wants to add a new data source or signal to the Signal Monitor ("add a collector for X", "can we track Y", "new source", "/add-source"). Guides a new collector from checking its terms to a tested chart.
---

# Add a source

A source is one module in `pipeline/sources/` that declares itself with `@source(...)` and yields observation rows. It is picked up
automatically: there is no registry file to edit. Copy the nearest existing page: `pipeline/sources/product.py` (status feed + cited ledgers),
`attention.py` (several APIs with backfill and rate limits), `hiring.py` (parsers and an importer).

## 1. Decide before writing code
- **Is it allowed?** Read the provider's terms. If they forbid scraping, stop and tell the user (that is why OpenRouter is not collected).
- **What does it cost?** A paid or keyed source needs the user's approval; make the key optional with `env_key(...)` so a missing key is `SKIP`.
- **Can the past be recovered?** If the provider keeps no history, set `backfillable=False` and start collecting now: every uncollected day is lost.
- **Which page owns it?** One of Hiring, Developer Adoption, Attention, Product, Customers, Capital. Only edit that page's own files (see CLAUDE.md).

## 2. Check the endpoint by hand first
Call it with `curl` and read real output. Confirm the fields, the date format, the rate limit, and how a missing company looks. Many bugs in
this project came from assuming: a feed that returns 100 items, a redirect that counts separately, a name spelled two ways.

## 3. Write the collector
- Declare: `@source(id=..., page=..., label=..., url=..., method="api"|"scrape", tier=..., cadence=..., sla_days=..., backfillable=..., caveats=...)`.
  The caveats appear on the Data & Methods page: say what the number cannot tell you.
- Use `pipeline.core.http` (`get`, `post`, `github_get`, `sec_get`, `env_key`, `SourceUnavailable`): it retries and waits out rate limits.
- Yield rows with `source_url`, `as_of` (the date the value describes), `entity`, `metric`, a numeric `value`, and flat `dims`. The core adds the rest.
  URL-encode anything in `source_url`.
- Per-company IDs go in `config/identifiers/<page>.yaml`, never in code.
- Incremental: `latest_as_of(company.root, "<source id>", company.slug, **dims)` gives the newest stored date; refetch a short overlap.
- A value you cannot get is **no row**, never a zero or an estimate.

## 4. Define, chart, test
- `config/metrics/<page>.yaml`: label, unit, definition, aggregation, `higher_is` for every new metric.
- A chart in `pipeline/marts/<page>.py`: use `ctx.names` for company names, compute the takeaway from the rows, list every assumption, and
  drop the running month. Look at the data before trusting any classifier or join: print every distinct input and where it lands.
- Tests in `tests/test_<page>.py`: mock the HTTP, test the parser on real saved samples, test the chart on synthetic rows, and test the failure you most
  expect (rate limit, missing key, renamed page).
- Run it for real: `uv run python -m pipeline.collect --source <id>`, `uv run python -m pipeline.build`, then serve and read the chart against the source.

## 5. Ship
Branch `p<N>/<name>` from the phase branch, small Conventional Commits (data in its own `chore(data):` commits, never `data/registry.json`), and a PR into
the phase branch that says what you checked against the real data and what that found. The Data & Methods page lists the new source by itself.
