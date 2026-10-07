"""Signal page: is Anthropic ahead of OpenAI, and does one public signal lead another?

Every chart draws what it tested, whether or not a pattern was found: the verdict (Finding, Hypothesis, Not supported) is a label
beside the plot, never a gate in front of it. Nothing here claims one thing causes another.

Levels all trend up together, so any two correlate. The tests therefore use weekly growth with each series' own momentum removed
(an AR(2) fit) and ranked, so one extreme week cannot decide a result (Spearman correlation). The best of eight lags is compared with what
surrogate series give by chance (same autocorrelation, random timing), and corrected for how many pairs were tried (Benjamini-Hochberg).
A fixed seed makes every run identical.
"""

import re

import numpy as np
import pandas as pd

from pipeline.core import mart
from pipeline.core.frames import dims, latest
from pipeline.marts import dev_adoption

ME, PEER = "anthropic", "openai"
SEED, DRAWS = 7, 1500
LAGS = range(1, 9)  # weeks by which x leads y
PROFILE = range(-8, 9)  # the lag chart also shows y leading x and the same week
OFFSETS = range(-4, 13)  # weeks from a release
AR = 2
MIN_WEEKS, MIN_PAIRS, MIN_EVENTS = 60, 40, 6
RECENT = (
    8  # periods of 13 weeks shown against OpenAI: the launch years' triple-digit growth would flatten everything after
)
FINDING, HYPOTHESIS, UNSUPPORTED = "Holds up", "Possible", "Could be luck"
WHO = {ME: "Anthropic", PEER: "OpenAI"}
VERSION = re.compile(r"(?i)^(gpt-?\d+(?:\.\d+)?|o\d+|gpt-oss)")
LABEL = {
    "pypi": "PyPI downloads",
    "npm": "npm downloads",
    "cli": "Coding-agent CLI downloads",
    "wiki_company": "Wikipedia: company article",
    "wiki_product": "Wikipedia: product article",
    "commits": "Commits written with Claude Code",
}
OWN = ["pypi", "npm", "cli", "wiki_company", "wiki_product", "commits"]
BOTH = ["pypi", "npm", "cli", "wiki_company", "wiki_product"]  # the series OpenAI has too
USAGE = ["pypi_downloads", "npm_downloads"]
ATTENTION = ["wikipedia_pageviews"]
SPIKE_NOTE = (
    "Download spikes are removed as on the Developer Adoption page (a day above 10 times the median around it). "
    "A week left with 5 or 6 clean days is scaled to 7; with fewer it is dropped."
)
BASE_NOTES = [
    "Weeks run Monday to Sunday and only complete weeks are used.",
    "Correlation is not cause. A lead shows what moved first, not why.",
]


def week_end(d) -> pd.Timestamp:
    """The Sunday that ends the Monday-to-Sunday week containing d."""
    return pd.Timestamp(d).to_period("W-SUN").end_time.normalize()


def day_label(d) -> str:
    return pd.Timestamp(d).strftime("%-d %b %Y")


def month_label(d) -> str:
    return pd.Timestamp(d).strftime("%b %Y")


# ---- weekly series, built from the same rows and spike rule as the Developer Adoption and Consumer & Attention pages ----


def weekly(daily: pd.Series, min_days: int = 5) -> pd.Series:
    """Daily values to complete Monday-to-Sunday weeks. A week with 5 or 6 days is scaled to 7; the first and last week must be whole."""
    s = pd.Series(daily.to_numpy(float), index=pd.to_datetime(daily.index)).sort_index()
    week = s.resample("W-SUN")
    out = (week.mean() * 7)[week.count() >= min_days]
    return out[(out.index <= s.index.max()) & (out.index - pd.Timedelta(days=6) >= s.index.min())]


def downloads(ctx, metric: str, role: str, ent: str) -> pd.Series | None:
    df = dims(ctx.obs(metric=metric, entity=ent), "package", "role")
    df = latest(df[df["role"] == role], keys=("entity", "package", "as_of"))
    if df.empty:
        return None
    df = dev_adoption.flag_spikes(df)
    return weekly(df[~df["spike"]].groupby("as_of")["value"].sum())


def wiki(ctx, kind: str, ent: str) -> pd.Series | None:
    df = dims(ctx.obs(metric="wiki_pageviews", entity=ent), "title", "kind")
    df = latest(df[df["kind"] == kind], keys=("entity", "title", "as_of"))
    return None if df.empty else weekly(df.groupby("as_of")["value"].sum(), min_days=7)


def commits(ctx) -> pd.Series | None:
    df = latest(ctx.obs(metric="coauthored_commits", entity=ME), keys=("entity", "as_of"))
    if df.empty:
        return None
    s = pd.Series(df["value"].to_numpy(float), index=[week_end(d) for d in df["as_of"]]).groupby(level=0).sum()
    return s.sort_index()


def panel(ctx, ent: str) -> dict[str, pd.Series]:
    """Weekly levels for each series this company has."""
    found = {
        "pypi": downloads(ctx, "pypi_downloads", "sdk", ent),
        "npm": downloads(ctx, "npm_downloads", "sdk", ent),
        "cli": downloads(ctx, "npm_downloads", "cli", ent),
        "wiki_company": wiki(ctx, "company", ent),
        "wiki_product": wiki(ctx, "product", ent),
        "commits": commits(ctx) if ent == ME else None,
    }
    return {k: v for k, v in found.items() if v is not None and len(v) >= MIN_WEEKS}


