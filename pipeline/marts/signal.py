"""Signal page: is Anthropic ahead of OpenAI, and does one public signal lead another?

Every chart draws what it tested, whether or not a pattern was found: the verdict (Finding, Hypothesis, Not supported) is a label
beside the plot, never a gate in front of it. Nothing here claims one thing causes another.

Levels all trend up together, so any two correlate. The tests therefore use weekly growth with each series' own momentum removed
(an AR(2) fit) and ranked, so one extreme week cannot decide a result (Spearman correlation). The best of eight lags is compared with what
surrogate series give by chance (same autocorrelation, random timing), and corrected for how many pairs were tried (Benjamini-Hochberg).
A fixed seed makes every run identical.
"""

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
FINDING, HYPOTHESIS, UNSUPPORTED = "Finding", "Hypothesis", "Not supported"
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
    rel = "relative to OpenAI: " if t["basis"] == "relative" else ""
    return f"{rel}{LABEL[t['x']]} → {LABEL[t['y']]}"


# ---- events ----


def release_weeks(ctx, ent: str) -> dict[pd.Timestamp, list[str]]:
    """The weeks a company released a model, with each model's name."""
    df = latest(dims(ctx.obs(metric="model_release", entity=ent), "model", "name"), keys=("entity", "model"))
    out: dict[pd.Timestamp, list[str]] = {}
    for r in df.itertuples():
        out.setdefault(week_end(r.as_of), []).append(str(r.name).removeprefix("Claude "))
    return dict(sorted(out.items()))


def event_profile(g: pd.Series, events: list, seed: int = SEED, draws: int = 3000) -> dict | None:
    """Average growth in each week around the events, against the average of the same weeks around randomly chosen weeks."""
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
    return {
        "events": len(ev),
        "obs": obs,
        "low": np.percentile(null, 2.5, axis=0),
        "high": np.percentile(null, 97.5, axis=0),
        "month": c_obs,
        "month_random": c_mid,
        "p": float((1 + (abs(c_null - c_mid) >= abs(c_obs - c_mid)).sum()) / (draws + 1)),
    }


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
    title = "Claude model releases over weekly growth in SDK downloads"
    sub = "Weekly growth in Anthropic's SDK downloads (PyPI plus npm, 4-week average); each line is a model Anthropic released."
    level, rel = sdk(ctx, ME), release_weeks(ctx, ME)
    if level is None or not rel:
        return empty(title, sub)
    named = {w: ", ".join(n) for w, n in rel.items() if level.index.min() <= w <= level.index.max()}
    smooth = np.expm1(growth(level).rolling(4, min_periods=4).mean()).dropna()
    start = min(rel) - pd.Timedelta(weeks=12)
    rows = [
        {
            "week": w.date().isoformat(),
            "growth": float(g),
            "downloads": int(level[w]),
            "release": named.get(w),
            "company": "Anthropic" if w in named else None,
        }
        for w, g in smooth[smooth.index >= start].items()
    ]
    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "line", "name": "Anthropic", "y": "growth"},
            {"mark": "rule", "name": "Anthropic release", "label": "release", "series": "company"},
        ],
        "encoding": {
            "x": {"field": "week", "type": "temporal", "label": "Week ending"},
            "y": {"field": "growth", "type": "quantitative", "label": "Average weekly growth", "format": "pct"},
        },
        "columns": [
            col("week", "Week ending", "date"),
            col("growth", "Growth, 4-week average", "pct"),
            col("downloads", "SDK downloads that week", "int"),
            col("release", "Model released", "text"),
            col("company", "Company", "text"),
        ],
        "rows": rows,
        "takeaway": [
            f"{sum(len(n) for n in rel.values())} Anthropic models were listed in {len(named)} weeks since {month_label(min(rel))}; each is drawn over the growth line so you can judge it."
        ],
        "assumptions": [
            "A release is the date OpenRouter listed the model, which can run a few days after Anthropic's announcement, and its list starts in May 2025.",
            "The line is the average of the last four weekly growth rates, so a one-week blip is spread over a month. The chart starts 12 weeks before the first release: earlier weeks are launch-era growth that would flatten the rest.",
            SPIKE_NOTE,
            *BASE_NOTES,
        ],
        "badges": ["arithmetic"],
    }


