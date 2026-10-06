"""Hiring charts. Function and region are classified from the words on each posting by the rules below, shown on each chart."""

import re

import pandas as pd

from pipeline.core import mart
from pipeline.core.frames import change, dims, latest, num

LIVE = ["ats_open_roles", "wayback_job_boards"]

# ---- function: department words first, then title words, then Other ----
DEPARTMENT_RULES = [  # first match wins; each pattern is searched in the lower-cased department
    ("Other", r"human data"),  # data-annotation tutors at xAI: neither research nor corporate
    ("Compute & data centers", r"\bcompute\b|data center|\bscaling\b"),
    ("Applied AI & solutions", r"applied ai|forward.deployed|solutions|life sciences|technical education"),
    ("Safety, security & policy", r"safe|trust|secur|polic|public (benefit|affairs)|global affairs|intelligence"),
    ("Research & modeling", r"research|(?<!data )science|^model|modeling|post.?training|^vision"),
    ("Go-to-market & support", r"sales|go to market|revenue|^business$|marketing|partnership|user operations|customer"),
    (
        "Engineering & product",
        r"engineer|software|infrastructure|platform|product|design|data science|analytics|hardware|device|inference",
    ),
    (
        "Corporate & operations",
        r"financ|legal|people|human resources|^hr$|communic|^pr\b|\bit\b|information technology|workplace|operations|program management|m&a|strateg|g&a|compliance",
    ),
]
TITLE_RULES = [  # for postings whose department matched nothing
    ("Research & modeling", r"research|scientist"),
    ("Applied AI & solutions", r"forward.deployed|solutions? (architect|engineer)|applied ai"),
    (
        "Go-to-market & support",
        r"account (executive|manager)|sales|customer success|business development|recruiter|marketing",
    ),
    ("Safety, security & policy", r"safety|security|policy|trust"),
    ("Engineering & product", r"engineer|developer|designer|product manager|software"),
    ("Corporate & operations", r"finance|legal|counsel|people|hr\b|accountant|operations"),
]


def classify_function(department: str, title: str) -> str:
    dept, title = (department or "").lower(), (title or "").lower()
    for name, pattern in DEPARTMENT_RULES:
        if re.search(pattern, dept):
            return name
    for name, pattern in TITLE_RULES:
        if re.search(pattern, title):
            return name
    return "Other"


# ---- region: first listed location that can be placed ----
US_STATES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}
PLACE_RULES = [
    (
        "UK & Europe",
        r"\b(uk|united kingdom|england|london|ireland|dublin|paris|france|germany|berlin|munich|zurich|switzerland|amsterdam|netherlands|spain|madrid|italy|milan|poland|warsaw|sweden|stockholm|denmark|copenhagen|norway|oslo|finland|helsinki|croatia|belgium|brussels|austria|vienna|portugal|lisbon|nordics|emea|europe|luxembourg|z[uü]rich|ch)\b",
    ),
    (
        "Asia-Pacific",
        r"\b(japan|tokyo|korea|seoul|singapore|india|bangalore|bengaluru|mumbai|delhi|sydney|australia|hong kong|china|taiwan|indonesia|apac|new zealand|vietnam|philippines)\b",
    ),
    (
        "Other international",
        r"\b(canada|can|ontario|alberta|calgary|winnipeg|halifax|toronto|montr[eé]al|vancouver|dubai|abu dhabi|united arab emirates|uae|ksa|saudi|middle east|brazil|s[aã]o paulo|mexico|israel|tel aviv|africa|nigeria|kenya|latam|international)\b",
    ),
    (
        "United States",
        r"\b(united states|usa|u\.s\.|us|san francisco|new york|nyc|seattle|palo alto|austin|memphis|southaven|boston|los angeles|chicago|denver|atlanta|miami|bellevue|mountain view|sunnyvale|san jose|washington)\b",
    ),
]
COUNTRY_REGION = {
    "united states": "United States",
    "united kingdom": "UK & Europe",
}


def classify_region(location: str, country: str = "") -> str:
    """Region of the first listed location that can be placed. A posting listed in several places counts where it is listed first."""
    tokens = [t.strip() for t in re.split(r"[|;]", location or "") if t.strip()]
    for tok in tokens:
        low = tok.lower()
        if low.startswith(
            "remote"
        ):  # "Remote-Friendly, United States" -> "united states"; bare "Remote" -> nothing to place
            low = re.sub(r"\(.*?\)|remote[- ]?(friendly)?", "", low).strip(" ,:-")
            if not low:
                continue
        if (m := re.search(r",\s*([A-Z]{2})\s*$", tok)) and m.group(1) in US_STATES:
            return "United States"
        for name, pattern in PLACE_RULES:
            if re.search(pattern, low):
                return name
    if (country or "").lower() in COUNTRY_REGION:
        return COUNTRY_REGION[country.lower()]
    return "Remote / unspecified"


# ---- shared ----
def snapshots(df: pd.DataFrame) -> pd.DataFrame:
    """For each (entity, as_of) keep only the newest retrieval, so a re-collected day never doubles its postings."""
    if df.empty:
        return df
    t = pd.to_datetime(df["retrieved_at"], utc=True, format="ISO8601")
    newest = df.assign(_t=t).groupby(["entity", "as_of"])["_t"].transform("max")
    return df[t == newest]


