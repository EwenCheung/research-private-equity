"""Hot Pick: the few hottest items about Anthropic over the last week and the last month.

Nothing is ranked on one scale: each kind has its own places and its own score (outlets, points, reactions, stars, filers, size of
the move). Insights reuse the Briefing's arithmetic. Headlines are shown with their link, never reproduced.
"""

import re
from datetime import date, timedelta

import pandas as pd
import yaml

from pipeline.core import ROOT, mart
from pipeline.core.frames import dims, latest
from pipeline.marts import briefing

ME = "anthropic"
SOURCES = list(
    dict.fromkeys(
        [
            "news_headlines",
            "ai_feeds",
            "hn_stories",
            "community_posts",
            "github_repos",
            "sec_filings_naming",
            *briefing.SOURCES,
        ]
    )
)
LABEL = {
    "news": "News",
    "discussion": "Discussion",
    "blog": "Blog",
    "community": "Community",
    "trend": "Trend",
    "filings": "Filings",
    "insights": "Insight",
}
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
    """The day of the newest collection of the news and discussion sources (any source if they are empty)."""
    feeds = ctx.df[ctx.df["metric"].isin(["news_headline", "feed_item", "hn_story_points"])]
    pool = feeds if len(feeds) else ctx.df
    return date.fromisoformat(str(pool["retrieved_at"].max())[:10]) if len(pool) else None


def in_window(day: pd.Series, start: date, end: date) -> pd.Series:
    return (day >= start.isoformat()) & (day <= end.isoformat())


def clip(text: str | None, n: int = 110) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


# ---- news: stories that several outlets ran ----


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


def news_picks(df: pd.DataFrame, cfg: dict, places: int, days: int) -> list[dict]:
    """The stories the most outlets ran, newest first among ties, skipping a story that mostly repeats one already chosen."""
    if df.empty:
        return []
    ranked = []
    for group in clusters(
        df[["title", "outlet", "published", "source_url"]].to_dict("records"), cfg["similarity"], cfg["min_shared"]
    ):
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
            "why": f"{n} outlets in {days} days, among them {', '.join(outlets[:3])}",
            "date": rep["published"][:10],
            "link": rep["source_url"],
        }
        for n, _, rep, outlets in chosen[:places]
    ]


# ---- discussion, blog, community, trend ----


def discussion_picks(df: pd.DataFrame, cfg: dict, places: int) -> list[dict]:
    top = df[df["value"] >= cfg["min_points"]].sort_values("value", ascending=False).head(places)
    return [
        {
            "kind": "discussion",
            "headline": r.title,
            "why": f"{int(r.value):,} points, {int(r.comments):,} comments on Hacker News",
            "date": r.created[:10],
            "link": r.source_url,
        }
        for r in top.itertuples()
    ]


def blog_picks(df: pd.DataFrame, places: int) -> list[dict]:
    """The newest posts from the curated blogs and newsletters whose title names Anthropic or Claude."""
    df = df[df["in_title"].astype(bool)].sort_values("published", ascending=False).drop_duplicates("title").head(places)
    return [
        {
            "kind": "blog",
            "headline": r.title,
            "why": f"{r.outlet}, a curated blog or newsletter, names Anthropic or Claude in the title",
            "date": r.published[:10],
            "link": r.source_url,
        }
        for r in df.itertuples()
    ]


def community_picks(df: pd.DataFrame, cfg: dict, places: int) -> list[dict]:
    top = df[df["value"] >= cfg["min_reactions"]].sort_values("value", ascending=False).head(places)
    return [
        {
            "kind": "community",
            "headline": r.title,
            "why": f"{int(r.value):,} reactions, {int(r.comments):,} comments on dev.to, by {r.author}",
            "date": r.published[:10],
            "link": r.source_url,
        }
        for r in top.itertuples()
    ]


def trend_picks(df: pd.DataFrame, cfg: dict, places: int) -> list[dict]:
    top = df[df["value"] >= cfg["min_stars"]].sort_values("value", ascending=False).head(places)
    return [
        {
            "kind": "trend",
            "headline": r.name,
            "why": f"{int(r.value):,} stars since it was created on {r.created[:10]}; {r.language or 'no main language'}. {clip(r.description)}",
            "date": r.created[:10],
            "link": r.source_url,
        }
        for r in top.itertuples()
    ]