def sdk(ctx, ent: str) -> pd.Series | None:
    """PyPI plus npm SDK downloads: one developer-usage series per company."""
    a, b = downloads(ctx, "pypi_downloads", "sdk", ent), downloads(ctx, "npm_downloads", "sdk", ent)
    if a is None or b is None:
        return None
    both = pd.concat([a, b], axis=1, join="inner")
    return both.sum(axis=1) if len(both) else None


# ---- statistics ----


def growth(level: pd.Series) -> pd.Series:
    """Weekly log growth on a full weekly grid; a missing week leaves a gap, never a jump across it."""
    grid = level.reindex(pd.date_range(level.index.min(), level.index.max(), freq="W-SUN"))
    return np.log(grid.where(grid > 0)).diff()


def whiten(g: pd.Series, p: int = AR) -> pd.Series:
    """What is left of weekly growth after its own last p weeks are removed, so a series that merely persists does not look like a leader."""
    d = pd.concat({"y": g, **{f"l{i}": g.shift(i) for i in range(1, p + 1)}}, axis=1).dropna()
    if len(d) < MIN_WEEKS:
        return pd.Series(dtype=float)
    X = np.column_stack([d[f"l{i}"].to_numpy() for i in range(1, p + 1)] + [np.ones(len(d))])
    return pd.Series(d["y"].to_numpy() - X @ np.linalg.lstsq(X, d["y"].to_numpy(), rcond=None)[0], index=d.index)


def ranked(s: pd.Series) -> pd.Series:
    """Ranks of the weeks that exist, so a correlation of the ranks (Spearman) ignores how extreme a week was."""
    return s.rank()


