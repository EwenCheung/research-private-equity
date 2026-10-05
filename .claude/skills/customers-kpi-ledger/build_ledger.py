"""Rebuild data/ledgers/customers_kpi_claims.csv. Every quote is fetched from Anthropic's own page and must appear in it word for word.

Run from the repo root:  uv run python .claude/skills/customers-kpi-ledger/build_ledger.py
The build fails (nothing is written) if a quote or a date is not on its page. Add a claim by appending to CLAIMS.
"""

import csv
import html
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[3] / "data" / "ledgers" / "customers_kpi_claims.csv"
NEWS = "https://www.anthropic.com/news/"
FIELDS = ["as_of", "entity", "metric", "value", "dims", "source_url", "entered_by", "retrieved_at", "evidence"]
ENTERED_BY = "EwenCheung via Claude"

# (slug, "Mon D, YYYY" as printed on the page, as_of, value, what, threshold, quote)
CLAIMS = [
    ("anthropic-raises-series-f-at-usd183b-post-money-valuation", "Sep 2, 2025", "2025-09-02", 300000,
     "business customers", "over 300,000",
     "Anthropic now serves over 300,000 business customers, and our number of large accounts—customers that each represent over $100,000 in run-rate revenue—has grown nearly 7x in the past year."),
    ("anthropic-invests-50-billion-in-american-ai-infrastructure", "Nov 12, 2025", "2025-11-12", 300000,
     "business customers", "more than 300,000",
     "Anthropic serves more than 300,000 business customers, and our number of large accounts—customers that each represent over $100,000 in run-rate revenue—has grown nearly sevenfold in the past year."),
    ("anthropic-raises-30-billion-series-g-funding-380-billion-post-money-valuation", "Feb 12, 2026", "2026-02-12", 500,
     "customers spending over $1M a year (annualized)", "exceeds 500",
     "Two years ago, a dozen customers spent over $1 million with us on an annualized basis. Today that number exceeds 500."),
    ("anthropic-raises-30-billion-series-g-funding-380-billion-post-money-valuation", "Feb 12, 2026", "2026-02-12", 7,
     "growth multiple (x), past year, in customers spending over $100K a year (run-rate)", "7x",
     "The number of customers spending over $100,000 annually on Claude (as represented by run-rate revenue) has grown 7x in the past year."),
    ("anthropic-raises-30-billion-series-g-funding-380-billion-post-money-valuation", "Feb 12, 2026", "2026-02-12", 8,
     "Fortune 10 companies that are Claude customers", "eight",
     "Eight of the Fortune 10 are now Claude customers."),
    ("google-broadcom-partnership-compute", "Apr 6, 2026", "2026-04-06", 1000,
     "business customers spending over $1M a year (annualized)", "exceeds 1,000",
     "When we announced our Series G fundraising in February, we shared that over 500 business customers were each spending over $1 million on an annualized basis. Today that number exceeds 1,000, doubling in less than two months."),
    ("anthropic-amazon-compute", "Apr 20, 2026", "2026-04-20", 100000,
     "customers running Claude on Amazon Bedrock (Amazon's platform, not all Anthropic customers)", "over 100,000",
     "We have worked closely with Amazon since 2023 and over 100,000 customers now run Claude on Amazon Bedrock."),
]  # fmt: skip


def page_text(slug: str) -> str:
    r = httpx.get(
        NEWS + slug,
        headers={"User-Agent": "signal-monitor/0.1 (private-company research)"},
        follow_redirects=True,
        timeout=60,
    )
    r.raise_for_status()
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", r.text, flags=re.DOTALL)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def main() -> int:
    old = {}
    if OUT.exists():
        with OUT.open(newline="", encoding="utf-8") as f:
            old = {(r["as_of"], r["evidence"], r["value"]): r["retrieved_at"] for r in csv.DictReader(f)}
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    texts, rows, failed = {}, [], []
    for slug, printed, as_of, value, what, threshold, quote in CLAIMS:
        text = texts.setdefault(slug, page_text(slug))
        if quote not in text:
            failed.append(f"{slug}: quote not found word for word: {quote[:70]}...")
        if printed not in text:
            failed.append(f"{slug}: date {printed!r} not on the page")
        rows.append({
            "as_of": as_of, "entity": "anthropic", "metric": "customer_kpi_claim", "value": value,
            "dims": json.dumps({"what": what, "threshold": threshold}), "source_url": NEWS + slug,
            "entered_by": ENTERED_BY, "retrieved_at": old.get((as_of, quote, str(value)), now), "evidence": quote,
        })  # fmt: skip
    if failed:
        print("\n".join(failed), file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["as_of"], r["value"])))
    print(f"wrote {len(rows)} rows to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
