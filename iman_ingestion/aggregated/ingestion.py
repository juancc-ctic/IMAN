"""Download PDFs and tender JSON from Plataformas Agregadas Sin Menores ATOM feeds."""

from __future__ import annotations

import io
import json
import logging
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

ATOM_NS = "http://www.w3.org/2005/Atom"
CBC_NS = "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2"
CAC_NS = "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2"

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

ALLOWED_CONTRACT_FOLDER_STATUSES = frozenset({"PRE", "PUB", "EV"})
ALLOWED_TYPE_CODE = "2"
ALLOWED_SUBTYPE_CODES = frozenset(
    {"5", "8", "9", "11", "12", "20", "23", "24", "25", "27"}
)


@dataclass
class IngestionConfig:
    """Parameters for a single ingestion run."""

    atom_source: str
    output_dir: Path
    json_out: Path
    cutoff_utc: Optional[datetime] = None
    max_tries: int = 5
    no_download: bool = False


@dataclass
class IngestionResult:
    """Outcome of :func:`run_ingestion`."""

    tenders_data: List[Dict[str, Any]]
    total: int
    ok: int
    failed_with_detail: List[Tuple[str, str]]
    json_out: Optional[Path] = None


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
    """Parse cutoff: YYYY-MM-DD (UTC midnight) or full ISO-8601 datetime."""
    s = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        y, m, d = (int(x) for x in s.split("-"))
        return datetime(y, m, d, tzinfo=timezone.utc)
    return parse_atom_datetime(s)


def get_feed_updated_utc(root: ET.Element) -> Optional[datetime]:
    """Return feed-level <updated> as UTC, or None if missing."""
    u_el = root.find(f"./{{{ATOM_NS}}}updated")
    if u_el is None or not (u_el.text or "").strip():
        return None
    try:
        return parse_atom_datetime(u_el.text.strip())
    except ValueError:
        return None


