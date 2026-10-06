"""Briefing: Anthropic's own direction in each signal family, computed with the same helpers as the page charts.

Nothing here is a model. Each signal is a series the pages already chart; the Briefing compares its latest window with the
window before it and the same window a year earlier, then groups the signals into families. Hand-entered data is never read.
"""

import calendar

import pandas as pd
import yaml

from pipeline.core import ROOT, mart
from pipeline.core.frames import latest, num, pct
from pipeline.marts import attention, capital, customers, dev_adoption, product

SOURCES = [
    "pypi_downloads",
    "npm_downloads",
    "github_coauthored_commits",
    "wikipedia_pageviews",
    "hn_who_is_hiring",
    "sec_filings_naming",
    "ats_open_roles",
    "wayback_job_boards",
    "status_incidents",
    "sec_nport_marks",
]
ME = "anthropic"
# periods in the window, and in a year, for each series frequency
WINDOW = {"M": (3, 12), "Q": (1, 4), "W": (13, 52)}
UNIT = {"M": "3 months", "Q": "quarter", "W": "13 weeks"}


def config() -> dict:
    return yaml.safe_load((ROOT / "config/briefing.yaml").read_text())["families"]


def tripwire_rules() -> list[dict]:
    return yaml.safe_load((ROOT / "config/tripwires.yaml").read_text())["tripwires"]


# ---- one adapter per signal: a numeric series indexed by pandas periods, plus how to read it ----


def series(freq: str, agg: str, data: dict, fmt: str = "num") -> dict | None:
    if not data:
        return None
    s = pd.Series(data).sort_index()
    return {"freq": freq, "agg": agg, "s": s, "fmt": fmt}


def monthly(df: pd.DataFrame, col: str = "value", month: str = "month") -> dict:
    return {pd.Period(m, "M"): float(v) for m, v in zip(df[month], df[col])}


def download_series(ctx, metric: str, role: str) -> dict | None:
    df = dev_adoption.monthly(ctx, metric, role)
    return series("M", "sum", monthly(df[df["entity"] == ME])) if len(df) else None


def wiki_series(ctx, kind: str) -> dict | None:
    df, _ = attention.wiki_monthly(ctx, kind)
    df = df[df["entity"] == ME] if len(df) else df
    return series("M", "sum", monthly(df, month="as_of")) if len(df) else None


def commit_series(ctx) -> dict | None:
    df = latest(ctx.obs(metric="coauthored_commits", entity=ME), keys=("entity", "as_of"))
    return (
        series("W", "mean", {pd.Period(a, "W"): float(v) for a, v in zip(df["as_of"], df["value"])})
        if len(df)
        else None
    )


def hn_series(ctx) -> dict | None:
    _, n = customers.hn_frames(ctx)
    if n.empty:
        return None
    n = n[(n["entity"] == ME) & (n["term"] == customers.ANY)]
    return series(
        "M", "mean", {pd.Period(m, "M"): a / p for m, a, p in zip(n["month"], n["naming"], n["posts"])}, "pct"
    )


def sec_series(ctx) -> dict | None:
    df, done = customers.sec_filings(ctx)
    if df.empty:
        return None
    counts = df[df["entity"] == ME].groupby("quarter")["cik"].nunique()
    quarters = pd.period_range(pd.Period(df["quarter"].min(), "Q"), pd.Period(done, "Q"), freq="Q")
    return series("Q", "sum", {q: float(counts.get(q.start_time.date().isoformat(), 0)) for q in quarters})


def roles_series(ctx) -> dict | None:
    df = latest(ctx.obs(metric="open_roles", entity=ME), keys=("entity", "as_of"))
    if df.empty:
        return None
    last = pd.Timestamp(df["as_of"].max())
    if last.day != calendar.monthrange(last.year, last.month)[1]:  # the newest month is still running
        df = df[pd.to_datetime(df["as_of"]).dt.to_period("M") < last.to_period("M")]
    monthly_mean = df.groupby(pd.to_datetime(df["as_of"]).dt.to_period("M"))["value"].mean()
    return series("M", "mean", monthly_mean.to_dict())


def incident_series(ctx) -> dict | None:
    df = product.incidents(ctx, ME)
    if df.empty:
        return None
    df = product.complete_months(df[df["impact"].isin(product.IMPACTS)], product.last_collected(ctx))
    counts = df.groupby("month").size()
    months = pd.period_range(pd.Period(counts.index.min(), "M"), pd.Period(counts.index.max(), "M"), freq="M")
    return series("M", "sum", {m: float(counts.get(m.start_time.date().isoformat(), 0)) for m in months})


