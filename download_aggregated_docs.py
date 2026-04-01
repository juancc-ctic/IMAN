#!/usr/bin/env python3
"""
Download PDFs from the Plataformas Agregadas Sin Menores ATOM feed.

Downloads PDFs under <cac:TechnicalDocumentReference> and <cac:LegalDocumentReference>
for entries where <cbc-place-ext:ContractFolderStatusCode> is PRE, PUB or EV,
<cbc:TypeCode> is 2, and <cbc:SubTypeCode> is one of 5, 8, 9, 11, 12, 20, 23, 24,
25 or 27.
Document URIs may point to external platforms (e.g. contractaciopublica.cat,
contratos-publicos.comunidad.madrid), not only contrataciondelestado.es.

Usage:
  python download_aggregated_docs.py <path_or_url> [--try N] [--output DIR] [--json-out]
  python download_aggregated_docs.py PlataformasAgregadasSinMenores_20260206_040054_1.atom
  python download_aggregated_docs.py 'https://example.org/feed.atom'
  python download_aggregated_docs.py feed.atom --cutoff-date 2026-03-01

With --cutoff-date, follows each feed's Atom rel="next" link until a feed's
<updated> is strictly before that date (UTC); that file is not processed.
Without --cutoff-date, only the initial file or URL is used (no chain).
"""

import argparse
import io
import re
import sys
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Tuple
from urllib.parse import urljoin

try:
    import requests
except ImportError:
    print("Install requests: pip install requests", file=sys.stderr)
    sys.exit(1)

ATOM_NS = "http://www.w3.org/2005/Atom"
CBC_NS = "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2"
CAC_NS = "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2"

# Browser-like headers; sometimes servlets fail when Referer/session is missing
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
    "Referer": "https://contrataciondelestado.es/",
}

FEED_HEADERS = {
    **HEADERS,
    "Accept": "application/atom+xml, application/xml, text/xml, */*",
}

FEED_FETCH_TIMEOUT_SEC = 120


def parse_atom_datetime(text: str) -> datetime:
    """Parse Atom RFC3339-style timestamps to timezone-aware datetime (UTC)."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty datetime string")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_cutoff_datetime(value: str) -> datetime:
    """Parse --cutoff-date: YYYY-MM-DD (UTC midnight) or full ISO-8601 datetime."""
    s = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        y, m, d = (int(x) for x in s.split("-"))
        return datetime(y, m, d, tzinfo=timezone.utc)
    return parse_atom_datetime(s)


def get_feed_updated_utc(root) -> Optional[datetime]:
    """Return feed-level <updated> as UTC, or None if missing."""
    u_el = root.find(f"./{{{ATOM_NS}}}updated")
    if u_el is None or not (u_el.text or "").strip():
        return None
    try:
        return parse_atom_datetime(u_el.text.strip())
    except ValueError:
        return None


def get_next_feed_href(root) -> Optional[str]:
    """Return href of the first Atom link with rel=\"next\", if any."""
    for link in root.findall(f".//{{{ATOM_NS}}}link"):
        if link.get("rel") == "next":
            href = link.get("href")
            if href and href.strip():
                return href.strip()
    return None


def resolve_next_feed_source(current: str, href: str) -> str:
    """Resolve next feed URL/path relative to the current feed source."""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if current.startswith("http://") or current.startswith("https://"):
        return urljoin(current, href)
    base = Path(current).resolve().parent
    return str((base / href).resolve())