def pair_r(a: np.ndarray, B: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Correlation of a with each row of B over the weeks where both exist (NaN is missing), and how many weeks that was."""
    ma, mb = (~np.isnan(a)).astype(float), (~np.isnan(B)).astype(float)
    a0, b0 = np.nan_to_num(a), np.nan_to_num(B)
    n, sx, sy = mb @ ma, mb @ a0, b0 @ ma
    sxx, syy, sxy = mb @ a0**2, b0**2 @ ma, b0 @ a0
    den = np.sqrt(np.maximum((n * sxx - sx**2) * (n * syy - sy**2), 0))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where((den > 0) & (n >= MIN_PAIRS), (n * sxy - sx * sy) / den, np.nan), n


def on_grid(x: pd.Series, y: pd.Series) -> tuple[np.ndarray, np.ndarray, pd.DatetimeIndex]:
    idx = pd.date_range(max(x.index.min(), y.index.min()), min(x.index.max(), y.index.max()), freq="W-SUN")
    return x.reindex(idx).to_numpy(), y.reindex(idx).to_numpy(), idx


def surrogates(b: np.ndarray, rng: np.random.Generator, draws: int) -> np.ndarray:
    """Series with the same spectrum as b, so the same autocorrelation, but random timing. Missing weeks stay missing.

    Rotating the series would do the same job but gives only one surrogate per week of history (about 160), too few to
    resolve a p-value of 0.001, which a correction across 50 pairs needs.
    """
    gone = np.isnan(b)
    z = np.where(gone, 0.0, b - np.nanmean(b))
    spectrum = np.fft.rfft(z)
    phase = rng.uniform(0, 2 * np.pi, (draws, len(spectrum)))
    phase[:, 0] = 0
    if len(z) % 2 == 0:
        phase[:, -1] = 0
    out = np.fft.irfft(np.abs(spectrum) * np.exp(1j * phase), n=len(z), axis=1)
    out[:, gone] = np.nan
    return out


def lead_test(x: pd.Series, y: pd.Series, seed: int = SEED) -> dict | None:
    """Does x lead y by 1 to 8 weeks? The best |r| over those lags, against the best |r| of surrogates of y."""
    a, b, idx = on_grid(x, y)
    if len(idx) < MIN_WEEKS:
        return None
    Y = np.vstack([b, surrogates(b, np.random.default_rng(seed), DRAWS)])  # row 0 is the real series
    rs, ns = zip(*(pair_r(a[:-k], Y[:, k:]) for k in LAGS), strict=True)
    R, N = np.nan_to_num(np.abs(np.vstack(rs)), nan=0.0), np.vstack(ns)
    best = int(np.argmax(R[:, 0]))
    if R[best, 0] == 0:
        return None
    null = R[:, 1:].max(axis=0)
    return {
        "lag": list(LAGS)[best],
        "r": float(np.vstack(rs)[best, 0]),
        "n": int(N[best, 0]),
        "p": float((1 + (null >= R[best, 0]).sum()) / (DRAWS + 1)),
        "ceiling": float(np.quantile(null, 0.95)),
    }


def halves(x: pd.Series, y: pd.Series, lag: int) -> tuple[float, float, int, int]:
    """The correlation at one lag in the first and the second half of the weeks, and the weeks in each."""
    a, b, idx = on_grid(x, y)
    d = pd.DataFrame({"x": pd.Series(a, idx).shift(lag), "y": pd.Series(b, idx)}).dropna()
    mid = len(d) // 2
    first, second = d.iloc[:mid], d.iloc[mid:]

    def corr(t: pd.DataFrame) -> float:
        return float(t["x"].corr(t["y"])) if len(t) > 10 else float("nan")

    return corr(first), corr(second), len(first), len(second)


def profile(x: pd.Series, y: pd.Series) -> list[tuple[int, float]]:
    """Correlation at every lag from -8 to +8; at a positive lag x leads y, at a negative lag y leads x, at 0 they move together."""
    a, b, idx = on_grid(x, y)
    xs, ys = pd.Series(a, idx), pd.Series(b, idx)
    return [(k, float(xs.shift(k).corr(ys))) for k in PROFILE]


def fdr(p: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values, in the order given."""
    m = len(p)
    order = np.argsort(p)
    adj = np.minimum.accumulate((np.array(p)[order] * m / (np.arange(m) + 1))[::-1])[::-1].clip(max=1)
    out = np.empty(m)
    out[order] = adj
    return out.tolist()


def verdict(q: float, r: float, n: int, r1: float, r2: float) -> str:
    """Finding: survives the correction, holds in both halves, enough weeks to check. Hypothesis: survives the correction only."""
    if q >= 0.05:
        return UNSUPPORTED
    steady = n >= 2 * MIN_WEEKS and r1 * r2 > 0 and min(abs(r1), abs(r2)) >= 0.5 * abs(r)
    return FINDING if steady else HYPOTHESIS


def tested(ctx) -> list[dict]:
    """Every ordered pair of Anthropic's own series, and of its growth relative to OpenAI's, tested for a 1 to 8 week lead."""
    mine, theirs = panel(ctx, ME), panel(ctx, PEER)
    series = {("own", k): ranked(whiten(growth(v))) for k, v in mine.items()}
    for k in BOTH:
        if k in mine and k in theirs:
            g = pd.concat([growth(mine[k]), growth(theirs[k])], axis=1, join="inner")
            series[("relative", k)] = ranked(whiten(g.iloc[:, 0] - g.iloc[:, 1]))
    series = {k: v for k, v in series.items() if len(v)}
    rows = []
    for (kx, ax), x in series.items():
        for (ky, ay), y in series.items():
            t = lead_test(x, y) if kx == ky and ax != ay else None
            if t:
                r1, r2, n1, n2 = halves(x, y, t["lag"])
                rows.append(
                    {**t, "basis": kx, "x": ax, "y": ay, "r1": r1, "r2": r2, "n1": n1, "n2": n2, "xs": x, "ys": y}
                )
    for row, q in zip(rows, fdr([r["p"] for r in rows]), strict=True):
        row["q"] = q
        row["verdict"] = verdict(q, row["r"], row["n"], row["r1"], row["r2"])
    return sorted(rows, key=lambda r: (r["p"], -abs(r["r"])))


def pair_name(t: dict) -> str:
    """ "Wikipedia: product article → PyPI downloads"; (vs OpenAI) marks Anthropic's growth minus OpenAI's, which is what is left once the market's moves cancel."""
    sfx = " (vs OpenAI)" if t["basis"] == "relative" else ""
    return f"{LABEL[t['x']]}{sfx} → {LABEL[t['y']]}{sfx}"


# ---- events ----


def family(name: str) -> str | None:
    """The numbered model line a listed name belongs to, so "GPT-5 Mini" and "GPT-5 Pro" are GPT-5, or None when the name has no version."""
    name = name.removeprefix("Claude ")
    m = VERSION.match(name)
    return m.group(1) if m else name if re.search(r"\d", name) else None


def release_weeks(ctx, ent: str) -> dict[pd.Timestamp, list[str]]:
    """The weeks a company first listed a new model line, with each line's name; a variant of a line already listed is not a release."""
    df = latest(dims(ctx.obs(metric="model_release", entity=ent), "model", "name"), keys=("entity", "model"))
    seen: set[str] = set()
    out: dict[pd.Timestamp, list[str]] = {}
    for r in df.sort_values(["as_of", "name"]).itertuples():
        key = family(str(r.name))
        if key and key.lower() not in seen:
            seen.add(key.lower())
            out.setdefault(week_end(r.as_of), []).append(key)
    return dict(sorted(out.items()))


def smoothed(level: pd.Series) -> pd.Series:
    """Average weekly growth over the last four weeks, so a one-week blip is spread over a month."""
    return np.expm1(growth(level).rolling(4, min_periods=4).mean()).dropna()


def event_profile(
    g: pd.Series, events: list, other: pd.Series | None = None, seed: int = SEED, draws: int = 3000
) -> dict | None:
    """Average growth in each week around the events, against the average of the same weeks around randomly chosen weeks.

    `other` is a second company's growth, averaged over the same weeks as a market check.
    """
    idx = pd.date_range(g.index.min(), g.index.max(), freq="W-SUN")
    a, pos = g.reindex(idx).to_numpy(), {d: i for i, d in enumerate(idx)}
    off = np.array(list(OFFSETS))
    lo, hi = -off.min(), off.max()
    ev = np.array(sorted({pos[w] for w in events if w in pos and lo <= pos[w] < len(a) - hi}))
    if len(ev) < MIN_EVENTS:
        return None
    rng = np.random.default_rng(seed)
    ok = np.arange(
        max(lo, ev.min() - lo), len(a) - hi
    )  # random weeks come from the same stretch of history as the releases
    picks = ok[rng.random((draws, len(ok))).argsort(axis=1)[:, : len(ev)]]
    null = np.nanmean(a[picks[:, :, None] + off[None, None, :]], axis=1)
    obs = np.nanmean(a[ev[:, None] + off[None, :]], axis=0)
    month = (off >= 4) & (off <= 8)
    c_obs, c_null = obs[month].sum(), null[:, month].sum(axis=1)
    c_mid = c_null.mean()
    out = {
        "events": len(ev),
        "obs": obs,
        "low": np.percentile(null, 2.5, axis=0),
        "high": np.percentile(null, 97.5, axis=0),
        "month": c_obs,
        "month_random": c_mid,
        "p": float((1 + (abs(c_null - c_mid) >= abs(c_obs - c_mid)).sum()) / (draws + 1)),
    }
    if other is not None:
        market = np.nanmean(other.reindex(idx).to_numpy()[ev[:, None] + off[None, :]], axis=0)
        out |= {"other": market, "other_month": market[month].sum()}
    return out


def blocks(level: pd.Series, size: int = 13) -> pd.Series:
    """Totals of non-overlapping blocks of weeks, counted back from the latest complete week."""
    n = len(level) // size
    v = level.iloc[len(level) - n * size :].to_numpy().reshape(n, size).sum(axis=1)
    return pd.Series(v, index=level.index[len(level) - n * size :][size - 1 :: size])


# ---- chart helpers ----


def col(field: str, label: str, fmt: str) -> dict:
    return {"field": field, "label": label, "format": fmt}


def pct(x: float) -> str:
    return f"{x * 100:+.0f}%"


def empty(title: str, subtitle: str) -> dict:
    """An honest empty chart: the page says it is waiting for data, not that nothing was found."""
    return {
        "title": title,
        "subtitle": subtitle,
        "kind": "combo",
        "layers": [{"mark": "line", "name": "Waiting for data", "y": "y"}],
        "encoding": {
            "x": {"field": "x", "type": "ordinal", "label": ""},
            "y": {"field": "y", "type": "quantitative", "label": ""},
        },
        "columns": [col("x", "", "text"), col("y", "", "float")],
        "rows": [],
        "takeaway": [],
        "assumptions": BASE_NOTES,
        "badges": [],
    }


# ---- charts ----


@mart(id="signal.releases", sources=[*USAGE, "model_releases"])
def releases(ctx):
    title = "Model releases over weekly growth in SDK downloads, Anthropic and OpenAI"
    sub = (
        "Weekly growth in each company's SDK downloads (PyPI plus npm, 4-week average). "
        "Each vertical line is a new model line the company listed, in the company's colour."
    )
    a, o = sdk(ctx, ME), sdk(ctx, PEER)
    rel = {ME: release_weeks(ctx, ME), PEER: release_weeks(ctx, PEER)}
    if a is None or o is None or not rel[ME]:
        return empty(title, sub)
    sa, so = smoothed(a), smoothed(o)
    start = min(rel[ME]) - pd.Timedelta(weeks=12)
    weeks = sorted(w for w in set(sa.index) | set(so.index) if w >= start)
    rows = [
        {
            "week": w.date().isoformat(),
            "anthropic": float(sa[w]) if w in sa.index else None,
            "openai": float(so[w]) if w in so.index else None,
            "release": None,
            "company": None,
        }
        for w in weeks
    ]
    count = {}
    for ent in (ME, PEER):
        shown = {w: n for w, n in rel[ent].items() if start <= w <= weeks[-1]}
        count[ent] = sum(len(n) for n in shown.values())
        rows += [
            {
                "week": w.date().isoformat(),
                "anthropic": None,
                "openai": None,
                "release": ", ".join(n),
                "company": WHO[ent],
            }
            for w, n in shown.items()
        ]
    rows.sort(key=lambda r: r["week"])
    both = pd.concat([sa[sa.index >= start], so[so.index >= start]], axis=1, join="inner").dropna()
    together = float(ranked(both.iloc[:, 0]).corr(ranked(both.iloc[:, 1])))
    how = (
        "mostly the market's"
        if together >= 0.6
        else "partly the market's"
        if together >= 0.3
        else "mostly each company's own"
    )
    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "line", "name": "Anthropic", "y": "anthropic"},
            {"mark": "line", "name": "OpenAI", "y": "openai"},
            {"mark": "rule", "name": "Release", "label": "release", "series": "company"},
        ],
        "encoding": {
            "x": {"field": "week", "type": "temporal", "label": "Week ending"},
            "y": {"field": "anthropic", "type": "quantitative", "label": "Average weekly growth", "format": "pct"},
        },
        "columns": [
            col("week", "Week ending", "date"),
            col("anthropic", "Anthropic SDK growth", "pct"),
            col("openai", "OpenAI SDK growth", "pct"),
            col("release", "Model line released", "text"),
            col("company", "Company", "text"),
        ],
        "rows": rows,
        "takeaway": [
            (
                f"Since {month_label(start)} Anthropic listed {count[ME]} new model lines and OpenAI {count[PEER]}. "
                "A jump in both lines is the market; a jump in one is that company's own."
            ),
            f"The two growth lines move together (match {together:+.2f} over {len(both)} weeks), so the swings are {how}.",
        ],
        "assumptions": [
            "A release is the first time a numbered model line (Opus 4.1, GPT-5, o3) appears on OpenRouter's public list, the same rule for both companies; a variant such as Mini, Pro or Codex is not a new release. The date is when OpenRouter listed it, which can run a few days after the announcement.",
            "Each line is the average of the last four weekly growth rates. The chart starts 12 weeks before Anthropic's first listed release: earlier weeks are launch-era growth that would flatten the rest.",
            SPIKE_NOTE,
            *BASE_NOTES,
        ],
        "badges": ["arithmetic"],
    }


