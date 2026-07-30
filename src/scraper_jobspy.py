"""Source `type: jobspy` — scrape LinkedIn / Indeed / Glassdoor via python-jobspy."""
import math
from datetime import date


def fetch_jobspy(source: dict) -> list[dict]:
    """Scrape les sites configurés via python-jobspy et retourne des offres normalisées.

    Import paresseux : le reste du pipeline fonctionne même si python-jobspy
    n'est pas installé (la source est alors ignorée avec un message).
    """
    try:
        from jobspy import scrape_jobs
    except ImportError:
        print("    python-jobspy non installé (pip install python-jobspy) — source ignorée.")
        return []

    from src.config import get_search

    defaults = get_search().source_defaults("jobspy")
    params = {**defaults, **(source.get("params", {}) or {})}
    df = scrape_jobs(
        site_name=params.get("sites", ["linkedin", "indeed"]),
        search_term=params.get("search_term", "software intern"),
        location=params.get("location", ""),
        results_wanted=params.get("results_wanted", 30),
        hours_old=params.get("hours_old", 72),
        country_indeed=params.get("country_indeed", "netherlands"),
        linkedin_fetch_description=params.get("fetch_description", True),
        verbose=0,
    )
    if df is None or df.empty:
        return []

    offers = []
    for _, row in df.iterrows():
        url = _clean(row.get("job_url"))
        if not url:
            continue
        offers.append(_normalize_row(row, url, source))
    return offers


def _clean(value) -> str:
    """Convertit une valeur pandas (potentiellement NaN) en str propre."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def _normalize_row(row, url: str, source: dict) -> dict:
    from src.scraper import make_offer_id

    published = _clean(row.get("date_posted"))[:10] or str(date.today())
    return {
        "id": make_offer_id(url),
        "title": _clean(row.get("title")),
        "company": _clean(row.get("company")),
        "location": _clean(row.get("location")) or source.get("country", ""),
        "description": _clean(row.get("description")),
        "url": url,
        "date_published": published,
        "date_scraped": str(date.today()),
        "source": f"{source.get('name', '')} ({_clean(row.get('site'))})",
        "country": source.get("country", ""),
        "tags": [],
        "remote": bool(row.get("is_remote")) if _clean(row.get("is_remote")) else False,
    }
