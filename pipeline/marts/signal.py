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
CLAUDE = (
    re.compile(r"claude-(?P<tier>opus|sonnet|haiku|fable)-(?P<maj>\d)(?:[.-](?P<min>\d)(?!\d))?"),
    re.compile(r"claude-(?P<maj>\d)(?:[.-](?P<min>\d)(?!\d))?-(?P<tier>opus|sonnet|haiku|fable)"),
)
SAME_SCORE, SAME_PRICE = (
    0.03,
    0.10,
)  # a change in the Intelligence Index within 3%, or in price per task within 10%, reads as the same
BENCH = ["openrouter_benchmarks", "arena_text_leaderboard"]
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
    """Average growth in each week around the events, against what an ordinary week shows.

    The ordinary week is the average of the same weeks around randomly chosen weeks (3,000 draws from the same stretch of
    history). `other` is a second company's growth taken over the same event weeks and the same random weeks, as a market check.
    """
    idx = pd.date_range(g.index.min(), g.index.max(), freq="W-SUN")
    pos = {d: i for i, d in enumerate(idx)}
    off = np.array(list(OFFSETS))
    lo, hi = -off.min(), off.max()
    ev = np.array(sorted({pos[w] for w in events if w in pos and lo <= pos[w] < len(idx) - hi}))
    if len(ev) < MIN_EVENTS:
        return None
    rng = np.random.default_rng(seed)
    ok = np.arange(
        max(lo, ev.min() - lo), len(idx) - hi
    )  # random weeks come from the same stretch of history as the releases
    picks = ok[rng.random((draws, len(ok))).argsort(axis=1)[:, : len(ev)]]
    month = (off >= 4) & (off <= 8)

    def around(s: pd.Series) -> dict:
        a = s.reindex(idx).to_numpy()
        null = np.nanmean(a[picks[:, :, None] + off[None, None, :]], axis=1)
        obs = np.nanmean(a[ev[:, None] + off[None, :]], axis=0)
        c_obs, c_null = obs[month].sum(), null[:, month].sum(axis=1)
        c_mid = c_null.mean()
        return {
            "obs": obs,
            "normal": null.mean(axis=0),
            "low": np.percentile(null, 2.5, axis=0),
            "high": np.percentile(null, 97.5, axis=0),
            "month": c_obs,
            "month_random": c_mid,
            "p": float((1 + (abs(c_null - c_mid) >= abs(c_obs - c_mid)).sum()) / (draws + 1)),
        }

    out = {"events": len(ev), **around(g)}
    if other is not None:
        out["other"] = around(other)
    return out


def blocks(level: pd.Series, size: int = 13) -> pd.Series:
    """Totals of non-overlapping blocks of weeks, counted back from the latest complete week."""
    n = len(level) // size
    v = level.iloc[len(level) - n * size :].to_numpy().reshape(n, size).sum(axis=1)
    return pd.Series(v, index=level.index[len(level) - n * size :][size - 1 :: size])


# ---- how good and how costly each model line is ----


def line_of(text: str) -> tuple[str, tuple[int, int], str] | None:
    """(tier, version, label) of the model line a benchmark or Arena name belongs to, or None.

    "claude-4.1-opus-20250805" and "claude-opus-4-1-thinking" are both ("opus", (4, 1), "Opus 4.1"); "gpt-5.2-high" is
    ("GPT", (5, 2), "GPT-5.2"). Tiers are compared like with like: Opus with Opus, GPT with GPT, o with o.
    """
    t = text.lower().removeprefix("openai/").removeprefix("anthropic/").replace("_", "-")
    for pattern in CLAUDE:
        if m := pattern.search(t):
            ver = (int(m["maj"]), int(m["min"] or 0))
            return m["tier"], ver, f"{m['tier'].title()} {ver[0]}" + (f".{ver[1]}" if m["min"] else "")
    if t.startswith("gpt-oss"):
        return "gpt-oss", (0, 0), "gpt-oss"
    if m := re.match(r"gpt-(\d+)o(?![a-z])", t):
        return "GPT-4o", (int(m[1]), 0), f"GPT-{m[1]}o"
    if m := re.match(r"gpt-(\d+)(?:\.(\d+))?(?![\d.])", t):
        return "GPT", (int(m[1]), int(m[2] or 0)), f"GPT-{m[1]}" + (f".{m[2]}" if m[2] else "")
    if m := re.match(r"o(\d+)(?!\d)", t):
        return "o", (int(m[1]), 0), f"o{m[1]}"
    return None


