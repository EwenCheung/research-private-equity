"""Hot Pick: the few hottest news items, discussions, filings and insights about Anthropic in the last seven days.

Nothing is ranked on one scale: each kind has its own places and its own score (outlets, points, filers, size of the move).
Insights reuse the Briefing's arithmetic. Headlines are shown with their link, never reproduced.
"""

import re
from datetime import date, timedelta

import pandas as pd
import yaml

from pipeline.core import ROOT, mart
from pipeline.core.frames import dims, latest
from pipeline.marts import briefing

ME = "anthropic"
SOURCES = list(dict.fromkeys(["news_headlines", "hn_stories", "sec_filings_naming", *briefing.SOURCES]))
LABEL = {"news": "News", "discussion": "Discussion", "filings": "Filings", "insights": "Insight"}
PLURAL = {"news": "news", "discussion": "discussion", "filings": "filings", "insights": "insights"}
# words every headline here shares or that carry no story, so they must not make two headlines look alike
STOP = {
    *[
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "over",
        "says",
        "say",
        "new",
        "than",
        "that",
        "the",
        "their",
        "to",
        "up",
        "was",
    ],
    *["were", "will", "with", "after", "about", "amid", "anthropic", "ai"],
}


def config() -> dict:
    return yaml.safe_load((ROOT / "config/hot_pick.yaml").read_text())


def window_end(ctx) -> date | None:
    """The day of the newest collection of the daily feeds (any source if they are empty); the window is the 7 days to it."""
    feeds = ctx.df[ctx.df["metric"].isin(["news_headline", "hn_story_points"])]
    pool = feeds if len(feeds) else ctx.df
    return date.fromisoformat(str(pool["retrieved_at"].max())[:10]) if len(pool) else None


def in_window(day: pd.Series, start: date, end: date) -> pd.Series:
    return (day >= start.isoformat()) & (day <= end.isoformat())


# ---- news: headlines that several outlets ran ----