@mart(id="signal.around_release", sources=[*USAGE, "model_releases"])
def around_release(ctx):
    title = "Average weekly growth in SDK downloads around a model release"
    sub = "Weeks before and after each Anthropic release, against what a randomly chosen week would show (shaded: the middle 95% of random weeks)."
    level, rel = sdk(ctx, ME), release_weeks(ctx, ME)
    result = event_profile(growth(level), list(rel)) if level is not None else None
    if result is None:
        return empty(title, sub)
    off = list(OFFSETS)
    rows = [
        {"week": "0" if k == 0 else f"{k:+d}", "growth": float(g), "low": float(lo), "high": float(hi)}
        for k, g, lo, hi in zip(off, result["obs"], result["low"], result["high"], strict=True)
    ]
    outside = [r["week"] for r in rows if not r["low"] <= r["growth"] <= r["high"]]
    month = f"In the weeks +4 to +8 growth added up to {pct(result['month'])} after a release against {pct(result['month_random'])} for random weeks (p {result['p']:.2f})."
    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "band", "name": "Random weeks", "y_low": "low", "y_high": "high"},
            {"mark": "bar", "name": "After a release", "y": "growth"},
        ],
        "encoding": {
            "x": {"field": "week", "type": "ordinal", "label": "Weeks from the release"},
            "y": {"field": "growth", "type": "quantitative", "label": "Average weekly growth", "format": "pct"},
        },
        "columns": [
            col("week", "Weeks from release", "text"),
            col("growth", "Average growth", "pct"),
            col("low", "Random weeks, low", "pct"),
            col("high", "Random weeks, high", "pct"),
        ],
        "rows": rows,
        "takeaway": [
            f"Across {result['events']} releases, "
            + (
                "no week fell outside what a random week shows."
                if not outside
                else f"weeks {', '.join(outside)} fell outside what a random week shows."
            ),
            month,
        ],
        "assumptions": [
            f"{result['events']} releases had a full window. With so few, only a large and quick effect would show.",
            "Growth is the weekly log change in PyPI plus npm downloads.",
            "The shaded band is the middle 95% of the same average taken over randomly chosen weeks from the same stretch of history as the releases (3,000 draws, fixed seed).",
            *BASE_NOTES,
        ],
        "badges": ["arithmetic"],
    }


@mart(id="signal.vs_openai", sources=USAGE)
def vs_openai(ctx):
    title, sub = (
        "Anthropic's SDK downloads against OpenAI's",
        "Growth over each 13 weeks against the 13 weeks before, for both companies; the bar is Anthropic minus OpenAI.",
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
            )
        ],
        "assumptions": [
            "A positive bar means Anthropic grew faster, or fell less, than OpenAI. Anthropic can be shrinking and still be ahead of a market that shrank more.",
            SPIKE_NOTE,
            f"The latest {RECENT} blocks of 13 weeks are shown, counted back from the latest complete week, so they do not line up with calendar quarters.",
            *BASE_NOTES,
        ],
        "badges": ["arithmetic"],
    }