def scorecard(ctx, ent: str) -> dict[str, dict]:
    """For each model line of a company, its best model's scores, keyed by the line's label in lower case.

    The best model is the one with the highest Intelligence Index. Its accuracy and cost per task on GPQA Diamond are
    OpenRouter's own evaluation of that same model; Arena's score is the best of the line's variants (reasoning levels).
    """
    cards: dict[str, dict] = {}
    index = latest(
        dims(ctx.obs(metric="openrouter_aa_intelligence_index", entity=ent), "model", "permaslug"),
        keys=("entity", "permaslug"),
    )
    for r in index.itertuples():
        line = line_of(r.permaslug)
        if line and (line[2].lower() not in cards or r.value > cards[line[2].lower()]["index"]):
            cards[line[2].lower()] = {
                "tier": line[0],
                "version": line[1],
                "label": line[2],
                "index": float(r.value),
                "permaslug": r.permaslug,
                "model": r.model,
            }
    evals = dims(ctx.obs(metric="openrouter_eval_accuracy", entity=ent), "permaslug", "benchmark", "cost_per_task_usd")
    evals = latest(evals[evals["benchmark"] == "gpqa_diamond"], keys=("entity", "permaslug"))
    by_slug = {r.permaslug: r for r in evals.itertuples()}
    for card in cards.values():
        if card["permaslug"] in by_slug:
            e = by_slug[card["permaslug"]]
            card["accuracy"], card["cost"] = float(e.value), float(e.cost_per_task_usd)
    arena = latest(dims(ctx.obs(metric="arena_text_score", entity=ent), "model"), keys=("entity", "model"))
    for r in arena.itertuples():
        line = line_of(r.model)
        if line and line[2].lower() in cards:
            card = cards[line[2].lower()]
            card["arena"] = max(card.get("arena", 0.0), float(r.value))
    return cards


def small(card: dict) -> bool:
    """Whether the line's best model is a cut-down one (Mini, Nano): not like for like with a full-size model."""
    return bool(re.search(r"\b(mini|nano)\b", card["model"], re.IGNORECASE))


def previous_in_tier(cards: dict[str, dict], card: dict) -> dict | None:
    """The line just before this one in the same tier, if it has a score and is the same size of model."""
    before = [c for c in cards.values() if c["tier"] == card["tier"] and c["version"] < card["version"]]
    prev = max(before, key=lambda c: c["version"]) if before else None
    return prev if prev is None or small(prev) == small(card) else None


def change(new: float | None, old: float | None) -> float | None:
    return new / old - 1 if new and old else None


def verdict_of(d_index: float | None, d_price: float | None, earlier: bool = False) -> str:
    if d_index is None:
        return "No like-for-like comparison" if earlier else "First in its line"
    score = "Higher score" if d_index > SAME_SCORE else "Lower score" if d_index < -SAME_SCORE else "Same score"
    if d_price is None:
        return f"{score}, price unknown"
    return f"{score}, {'pricier' if d_price > SAME_PRICE else 'cheaper' if d_price < -SAME_PRICE else 'same price'}"