def get_next_feed_href(root: ET.Element) -> Optional[str]:
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
    """Walk chained feeds (rel=next) while each feed's <updated> >= cutoff."""
    current = start_source.strip()
    seen: set[str] = set()

    while True:
        key = current
        if current.startswith("http://") or current.startswith("https://"):
            key = current
        else:
            key = str(Path(current).resolve())

        if key in seen:
            logger.warning("Stopping: repeated feed URL/path in next chain (cycle).")
            break
        seen.add(key)

        tree = load_atom_tree(current)
        root = tree.getroot()
        feed_updated = get_feed_updated_utc(root)

        if cutoff_utc is not None:
            if feed_updated is None:
                logger.warning("Feed has no parseable <updated>; stopping pagination.")
                break
            if feed_updated < cutoff_utc:
                logger.info(
                    "Stopping chain: feed updated %s < cutoff %s (%s)",
                    feed_updated.isoformat(),
                    cutoff_utc.isoformat(),
                    current,
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
    """Parse ATOM XML from a local file path or an http(s) URL."""
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


def _xml_local_name(tag: str) -> str:
    """Local name of a Clark notation tag."""
    if not tag:
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _normalize_cbc_code_text(elem: ET.Element) -> str:
    """Return stripped element text; normalize plain integers to no leading zeros."""
    raw = (elem.text or "").strip()
    if raw.isdigit():
        return str(int(raw))
    return raw


def entry_has_allowed_type_and_subtype(entry_el: ET.Element) -> bool:
    """True if entry has TypeCode 2 and SubTypeCode in allowed set."""
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


def entry_has_allowed_contract_folder_status(entry_el: ET.Element) -> bool:
    """True if ContractFolderStatusCode is PRE, PUB or EV."""
    for elem in entry_el.iter():
        tag = elem.tag or ""
        if "ContractFolderStatusCode" not in tag:
            continue
        status = (elem.text or "").strip()
        if status in ALLOWED_CONTRACT_FOLDER_STATUSES:
            return True
    return False


def _extract_docs_from_ref_element(
    elem: ET.Element,
) -> Optional[Tuple[str, str]]:
    """From a Reference element, return (document_name, url) or None."""
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


def extract_technical_documents_from_entry(
    entry_el: ET.Element,
) -> List[Tuple[str, str]]:
    """Return (output_filename, url): Legal -> PCAP.pdf, Technical -> PPT.pdf."""
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


def folder_name_from_tender_id(tender_id: str) -> str:
    """Match :func:`get_entry_folder_name` logic for a stored tender id URL."""
    raw = (tender_id or "").strip()
    if "/" in raw:
        return raw.rsplit("/", 1)[-1].strip() or "unknown"
    return raw or "unknown"


def get_entry_folder_name(entry_el: ET.Element) -> str:
    """Segment after last '/' in entry <id>, for folder name."""
    for child in entry_el:
        tag = (child.tag or "").lower()
        if "id" in tag and child.text:
            raw = (child.text or "").strip()
            if "/" in raw:
                return raw.rsplit("/", 1)[-1].strip() or "unknown"
            return raw or "unknown"
    return "unknown"


def get_entry_detail_link(entry_el: ET.Element) -> Optional[str]:
    """Tender detail link: detalle_licitacion if present, else first http link."""
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


def extract_tender_data(entry_el: ET.Element) -> Dict[str, Any]:
    """Extract tender fields from an Atom entry for JSON export."""
    ATOM = f"{{{ATOM_NS}}}"
    CAC = f"{{{CAC_NS}}}"
    CBC = f"{{{CBC_NS}}}"

    data: Dict[str, Any] = {
        "id": None,
        "link": get_entry_detail_link(entry_el),
        "title": None,
        "party_name": None,
        "tax_exclusive_amount": None,
        "estimated_overall_contract_amount": None,
    }

    id_el = entry_el.find(f".//{ATOM}id")
    if id_el is not None:
        data["id"] = (id_el.text or "").strip()

    title_el = entry_el.find(f".//{ATOM}title")
    if title_el is not None:
        data["title"] = (title_el.text or "").strip()

    party_name_el = entry_el.find(f".//{CAC}PartyName/{CBC}Name")
    if party_name_el is not None:
        data["party_name"] = (party_name_el.text or "").strip()

    tax_excl_el = entry_el.find(f".//{CBC}TaxExclusiveAmount")
    if tax_excl_el is not None:
        data["tax_exclusive_amount"] = (tax_excl_el.text or "").strip()

    est_amount_el = entry_el.find(f".//{CBC}EstimatedOverallContractAmount")
    if est_amount_el is not None:
        data["estimated_overall_contract_amount"] = (est_amount_el.text or "").strip()

    return data


def run_ingestion(
    config: IngestionConfig,
    *,
    verbose: bool = False,
) -> IngestionResult:
    """Run feed walk, filters, optional PDF download, and optional JSON export.

    If ``config.no_download`` is True, PDFs are not fetched and JSON is not written
    (list-only mode), matching the legacy CLI.

    Args:
        config: Paths and options for the run.
        verbose: If True, print feed and per-document lines (CLI behavior).

    Returns:
        IngestionResult with tenders list and download counters.

    Raises:
        FileNotFoundError: Missing local atom path.
        requests.HTTPError: Feed HTTP error.
        requests.RequestException: Network failure.
        ET.ParseError: Invalid XML.
    """
    cutoff_utc = config.cutoff_utc
    if cutoff_utc is not None and cutoff_utc.tzinfo is None:
        cutoff_utc = cutoff_utc.replace(tzinfo=timezone.utc)
    elif cutoff_utc is not None:
        cutoff_utc = cutoff_utc.astimezone(timezone.utc)

    total = 0
    ok = 0
    failed_with_detail: List[Tuple[str, str]] = []
    tenders_data: List[Dict[str, Any]] = []

    for feed_source, root, feed_updated in iter_feed_documents(
        config.atom_source,
        cutoff_utc,
    ):
        if verbose and cutoff_utc is not None:
            updated_s = feed_updated.isoformat() if feed_updated else "?"
            print(f"Using feed {feed_source} (updated {updated_s})", file=sys.stdout)
        elif not verbose and cutoff_utc is not None:
            updated_s = feed_updated.isoformat() if feed_updated else "?"
            logger.info("Using feed %s (updated %s)", feed_source, updated_s)

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
            entry_dir = config.output_dir / folder_name
            detail_url = get_entry_detail_link(entry)
            docs = extract_technical_documents_from_entry(entry)
            for name, url in docs:
                if not url:
                    continue
                if config.max_tries and total >= config.max_tries:
                    hit_limit = True
                    break
                total += 1
                dest = entry_dir / name
                if config.no_download:
                    if verbose:
                        print(f"  [{folder_name}] {name}\n    {url}")
                        if detail_url:
                            print(f"    Tender page: {detail_url}")
                    continue
                success, msg = try_download(url, dest)
                if success:
                    ok += 1
                    if verbose:
                        print(f"[OK] {name} -> {dest}")
                else:
                    if verbose:
                        print(f"[FAIL] {name}: {msg}")
                    if detail_url:
                        failed_with_detail.append((name, detail_url))
            if hit_limit:
                break
        if hit_limit:
            break

    json_written: Optional[Path] = None
    if not config.no_download:
        config.json_out.parent.mkdir(parents=True, exist_ok=True)
        with open(config.json_out, "w", encoding="utf-8") as f:
            json.dump(tenders_data, f, ensure_ascii=False, indent=4)
        json_written = config.json_out

    return IngestionResult(
        tenders_data=tenders_data,
        total=total,
        ok=ok,
        failed_with_detail=failed_with_detail,
        json_out=json_written,
    )
