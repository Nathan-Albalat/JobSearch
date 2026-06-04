import hashlib
import json
import os
from datetime import date, datetime

import feedparser
import requests
import yaml


def load_sources(config_dir: str) -> list[dict]:
    path = os.path.join(config_dir, "sources.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("sources", [])


def load_seen_offers(data_dir: str) -> dict:
    path = os.path.join(data_dir, "seen_offers.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_seen_offers(data_dir: str, seen: dict) -> None:
    path = os.path.join(data_dir, "seen_offers.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def make_offer_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:8]


def parse_feed(source: dict) -> list:
    feed = feedparser.parse(
        source["url"],
        agent="Mozilla/5.0 (compatible; stage-bot/1.0)",
    )
    return feed.entries


def fetch_api_json(source: dict) -> list[dict]:
    """Fetches a paginated JSON job API (arbeitnow-style)."""
    raw_params = dict(source.get("params", {}) or {})
    pages = raw_params.pop("pages", 1)
    # job_types list → repeated job_types[] params
    job_types = raw_params.pop("job_types", [])
    all_jobs: list[dict] = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; stage-bot/1.0)"}
    for page in range(1, pages + 1):
        query: list[tuple[str, str]] = [(k, str(v)) for k, v in raw_params.items()]
        query += [("job_types[]", jt) for jt in job_types]
        query.append(("page", str(page)))
        resp = requests.get(source["url"], params=query, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        jobs = data.get("data", [])
        if not jobs:
            break
        all_jobs.extend(jobs)
    return all_jobs


def normalize_api_json(job: dict, source: dict) -> dict:
    """Normalizes an arbeitnow API job record to our offer format."""
    url = job.get("url", "")
    tags = job.get("tags", [])
    return {
        "id": make_offer_id(url),
        "title": job.get("title", "").strip(),
        "company": job.get("company_name", ""),
        "location": job.get("location", "") or source.get("country", ""),
        "description": job.get("description", ""),
        "url": url,
        "date_published": datetime.fromtimestamp(job["created_at"]).strftime("%Y-%m-%d")
        if isinstance(job.get("created_at"), (int, float))
        else str(job.get("created_at", date.today()))[:10],
        "date_scraped": str(date.today()),
        "source": source.get("name", ""),
        "country": source.get("country", ""),
        "tags": tags,
        "remote": job.get("remote", False),
    }


def normalize_offer(entry, source: dict) -> dict:
    url = entry.get("link", "")
    title = entry.get("title", "").strip()

    # Entreprise : champ variable selon le board (RemoteOK met le nom dans author)
    company = (
        entry.get("author", "")
        or getattr(entry.get("source"), "title", "")  # type: ignore[union-attr]
        or ""
    )

    # Lieu : rarement dans le RSS, on prend le pays de la source comme fallback
    tags = entry.get("tags", [])
    tag_label = tags[0].get("label", "") if tags else ""
    location = entry.get("location", "") or tag_label or source.get("country", "")

    description = entry.get("summary", "") or entry.get("description", "")

    raw_date = entry.get("published", "") or entry.get("updated", "")
    try:
        parsed_date = datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d")
    except (AttributeError, TypeError):
        parsed_date = str(date.today())

    return {
        "id": make_offer_id(url),
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "url": url,
        "date_published": parsed_date,
        "date_scraped": str(date.today()),
        "source": source.get("name", ""),
        "country": source.get("country", ""),
    }


def save_offer(offer: dict, data_dir: str) -> None:
    offers_dir = os.path.join(data_dir, "offers")
    os.makedirs(offers_dir, exist_ok=True)
    path = os.path.join(offers_dir, f"{offer['id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(offer, f, ensure_ascii=False, indent=2)


def scrape_all(config_dir: str = "config", data_dir: str = "data") -> list[dict]:
    sources = load_sources(config_dir)
    seen = load_seen_offers(data_dir)
    new_offers = []

    for source in sources:
        print(f"  Scraping : {source['name']}...")
        source_type = source.get("type", "rss")
        try:
            if source_type == "api_json":
                raw_jobs = fetch_api_json(source)
                candidates = [
                    (normalize_api_json(j, source), j.get("url", ""))
                    for j in raw_jobs
                ]
            else:
                entries = parse_feed(source)
                candidates = [
                    (normalize_offer(e, source), e.get("link", ""))
                    for e in entries
                ]
        except Exception as e:
            print(f"    Erreur sur {source['name']} : {e}")
            continue

        for offer, url in candidates:
            if not url:
                continue
            offer_id = make_offer_id(url)
            if offer_id in seen:
                continue

            save_offer(offer, data_dir)
            seen[offer_id] = offer["date_scraped"]
            new_offers.append(offer)

    save_seen_offers(data_dir, seen)
    return new_offers


if __name__ == "__main__":
    print("Démarrage du scraper...")
    offers = scrape_all()
    print(f"\n{len(offers)} nouvelle(s) offre(s) récupérée(s).")
    for o in offers[:5]:
        print(f"  [{o['country']}] {o['title']} — {o['company']} ({o['source']})")
    if len(offers) > 5:
        print(f"  ... et {len(offers) - 5} autre(s).")