def mark_change(ctx) -> dict | None:
    """Median per-share mark change for funds that reported at the latest date and at least 60 days earlier, same fund and share series."""
    df = capital.marks(ctx)
    if df.empty:
        return None
    df = df.assign(day=pd.to_datetime(df["as_of"]), key=df["fund_key"] + "|" + df["series"])
    newest = df["day"].max()
    now = df[df["day"] == newest].set_index("key")["mark"]
    earlier = df[df["day"] <= newest - pd.Timedelta(days=60)].sort_values("day").groupby("key").last()
    both = now.index.intersection(earlier.index)
    if len(both) < 5:
        return None
    ratios = (now[both] / earlier.loc[both, "mark"]) - 1
    return {"direct": float(ratios.median()), "n": len(both), "as_of": newest.date().isoformat()}


def signals(ctx) -> dict:
    return {
        "python_sdk": download_series(ctx, "pypi_downloads", "sdk"),
        "js_sdk": download_series(ctx, "npm_downloads", "sdk"),
        "claude_code_downloads": download_series(ctx, "npm_downloads", "cli"),
        "claude_code_commits": commit_series(ctx),
        "hn_job_posts": hn_series(ctx),
        "sec_filers": sec_series(ctx),
        "wiki_assistant": wiki_series(ctx, "product"),
        "wiki_company": wiki_series(ctx, "company"),
        "open_roles": roles_series(ctx),
        "incidents": incident_series(ctx),
        "fund_marks": mark_change(ctx),
    }


# ---- reading a signal ----


