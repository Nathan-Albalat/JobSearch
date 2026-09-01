import difflib
import functools
import hashlib
import json
import os
import re
import time
from datetime import date, datetime

import feedparser
import requests
import yaml
from dotenv import load_dotenv

from jobsearch.config import PATHS, get_search


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


def fetch_adzuna(source: dict) -> list[dict]:
    """Interroge l'API Adzuna (clés ADZUNA_APP_ID / ADZUNA_APP_KEY dans .env)."""
    load_dotenv(PATHS.env_path)
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        print("    ADZUNA_APP_ID / ADZUNA_APP_KEY manquants dans .env — source ignorée.")
        return []

    defaults = get_search().source_defaults("adzuna")
    params = {**defaults, **(source.get("params", {}) or {})}
    country = params.pop("country", "nl")
    pages = params.pop("pages", 1)
    results = []
    for page in range(1, pages + 1):
        query = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": params.get("results_per_page", 50),
            "what": params.get("what", "internship software"),
            "content-type": "application/json",
        }
        if params.get("contract_type"):
            query[params["contract_type"]] = "1"  # ex: contract_type: full_time
        resp = requests.get(
            f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}",
            params=query,
            timeout=15,
        )
        resp.raise_for_status()
        jobs = resp.json().get("results", [])
        if not jobs:
            break
        results.extend(jobs)
    return results


def normalize_adzuna(job: dict, source: dict) -> dict:
    url = job.get("redirect_url", "")
    return {
        "id": make_offer_id(url),
        "title": job.get("title", "").strip(),
        "company": (job.get("company") or {}).get("display_name", ""),
        "location": (job.get("location") or {}).get("display_name", "") or source.get("country", ""),
        "description": job.get("description", ""),
        "url": url,
        "date_published": str(job.get("created", ""))[:10] or str(date.today()),
        "date_scraped": str(date.today()),
        "source": source.get("name", ""),
        "country": source.get("country", ""),
        "tags": [],
        "remote": False,
    }