# ---- filings: listed companies that newly name Anthropic ----


def filing_picks(df: pd.DataFrame, cfg: dict, places: int) -> list[dict]:
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
            "why": f"{len(filers)} filers: {shown}{more}. A mention is not a purchase.",
            "date": newest["as_of"],
            "link": newest["source_url"],
        }
    ][:places]


# ---- insights: tripwires and the biggest moves in the Briefing ----


def insight_picks(cfg_b: dict, reads: dict, rules: list[dict], cfg: dict, places: int, end: date) -> list[dict]:
    by_id = {m["id"]: m for members in reads.values() for m in members}
    page = {m["id"]: cfg_b[f]["page"] for f, members in reads.items() for m in members}
    band = {sid: sc.get("band", 0.10) for fam in cfg_b.values() for sid, sc in fam["signals"].items()}
    fired, picks = set(), []
    for t in rules:
        status, now = briefing.check(t["rule"], by_id.get(t["signal"]))
        if status == "Fired":
            fired.add(t["signal"])
            why = f"Tripwire fired: {now}; it fires when {briefing.threshold_text(t['rule'])}. See the {page[t['signal']]} page."
            picks.append(((0, 0.0), {"kind": "insights", "headline": t["label"], "why": why}))
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
                    "why": f"{size:.1f}× its flat band: {m['text']} in {m['based']}{before}. See the {page[sid]} page.",
                },
            )
        )
    picks.sort(key=lambda p: p[0])
    return [{**p, "date": end.isoformat(), "link": None} for _, p in picks[:places]]


# ---- the charts ----


def frames(ctx, start: date, end: date) -> dict:
    """The rows of each source inside the window, newest collection per item."""
    google = dims(ctx.obs(metric="news_headline", entity=ME), "title", "outlet", "published")
    feed = dims(ctx.obs(metric="feed_item", entity=ME), "title", "outlet", "kind", "published", "in_title")
    parts = [google[["title", "outlet", "published", "source_url", "retrieved_at"]]] if len(google) else []
    if len(feed):
        parts.append(feed[feed["kind"] == "news"][["title", "outlet", "published", "source_url", "retrieved_at"]])
    news = (
        pd.concat(parts)
        if parts
        else pd.DataFrame(columns=["title", "outlet", "published", "source_url", "retrieved_at"])
    )
    news = news.sort_values("retrieved_at").drop_duplicates(["title", "outlet"], keep="last") if len(news) else news
    blog = feed[feed["kind"] == "blog"] if len(feed) else feed
    hn = dims(ctx.obs(metric="hn_story_points", entity=ME), "id", "title", "comments", "url", "created")
    posts = dims(ctx.obs(metric="post_reactions", entity=ME), "id", "title", "comments", "author", "published")
    repos = dims(ctx.obs(metric="repo_stars", entity=ME), "name", "description", "language", "created")
    sec = dims(ctx.obs(metric="sec_filing_mention", entity=ME), "adsh", "cik", "filer", "form", "term")

    sec = latest(sec, keys=("entity", "adsh", "term")).drop_duplicates(["entity", "adsh"]) if len(sec) else sec
    sec = sec[in_window(sec["as_of"], start, end)] if len(sec) else sec

    def pick(df, keys, day):
        df = latest(df, keys=keys) if len(df) else df
        return df[in_window(df[day].str[:10], start, end)] if len(df) else df

    return {
        "news": news[in_window(news["published"].str[:10], start, end)] if len(news) else news,
        "blog": pick(blog, ("entity", "title", "outlet"), "published"),
        "hn": pick(hn, ("entity", "id"), "created"),
        "posts": pick(posts, ("entity", "id"), "published"),
        "repos": pick(repos, ("entity", "name"), "created"),
        "sec": sec,
    }