def window(s: pd.Series, w: int, end: int, agg: str) -> float | None:
    """The aggregate of w periods ending `end` periods before the last one; None when history or data is too thin."""
    part = s.iloc[len(s) - w - end : len(s) - end] if len(s) >= w + end else None
    if part is None or part.notna().sum() < max(1, w // 2 + w % 2):
        return None
    return float(part.sum() if agg == "sum" else part.mean())


def rel(new: float | None, old: float | None) -> float | None:
    return None if new is None or not old else (new - old) / abs(old)


def span(freq: str, s: pd.Series, w: int) -> str:
    first, last = s.index[-w], s.index[-1]
    if freq == "Q":
        return f"Q{last.quarter} {last.year}"
    if freq == "W":
        return f"{w} weeks to {last.end_time.date().isoformat()}"
    month = lambda p: calendar.month_abbr[p.month]
    return f"{month(first)} to {month(last)} {last.year}" if first != last else f"{month(last)} {last.year}"


def read(sid: str, cfg: dict, sig: dict | None) -> dict | None:
    """One signal's reading, or None when there is no data for it."""
    if sig is None:
        return None
    band, better = cfg.get("band", 0.10), cfg.get("better", "up")
    if "direct" in sig:  # fund marks arrive already as a change
        chg, text, based, yoy, prev, recent = (
            sig["direct"],
            f"{pct(sig['direct'], 1)} across {sig['n']} funds",
            f"marks to {sig['as_of']}",
            None,
            None,
            None,
        )
    else:
        s, agg, w, year = sig["s"], sig["agg"], *WINDOW[sig["freq"]]
        recent, prev_win, prev2, ago = (window(s, w, e, agg) for e in (0, w, 2 * w, year))
        chg, yoy, prev = rel(recent, prev_win), rel(recent, ago), rel(prev_win, prev2)
        if recent is None or chg is None:
            return None
        shown = pct(recent, 1) if sig["fmt"] == "pct" else num(round(recent) if abs(recent) >= 100 else recent)
        text = shown + (" a week" if sig["freq"] == "W" else "")
        based = span(sig["freq"], s, w)
    direction = "up" if chg > band else "down" if chg < -band else "flat"
    tone = "flat" if direction == "flat" else "good" if (direction == "up") == (better == "up") else "bad"
    return {
        "id": sid,
        "label": cfg["label"],
        "recent": recent,
        "text": text,
        "based": based,
        "chg": chg,
        "yoy": yoy,
        "prev": prev,
        "direction": direction,
        "tone": tone,
        "series": sig.get("s"),
        "agg": sig.get("agg"),
        "freq": sig.get("freq"),
    }


def read_all(ctx) -> tuple[dict, dict]:
    cfg, sigs = config(), signals(ctx)
    out = {}
    for fid, fam in cfg.items():
        out[fid] = [r for sid, sc in fam["signals"].items() if (r := read(sid, sc, sigs.get(sid)))]
    return cfg, out


# ---- families ----

WORDS = {
    "volume": {"up": "Rising", "down": "Falling", "flat": "Flat", "mixed": "Mixed"},
    "quality": {"up": "Improving", "down": "Worsening", "flat": "Steady", "mixed": "Mixed"},
}
ARROW = {"Rising": "▲", "Improving": "▲", "Falling": "▼", "Worsening": "▼", "Flat": "→", "Steady": "→", "Mixed": "◆"}


def family_verdict(members: list[dict], words: str) -> str:
    """Rising/Falling when up minus down is more than half the signals, Flat when all are flat, otherwise Mixed."""
    good = sum(m["tone"] == "good" for m in members)
    bad = sum(m["tone"] == "bad" for m in members)
    if all(m["tone"] == "flat" for m in members):
        return WORDS[words]["flat"]
    score = (good - bad) / len(members)
    if score > 0.5:
        return WORDS[words]["up"]
    if score < -0.5:
        return WORDS[words]["down"]
    return WORDS[words]["mixed"]


def moved(members: list[dict]) -> str:
    was = lambda m: f" (was {m['prev'] * 100:+.0f}%)" if m["prev"] is not None else ""
    return "; ".join(f"{m['label']} {m['chg'] * 100:+.0f}%{was(m)}" for m in members)


def divergences(cfg: dict, verdicts: dict) -> list[str]:
    """One sentence per falling family: it is falling while the rising ones are rising."""
    up = [cfg[f]["short"] for f, v in verdicts.items() if v == "Rising"]
    down = [cfg[f]["short"] for f, v in verdicts.items() if v == "Falling"]
    join = lambda xs: ", ".join(xs[:-1]) + f" and {xs[-1]}" if len(xs) > 1 else xs[0]
    return (
        [
            f"{join(down).capitalize()} {'is' if len(down) == 1 else 'are'} falling while {join(up)} {'is' if len(up) == 1 else 'are'} rising."
        ]
        if up and down
        else []
    )


# ---- tripwires ----


def trailing_declines(values: list[float]) -> int:
    k = 0
    while k + 1 < len(values) and values[-1 - k] < values[-2 - k]:
        k += 1
    return k


def check(rule: dict, r: dict | None) -> tuple[str, str]:
    """(status, what the signal reads now) for one tripwire rule."""
    if r is None:
        return "No data", "n/a"
    kind, unit = rule["type"], {"M": "month", "Q": "quarter", "W": "week"}.get(r["freq"] or "M", "month")
    if kind == "consecutive_declines":
        if r["series"] is None:
            return "No data", "n/a"
        k = trailing_declines(r["series"].tolist())
        return ("Fired" if k >= rule["n"] else "Clear"), (
            f"down {k} {unit}{'s' if k != 1 else ''} in a row" if k else f"not down last {unit}"
        )
    if kind in ("change_below", "change_above"):
        hit = r["chg"] <= rule["value"] if kind == "change_below" else r["chg"] >= rule["value"]
        return ("Fired" if hit else "Clear"), f"{r['chg'] * 100:+.0f}% ({r['based']})"
    if kind == "below_peak":
        if r["series"] is None:
            return "No data", "n/a"
        recent = r["series"].iloc[-rule["periods"] :]
        gap = 1 - recent.iloc[-1] / recent.max()
        return (
            "Fired" if gap >= rule["value"] else "Clear"
        ), f"{gap * 100:.0f}% below the {rule['periods']}-month peak"
    raise ValueError(f"unknown tripwire rule {kind!r}")


def threshold_text(rule: dict) -> str:
    kind = rule["type"]
    if kind == "consecutive_declines":
        return f"{rule['n']} declines in a row"
    if kind == "change_below":
        return f"change at or below {rule['value'] * 100:+.0f}%"
    if kind == "change_above":
        return f"change at or above {rule['value'] * 100:+.0f}%"
    return f"{rule['value'] * 100:.0f}% or more below the {rule['periods']}-month peak"


# ---- the three charts ----

READING = (
    "A change smaller than a signal's band (10% unless its row says otherwise in config/briefing.yaml) counts as flat."
)
BASIS = (
    "The latest 3 complete months against the 3 before them and the same 3 months a year earlier. Quarterly series (SEC filings) use the latest complete "
    "quarter, the weekly commit series 13 weeks, and fund marks the latest report date against one at least 60 days earlier."
)
LEVELS = "It judges each signal against its own history, never its level: levels trend up together, so they would agree with almost anything. It makes no claim that one signal causes another."
INPUTS = "Every number is computed from the same sources and rules as the chart on its page. Hand-entered data is never used, and peers are not part of any verdict."


def table(
    title: str, subtitle: str, columns: list[dict], rows: list[dict], takeaway: list[str], assumptions: list[str]
) -> dict:
    return {
        "title": title,
        "subtitle": subtitle,
        "kind": "table",
        "encoding": {},
        "columns": columns,
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": assumptions,
        "badges": ["arithmetic"],
    }


@mart(id="briefing.verdicts", sources=SOURCES)
def verdicts(ctx):
    cfg, reads = read_all(ctx)
    rows, state, takeaway = [], {}, []
    for fid, fam in cfg.items():
        members = reads[fid]
        if not members:
            continue
        words = fam.get("words", "volume")
        verdict = family_verdict(members, words)
        state[fid] = verdict
        good = sum(m["tone"] == "good" for m in members)
        bad = sum(m["tone"] == "bad" for m in members)
        rows.append(
            {
                "family": fam["label"],
                "verdict": f"{ARROW[verdict]} {verdict}",
                "moved": moved(members),
                "agree": f"{good} up, {bad} down, {len(members) - good - bad} flat of {len(members)}"
                if words == "volume"
                else f"{good} better, {bad} worse, {len(members) - good - bad} flat of {len(members)}",
                "where": fam["page"],
            }
        )
    if rows:
        takeaway = [", ".join(f"{cfg[f]['short']} {v.lower()}" for f, v in state.items()).capitalize() + "."]
        takeaway += divergences(cfg, state)
    return table(
        "Where Anthropic stands",
        "One verdict per family of signals, from the latest 3 months against the 3 before",
        [
            {"field": "family", "label": "Family", "format": "text"},
            {"field": "verdict", "label": "Verdict", "format": "text"},
            {"field": "moved", "label": "What moved (the period before in brackets)", "format": "text"},
            {"field": "agree", "label": "How the signals split", "format": "text"},
            {"field": "where", "label": "Where to look", "format": "text"},
        ],
        rows,
        takeaway,
        [
            BASIS,
            READING,
            "A family is Rising or Improving when up minus down is more than half its signals, Falling or Worsening for the reverse, Flat when every signal is flat, and Mixed otherwise.",
            "The bracketed figure is the change in the period before, so you can see whether a rise is cooling or a fall is deepening.",
            "Funds mark Anthropic shares in steps, usually when a round is priced, so a large change in the marks follows a new round rather than a trend.",
            LEVELS,
            INPUTS,
        ],
    )


@mart(id="briefing.signals", sources=SOURCES)
def signal_rows(ctx):
    cfg, reads = read_all(ctx)
    rows = []
    for fid, fam in cfg.items():
        for m in reads[fid]:
            word = {"up": "Up", "down": "Down", "flat": "Flat"}[m["direction"]]
            rows.append(
                {
                    "family": fam["label"],
                    "signal": m["label"],
                    "latest": m["text"],
                    "based": m["based"],
                    "chg": m["chg"],
                    "prev": m["prev"],
                    "yoy": m["yoy"],
                    "reading": word,
                }
            )
    return table(
        "Underneath the verdicts",
        "Each signal's latest window, and how it moved",
        [
            {"field": "family", "label": "Family", "format": "text"},
            {"field": "signal", "label": "Signal", "format": "text"},
            {"field": "latest", "label": "Latest window", "format": "text"},
            {"field": "based", "label": "Window", "format": "text"},
            {"field": "chg", "label": "On previous period", "format": "pct"},
            {"field": "prev", "label": "Period before: its change", "format": "pct"},
            {"field": "yoy", "label": "On a year earlier", "format": "pct"},
            {"field": "reading", "label": "Reading", "format": "text"},
        ],
        rows,
        [],
        [BASIS, READING, "Blank means the history is too short for that comparison, never zero.", LEVELS, INPUTS],
    )


@mart(id="briefing.tripwires", sources=SOURCES)
def tripwires(ctx):
    _, reads = read_all(ctx)
    by_id = {m["id"]: m for members in reads.values() for m in members}
    rows = []
    for t in tripwire_rules():
        status, now = check(t["rule"], by_id.get(t["signal"]))
        rows.append({"rule": t["label"], "status": status, "now": now, "threshold": threshold_text(t["rule"])})
    order = {"Fired": 0, "No data": 1, "Clear": 2}
    rows.sort(key=lambda r: order[r["status"]])
    fired = [r["rule"] for r in rows if r["status"] == "Fired"]
    takeaway = (
        (
            [f"{len(fired)} of {len(rows)} tripwires fired: " + "; ".join(fired).lower() + "."]
            if fired
            else [f"None of the {len(rows)} tripwires has fired."]
        )
        if rows
        else []
    )
    return table(
        "What would change the view",
        "Plain rules checked every time the Briefing is built",
        [
            {"field": "rule", "label": "Rule", "format": "text"},
            {"field": "status", "label": "Status", "format": "text"},
            {"field": "now", "label": "Now", "format": "text"},
            {"field": "threshold", "label": "Fires when", "format": "text"},
        ],
        rows,
        takeaway,
        [
            "The rules are in config/tripwires.yaml in plain terms: edit them there. Each names a signal from config/briefing.yaml.",
            "A tripwire is a prompt to look, not a forecast: a rule that fires says the signal crossed a line you chose.",
            BASIS,
            INPUTS,
        ],
    )