@mart(id="signal.lead_lag", sources=[*USAGE, *ATTENTION, "github_coauthored_commits"])
def lead_lag(ctx):
    title = "The strongest candidate for a lead, week by week"
    results = tested(ctx)
    if not results:
        return empty(title, "Needs about a year of weekly history for two series.")
    t = results[0]
    sub = f"{pair_name(t)}: correlation of weekly growth at each lag. The shaded band is what chance alone gives for the best of 8 lags."
    rows = [
        {"lag": f"{k:+d}" if k else "0", "r": r, "low": -t["ceiling"], "high": t["ceiling"]}
        for k, r in profile(t["xs"], t["ys"])
        if not np.isnan(r)
    ]
    first, second = (f"{t['r1']:+.2f}", f"{t['r2']:+.2f}") if not np.isnan(t["r1"]) else ("n/a", "n/a")
    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "band", "name": "Chance", "y_low": "low", "y_high": "high"},
            {"mark": "bar", "name": "Correlation", "y": "r"},
        ],
        "encoding": {
            "x": {"field": "lag", "type": "ordinal", "label": "Weeks (positive: the first series leads)"},
            "y": {"field": "r", "type": "quantitative", "label": "Correlation of weekly growth", "format": "float"},
        },
        "columns": [
            col("lag", "Lag, weeks", "text"),
            col("r", "Correlation", "float"),
            col("low", "Chance, low", "float"),
            col("high", "Chance, high", "float"),
        ],
        "rows": rows,
        "takeaway": [
            (
                f"{t['verdict']}: the best lead is {t['lag']} weeks (r {t['r']:+.2f}, {t['n']} weeks, corrected p {t['q']:.2f}); "
                f"in the first half of the history r was {first} and in the second half {second}."
            )
        ],
        "assumptions": [
            f"The best of {len(results)} pairs tested, so a strong-looking bar is expected somewhere; the correction for that is in the verdict.",
            "The chance band comes from 1,500 surrogate series with the same autocorrelation and random timing (fixed seed).",
            "Finding: survives the correction, holds in both halves of the history, and has at least 120 weeks. Hypothesis: survives the correction only. Not supported: does not.",
            "Weekly growth has its own last two weeks removed, so a series that merely persists is not mistaken for a leader.",
            *BASE_NOTES,
        ],
        "badges": ["arithmetic"],
    }