def new_models(ctx, since: pd.Timestamp) -> list[dict]:
    """Every release from `since` on, both companies, in date order, with its scores and its change on the line before."""
    out = []
    for ent in (ME, PEER):
        cards = scorecard(ctx, ent)
        for week, keys in release_weeks(ctx, ent).items():
            for key in keys if week >= since else []:
                card = cards.get(key.lower())
                prev = previous_in_tier(cards, card) if card else None
                d_index = change(card["index"], prev["index"]) if card and prev else None
                d_price = change(card.get("cost"), prev.get("cost")) if card and prev else None
                out.append(
                    {
                        "week": week,
                        "company": WHO[ent],
                        "release": key,
                        "index": card["index"] if card else None,
                        "arena": card.get("arena") if card else None,
                        "accuracy": card.get("accuracy") if card else None,
                        "cost": card.get("cost") if card else None,
                        "model": card["model"] if card else None,
                        "after": prev["label"] if prev else None,
                        "d_index": d_index,
                        "d_price": d_price,
                        "verdict": verdict_of(
                            d_index,
                            d_price,
                            earlier=any(
                                c["tier"] == card["tier"] and c["version"] < card["version"] for c in cards.values()
                            ),
                        )
                        if card
                        else "No score yet",
                    }
                )
    return sorted(out, key=lambda m: (m["week"], m["company"]))


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


@mart(id="signal.releases", sources=[*USAGE, "model_releases", "openrouter_benchmarks"])
def releases(ctx):
    title = "Model releases over weekly growth in SDK downloads, Anthropic and OpenAI"
    sub = (
        "Weekly growth in each company's SDK downloads (PyPI plus npm, 4-week average). "
        "Each vertical line is a new model line the company listed, in the company's colour; the number in brackets is its Artificial Analysis Intelligence Index "
        "(higher is smarter, best model in the line)."
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
        cards = scorecard(ctx, ent)
        rows += [
            {
                "week": w.date().isoformat(),
                "anthropic": None,
                "openai": None,
                "release": ", ".join(f"{k} ({cards[k.lower()]['index']:.0f})" if k.lower() in cards else k for k in n),
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


LEAD_ASSUMPTIONS = [
    "A lead is one signal moving first and another following some weeks later, for example Wikipedia views jumping before SDK downloads do. Each bar is how closely the second signal's week-to-week changes matched the first's that many weeks earlier.",
    "Weekly growth has its own last two weeks removed, so a series that merely persists is not mistaken for a leader, and weeks are ranked, so one extreme week cannot decide the result.",
    "The grey band is what luck alone reaches for the best of 8 lags: 1,500 surrogate series with the same autocorrelation and random timing (fixed seed). It is corrected for how many pairs were tried before a pair is called anything but luck.",
    f"{HYPOTHESIS}: survives the correction only. {FINDING}: survives it, holds in both halves of the history, and has at least 120 weeks. {UNSUPPORTED}: does not survive it.",
    *BASE_NOTES,
]


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

    def said(got: list[float]) -> str:
        """The multiples from each round to the next, e.g. "3.0x, 2.1x and 2.5x"."""
        return ", ".join(f"{g:.1f}x" for g in got[:-1]) + (" and " if len(got) > 1 else "") + f"{got[-1]:.1f}x"

    def usage(level: pd.Series) -> list[float]:
        return [mean4(level, r["as_of"].iloc[i]) / mean4(level, r["as_of"].iloc[i - 1]) for i in range(1, len(r))]

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
            ),
            (
                f"Round to round Anthropic's valuation rose {said([r['value'].iloc[i] / r['value'].iloc[i - 1] for i in range(1, len(r))])}, "
                f"its SDK downloads {said(usage(level))} and OpenAI's {said(usage(rival))}."
            ),
        ],
        "assumptions": [
            f"Only {len(r)} rounds state a valuation, so this is a picture of what moved in the same period, not a measure of what drives valuation.",
            "Valuations are the post-money figures Anthropic quoted in its own announcements (a cited ledger). Series A to C stated none and Series D was never announced.",
            "SDK downloads are the four-week average ending that week, compared with the same average at the first round.",
            *BASE_NOTES,
        ],
        "badges": ["arithmetic"],
    }


# ---- did each new model improve? ----