def around_chart(ctx, who: str, other: str) -> dict:
    name, rival = WHO[who], WHO[other]
    article = "an" if name[0] in "AEIOU" else "a"
    title = f"{name}'s and {rival}'s SDK growth around {article} {name} model release"
    sub = (
        f"Bars: {name}'s average weekly growth in each week around its releases. Line: {rival}'s over the same weeks, as the market. "
        f"Shaded: the middle 95% of random weeks for {name}."
    )
    mine, theirs = sdk(ctx, who), sdk(ctx, other)
    rel = release_weeks(ctx, who)
    result = (
        event_profile(growth(mine), list(rel), other=growth(theirs))
        if mine is not None and theirs is not None
        else None
    )
    if result is None:
        return empty(title, sub)
    rows = [
        {
            "week": "0" if k == 0 else f"{k:+d}",
            "growth": float(g),
            "other": float(m),
            "low": float(lo),
            "high": float(hi),
        }
        for k, g, m, lo, hi in zip(OFFSETS, result["obs"], result["other"], result["low"], result["high"], strict=True)
    ]
    outside = [r["week"] for r in rows if not r["low"] <= r["growth"] <= r["high"]]
    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "band", "name": "Random weeks", "y_low": "low", "y_high": "high"},
            {"mark": "bar", "name": name, "y": "growth"},
            {"mark": "line", "name": rival, "y": "other"},
        ],
        "encoding": {
            "x": {"field": "week", "type": "ordinal", "label": "Weeks from the release"},
            "y": {"field": "growth", "type": "quantitative", "label": "Average weekly growth", "format": "pct"},
        },
        "columns": [
            col("week", "Weeks from release", "text"),
            col("growth", f"{name} average growth", "pct"),
            col("other", f"{rival} average growth", "pct"),
            col("low", "Random weeks, low", "pct"),
            col("high", "Random weeks, high", "pct"),
        ],
        "rows": rows,
        "takeaway": [
            f"Across {result['events']} {name} releases, "
            + (
                "no week fell outside what a random week shows."
                if not outside
                else f"weeks {', '.join(outside)} fell outside what a random week shows."
            ),
            (
                f"In the weeks +4 to +8 {name} grew {pct(result['month'])} after a release against {pct(result['month_random'])} for random weeks "
                f"(p {result['p']:.2f}); {rival} grew {pct(result['other_month'])} over the same weeks."
            ),
        ],
        "assumptions": [
            f"{result['events']} {name} releases had a full window. With so few, only a large and quick effect would show.",
            "Growth is the weekly log change in PyPI plus npm downloads. If the bars and the line rise together after a release, that is the market (or the launch lifting both); if only the bars rise, it is the company's own.",
            "The shaded band is the middle 95% of the same average taken over randomly chosen weeks from the same stretch of history as the releases (3,000 draws, fixed seed).",
            *BASE_NOTES,
        ],
        "badges": ["arithmetic"],
    }