def iter_feed_documents(
    start_source: str,
    cutoff_utc: Optional[datetime],
) -> Iterator[Tuple[str, ET.Element, Optional[datetime]]]:
    """Walk chained feeds (rel=next) while each feed's <updated> >= cutoff.

    If cutoff_utc is None, only the starting document is yielded (no pagination).

    Yields:
        (source_label, root_element, feed_updated_utc) for each feed whose
        updated is on/after cutoff when pagination is enabled.
        Stops before yielding a feed whose <updated> is strictly before cutoff,
        when there is no next link, or when the next source repeats (cycle guard).

    Args:
        start_source: Local path or http(s) URL of the first atom file.
        cutoff_utc: Inclusive lower bound on feed <updated> in UTC; pagination
            only applies when this is not None.

    Raises:
        Same as load_atom_tree for fetch/parse errors on each hop.
    """
    current = start_source.strip()
    seen: set[str] = set()

    while True:
        key = current
        if current.startswith("http://") or current.startswith("https://"):
            key = current
        else:
            key = str(Path(current).resolve())

        if key in seen:
            print(
                "Stopping: repeated feed URL/path in next chain (cycle).",
                file=sys.stderr,
            )
            break
        seen.add(key)

        tree = load_atom_tree(current)
        root = tree.getroot()
        feed_updated = get_feed_updated_utc(root)

        if cutoff_utc is not None:
            if feed_updated is None:
                print(
                    "Feed has no parseable <updated>; stopping pagination.",
                    file=sys.stderr,
                )
                break
            if feed_updated < cutoff_utc:
                print(
                    f"Stopping chain: feed updated {feed_updated.isoformat()} "
                    f"< cutoff {cutoff_utc.isoformat()} ({current})",
                    file=sys.stderr,
                )
                break

        yield current, root, feed_updated

        if cutoff_utc is None:
            break

        next_href = get_next_feed_href(root)
        if not next_href:
            break
        current = resolve_next_feed_source(current, next_href)


def load_atom_tree(source: str) -> ET.ElementTree:
    """Parse ATOM XML from a local file path or an http(s) URL.

    Args:
        source: Filesystem path, or URL starting with http:// or https://.

    Returns:
        Parsed ElementTree for the ATOM document.

    Raises:
        FileNotFoundError: If source is a path that does not exist.
        requests.HTTPError: If the URL returns an error HTTP status.
        requests.RequestException: On network errors when fetching a URL.
    """
    stripped = source.strip()
    if stripped.startswith("http://") or stripped.startswith("https://"):
        response = requests.get(
            stripped,
            headers=FEED_HEADERS,
            timeout=FEED_FETCH_TIMEOUT_SEC,
        )
        response.raise_for_status()
        return ET.parse(io.BytesIO(response.content))
    path = Path(stripped)
    if not path.exists():
        raise FileNotFoundError(str(path))
    return ET.parse(path)


def get_document_url_from_uri_text(uri_text: str) -> str:
    """Decode XML entity &amp; to & so the URL is valid."""
    if not uri_text:
        return ""
    return uri_text.strip().replace("&amp;", "&")


ALLOWED_CONTRACT_FOLDER_STATUSES = frozenset({"PRE", "PUB", "EV"})

ALLOWED_TYPE_CODE = "2"

ALLOWED_SUBTYPE_CODES = frozenset(
    {"5", "8", "9", "11", "12", "20", "23", "24", "25", "27"}
)


def _xml_local_name(tag: str) -> str:
    """Local name of a Clark notation tag, e.g. '{uri}TypeCode' -> 'TypeCode'."""
    if not tag:
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _normalize_cbc_code_text(elem) -> str:
    """Return stripped element text; normalize plain integers to no leading zeros."""
    raw = (elem.text or "").strip()
    if raw.isdigit():
        return str(int(raw))
    return raw


def entry_has_allowed_type_and_subtype(entry_el) -> bool:
    """True if entry has <cbc:TypeCode> 2 and <cbc:SubTypeCode> in ALLOWED_SUBTYPE_CODES."""
    has_type = False
    has_subtype = False
    for elem in entry_el.iter():
        local = _xml_local_name(elem.tag or "")
        if local == "SubTypeCode":
            val = _normalize_cbc_code_text(elem)
            if val in ALLOWED_SUBTYPE_CODES:
                has_subtype = True
        elif local == "TypeCode":
            val = _normalize_cbc_code_text(elem)
            if val == ALLOWED_TYPE_CODE:
                has_type = True
    return has_type and has_subtype


def entry_has_allowed_contract_folder_status(entry_el) -> bool:
    """True if entry has <cbc-place-ext:ContractFolderStatusCode> in PRE, PUB or EV."""
    for elem in entry_el.iter():
        tag = elem.tag or ""
        if "ContractFolderStatusCode" not in tag:
            continue
        status = (elem.text or "").strip()
        if status in ALLOWED_CONTRACT_FOLDER_STATUSES:
            return True
    return False