def latest_postings(ctx) -> pd.DataFrame:
    """The newest full weekly snapshot of postings per company, with function and region classified."""
    df = ctx.obs(source="ats_postings", metric="job_posting")
    if df.empty:
        return df
    df = snapshots(df)
    df = df[df["as_of"] == df.groupby("entity")["as_of"].transform("max")]
    df = dims(df, "title", "department", "location", "country")
    df = df.assign(
        function=[
            classify_function(d, t) for d, t in zip(df["department"].fillna(""), df["title"].fillna(""), strict=True)
        ],
        region=[
            classify_region(loc, c) for loc, c in zip(df["location"].fillna(""), df["country"].fillna(""), strict=True)
        ],
    )
    return df


def order_by_size(df: pd.DataFrame) -> list[str]:
    return df.groupby("entity").size().sort_values(ascending=False).index.tolist()


SNAPSHOT_NOTE = (
    "Counts postings listed on each company's own job board in the weekly snapshot. Boards list roles, not headcount: "
    "one posting can fill several seats, and a company can leave roles unlisted."
)
GAPS = "Google DeepMind is missing: it posts only on Google's careers site, which has no public API."


# ---- charts ----


@mart(id="hiring.open_roles", sources=LIVE)
def open_roles(ctx):
    df = latest(ctx.obs(metric="open_roles"), keys=("entity", "as_of")).sort_values(["entity", "as_of"])
    rows = [
        {"date": r.as_of, "company": ctx.names.get(r.entity, r.entity), "roles": int(r.value)} for r in df.itertuples()
    ]
    takeaway = []
    a = df[df["entity"] == "anthropic"]
    if len(a) > 1:
        first, last = a.iloc[0], a.iloc[-1]
        takeaway = [
            f"Anthropic's board went from {num(first.value)} roles in {first.as_of[:7]} to {num(last.value)} on {last.as_of} ({change(last.value, first.value)})."
        ]
    return {
        "title": "Open roles on each company's job board",
        "subtitle": "Postings live on each company's own board: daily since collection began, monthly Internet Archive captures before",
        "kind": "line",
        "encoding": {
            "x": {"field": "date", "type": "temporal", "label": "Date"},
            "y": {"field": "roles", "type": "quantitative", "label": "Open roles", "format": "int"},
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            {"field": "date", "label": "Date", "format": "date"},
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "roles", "label": "Open roles", "format": "int"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            SNAPSHOT_NOTE,
            "History before our own collection comes from Internet Archive captures, so it is only as dense as the Archive's crawls and starts when each board was first captured.",
            "Archived pages that could not be parsed are skipped, not estimated.",
            GAPS,
        ],
        "badges": [],
    }


@mart(id="hiring.gtm_to_rd", sources=["ats_postings"])
def gtm_to_rd(ctx):
    df = latest_postings(ctx)
    gtm = ("Go-to-market & support", "Applied AI & solutions")
    rd = ("Research & modeling", "Engineering & product")
    rows = []
    for entity in order_by_size(df) if len(df) else []:
        g = df[df["entity"] == entity]
        n_gtm, n_rd = int(g["function"].isin(gtm).sum()), int(g["function"].isin(rd).sum())
        if n_rd:
            rows.append(
                {
                    "company": ctx.names.get(entity, entity),
                    "ratio": round(n_gtm / n_rd, 2),
                    "customer_facing": n_gtm,
                    "build": n_rd,
                }
            )
    takeaway = []
    a = next((r for r in rows if r["company"] == ctx.names.get("anthropic")), None)
    if a and len(rows) > 1:
        rest = [r for r in rows if r is not a]
        takeaway = [
            (
                f"Anthropic lists {a['ratio']:.1f} customer-facing roles for every research or engineering role; peers range from "
                f"{min(r['ratio'] for r in rest):.1f} to {max(r['ratio'] for r in rest):.1f}."
            )
        ]
    return {
        "title": "Customer-facing roles per research or engineering role",
        "subtitle": "Open sales, support and applied AI roles for every research or engineering role. Higher means more of the hiring faces customers.",
        "kind": "bar",
        "encoding": {
            "x": {"field": "company", "type": "nominal", "label": "Company"},
            "y": {
                "field": "ratio",
                "type": "quantitative",
                "label": "Customer-facing roles per build role",
                "format": "float",
            },
            "color": {"field": "company", "type": "nominal", "label": "Company"},
        },
        "columns": [
            {"field": "company", "label": "Company", "format": "text"},
            {"field": "ratio", "label": "Ratio", "format": "float"},
            {"field": "customer_facing", "label": "Go-to-market + applied AI roles", "format": "int"},
            {"field": "build", "label": "Research + engineering & product roles", "format": "int"},
        ],
        "rows": rows,
        "takeaway": takeaway,
        "assumptions": [
            "Ratio = (go-to-market & support + applied AI & solutions) ÷ (research & modeling + engineering & product), by the function rules on the function chart.",
            "A high ratio means more of the hiring is selling and deploying than building. It is a mix indicator, not a quality judgement.",
            SNAPSHOT_NOTE,
            GAPS,
        ],
        "badges": ["arithmetic"],
    }