@mart(id="signal.around_release", sources=[*USAGE, "model_releases"])
def around_release(ctx):
    return around_chart(ctx, ME, PEER)


@mart(id="signal.around_openai_release", sources=[*USAGE, "model_releases"])
def around_openai_release(ctx):
    return around_chart(ctx, PEER, ME)


@mart(id="signal.vs_openai", sources=USAGE)
def vs_openai(ctx):
    title, sub = (
        "Anthropic's SDK downloads against OpenAI's",
        "Growth over each 13 weeks against the 13 weeks before, for both companies; the bar is Anthropic minus OpenAI, so it is Anthropic's own part once the market is taken out.",
    )
    a, o = sdk(ctx, ME), sdk(ctx, PEER)
    if a is None or o is None:
        return empty(title, sub)
    both = pd.concat([a, o], axis=1, join="inner").dropna()
    ba, bo = blocks(both.iloc[:, 0]), blocks(both.iloc[:, 1])
    ga, go = ba.pct_change().dropna().iloc[-RECENT:], bo.pct_change().dropna().iloc[-RECENT:]
    rows = [
        {"period": month_label(d), "gap": float(ga[d] - go[d]), "anthropic": float(ga[d]), "openai": float(go[d])}
        for d in ga.index
    ]
    if not rows:
        return empty(title, sub)
    ahead = sum(r["gap"] > 0 for r in rows)
    now = rows[-1]
    split = (
        f"Of Anthropic's {pct(now['anthropic'])}, {pct(now['openai'])} is what OpenAI also saw (the market) and {now['gap'] * 100:.0f} points are Anthropic's own."
        if now["gap"] > 0
        else f"OpenAI grew {-now['gap'] * 100:.0f} points more than Anthropic, so the market outgrew it."
    )
    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "bar", "name": "Anthropic minus OpenAI", "y": "gap"},
            {"mark": "line", "name": "Anthropic", "y": "anthropic"},
            {"mark": "line", "name": "OpenAI", "y": "openai"},
        ],
        "encoding": {
            "x": {"field": "period", "type": "ordinal", "label": "13 weeks to"},
            "y": {"field": "gap", "type": "quantitative", "label": "Growth", "format": "pct"},
        },
        "columns": [
            col("period", "13 weeks to", "text"),
            col("gap", "Anthropic minus OpenAI", "pct"),
            col("anthropic", "Anthropic growth", "pct"),
            col("openai", "OpenAI growth", "pct"),
        ],
        "rows": rows,
        "takeaway": [
            (
                f"In the 13 weeks to {now['period']} Anthropic's SDK downloads grew {pct(now['anthropic'])} and OpenAI's {pct(now['openai'])}; "
                f"Anthropic grew faster in {ahead} of {len(rows)} periods."
            ),
            split,
        ],
        "assumptions": [
            "A positive bar means Anthropic grew faster, or fell less, than OpenAI. Anthropic can be shrinking and still be ahead of a market that shrank more.",
            "OpenAI stands in for the market: what both companies share is market growth, and the difference is Anthropic's own.",
            SPIKE_NOTE,
            f"The latest {RECENT} blocks of 13 weeks are shown, counted back from the latest complete week, so they do not line up with calendar quarters.",
            *BASE_NOTES,
        ],
        "badges": ["arithmetic"],
    }


