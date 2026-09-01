"""
Sous-programme candidature manuelle.
Extrait les données d'une offre depuis un fichier HTML ou une URL,
puis génère directement lettre + CV adapté.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date

import requests
from bs4 import BeautifulSoup

from src.config import PATHS
from src.scraper import make_offer_id
from src.generator import generate

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


# ── Stratégie 1 : __NEXT_DATA__ (Next.js — WTTJ et autres) ─────────────────

def _find_in_dict(d, *keys):
    """Cherche récursivement les premières clés trouvées dans un dict imbriqué."""
    if not isinstance(d, dict):
        return None
    for key in keys:
        if key in d:
            return d[key]
    for v in d.values():
        result = _find_in_dict(v, *keys)
        if result:
            return result
    return None


def _extract_next_data(soup: BeautifulSoup) -> dict | None:
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        data = json.loads(tag.string)
    except json.JSONDecodeError:
        return None

    props = data.get("props", {}).get("pageProps", {})

    # Cherche un objet "job" ou "offer" dans les props
    job = props.get("job") or props.get("offer") or props.get("jobOffer")
    if not job and isinstance(props, dict):
        # Recherche récursive dans les props
        for v in props.values():
            if isinstance(v, dict) and ("name" in v or "title" in v):
                job = v
                break

    if not job:
        return None

    title = job.get("name") or job.get("title") or ""
    company = _find_in_dict(job, "organization", "company", "employer")
    if isinstance(company, dict):
        company = company.get("name", "")
    location = _find_in_dict(job, "office", "location", "jobLocation")
    if isinstance(location, dict):
        location = location.get("city") or location.get("name") or location.get("addressLocality") or ""
    description = job.get("description") or job.get("profile") or ""
    if isinstance(description, dict):
        description = description.get("description") or ""

    if not title and not description:
        return None

    return {
        "title": str(title).strip(),
        "company": str(company).strip() if company else "",
        "location": str(location).strip() if location else "",
        "description": str(description).strip(),
    }


# ── Stratégie 2 : JSON-LD JobPosting ───────────────────────────────────────

def _extract_jsonld_location(job_location) -> str:
    """Extrait un lieu propre depuis un jobLocation JSON-LD (dict OU liste de Place)."""
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else {}
    if not isinstance(job_location, dict):
        return ""
    addr = job_location.get("address") or job_location
    if not isinstance(addr, dict):
        return ""
    city = addr.get("addressLocality") or ""
    country = addr.get("addressCountry") or ""
    if isinstance(country, dict):
        country = country.get("name") or ""
    parts = [p.strip() for p in (city, country) if isinstance(p, str) and p.strip()]
    return ", ".join(parts) or (addr.get("addressRegion") or "")


def _extract_jsonld(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            ld = json.loads(script.string)
        except json.JSONDecodeError:
            continue

        # Peut être un graphe (@graph) ou un objet direct
        items = ld.get("@graph", [ld]) if isinstance(ld, dict) else ld
        if isinstance(items, dict):
            items = [items]

        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") not in ("JobPosting", "job"):
                continue

            title = item.get("title") or item.get("name") or ""
            company_raw = item.get("hiringOrganization") or item.get("employer") or {}
            company = company_raw.get("name", "") if isinstance(company_raw, dict) else str(company_raw)
            location = _extract_jsonld_location(item.get("jobLocation"))
            description = item.get("description") or ""

            if title or description:
                return {
                    "title": str(title).strip(),
                    "company": str(company).strip(),
                    "location": str(location).strip(),
                    "description": str(description).strip(),
                }
    return None


# ── Stratégie 3 : Open Graph meta ──────────────────────────────────────────

def _extract_og(soup: BeautifulSoup) -> dict | None:
    def meta(prop: str) -> str:
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return (tag.get("content") or "") if tag else ""

    title = meta("og:title") or meta("twitter:title")
    description = meta("og:description") or meta("twitter:description")

    if not title and not description:
        return None
    return {
        "title": title.strip(),
        "company": "",
        "location": "",
        "description": description.strip(),
    }


# ── Stratégie 4 : Heuristiques HTML ────────────────────────────────────────

def _first_text(soup, *selectors) -> str:
    for sel in selectors:
        tag = soup.select_one(sel)
        if tag:
            return tag.get_text(strip=True)
    return ""


def _extract_heuristic(soup: BeautifulSoup) -> dict:
    title = (
        _first_text(soup, "[itemprop='title']", "[data-testid*='title']", "h1")
        or (soup.title.string.split("|")[0].split("–")[0].strip() if soup.title else "")
    )
    company = _first_text(
        soup,
        "[itemprop='hiringOrganization']",
        "[class*='company']",
        "[class*='employer']",
        "[class*='organization']",
    )
    location = _first_text(
        soup,
        "[itemprop='jobLocation']",
        "[class*='location']",
        "[class*='city']",
        "[class*='address']",
    )
    desc_tag = (
        soup.select_one("[itemprop='description']")
        or soup.select_one("[class*='description']")
        or soup.select_one("article")
        or soup.select_one("main")
    )
    description = desc_tag.get_text(separator="\n", strip=True) if desc_tag else ""

    return {
        "title": title,
        "company": company,
        "location": location,
        "description": description[:4000],
    }


# ── Extraction principale ───────────────────────────────────────────────────

def extract_offer_data(html: str, source_url: str = "") -> dict:
    """Tries strategies in order, returns best extraction result."""
    soup = BeautifulSoup(html, "html.parser")

    for strategy in (_extract_next_data, _extract_jsonld, _extract_og):
        result = strategy(soup)
        if result and (result.get("title") or result.get("description")):
            return result

    # Always succeeds (may have empty fields)
    return _extract_heuristic(soup)


def fetch_url(url: str) -> str:
    resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


# ── Construction du stub offre ──────────────────────────────────────────────

def build_offer_stub(
    data: dict,
    source_url: str = "",
    title_override: str = "",
    company_override: str = "",
) -> dict:
    url = source_url or ""
    offer_id = make_offer_id(url) if url else hashlib.sha1(
        (data.get("title", "") + data.get("company", "") + str(date.today())).encode()
    ).hexdigest()[:8]

    return {
        "id": offer_id,
        "title": (title_override or data.get("title") or "Offre sans titre").strip(),
        "company": (company_override or data.get("company") or "").strip(),
        "location": (data.get("location") or "").strip(),
        "description": (data.get("description") or "").strip(),
        "url": url,
        "date_published": str(date.today()),
        "date_scraped": str(date.today()),
        "source": "Manuel",
        "country": "",
    }


def save_offer_stub(offer: dict, data_dir: str = PATHS.data_dir) -> str:
    offers_dir = os.path.join(data_dir, "offers")
    os.makedirs(offers_dir, exist_ok=True)
    path = os.path.join(offers_dir, f"{offer['id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(offer, f, ensure_ascii=False, indent=2)
    return path


def _print_extraction_summary(offer: dict) -> None:
    print(f"  Titre    : {offer['title'] or '(non trouvé)'}")
    print(f"  Société  : {offer['company'] or '(non trouvé)'}")
    print(f"  Lieu     : {offer['location'] or '(non trouvé)'}")
    print(f"  Desc.    : {len(offer['description'])} caractères extraits")


# ── Points d'entrée ─────────────────────────────────────────────────────────

def create_stub_from_file(
    path: str,
    url_source: str = "",
    title_override: str = "",
    company_override: str = "",
    data_dir: str = PATHS.data_dir,
) -> dict:
    """Extrait + sauvegarde le stub d'offre depuis un fichier HTML (sans générer la candidature)."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Fichier introuvable : {path}")

    print(f"Lecture du fichier : {path}")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()

    data = extract_offer_data(html, url_source)
    offer = build_offer_stub(data, url_source, title_override, company_override)

    print("Informations extraites :")
    _print_extraction_summary(offer)

    stub_path = save_offer_stub(offer, data_dir)
    print(f"Stub sauvegardé : {stub_path}\n")
    return offer