@mart(id="signal.tested", sources=[*USAGE, *ATTENTION, "github_coauthored_commits"])
def tested_pairs(ctx):
    title = "Every lead we tested, strongest first"
    sub = "Each bar is the best lagged correlation of a pair, as a multiple of what chance gives (1 = the 95% line; shaded is within chance). Hover a bar, or open the table, to see the pair."
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
            {"mark": "band", "name": "Within chance", "y_low": "low", "y_high": "high"},
            {"mark": "bar", "name": "Strength", "y": "strength", "series": "verdict"},
        ],
        "encoding": {
            "x": {"field": "rank", "type": "ordinal", "label": "Rank (hover for the pair; the table names them all)"},
            "y": {
                "field": "strength",
                "type": "quantitative",
                "label": "Multiple of the chance line",
                "format": "float",
            },
        },
        "columns": [
            col("rank", "Rank", "text"),
            col("pair", "Pair (leader → follower, best lead)", "text"),
            col("strength", "Multiple of chance line", "float"),
            col("r", "Correlation", "float"),
            col("q", "Corrected p", "float"),
            col("verdict", "Verdict", "text"),
            col("low", "Within chance, from", "float"),
            col("high", "Within chance, to", "float"),
        ],
        "rows": rows,
        "takeaway": [
            f"{len(results)} pairs were tested: {counts[FINDING]} Findings, {counts[HYPOTHESIS]} Hypotheses and {counts[UNSUPPORTED]} Not supported.",
            f"The strongest is {pair_name(results[0])}, {abs(results[0]['r']) / results[0]['ceiling']:.1f} times the chance line.",
        ],
        "assumptions": [
            f"The {len(shown)} strongest of {len(results)} pairs are drawn; the weakest of all is {min(abs(t['r']) / t['ceiling'] for t in results):.1f} times the chance line.",
            "Pairs are every ordered pair of Anthropic's own series, and of its growth relative to OpenAI's, tested for a lead of 1 to 8 weeks.",
            "Pairs from the same source (the two Wikipedia articles, the two download counts) share noise and can look related for that reason alone.",
            *BASE_NOTES,
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
    title = "Anthropic's funding-round valuations over SDK downloads"
    sub = (
        "Post-money valuation at each announced round and weekly SDK downloads, both rebased to 100 at the first round."
    )
    level, r = sdk(ctx, ME), rounds(ctx)
    if level is None or len(r) < 2:
        return empty(title, sub)
    base_v, base_s = float(r["value"].iloc[0]), mean4(level, r["as_of"].iloc[0])
    if not base_s:
        return empty(title, sub)
    start = week_end(r["as_of"].iloc[0])
    rows = []
    marks = {
        week_end(a): (f"{n} ${v / 1e9:g}B", v / base_v * 100)
        for a, n, v in zip(r["as_of"], r["round"], r["value"], strict=True)
    }
    for w in level.index[level.index >= start]:
        s = mean4(level, w)
        row = {
            "date": w.date().isoformat(),
            "signal": s / base_s * 100 if s else None,
            "round": None,
            "valuation": None,
        }
        if w in marks:
            row["round"], row["valuation"] = marks[w]
        rows.append(row)
    last = next(x for x in reversed(rows) if x["signal"])
    top = r.iloc[-1]
    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "line", "name": "SDK downloads", "y": "signal"},
            {"mark": "rule", "name": "Funding round", "label": "round"},
            {"mark": "point", "name": "Valuation", "y": "valuation"},
        ],
        "encoding": {
            "x": {"field": "date", "type": "temporal", "label": "Week ending"},
            "y": {"field": "signal", "type": "quantitative", "label": "Rebased to 100", "format": "int"},
        },
        "columns": [
            col("date", "Week ending", "date"),
            col("signal", "SDK downloads (rebased)", "float"),
            col("round", "Funding round", "text"),
            col("valuation", "Valuation (rebased)", "float"),
        ],
        "rows": rows,
        "takeaway": [
            f"From {r['round'].iloc[0]} to {top['round']} the announced valuation rose {top['value'] / base_v:.1f}x while four-week SDK downloads rose {last['signal'] / 100:.1f}x."
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
    title = "How much each signal grew when the valuation stepped up"
    sub = "Bars: the valuation multiple from one round to the next. Dots: how much each signal grew over the same interval."
    r = rounds(ctx)
    levels = {
        "SDK downloads": sdk(ctx, ME),
        "Wikipedia: company article": wiki(ctx, "company", ME),
        "Wikipedia: product article": wiki(ctx, "product", ME),
    }
    levels = {k: v for k, v in levels.items() if v is not None}
    if len(r) < 2 or not levels:
        return empty(title, sub)
    rows = []
    for i in range(1, len(r)):
        step = f"{r['round'].iloc[i - 1]} to {r['round'].iloc[i]}"
        base = {"step": step, "valuation": float(r["value"].iloc[i] / r["value"].iloc[i - 1])}
        grown = {k: (mean4(v, r["as_of"].iloc[i]), mean4(v, r["as_of"].iloc[i - 1])) for k, v in levels.items()}
        pts = [(k, a / b) for k, (a, b) in grown.items() if a and b]
        rows.append({**base, "signal": None, "growth": None})
        rows += [{"step": step, "valuation": None, "signal": k, "growth": g} for k, g in pts]
    steps = [x for x in rows if x["valuation"] is not None]
    most = max(steps, key=lambda s: s["valuation"])
    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "bar", "name": "Valuation", "y": "valuation"},
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
        "takeaway": [f"The valuation stepped up most from {most['step']} ({most['valuation']:.1f}x)."],
        "assumptions": [
            f"{len(steps)} intervals: far too few to say which signal tracks the valuation. This shows the figures side by side and no more.",
            "A signal's growth is the four-week average ending at the later round divided by the same average at the earlier round.",
            *BASE_NOTES,
        ],
        "badges": ["arithmetic"],
    }