LEAD_ASSUMPTIONS = [
    "A lead is one signal moving first and another following some weeks later, for example Wikipedia views jumping before SDK downloads do. Each bar is how closely the second signal's week-to-week changes matched the first's that many weeks earlier.",
    "Weekly growth has its own last two weeks removed, so a series that merely persists is not mistaken for a leader, and weeks are ranked, so one extreme week cannot decide the result.",
    "The grey band is what luck alone reaches for the best of 8 lags: 1,500 surrogate series with the same autocorrelation and random timing (fixed seed). It is corrected for how many pairs were tried before a pair is called anything but luck.",
    f"{HYPOTHESIS}: survives the correction only. {FINDING}: survives it, holds in both halves of the history, and has at least 120 weeks. {UNSUPPORTED}: does not survive it.",
    *BASE_NOTES,
]


def lead_chart(ctx, basis: str) -> dict:
    own = basis == "own"
    title = (
        "Does one of Anthropic's signals move before another? The closest candidate"
        if own
        else "Does one signal move before another once OpenAI is taken out? The closest candidate"
    )
    results = [t for t in tested(ctx) if t["basis"] == basis]
    if not results:
        return empty(title, "Needs about a year of weekly history for two series.")
    t = results[0]
    rows = [
        {"lag": f"{k:+d}" if k else "0", "r": r, "low": -t["ceiling"], "high": t["ceiling"]}
        for k, r in profile(t["xs"], t["ys"])
        if not np.isnan(r)
    ]
    outside = sum(not -t["ceiling"] <= r["r"] <= t["ceiling"] for r in rows)
    first, second = (f"{t['r1']:+.2f}", f"{t['r2']:+.2f}") if not np.isnan(t["r1"]) else ("n/a", "n/a")
    edge = "" if own else " (vs OpenAI means Anthropic's growth minus OpenAI's, so the market's moves cancel.)"
    return {
        "title": title,
        "subtitle": (
            f"{pair_name(t)}. Each bar asks: when the first signal moved, did the second move the same way that many weeks later? "
            f"Right of 0 the first signal moves first. A bar outside the grey band would be more than luck.{edge}"
        ),
        "kind": "combo",
        "layers": [
            {"mark": "band", "name": "Luck", "y_low": "low", "y_high": "high"},
            {"mark": "bar", "name": "Match", "y": "r"},
        ],
        "encoding": {
            "x": {"field": "lag", "type": "ordinal", "label": "Weeks later (right of 0: the first signal moves first)"},
            "y": {"field": "r", "type": "quantitative", "label": "How closely they match", "format": "float"},
        },
        "columns": [
            col("lag", "Weeks later", "text"),
            col("r", "Match (correlation)", "float"),
            col("low", "Luck, low", "float"),
            col("high", "Luck, high", "float"),
        ],
        "rows": rows,
        "takeaway": [
            (
                f"{t['verdict']}: the closest match is {t['lag']} weeks later (correlation {t['r']:+.2f}, {t['n']} weeks); "
                f"{outside} of {len(rows)} bars {'leaves' if outside == 1 else 'leave'} the grey band, and with this many tries a few are expected by luck."
            ),
            f"In the first half of the history the match was {first} and in the second half {second}.",
        ],
        "assumptions": [f"The closest of {len(results)} pairs tested on this basis.", *LEAD_ASSUMPTIONS],
        "badges": ["arithmetic"],
    }