def stem(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:  # testifies -> testify
        return word[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokens(title: str) -> frozenset[str]:
    return frozenset(stem(w) for w in re.findall(r"[a-z0-9']+", title.lower()) if len(w) > 2 and w not in STOP)


def similar(a: frozenset[str], b: frozenset[str], min_shared: int) -> float:
    """Jaccard overlap of two headlines' content words, zero unless they share at least `min_shared` of them."""
    shared = len(a & b)
    return shared / len(a | b) if shared >= min_shared else 0.0


def clusters(items: list[dict], threshold: float, min_shared: int) -> list[list[dict]]:
    """Group headlines about the same story: an item joins the first group holding a headline at least `threshold` alike."""
    groups: list[list[dict]] = []
    for item in sorted(items, key=lambda i: i["published"]):
        item = {**item, "tokens": tokens(item["title"])}
        for group in groups:
            if any(similar(item["tokens"], m["tokens"], min_shared) >= threshold for m in group):
                group.append(item)
                break
        else:
            groups.append([item])
    return groups


def news_picks(df: pd.DataFrame, cfg: dict, days: int) -> list[dict]:
    """The stories the most outlets ran, newest first among ties, skipping a story that mostly repeats one already chosen."""
    if df.empty:
        return []
    items = df[["title", "outlet", "published", "source_url"]].to_dict("records")
    ranked = []
    for group in clusters(items, cfg["similarity"], cfg["min_shared"]):
        outlets = list(dict.fromkeys(i["outlet"] for i in group))
        if len(outlets) >= cfg["min_outlets"]:
            ranked.append((len(outlets), max(i["published"] for i in group), group[0], outlets))
    chosen = []
    for n, when, rep, outlets in sorted(ranked, key=lambda r: (r[0], r[1]), reverse=True):
        if all(len(rep["tokens"] & c[2]["tokens"]) < cfg["repeat_shared"] for c in chosen):
            chosen.append((n, when, rep, outlets))
    return [
        {
            "kind": "news",
            "headline": rep["title"],
            "score": f"{n} outlets",
            "why": f"Ran by {n} outlets in {days} days, among them {', '.join(outlets[:3])}",
            "date": rep["published"][:10],
            "link": rep["source_url"],
        }
        for n, _, rep, outlets in chosen[: cfg["places"]]
    ]


# ---- discussion: the Hacker News stories with the most points ----


def discussion_picks(df: pd.DataFrame, cfg: dict) -> list[dict]:
    top = df[df["value"] >= cfg["min_points"]].sort_values("value", ascending=False).head(cfg["places"])
    return [
        {
            "kind": "discussion",
            "headline": r.title,
            "score": f"{int(r.value):,} points",
            "why": f"{int(r.value):,} points and {int(r.comments):,} comments on Hacker News",
            "date": r.created[:10],
            "link": r.source_url,
        }
        for r in top.itertuples()
    ]


# ---- filings: listed companies that newly name Anthropic ----


def filing_picks(df: pd.DataFrame, cfg: dict) -> list[dict]:
    filers = df.sort_values("as_of", ascending=False).drop_duplicates("cik")
    if len(filers) < cfg["min_filers"]:
        return []
    shown = ", ".join(f"{r.filer.title()} ({r.form})" for r in filers.head(3).itertuples())
    more = f" and {len(filers) - 3} more" if len(filers) > 3 else ""
    newest = filers.iloc[0]
    return [
        {
            "kind": "filings",
            "headline": f"{len(filers)} listed companies named Anthropic in new filings",
            "score": f"{len(filers)} filers",
            "why": f"{shown}{more}. A mention is not a purchase.",
            "date": newest["as_of"],
            "link": newest["source_url"],
        }
    ]


# ---- insights: tripwires and the biggest moves in the Briefing ----


def insight_picks(cfg_b: dict, reads: dict, rules: list[dict], cfg: dict, end: date) -> list[dict]:
    by_id = {m["id"]: m for members in reads.values() for m in members}
    page = {m["id"]: cfg_b[f]["page"] for f, members in reads.items() for m in members}
    band = {sid: sc.get("band", 0.10) for fam in cfg_b.values() for sid, sc in fam["signals"].items()}
    fired, picks = set(), []
    for t in rules:
        status, now = briefing.check(t["rule"], by_id.get(t["signal"]))
        if status == "Fired":
            fired.add(t["signal"])
            picks.append(
                (
                    (0, 0.0),
                    {
                        "kind": "insights",
                        "headline": t["label"],
                        "score": "Tripwire fired",
                        "why": f"{now}; it fires when {briefing.threshold_text(t['rule'])}. See the {page[t['signal']]} page.",
                    },
                )
            )
    for sid, m in by_id.items():
        size = abs(m["chg"]) / band[sid]
        if sid in fired or sid in cfg["exclude"] or size < cfg["min_bands"]:
            continue
        unit = briefing.UNIT.get(m["freq"], "report")
        before = f"; the period before it was {m['prev'] * 100:+.0f}%" if m["prev"] is not None else ""
        picks.append(
            (
                (1, -size),
                {
                    "kind": "insights",
                    "headline": f"{m['label']}: {m['chg'] * 100:+.0f}% on the previous {unit}",
                    "score": f"{size:.1f}× its flat band",
                    "why": f"{m['text']} in {m['based']}{before}. See the {page[sid]} page.",
                },
            )
        )
    picks.sort(key=lambda p: p[0])
    return [{**p, "date": end.isoformat(), "link": None} for _, p in picks[: cfg["places"]]]


# ---- the chart ----


@mart(id="hot_pick.this_week", sources=SOURCES)
def this_week(ctx):
    cfg, end = config(), window_end(ctx)
    rows, takeaway = [], []
    days = cfg["window_days"]
    if end is not None:
        start = end - timedelta(days=days - 1)
        news = dims(ctx.obs(metric="news_headline", entity=ME), "title", "outlet", "published")
        news = latest(news, keys=("entity", "title", "outlet")) if len(news) else news
        news = news[in_window(news["published"].str[:10], start, end)] if len(news) else news
        hn = dims(ctx.obs(metric="hn_story_points", entity=ME), "id", "title", "comments", "url", "created")
        hn = latest(hn, keys=("entity", "id")) if len(hn) else hn
        hn = hn[in_window(hn["created"].str[:10], start, end)] if len(hn) else hn
        sec = dims(ctx.obs(metric="sec_filing_mention", entity=ME), "adsh", "cik", "filer", "form", "term")
        sec = latest(sec, keys=("entity", "adsh", "term")).drop_duplicates(["entity", "adsh"]) if len(sec) else sec
        sec = sec[in_window(sec["as_of"], start, end)] if len(sec) else sec
        cfg_b, reads = briefing.read_all(ctx)
        by_kind = {
            "news": news_picks(news, cfg["news"], days),
            "discussion": discussion_picks(hn, cfg["discussion"]) if len(hn) else [],
            "filings": filing_picks(sec, cfg["filings"]) if len(sec) else [],
            "insights": insight_picks(cfg_b, reads, briefing.tripwire_rules(), cfg["insights"], end),
        }
        for kind in cfg["order"]:
            rows += by_kind[kind]
        rows = [{"rank": i + 1, **r} for i, r in enumerate(rows)]
        if rows:
            counts = ", ".join(f"{n} {PLURAL[k]}" for k in cfg["order"] if (n := sum(r["kind"] == k for r in rows)))
            takeaway = [f"{len(rows)} picks in the {days} days to {end.isoformat()}: {counts}."]
    for r in rows:
        r["kind"] = LABEL[r["kind"]]
    return {
        "title": "This week's Hot Pick",
        "subtitle": f"The hottest news, discussion, filings and insights about Anthropic in the {days} days to {end.isoformat() if end else 'the latest collection'}",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "rank", "label": "#", "format": "int"},
            {"field": "kind", "label": "Kind", "format": "text"},
            {"field": "headline", "label": "Headline", "format": "text"},
            {"field": "score", "label": "Score", "format": "text"},
            {"field": "why", "label": "Why it is hot", "format": "text"},
            {"field": "date", "label": "Date", "format": "date"},
            {"field": "link", "label": "Link", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            f"News: headlines from Google News's recent results for Anthropic, grouped when they share at least {cfg['news']['min_shared']} content words and are at least {cfg['news']['similarity']:.0%} alike once common words are removed, and a story that mostly repeats one already shown is skipped. A story needs {cfg['news']['min_outlets']} or more outlets to qualify; its score is the number of outlets, and up to {cfg['news']['places']} stories are shown. Grouping by headline can merge or split stories.",
            f"Discussion: Hacker News stories matching Anthropic with at least {cfg['discussion']['min_points']} points, ranked by points (upvotes, not importance); {cfg['discussion']['places']} place.",
            f"Filings: listed companies whose new 10-K or 10-Q names Anthropic, shown when {cfg['filings']['min_filers']} or more did. A mention is not a purchase.",
            f"Insights: tripwires that have fired, then the signals whose latest change is widest against their flat band (at least {cfg['insights']['min_bands']} bands), from the Briefing's arithmetic; {cfg['insights']['places']} places. Fund marks are left out because they move in steps when a round is priced. Insights use the latest three months, so they change slowly.",
            "Each kind is scored in its own unit and kinds are never ranked against each other. A kind that does not qualify leaves its places empty, so a quiet week shows fewer picks, not filler.",
            "Headlines are shown with their link, not reproduced. The picks are rules applied to public feeds, not a judgement of importance, and they are not investment advice.",
        ],
        "badges": ["arithmetic"],
    }