def _extract_docs_from_ref_element(elem) -> Optional[Tuple[str, str]]:
    """From a Reference element (Technical or Legal), return (document_name, url) or None."""
    name_el = None
    uri_el = None
    for child in elem:
        ctag = child.tag or ""
        if "ID" in ctag:
            name_el = child
        if "Attachment" in ctag:
            for sub in child.iter():
                if "URI" in (sub.tag or ""):
                    uri_el = sub
                    break
    if uri_el is None or not uri_el.text:
        return None
    raw_uri = (uri_el.text or "").strip()
    if not (raw_uri.startswith("http://") or raw_uri.startswith("https://")):
        return None
    url = get_document_url_from_uri_text(uri_el.text)
    name = (name_el.text or "document.pdf").strip() if name_el is not None else "document.pdf"
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    if not name.lower().endswith(".pdf"):
        name = name + ".pdf"
    return (name, url)


def extract_technical_documents_from_entry(entry_el) -> List[Tuple[str, str]]:
    """Return list of (output_filename, url) for PDFs: Legal -> PCAP.pdf, Technical -> PPT.pdf."""
    results = []
    for elem in entry_el.iter():
        tag = elem.tag or ""
        if "TechnicalDocumentReference" not in tag and "LegalDocumentReference" not in tag:
            continue
        doc = _extract_docs_from_ref_element(elem)
        if doc is None:
            continue
        _original_name, url = doc
        output_name = "PCAP.pdf" if "LegalDocumentReference" in tag else "PPT.pdf"
        results.append((output_name, url))
    return results


def get_entry_folder_name(entry_el) -> str:
    """Return the segment after the last '/' in the entry's <id>, for use as folder name."""
    for child in entry_el:
        tag = (child.tag or "").lower()
        if "id" in tag and child.text:
            raw = (child.text or "").strip()
            if "/" in raw:
                return raw.rsplit("/", 1)[-1].strip() or "unknown"
            return raw or "unknown"
    return "unknown"


def get_entry_detail_link(entry_el) -> Optional[str]:
    """Get the tender detail link: detalle_licitacion if present, else first link (e.g. external platform)."""
    for link in entry_el.findall(".//{http://www.w3.org/2005/Atom}link"):
        href = link.get("href")
        if not href:
            continue
        if "detalle_licitacion" in href:
            return get_document_url_from_uri_text(href)
    for link in entry_el.findall(".//{http://www.w3.org/2005/Atom}link"):
        href = link.get("href")
        if href and href.strip().startswith("http"):
            return get_document_url_from_uri_text(href)
    return None