MODEL_NOTES = [
    "A model line is a numbered release (Opus 4.5, GPT-5.2). Each line is scored by its best-scoring model in Artificial Analysis's Intelligence Index, relayed by OpenRouter; the table names that model.",
    "Price per task is what one GPQA Diamond question (graduate-level science) cost OpenRouter to run on that model, reasoning tokens included, in US cents. It is not a general business task, and a model OpenRouter has not evaluated has none.",
    "Each model is compared with the previous line of the same kind (Opus after Opus, GPT after GPT, o after o). A line with no earlier one, or whose earlier line has no score, or where one best model is a Mini or Nano and the other is not, is not compared.",
    "Scores and prices are as collected on the dates shown, not as at each release. A score within 3%, or a price within 10%, of the earlier model reads as the same.",
    *BASE_NOTES,
]


def since_first_release(ctx) -> pd.Timestamp | None:
    rel = release_weeks(ctx, ME)
    return min(rel) - pd.Timedelta(weeks=12) if rel else None


def cents(x: float | None) -> float | None:
    return None if x is None else x * 100


def comparable(models: list[dict]) -> list[dict]:
    return [m for m in models if m["d_index"] is not None]


@mart(id="signal.model_change", sources=["model_releases", *BENCH])
def model_change(ctx):
    title = "Did each new model improve on the one before it?"
    sub = (
        "Bars: change in Intelligence Index against the previous model line of the same kind, above 0 is smarter. "
        "Dots: change in price per task against the same model, below 0 is cheaper."
    )
    start = since_first_release(ctx)
    models = comparable(new_models(ctx, start)) if start is not None else []
    if not models:
        return empty(title, sub)
    rows = [
        {
            "release": m["release"],
            "company": m["company"],
            "after": m["after"],
            "d_index": m["d_index"],
            "d_price": m["d_price"],
        }
        for m in models
    ]
    up = sum(m["d_index"] > SAME_SCORE for m in models)
    down = sum(m["d_index"] < -SAME_SCORE for m in models)
    priced = [m for m in models if m["d_price"] is not None]
    cheaper = sum(m["d_price"] < -SAME_PRICE for m in priced)
    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "bar", "name": "Intelligence Index change", "y": "d_index", "series": "company"},
            {"mark": "point", "name": "Price per task change", "y": "d_price"},
        ],
        "encoding": {
            "x": {"field": "release", "type": "ordinal", "label": "Model line, in release order"},
            "y": {"field": "d_index", "type": "quantitative", "label": "Change on the previous model", "format": "pct"},
        },
        "columns": [
            col("release", "Model line", "text"),
            col("company", "Company", "text"),
            col("after", "Compared with", "text"),
            col("d_index", "Intelligence Index change", "pct"),
            col("d_price", "Price per task change", "pct"),
        ],
        "rows": rows,
        "takeaway": [
            f"Of {len(models)} releases that can be compared with an earlier model, {up} scored higher, {len(models) - up - down} about the same and {down} lower.",
            f"Of the {len(priced)} with a price per task for both models, {cheaper} cost less per task and {sum(m['d_price'] > SAME_PRICE for m in priced)} cost more.",
        ],
        "assumptions": MODEL_NOTES,
        "badges": ["arithmetic"],
    }