@mart(id="signal.lead_lag", sources=[*USAGE, *ATTENTION, "github_coauthored_commits"])
def lead_lag(ctx):
    return lead_chart(ctx, "own")


@mart(id="signal.lead_lag_edge", sources=[*USAGE, *ATTENTION, "github_coauthored_commits"])
def lead_lag_edge(ctx):
    return lead_chart(ctx, "relative")


@mart(id="signal.tested", sources=[*USAGE, *ATTENTION, "github_coauthored_commits"])
def tested_pairs(ctx):
    title = "Every pair of signals we tested, closest match first"
    sub = (
        "Each bar is one pair of signals: how closely the second followed the first within 8 weeks, as a multiple of what luck alone reaches "
        "(1 = luck's upper range, shaded). Hover a bar, or open the table, to see the pair."
    )
    results = tested(ctx)
    if not results:
        return empty(title, sub)
    shown = results[:16]
    rows = [
        {
            "rank": str(i),
            "pair": f"{pair_name(t)} ({t['lag']} weeks)",
            "strength": abs(t["r"]) / t["ceiling"],
            "r": t["r"],
            "q": t["q"],
            "verdict": t["verdict"],
            "low": 0.0,
            "high": 1.0,
        }
        for i, t in enumerate(shown, 1)
    ]
    counts = {v: sum(t["verdict"] == v for t in results) for v in (FINDING, HYPOTHESIS, UNSUPPORTED)}
    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "band", "name": "Within luck", "y_low": "low", "y_high": "high"},
            {"mark": "bar", "name": "Strength", "y": "strength", "series": "verdict"},
        ],
        "encoding": {
            "x": {"field": "rank", "type": "ordinal", "label": "Rank (hover for the pair; the table names them all)"},
            "y": {
                "field": "strength",
                "type": "quantitative",
                "label": "Multiple of what luck reaches",
                "format": "float",
            },
        },
        "columns": [
            col("rank", "Rank", "text"),
            col("pair", "Pair (first signal → second signal, best lead)", "text"),
            col("strength", "Multiple of what luck reaches", "float"),
            col("r", "Match (correlation)", "float"),
            col("q", "Corrected p", "float"),
            col("verdict", "Verdict", "text"),
            col("low", "Within luck, from", "float"),
            col("high", "Within luck, to", "float"),
        ],
        "rows": rows,
        "takeaway": [
            f"{len(results)} pairs were tested: {counts[FINDING]} hold up, {counts[HYPOTHESIS]} are possible and {counts[UNSUPPORTED]} could be luck.",
            f"The closest is {pair_name(results[0])}, {abs(results[0]['r']) / results[0]['ceiling']:.1f} times what luck reaches.",
        ],
        "assumptions": [
            f"The {len(shown)} closest of {len(results)} pairs are drawn; the weakest of all is {min(abs(t['r']) / t['ceiling'] for t in results):.1f} times what luck reaches.",
            "Pairs are every ordered pair of Anthropic's own signals, and of its growth relative to OpenAI's (marked vs OpenAI), tested for a lead of 1 to 8 weeks.",
            "Pairs from the same source (the two Wikipedia articles, the two download counts) share noise and can look related for that reason alone.",
            *LEAD_ASSUMPTIONS,
        ],
        "badges": ["arithmetic"],
    }


def rounds(ctx) -> pd.DataFrame:
    df = dims(ctx.obs(metric="round_post_money_usd", entity=ME), "round")
    return df.sort_values("as_of")[["as_of", "round", "value"]].reset_index(drop=True) if len(df) else df


def mean4(level: pd.Series, when) -> float | None:
    """The average of the four weeks ending in the week of `when`, or None outside the series."""
    end = week_end(when)
    window = level[(level.index <= end) & (level.index > end - pd.Timedelta(weeks=4))]
    return float(window.mean()) if len(window) == 4 else None