def try_download(url: str, dest_path: Path, timeout: int = 30) -> Tuple[bool, str]:
    """Attempt download; return (success, message)."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, stream=True)
        if r.status_code == 200:
            content_type = (r.headers.get("Content-Type") or "").lower()
            if "pdf" in content_type or len(r.content) > 100:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(r.content)
                return True, "OK"
            return False, f"Unexpected Content-Type: {r.headers.get('Content-Type')}"
        if r.status_code == 500:
            return False, "500 (server error: NullPointerException on platform)"
        return False, f"HTTP {r.status_code}"
    except requests.RequestException as e:
        return False, str(e)
    
def extract_tender_data(entry_el) -> dict:
    """Extrae los campos requeridos de la entrada XML para el JSON."""
    ATOM = f"{{{ATOM_NS}}}"
    CAC = f"{{{CAC_NS}}}"
    CBC = f"{{{CBC_NS}}}"

    data = {
        "id": None,
        "link": get_entry_detail_link(entry_el), 
        "title": None,
        "party_name": None,
        "tax_exclusive_amount": None,
        "estimated_overall_contract_amount": None
    }

    # <id>
    id_el = entry_el.find(f".//{ATOM}id")
    if id_el is not None:
        data["id"] = (id_el.text or "").strip()

    # <title>
    title_el = entry_el.find(f".//{ATOM}title")
    if title_el is not None:
        data["title"] = (title_el.text or "").strip()

    # <cac:PartyName> -> <cbc:Name>
    party_name_el = entry_el.find(f".//{CAC}PartyName/{CBC}Name")
    if party_name_el is not None:
        data["party_name"] = (party_name_el.text or "").strip()

    # <cbc:TaxExclusiveAmount>
    tax_excl_el = entry_el.find(f".//{CBC}TaxExclusiveAmount")
    if tax_excl_el is not None:
        data["tax_exclusive_amount"] = (tax_excl_el.text or "").strip()

    # <cbc:EstimatedOverallContractAmount>
    est_amount_el = entry_el.find(f".//{CBC}EstimatedOverallContractAmount")
    if est_amount_el is not None:
        data["estimated_overall_contract_amount"] = (est_amount_el.text or "").strip()

    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n")[1])
    parser.add_argument(
        "atom_source",
        metavar="ATOM",
        help="Local path to .atom file or http(s) URL of the feed",
    )
    parser.add_argument(
        "--try",
        dest="max_tries",
        type=int,
        default=5,
        metavar="N",
        help="Try first N documents only (default 5). Use 0 for all.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("downloads"),
        help="Output directory for PDFs (default: downloads)",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Only print document list and detail links, do not download.",
    )

    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("licitaciones_extraidas.json"),
        help="JSON output file (default: licitaciones_extraidas.json)",
    )
    parser.add_argument(
        "--cutoff-date",
        type=parse_cutoff_datetime,
        default=None,
        metavar="DATE",
        help=(
            "UTC cutoff: follow rel=next until a feed <updated> is before this. "
            "Use YYYY-MM-DD or ISO-8601 datetime. Enables chained atom files."
        ),
    )
    args = parser.parse_args()

    cutoff_utc: Optional[datetime] = args.cutoff_date
    if cutoff_utc is not None and cutoff_utc.tzinfo is None:
        cutoff_utc = cutoff_utc.replace(tzinfo=timezone.utc)
    elif cutoff_utc is not None:
        cutoff_utc = cutoff_utc.astimezone(timezone.utc)

    total = 0
    ok = 0
    failed_with_detail: List[Tuple[str, str]] = []

    tenders_data: List[dict] = []

    try:
        for feed_source, root, feed_updated in iter_feed_documents(
            args.atom_source,
            cutoff_utc,
        ):
            if cutoff_utc is not None:
                updated_s = feed_updated.isoformat() if feed_updated else "?"
                print(f"Using feed {feed_source} (updated {updated_s})")

            entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
            if not entries:
                entries = list(root)

            hit_limit = False
            for entry in entries:
                if not entry_has_allowed_contract_folder_status(entry):
                    continue
                if not entry_has_allowed_type_and_subtype(entry):
                    continue

                tender_info = extract_tender_data(entry)
                tenders_data.append(tender_info)

                folder_name = get_entry_folder_name(entry)
                entry_dir = args.output / folder_name
                detail_url = get_entry_detail_link(entry)
                docs = extract_technical_documents_from_entry(entry)
                for name, url in docs:
                    if not url:
                        continue
                    if args.max_tries and total >= args.max_tries:
                        hit_limit = True
                        break
                    total += 1
                    dest = entry_dir / name
                    if args.no_download:
                        print(f"  [{folder_name}] {name}\n    {url}")
                        if detail_url:
                            print(f"    Tender page: {detail_url}")
                        continue
                    success, msg = try_download(url, dest)
                    if success:
                        ok += 1
                        print(f"[OK] {name} -> {dest}")
                    else:
                        print(f"[FAIL] {name}: {msg}")
                        if detail_url:
                            failed_with_detail.append((name, detail_url))
                if hit_limit:
                    break
            if hit_limit:
                break
    except FileNotFoundError:
        print(f"File not found: {args.atom_source}", file=sys.stderr)
        return 1
    except requests.HTTPError as exc:
        print(f"Failed to fetch feed: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"Network error while fetching feed: {exc}", file=sys.stderr)
        return 1
    except ET.ParseError as exc:
        print(f"Invalid XML in feed: {exc}", file=sys.stderr)
        return 1

    if args.no_download:
        return 0

    if failed_with_detail:
        print("\n--- Documents that failed (500) ---")
        print("Download them from the tender page in your browser:")
        seen_urls = set()
        for name, detail_url in failed_with_detail:
            if detail_url not in seen_urls:
                seen_urls.add(detail_url)
                print(f"  {detail_url}")
        print("\n(You can try opening the tender page in your browser to download documents manually.)")

    print(f"\nDownloaded {ok}/{total} documents.")
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(tenders_data, f, ensure_ascii=False, indent=4)
    print(f"Data exported to JSON in: {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