def create_stub_from_url(
    url: str,
    title_override: str = "",
    company_override: str = "",
    data_dir: str = PATHS.data_dir,
) -> dict:
    """Télécharge + extrait + sauvegarde le stub d'offre depuis une URL (sans générer la candidature)."""
    print(f"Téléchargement : {url}")
    html = fetch_url(url)

    data = extract_offer_data(html, url)
    offer = build_offer_stub(data, url, title_override, company_override)

    print("Informations extraites :")
    _print_extraction_summary(offer)

    stub_path = save_offer_stub(offer, data_dir)
    print(f"Stub sauvegardé : {stub_path}\n")
    return offer


def from_file(
    path: str,
    url_source: str = "",
    title_override: str = "",
    company_override: str = "",
    data_dir: str = PATHS.data_dir,
    config_dir: str = PATHS.config_dir,
    cv_dir: str = PATHS.cv_dir,
    output_dir: str = PATHS.output_dir,
) -> str:
    try:
        offer = create_stub_from_file(path, url_source, title_override, company_override, data_dir)
    except FileNotFoundError as e:
        print(f"Erreur : {e}", file=sys.stderr)
        sys.exit(1)

    return generate(offer["id"], config_dir, data_dir, cv_dir, output_dir)


def from_url(
    url: str,
    title_override: str = "",
    company_override: str = "",
    data_dir: str = PATHS.data_dir,
    config_dir: str = PATHS.config_dir,
    cv_dir: str = PATHS.cv_dir,
    output_dir: str = PATHS.output_dir,
) -> str:
    try:
        offer = create_stub_from_url(url, title_override, company_override, data_dir)
    except Exception as e:
        print(f"Erreur lors du téléchargement : {e}", file=sys.stderr)
        sys.exit(1)

    return generate(offer["id"], config_dir, data_dir, cv_dir, output_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Génère une candidature depuis une page web")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", metavar="PATH", help="Fichier HTML sauvegardé depuis le navigateur")
    group.add_argument("--url", metavar="URL", help="URL de l'offre à télécharger")
    parser.add_argument("--url-source", default="", help="URL source (avec --file)")
    parser.add_argument("--title", default="", help="Surcharge le titre extrait")
    parser.add_argument("--company", default="", help="Surcharge l'entreprise extraite")
    args = parser.parse_args()

    if args.file:
        folder = from_file(args.file, args.url_source, args.title, args.company)
    else:
        folder = from_url(args.url, args.title, args.company)

    print(f"\nCandidature prête dans : {folder}")
    print(f"  - {folder}/lettre.md")
    print(f"  - {folder}/cv_adapte.md")