@mart(id="signal.model_table", sources=["model_releases", *BENCH])
def model_table(ctx):
    title = "Every new model: score, human preference and price per task"
    sub = "Newest first. Verdict compares the model with the previous line of the same kind, on score and on price per task."
    start = since_first_release(ctx)
    models = list(reversed(new_models(ctx, start))) if start is not None else []
    if not models:
        return empty(title, sub)
    rows = [
        {
            "release": m["release"],
            "company": m["company"],
            "listed": m["week"].date().isoformat(),
            "verdict": m["verdict"],
            "after": m["after"],
            "index": m["index"],
            "d_index": m["d_index"],
            "arena": m["arena"],
            "accuracy": m["accuracy"],
            "cents": cents(m["cost"]),
            "d_price": m["d_price"],
            "model": m["model"],
        }
        for m in models
    ]
    scored = comparable(models)
    return {
        "title": title,
        "subtitle": sub,
        "kind": "table",
        "encoding": {},
        "columns": [
            col("release", "Model line", "text"),
            col("company", "Company", "text"),
            col("listed", "Listed", "date"),
            col("verdict", "Verdict", "text"),
            col("after", "Compared with", "text"),
            col("index", "Intelligence Index", "float"),
            col("d_index", "Index change", "pct"),
            col("arena", "Arena score", "int"),
            col("accuracy", "GPQA accuracy", "pct"),
            col("cents", "Price per task, US cents", "float"),
            col("d_price", "Price change", "pct"),
            col("model", "Model scored", "text"),
        ],
        "rows": rows,
        "takeaway": [
            f"{len(models)} new model lines since {month_label(start)}; {len(scored)} can be compared with an earlier line, and {sum(m['verdict'].startswith('Higher') for m in scored)} of those scored higher."
        ],
        "assumptions": MODEL_NOTES,
        "badges": ["arithmetic"],
    }


# ---- the redesigned page: one chart per question ----

DIMENSIONS = {
    "SDK downloads": lambda ctx, ent: sdk(ctx, ent),
    "Coding-agent CLI downloads": lambda ctx, ent: downloads(ctx, "npm_downloads", "cli", ent),
    "Wikipedia: product article": lambda ctx, ent: wiki(ctx, "product", ent),
    "Wikipedia: company article": lambda ctx, ent: wiki(ctx, "company", ent),
}


@mart(id="signal.market_or_own", sources=[*USAGE, *ATTENTION])
def market_or_own(ctx):
    title = "Is Anthropic's growth the market's or its own?"
    sub = (
        "Dots: each company's growth over the latest 13 weeks against the 13 weeks before, in four dimensions. "
        "Bars: Anthropic minus OpenAI, the part that is Anthropic's own once the market (OpenAI) is taken out."
    )
    rows = []
    for dim, build in DIMENSIONS.items():
        a, o = build(ctx, ME), build(ctx, PEER)
        if a is None or o is None:
            continue
        both = pd.concat([a, o], axis=1, join="inner").dropna()
        if len(both) < 27:
            continue
        ga, go = (float(blocks(both.iloc[:, i]).pct_change().iloc[-1]) for i in (0, 1))
        rows += [
            {"dimension": dim, "company": "Anthropic", "growth": ga, "gap": ga - go},
            {"dimension": dim, "company": "OpenAI", "growth": go, "gap": None},
        ]
    if not rows:
        return empty(title, sub)
    gaps = {r["dimension"]: r["gap"] for r in rows if r["gap"] is not None}
    ahead = [d for d, g in gaps.items() if g > 0]
    widest, narrowest = max(gaps, key=gaps.get), min(gaps, key=gaps.get)
    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "bar", "name": "Anthropic's own (gap to OpenAI)", "y": "gap"},
            {"mark": "point", "name": "Growth over 13 weeks", "y": "growth", "series": "company"},
        ],
        "encoding": {
            "x": {"field": "dimension", "type": "ordinal", "label": "Dimension"},
            "y": {"field": "growth", "type": "quantitative", "label": "Growth over 13 weeks", "format": "pct"},
        },
        "columns": [
            col("dimension", "Dimension", "text"),
            col("company", "Company", "text"),
            col("growth", "Growth over 13 weeks", "pct"),
            col("gap", "Anthropic minus OpenAI", "pct"),
        ],
        "rows": rows,
        "takeaway": [
            f"Anthropic grew faster than OpenAI in {len(ahead)} of {len(gaps)} dimensions over the latest 13 weeks.",
            f"Its widest lead is in {widest} ({gaps[widest] * 100:+.0f} points) and its narrowest in {narrowest} ({gaps[narrowest] * 100:+.0f} points).",
        ],
        "assumptions": [
            "OpenAI stands in for the market: what both share is market growth, and the difference is Anthropic's own. A negative bar means OpenAI grew faster, or fell less.",
            "Each dimension is a weekly series summed over the latest 13 complete weeks and compared with the 13 before: SDK downloads (PyPI plus npm), coding-agent CLI downloads (npm), and English Wikipedia views of the company article and of the product article.",
            "The Briefing compares every signal over 3 months; this page takes the same view across four dimensions and splits it into market and own.",
            SPIKE_NOTE,
            *BASE_NOTES,
        ],
        "badges": ["arithmetic"],
    }