_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def fetch_hellowork(source: dict) -> list[dict]:
    """Scrape HelloWork : liste + fetch détail par offre pour le JSON-LD JobPosting.

    Contrat stable : le JSON-LD `schema.org/JobPosting` embarqué dans chaque page
    détail. Ne jamais basculer sur des sélecteurs CSS — ils cassent à chaque
    refonte front (voir CLAUDE.md).
    """
    defaults = get_search().source_defaults("hellowork")
    params = {**defaults, **(source.get("params", {}) or {})}
    pages = params.pop("pages", 2)
    delay = params.pop("delay", 0.3)

    headers = {"User-Agent": _BROWSER_UA}
    links: list[str] = []
    for page in range(1, pages + 1):
        query = {**params, "p": page}
        resp = requests.get(
            "https://www.hellowork.com/fr-fr/emploi/recherche.html",
            params=query,
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        page_links = re.findall(r'href="(/fr-fr/emplois/\d+\.html)"', resp.text)
        if not page_links:
            break
        links.extend(page_links)

    links = list(dict.fromkeys(links))

    jobs: list[dict] = []
    for link in links:
        url = f"https://www.hellowork.com{link}"
        try:
            time.sleep(delay)
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            job = None
            for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', resp.text, re.S):
                try:
                    data = json.loads(block.strip())
                except json.JSONDecodeError:
                    continue
                if data.get("@type") == "JobPosting":
                    job = data
                    break
            if job is None:
                continue
            job["url"] = url
            jobs.append(job)
        except Exception as e:
            print(f"    Erreur sur l'offre HelloWork {url} : {e}")
            continue

    return jobs


def normalize_hellowork(job: dict, source: dict) -> dict:
    """Normalise un JSON-LD JobPosting HelloWork au format offre du pipeline."""
    url = job.get("url", "")
    org = job.get("hiringOrganization") or {}
    company = org.get("name", "") if isinstance(org, dict) else ""

    job_location = job.get("jobLocation")
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else {}
    job_location = job_location or {}
    address = job_location.get("address") or {}
    locality = address.get("addressLocality") or address.get("addressRegion") or source.get("country", "")
    country = address.get("addressCountry", "")
    location = f"{locality}, {country}" if country else str(locality)

    tags = job.get("skills", [])
    if isinstance(tags, str):
        tags = [tags]

    date_published = str(job.get("datePosted", ""))[:10] or str(date.today())

    return {
        "id": make_offer_id(url),
        "title": (job.get("title", "") or "").strip(),
        "company": company,
        "location": location,
        "description": job.get("description", ""),
        "url": url,
        "date_published": date_published,
        "date_scraped": str(date.today()),
        "source": source.get("name", ""),
        "country": source.get("country", "FR"),
        "tags": tags or [],
        "remote": False,
    }


def fetch_wttj(source: dict) -> list[dict]:
    """Scrape Welcome to the Jungle : liste (API publique v3, sans auth) + fetch détail.

    Pièges vérifiés (voir CLAUDE.md) :
    - l'API répète chaque offre une fois par bureau de l'entreprise → dédup sur
      `(organization.slug, name)`, jamais sur `reference`/`slug`, AVANT la boucle détail
    - `per_page` est verrouillé à 10 côté serveur, `contract_type`/`query`/`per_page`
      sont refusés en query (422) → `contract_types` est filtré côté client
    """
    defaults = get_search().source_defaults("wttj")
    params = {**defaults, **(source.get("params", {}) or {})}
    pages = params.pop("pages", 3)
    delay = params.pop("delay", 0.3)
    contract_types = [c.lower() for c in (params.pop("contract_types", []) or [])]
    max_offers = params.pop("max_offers", 0)
    locations = params.pop("locations", None)
    if locations is not None:
        if isinstance(locations, dict):
            locations = [locations]
        params["locations"] = json.dumps(locations, ensure_ascii=False)

    headers = {"User-Agent": _BROWSER_UA}
    raw: list[dict] = []
    page_count = None
    for page in range(1, pages + 1):
        query = {**params, "page": page}
        try:
            resp = requests.get(
                "https://api.welcometothejungle.com/api/v3/public/jobs",
                params=query,
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 422:
                print(f"    WTTJ 422 : {e.response.text[:200]}")
            raise
        payload = resp.json()
        data = payload.get("data") or []
        if page_count is None:
            page_count = payload.get("metadata", {}).get("page_count", pages)
        if not data:
            break
        raw.extend(data)
        if page > page_count:
            break

    seen_keys: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for job in raw:
        status = job.get("status")
        if status not in (None, "published"):
            continue
        contract_type = str(job.get("contract_type") or "").lower()
        if contract_types and contract_type not in contract_types:
            continue
        org = job.get("organization") or {}
        org_slug = org.get("slug")
        slug = job.get("slug")
        if not org_slug or not slug:
            continue
        name = (job.get("name") or "").strip().lower()
        key = (org_slug, name)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique.append(job)
        if max_offers and len(unique) >= max_offers:
            break

    print(
        f"    WTTJ : {len(raw)} résultat(s) bruts → {len(unique)} offre(s) uniques (dédup bureaux)."
    )

    jobs: list[dict] = []
    for job in unique:
        org = job.get("organization") or {}
        org_slug = org.get("slug")
        slug = job.get("slug")
        url = f"https://www.welcometothejungle.com/fr/companies/{org_slug}/jobs/{slug}"
        try:
            time.sleep(delay)
            resp = requests.get(
                f"https://api.welcometothejungle.com/api/v3/organizations/{org_slug}/jobs/{slug}",
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            detail = resp.json().get("job") or {}
            merged = dict(job)
            for k, v in detail.items():
                if v not in (None, "", []):
                    merged[k] = v
            merged["url"] = url
            jobs.append(merged)
        except Exception as e:
            print(f"    Erreur détail WTTJ {url} : {e} — offre conservée avec les données de liste.")
            job["url"] = url
            jobs.append(job)

    return jobs


def _wttj_text_list(value) -> list[str]:
    """Tolère une liste d'objets `{"name": ...}` ou de chaînes.

    `name` peut lui-même être un dict multilingue (`{"fr": ..., "en": ...}`)
    plutôt qu'une chaîne — on privilégie le français, puis l'anglais.
    """
    result = []
    for x in value or []:
        if isinstance(x, str):
            result.append(x)
        elif isinstance(x, dict) and x.get("name"):
            name = x["name"]
            if isinstance(name, dict):
                name = name.get("fr") or name.get("en") or next(iter(name.values()), "")
            if name:
                result.append(name)
    return result


def normalize_wttj(job: dict, source: dict) -> dict:
    """Normalise une offre WTTJ (liste + détail fusionnés) au format offre du pipeline."""
    url = job.get("url", "")
    org = job.get("organization") or {}
    company = org.get("name", "")

    office = job.get("office") or {}
    offices = job.get("offices") or []
    if not office and offices:
        office = offices[0] or {}
    city = office.get("city") or ""
    country_code = office.get("country_code") or ""
    if not city:
        # `office.city` peut exister avec la valeur None : on retente sur les
        # autres bureaux avant de retomber sur le pays de la source.
        for other in offices:
            other = other or {}
            if other.get("city"):
                city = other["city"]
                country_code = country_code or (other.get("country_code") or "")
                break
    if city and country_code:
        location = f"{city}, {country_code}"
    else:
        location = city or country_code or source.get("country", "")

    remote_raw = job.get("remote")
    remote = bool(remote_raw) and str(remote_raw).lower() not in ("no", "none", "false", "0", "unknown")

    duration_min = job.get("contract_duration_min")
    duration_max = job.get("contract_duration_max")
    start_date = str(job.get("start_date") or "")[:10]

    header_bits = []
    if duration_min or duration_max:
        if duration_min and duration_max and duration_min != duration_max:
            header_bits.append(f"Durée : {duration_min}-{duration_max} mois")
        else:
            header_bits.append(f"Durée : {duration_min or duration_max} mois")
    if start_date:
        header_bits.append(f"Début : {start_date}")

    desc_parts = []
    if header_bits:
        desc_parts.append(f"<p><strong>{' — '.join(header_bits)}</strong></p>")
    for field in ("description",):
        if job.get(field):
            desc_parts.append(f"<h3>{field}</h3>" + job[field])
    profile_text = job.get("profile") or job.get("looking_for_candidate_description")
    if profile_text:
        desc_parts.append("<h3>profile</h3>" + profile_text)
    if job.get("recruitment_process"):
        desc_parts.append("<h3>recruitment_process</h3>" + job["recruitment_process"])
    if job.get("company_description"):
        desc_parts.append("<h3>company_description</h3>" + job["company_description"])
    description = "\n".join(desc_parts)

    date_published = str(job.get("published_at") or "")[:10] or str(job.get("updated_at") or "")[:10] or str(date.today())

    tags: list[str] = []
    if duration_min or duration_max:
        if duration_min and duration_max and duration_min != duration_max:
            tags.append(f"durée: {duration_min}-{duration_max} mois")
        else:
            tags.append(f"durée: {duration_min or duration_max} mois")
    if start_date:
        tags.append(f"début: {start_date}")
    tags.extend(_wttj_text_list(job.get("skills")))
    tags.extend(_wttj_text_list(job.get("tools")))
    tags = list(dict.fromkeys(tags))[:20]

    return {
        "id": make_offer_id(url),
        "title": (job.get("name", "") or "").strip(),
        "company": company,
        "location": location,
        "description": description,
        "url": url,
        "date_published": date_published,
        "date_scraped": str(date.today()),
        "source": source.get("name", ""),
        "country": source.get("country", ""),
        "tags": tags,
        "remote": remote,
    }


# Mots parasites retirés des titres avant comparaison fuzzy inter-sources
@functools.lru_cache(maxsize=4)
def _title_noise_re(noise_words: tuple, extra_regex: str) -> re.Pattern:
    alternation = "|".join(re.escape(w) for w in noise_words)
    if extra_regex:
        alternation = f"{alternation}|{extra_regex}" if alternation else extra_regex
    return re.compile(rf"\b({alternation})\b|[^\w\s]", re.IGNORECASE)


def _normalize_for_dedup(text: str) -> str:
    search = get_search()
    pattern = _title_noise_re(search.dedup_title_noise, search.dedup_extra_regex)
    return " ".join(pattern.sub(" ", text.lower()).split())


def load_existing_dedup_keys(data_dir: str) -> list[tuple[str, str]]:
    """Retourne les clés (entreprise, titre) normalisées des offres déjà stockées."""
    offers_dir = os.path.join(data_dir, "offers")
    keys = []
    if not os.path.isdir(offers_dir):
        return keys
    for filename in os.listdir(offers_dir):
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(offers_dir, filename), encoding="utf-8") as f:
                o = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        keys.append((_normalize_for_dedup(o.get("company", "")), _normalize_for_dedup(o.get("title", ""))))
    return keys


def is_cross_source_duplicate(offer: dict, keys: list[tuple[str, str]]) -> bool:
    """Détecte la même offre arrivée via deux boards (entreprise identique + titre ~identique)."""
    company = _normalize_for_dedup(offer.get("company", ""))
    title = _normalize_for_dedup(offer.get("title", ""))
    if not company or not title:
        return False
    similarity = get_search().dedup_similarity
    for k_company, k_title in keys:
        if k_company != company or not k_title:
            continue
        if difflib.SequenceMatcher(None, title, k_title).ratio() >= similarity:
            return True
    return False


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


def scrape_all(
    config_dir: str = PATHS.config_dir,
    data_dir: str = PATHS.data_dir,
    include_groups: set[str] | None = None,
) -> list[dict]:
    sources = load_sources(config_dir)
    seen = load_seen_offers(data_dir)
    dedup_keys = load_existing_dedup_keys(data_dir)
    new_offers = []

    groups = set(include_groups) if include_groups is not None else set(get_search().enabled_groups)

    for source in sources:
        if source.get("optional") and source.get("group") not in groups:
            print(
                f"  Ignorée : {source.get('name', '?')} — groupe '{source.get('group')}' non activé "
                f"(--with {source.get('group')} pour l'inclure)."
            )
            continue

        print(f"  Scraping : {source.get('name', '?')}...")
        source_type = source.get("type", "rss")
        try:
            if source_type == "api_json":
                raw_jobs = fetch_api_json(source)
                candidates = [
                    (normalize_api_json(j, source), j.get("url", ""))
                    for j in raw_jobs
                ]
            elif source_type == "jobspy":
                from jobsearch.scraper_jobspy import fetch_jobspy
                candidates = [(o, o["url"]) for o in fetch_jobspy(source)]
            elif source_type == "adzuna":
                raw_jobs = fetch_adzuna(source)
                candidates = [
                    (normalize_adzuna(j, source), j.get("redirect_url", ""))
                    for j in raw_jobs
                ]
            elif source_type == "hellowork":
                raw_jobs = fetch_hellowork(source)
                candidates = [
                    (normalize_hellowork(j, source), j.get("url", ""))
                    for j in raw_jobs
                ]
            elif source_type == "wttj":
                raw_jobs = fetch_wttj(source)
                candidates = [(normalize_wttj(j, source), j.get("url", "")) for j in raw_jobs]
            else:
                entries = parse_feed(source)
                candidates = [
                    (normalize_offer(e, source), e.get("link", ""))
                    for e in entries
                ]
        except Exception as e:
            print(f"    Erreur sur {source.get('name', '?')} : {e}")
            continue

        for offer, url in candidates:
            if not url:
                continue
            offer_id = make_offer_id(url)
            if offer_id in seen:
                continue
            if is_cross_source_duplicate(offer, dedup_keys):
                print(f"    Doublon inter-sources ignoré : {offer.get('title', '')[:60]} — {offer.get('company', '')}")
                seen[offer_id] = offer["date_scraped"]
                continue

            save_offer(offer, data_dir)
            seen[offer_id] = offer["date_scraped"]
            dedup_keys.append(
                (_normalize_for_dedup(offer.get("company", "")), _normalize_for_dedup(offer.get("title", "")))
            )
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