def picks_chart(ctx, window: str) -> dict:
    cfg = config()
    spec, end = cfg["windows"][window], window_end(ctx)
    days, places = spec["days"], spec["places"]
    rows, takeaway = [], []
    if end is not None:
        data = frames(ctx, end - timedelta(days=days - 1), end)
        cfg_b, reads = briefing.read_all(ctx)
        by_kind = {
            "news": news_picks(data["news"], cfg["news"], places["news"], days),
            "discussion": discussion_picks(data["hn"], cfg["discussion"], places["discussion"])
            if len(data["hn"])
            else [],
            "blog": blog_picks(data["blog"], places["blog"]) if len(data["blog"]) else [],
            "community": community_picks(data["posts"], cfg["community"], places["community"])
            if len(data["posts"])
            else [],
            "trend": trend_picks(data["repos"], cfg["trend"], places["trend"]) if len(data["repos"]) else [],
            "filings": filing_picks(data["sec"], cfg["filings"], places["filings"]) if len(data["sec"]) else [],
            "insights": insight_picks(
                cfg_b, reads, briefing.tripwire_rules(), cfg["insights"], places["insights"], end
            ),
        }
        for kind in cfg["order"]:
            rows += by_kind[kind]
        rows = [{"rank": i + 1, **r} for i, r in enumerate(rows)]
        if rows:
            counts = ", ".join(f"{n} {k}" for k in cfg["order"] if (n := sum(r["kind"] == k for r in rows)))
            takeaway = [f"{len(rows)} picks in the {days} days to {end.isoformat()}: {counts}."]
    for r in rows:
        r["kind"] = LABEL[r["kind"]]
    n, d, c, t, f, i = (cfg[k] for k in ("news", "discussion", "community", "trend", "filings", "insights"))
    return {
        "title": spec["title"] + "'s Hot Pick",
        "subtitle": f"The hottest news, blogs, discussion, technology trends and insights about Anthropic in the {days} days to {end.isoformat() if end else 'the latest collection'}",
        "kind": "table",
        "encoding": {},
        "columns": [
            {"field": "rank", "label": "#", "format": "int"},
            {"field": "kind", "label": "Kind", "format": "text"},
            {"field": "headline", "label": "Headline", "format": "text"},
            {"field": "why", "label": "Why it is hot (its score first)", "format": "text"},
            {"field": "date", "label": "Date", "format": "date"},
            {"field": "link", "label": "Link", "format": "url"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            f"News: Google News's recent results for Anthropic plus the AI and technology feeds of publishers (config/identifiers/hot_pick.yaml), kept to items that name Anthropic, or Claude beside an AI word. Headlines are grouped when they share at least {n['min_shared']} content words and are at least {n['similarity']:.0%} alike, and a story that mostly repeats one already shown is skipped. A story needs {n['min_outlets']} or more outlets; its score is the number of outlets (up to {places['news']} shown). Grouping by headline can merge or split stories.",
            f"Discussion: Hacker News stories whose title names Anthropic or Claude, with at least {d['min_points']} points, ranked by points (upvotes, not importance); up to {places['discussion']}.",
            f"Blog: the newest posts from curated blogs and newsletters whose title names Anthropic or Claude; they have no popularity number, so recency decides; up to {places['blog']}.",
            f"Community: the most popular dev.to posts tagged Claude or Anthropic with at least {c['min_reactions']} reactions, ranked by reactions (likes, not readers); up to {places['community']}.",
            f"Trend: new GitHub repositories tagged claude-code, claude or anthropic with at least {t['min_stars']} stars, ranked by stars, which show attention and can be gamed; up to {places['trend']}.",
            f"Filings: listed companies whose new 10-K or 10-Q names Anthropic, shown when {f['min_filers']} or more did. A mention is not a purchase.",
            f"Insights: tripwires that have fired, then the signals whose latest change is widest against their flat band (at least {i['min_bands']} bands), from the Briefing's arithmetic, up to {places['insights']}. Fund marks are left out because they move in steps when a round is priced. Insights use the latest three months, so they are the same in the week and the month.",
            "Each kind is scored in its own unit (the first words of its reason) and kinds are never ranked against each other. A kind that does not qualify leaves its places empty, so a quiet period shows fewer picks, not filler. A feed that was down when collected leaves a gap.",
            "Headlines are shown with their link, not reproduced. The picks are rules applied to public feeds, not a judgement of importance, and they are not investment advice.",
        ],
        "badges": ["arithmetic"],
    }


@mart(id="hot_pick.this_week", sources=SOURCES)
def this_week(ctx):
    return picks_chart(ctx, "week")


@mart(id="hot_pick.this_month", sources=SOURCES)
def this_month(ctx):
    return picks_chart(ctx, "month")