@mart(id="signal.release_effect", sources=[*USAGE, "model_releases"])
def release_effect(ctx):
    title = "Do model releases move SDK growth? Both companies, both sets of releases"
    sub = (
        "Each mark is how much faster than a normal week a company's SDK downloads grew in each week around a release (0 = a normal week). "
        "Lines: around Anthropic's releases. Dots: around OpenAI's. Grey: the range a normal week reaches by luck. "
        "A company's own release should lift its own mark; a lift in both is the market."
    )
    a, o = sdk(ctx, ME), sdk(ctx, PEER)
    if a is None or o is None:
        return empty(title, sub)
    ga, go = growth(a), growth(o)
    by_a = event_profile(ga, list(release_weeks(ctx, ME)), other=go)  # around Anthropic's releases
    by_o = event_profile(go, list(release_weeks(ctx, PEER)), other=ga)  # around OpenAI's releases
    if by_a is None and by_o is None:
        return empty(title, sub)

    def extra(res: dict | None, key: str, i: int) -> float | None:
        """Growth above a normal week at one offset for `key` ('main' is the releasing company, 'other' the market check)."""
        if res is None:
            return None
        part = res if key == "main" else res["other"]
        return float(part["obs"][i] - part["normal"][i])

    def spread(i: int) -> tuple[float, float]:
        parts = [p for res in (by_a, by_o) if res for p in (res, res["other"])]
        return (
            min(float(p["low"][i] - p["normal"][i]) for p in parts),
            max(float(p["high"][i] - p["normal"][i]) for p in parts),
        )

    rows = []
    for i, k in enumerate(OFFSETS):
        low, high = spread(i)
        label = "0" if k == 0 else f"{k:+d}"
        rows.append(
            {
                "week": label,
                "company": "Anthropic",
                "around_a": extra(by_a, "main", i),
                "around_o": extra(by_o, "other", i),
                "low": low,
                "high": high,
            }
        )
        rows.append(
            {
                "week": label,
                "company": "OpenAI",
                "around_a": extra(by_a, "other", i),
                "around_o": extra(by_o, "main", i),
                "low": None,
                "high": None,
            }
        )

    def said(res: dict | None, releasing: str, rival: str) -> str | None:
        if res is None:
            return None
        main, other = res, res["other"]
        return (
            f"Over the weeks +4 to +8 after {res['events']} {releasing} releases, {releasing}'s growth added up to {pct(main['month'])} against {pct(main['month_random'])} "
            f"in a normal five weeks (p {main['p']:.2f}); {rival}'s added up to {pct(other['month'])} against {pct(other['month_random'])} (p {other['p']:.2f})."
        )

    return {
        "title": title,
        "subtitle": sub,
        "kind": "combo",
        "layers": [
            {"mark": "band", "name": "Normal week", "y_low": "low", "y_high": "high"},
            {"mark": "line", "name": "Around Anthropic's releases", "y": "around_a", "series": "company"},
            {"mark": "point", "name": "Around OpenAI's releases", "y": "around_o", "series": "company"},
        ],
        "encoding": {
            "x": {"field": "week", "type": "ordinal", "label": "Weeks from the release"},
            "y": {"field": "around_a", "type": "quantitative", "label": "Growth above a normal week", "format": "pct"},
        },
        "columns": [
            col("week", "Weeks from release", "text"),
            col("company", "Company measured", "text"),
            col("around_a", "Around Anthropic's releases", "pct"),
            col("around_o", "Around OpenAI's releases", "pct"),
            col("low", "Normal-week range, low", "pct"),
            col("high", "Normal-week range, high", "pct"),
        ],
        "rows": rows,
        "takeaway": [t for t in (said(by_a, "Anthropic", "OpenAI"), said(by_o, "OpenAI", "Anthropic")) if t],
        "assumptions": [
            "Growth is the weekly log change in PyPI plus npm downloads. A normal week is the average of the same weeks around randomly chosen weeks from the same stretch of history (3,000 draws, fixed seed), subtracted from each mark so all start from zero.",
            "The grey band is the range that normal weeks reach 95% of the time, taking the widest across the four. With a dozen releases per company, only a large and quick effect would show.",
            "A release is the first listing of a numbered model line on OpenRouter's public list, by the same rule for both companies.",
            *BASE_NOTES,
        ],
        "badges": ["arithmetic"],
    }