@mart(id="signal.valuation", sources=[*USAGE, "signal_funding_rounds"])
def valuation(ctx):
    title = "Anthropic's valuation at each round over SDK downloads, with OpenAI's downloads"
    sub = (
        "Anthropic's post-money valuation at each announced round, and both companies' weekly SDK downloads, all rebased to 100 at the first round. "
        "OpenAI's valuation is not shown: we hold no cited figures for it."
    )
    level, rival, r = sdk(ctx, ME), sdk(ctx, PEER), rounds(ctx)
    if level is None or rival is None or len(r) < 2:
        return empty(title, sub)
    base_v, base_a, base_o = (
        float(r["value"].iloc[0]),
        mean4(level, r["as_of"].iloc[0]),
        mean4(rival, r["as_of"].iloc[0]),
    )
    if not base_a or not base_o:
        return empty(title, sub)
    start = week_end(r["as_of"].iloc[0])
    marks = {
        week_end(a): (f"{n} ${v / 1e9:g}B", v / base_v * 100)
        for a, n, v in zip(r["as_of"], r["round"], r["value"], strict=True)
    }
    rows = []
    for w in level.index[level.index >= start]:
        a, o = mean4(level, w), mean4(rival, w)
        row = {
            "date": w.date().isoformat(),
            "anthropic": a / base_a * 100 if a else None,
            "openai": o / base_o * 100 if o else None,
            "round": None,
            "valuation": None,
        }
        if w in marks:
            row["round"], row["valuation"] = marks[w]
        rows.append(row)
    last = next(x for x in reversed(rows) if x["anthropic"] and x["openai"])
    top = r.iloc[-1]
    at_top = next(x for x in rows if x["round"] and x["round"].startswith(top["round"]))  # the week of the latest round
    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "line", "name": "Anthropic", "y": "anthropic"},
            {"mark": "line", "name": "OpenAI", "y": "openai"},
            {"mark": "rule", "name": "Funding round", "label": "round"},
            {"mark": "point", "name": "Anthropic's valuation", "y": "valuation"},
        ],
        "encoding": {
            "x": {"field": "date", "type": "temporal", "label": "Week ending"},
            "y": {"field": "anthropic", "type": "quantitative", "label": "Rebased to 100", "format": "int"},
        },
        "columns": [
            col("date", "Week ending", "date"),
            col("anthropic", "Anthropic SDK downloads (rebased)", "float"),
            col("openai", "OpenAI SDK downloads (rebased)", "float"),
            col("round", "Funding round", "text"),
            col("valuation", "Anthropic's valuation (rebased)", "float"),
        ],
        "rows": rows,
        "takeaway": [
            (
                f"From {r['round'].iloc[0]} to {top['round']} Anthropic's announced valuation rose {top['value'] / base_v:.1f}x while its SDK downloads rose "
                f"{at_top['anthropic'] / 100:.1f}x and OpenAI's {at_top['openai'] / 100:.1f}x; by the latest week they were up {last['anthropic'] / 100:.1f}x and {last['openai'] / 100:.1f}x."
            )
        ],
        "assumptions": [
            f"Only {len(r)} rounds state a valuation, so this is a picture of what moved in the same period, not a measure of what drives valuation.",
            "Valuations are the post-money figures Anthropic quoted in its own announcements (a cited ledger). Series A to C stated none and Series D was never announced.",
            "SDK downloads are the four-week average ending that week, compared with the same average at the first round.",
            *BASE_NOTES,
        ],
        "badges": ["arithmetic"],
    }


@mart(id="signal.valuation_steps", sources=[*USAGE, *ATTENTION, "signal_funding_rounds"])
def valuation_steps(ctx):
    title = "How much each signal grew when Anthropic's valuation stepped up"
    sub = (
        "Bars: Anthropic's valuation multiple from one round to the next. Dots: how much Anthropic's and OpenAI's SDK downloads, "
        "and Anthropic's Wikipedia views, grew over the same interval."
    )
    r = rounds(ctx)
    levels = {
        "Anthropic": sdk(ctx, ME),
        "OpenAI": sdk(ctx, PEER),
        "Wikipedia: company article": wiki(ctx, "company", ME),
        "Wikipedia: product article": wiki(ctx, "product", ME),
    }
    levels = {k: v for k, v in levels.items() if v is not None}
    if len(r) < 2 or "Anthropic" not in levels:
        return empty(title, sub)

    def grew(k: str, i: int, j: int) -> float | None:
        a, b = mean4(levels[k], r["as_of"].iloc[i]), mean4(levels[k], r["as_of"].iloc[j])
        return a / b if a and b else None

    rows = []
    for i in range(1, len(r)):
        step = f"{r['round'].iloc[i - 1]} to {r['round'].iloc[i]}"
        rows.append(
            {
                "step": step,
                "valuation": float(r["value"].iloc[i] / r["value"].iloc[i - 1]),
                "signal": None,
                "growth": None,
            }
        )
        rows += [
            {"step": step, "valuation": None, "signal": k, "growth": g} for k in levels if (g := grew(k, i, i - 1))
        ]
    steps = [x for x in rows if x["valuation"] is not None]
    most = max(steps, key=lambda s: s["valuation"])
    total = {k: grew(k, len(r) - 1, 0) for k in ("Anthropic", "OpenAI") if k in levels}
    takeaway = [f"The valuation stepped up most from {most['step']} ({most['valuation']:.1f}x)."]
    if all(total.values()) and len(total) == 2:
        takeaway.append(
            f"From {r['round'].iloc[0]} to {r['round'].iloc[-1]} Anthropic's SDK downloads grew {total['Anthropic']:.1f}x and OpenAI's {total['OpenAI']:.1f}x."
        )
    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "bar", "name": "Anthropic's valuation", "y": "valuation"},
            {"mark": "point", "name": "Signal", "y": "growth", "series": "signal"},
        ],
        "encoding": {
            "x": {"field": "step", "type": "ordinal", "label": "Round to round"},
            "y": {"field": "valuation", "type": "quantitative", "label": "Multiple", "format": "multiple"},
        },
        "columns": [
            col("step", "Round to round", "text"),
            col("valuation", "Valuation multiple", "multiple"),
            col("signal", "Signal", "text"),
            col("growth", "Signal growth", "multiple"),
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            f"{len(steps)} intervals: far too few to say which signal tracks the valuation. This shows the figures side by side and no more.",
            "A signal's growth is the four-week average ending at the later round divided by the same average at the earlier round. Anthropic and OpenAI are their SDK downloads (PyPI plus npm).",
            *BASE_NOTES,
        ],
        "badges": ["arithmetic"],
    }