@mart(id="signal.lead_lag", sources=[*USAGE, *ATTENTION, "github_coauthored_commits"])
def lead_lag(ctx):
    title = "Does one signal move before another? The closest candidates"
    results = tested(ctx)
    own = next((t for t in results if t["basis"] == "own"), None)
    edge = next((t for t in results if t["basis"] == "relative"), None)
    if own is None and edge is None:
        return empty(title, "Needs about a year of weekly history for two series.")

    def matches(t: dict | None) -> dict[int, float]:
        return {k: r for k, r in profile(t["xs"], t["ys"]) if not np.isnan(r)} if t else {}

    own_r, edge_r = matches(own), matches(edge)
    ceiling = max(t["ceiling"] for t in (own, edge) if t)
    rows = [
        {"lag": f"{k:+d}" if k else "0", "own": own_r.get(k), "edge": edge_r.get(k), "low": -ceiling, "high": ceiling}
        for k in PROFILE
    ]

    def said(t: dict | None, who: str) -> str | None:
        if t is None:
            return None
        first, second = (f"{t['r1']:+.2f}", f"{t['r2']:+.2f}") if not np.isnan(t["r1"]) else ("n/a", "n/a")
        return (
            f"{who}: {pair_name(t)}. {t['verdict']}: the closest match is {t['lag']} weeks later (correlation {t['r']:+.2f}, {t['n']} weeks); "
            f"in the first half of the history it was {first} and in the second half {second}."
        )

    return {
        "title": title,
        "subtitle": (
            "Each bar or line point asks: when the first signal moved, did the second move the same way that many weeks later? Right of 0 the first signal moves first. "
            "A point outside the grey band would be more than luck. Bars: Anthropic's own signals. Line: Anthropic's growth beyond OpenAI's, so the market's moves cancel (vs OpenAI)."
        ),
        "kind": "combo",
        "layers": [
            {"mark": "band", "name": "Luck", "y_low": "low", "y_high": "high"},
            {"mark": "bar", "name": "Anthropic's own signals", "y": "own"},
            {"mark": "line", "name": "Beyond OpenAI", "y": "edge"},
        ],
        "encoding": {
            "x": {"field": "lag", "type": "ordinal", "label": "Weeks later (right of 0: the first signal moves first)"},
            "y": {"field": "own", "type": "quantitative", "label": "How closely they match", "format": "float"},
        },
        "columns": [
            col("lag", "Weeks later", "text"),
            col("own", "Match, Anthropic's own signals", "float"),
            col("edge", "Match, beyond OpenAI", "float"),
            col("low", "Luck, low", "float"),
            col("high", "Luck, high", "float"),
        ],
        "rows": rows,
        "takeaway": [t for t in (said(own, "Own signals"), said(edge, "Beyond OpenAI")) if t],
        "assumptions": [
            f"The closest pair of {sum(t['basis'] == 'own' for t in results)} own and {sum(t['basis'] == 'relative' for t in results)} beyond-OpenAI pairs tested.",
            *LEAD_ASSUMPTIONS,
        ],
        "badges": ["arithmetic"],
    }
