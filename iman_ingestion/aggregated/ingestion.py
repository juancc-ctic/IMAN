"""Download PDFs and tender JSON from Plataformas Agregadas Sin Menores ATOM feeds."""

from __future__ import annotations

import io
import json
import logging
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
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

ALLOWED_CONTRACT_FOLDER_STATUSES = frozenset({"PRE", "PUB"})
ALLOWED_TYPE_CODE = "2"
ALLOWED_SUBTYPE_CODES = frozenset(
    {"5", "7", "8", "9", "11", "12", "20", "23", "24", "25", "27"}
)
CPV_IT_SERVICES_PREFIX = "72"  # CPV 72000000–72999999: Servicios TI


@dataclass
class IngestionConfig:
    """Parameters for a single ingestion run."""

    atom_sources: List[str]
    output_dir: Path
    json_out: Path
    cutoff_utc: Optional[datetime] = None
    max_tries: int = 0
    no_download: bool = False
    allowed_statuses: frozenset = field(
        default_factory=lambda: ALLOWED_CONTRACT_FOLDER_STATUSES
    )
    allowed_type_codes: Optional[frozenset] = None
    allowed_subtype_codes: Optional[frozenset] = None
    cpv_filters: Optional[frozenset] = None


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


def entry_has_allowed_type_and_subtype(
    entry_el: ET.Element,
    type_codes: Optional[frozenset] = None,
    subtype_codes: Optional[frozenset] = None,
) -> bool:
    """True if entry has an allowed TypeCode and an allowed SubTypeCode.

    When type_codes or subtype_codes is None, every value is accepted.
    """
    has_type = type_codes is None
    has_subtype = subtype_codes is None
    for elem in entry_el.iter():
        local = _xml_local_name(elem.tag or "")
        if local == "SubTypeCode":
            val = _normalize_cbc_code_text(elem)
            if subtype_codes is None or val in subtype_codes:
                has_subtype = True
        elif local == "TypeCode":
            val = _normalize_cbc_code_text(elem)
            if type_codes is None or val in type_codes:
                has_type = True
    return has_type and has_subtype


def entry_has_it_services_cpv(
    entry_el: ET.Element,
    cpv_filters: Optional[frozenset] = None,
) -> bool:
    """True if entry has at least one CPV code matching any entry in cpv_filters.

    Each filter is matched with startswith — short strings act as prefixes,
    full 8-digit codes act as exact matches.  None means every entry passes.
    """
    if not cpv_filters:
        return True
    for elem in entry_el.iter():
        if _xml_local_name(elem.tag or "") == "ItemClassificationCode":
            code = (elem.text or "").strip()
            if any(code.startswith(f) for f in cpv_filters):
                return True
    return False


def entry_has_allowed_contract_folder_status(
    entry_el: ET.Element,
    allowed: frozenset = ALLOWED_CONTRACT_FOLDER_STATUSES,
) -> bool:
    """True if ContractFolderStatusCode is in the allowed set."""
    for elem in entry_el.iter():
        tag = elem.tag or ""
        if "ContractFolderStatusCode" not in tag:
            continue
        status = (elem.text or "").strip()
        if status in allowed:
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


def extract_submission_deadline_from_entry(entry_el: ET.Element) -> Optional[str]:
    """Return submission deadline as 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS', or None."""
    CBC = f"{{{CBC_NS}}}"
    CAC = f"{{{CAC_NS}}}"
    period = entry_el.find(f".//{CAC}TenderSubmissionDeadlinePeriod")
    if period is None:
        return None
    date_el = period.find(f"{CBC}EndDate")
    time_el = period.find(f"{CBC}EndTime")
    date_str = (date_el.text or "").strip() if date_el is not None else ""
    time_str = (time_el.text or "").strip() if time_el is not None else ""
    if not date_str:
        return None
    if time_str:
        return f"{date_str}T{time_str}"
    return date_str


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
        url = get_document_url_from_uri_text(url)
        output_name = "PCAP.pdf" if "LegalDocumentReference" in tag else "PPT.pdf"
        results.append((output_name, url))
    return results


def folder_name_from_tender_id(tender_id: str) -> str:
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


def extract_enrichment_from_atom(entry_el: ET.Element) -> Dict[str, Any]:
    """Extract LLM-enrichment fields determinable directly from the Atom XML entry.

    Returns a partial enrichment dict shaped identically to the LLM schema so
    it can be merged into ``accumulated`` before the LLM is called, skipping
    re-extraction of fields already known from structured metadata.
    """
    CAC = f"{{{CAC_NS}}}"
    CBC = f"{{{CBC_NS}}}"

    enrichment: Dict[str, Any] = {}

    # object_of_the_contract — ProcurementProject/Name
    name_el = entry_el.find(f".//{CAC}ProcurementProject/{CBC}Name")
    if name_el is not None:
        val = (name_el.text or "").strip()
        if val:
            enrichment["object_of_the_contract"] = val

    # execution_period — PlannedPeriod/DurationMeasure (unit code → Spanish label)
    dur_el = entry_el.find(f".//{CAC}PlannedPeriod/{CBC}DurationMeasure")
    execution_period: Optional[str] = None
    dur_n: Optional[int] = None
    dur_unit: Optional[str] = None
    if dur_el is not None:
        raw_dur = (dur_el.text or "").strip()
        unit = (dur_el.get("unitCode") or "").upper()
        unit_labels = {"MON": "Meses", "DAY": "Días", "WEE": "Semanas", "ANN": "Años"}
        label = unit_labels.get(unit)
        if raw_dur.isdigit() and label:
            dur_n = int(raw_dur)
            dur_unit = unit
            execution_period = f"{dur_n} {label}"
            enrichment["execution_period"] = execution_period

    # assessment_criteria — all AwardingCriteria blocks with weights
    crit_lines: List[str] = []
    total_weight = 0.0
    obj_weight = 0.0
    price_weight = 0.0  # OBJ + SubTypeCode "1" = explicit price/cost formula
    for crit in entry_el.findall(f".//{CAC}AwardingCriteria"):
        desc_el = crit.find(f"{CBC}Description")
        weight_el = crit.find(f"{CBC}WeightNumeric")
        type_el = crit.find(f"{CBC}AwardingCriteriaTypeCode")
        subtype_el = crit.find(f"{CBC}AwardingCriteriaSubTypeCode")
        desc = (desc_el.text or "").strip() if desc_el is not None else ""
        weight_str = (weight_el.text or "").strip() if weight_el is not None else ""
        ctype = (type_el.text or "").strip() if type_el is not None else ""
        subtype = (subtype_el.text or "").strip() if subtype_el is not None else ""
        if not desc:
            continue
        try:
            w = float(weight_str) if weight_str else 0.0
        except ValueError:
            w = 0.0
        total_weight += w
        if ctype == "OBJ":
            obj_weight += w
            if subtype == "1":
                price_weight += w
        crit_lines.append(f"{desc} [{weight_str} pts]" if weight_str else desc)
    if crit_lines:
        enrichment["assessment_criteria"] = "\n".join(crit_lines)

    # discard_review.criteria_flags — fields computable from structured XML data
    criteria_flags: Dict[str, Any] = {}

    # place_of_execution_not_asturias — NUTS code from RealizedLocation
    loc_el = entry_el.find(f".//{CAC}RealizedLocation")
    if loc_el is not None:
        code_el = loc_el.find(f".//{CBC}CountrySubentityCode")
        region_el = loc_el.find(f".//{CBC}CountrySubentity")
        nuts_code = (code_el.text or "").strip() if code_el is not None else ""
        region = (region_el.text or "").strip() if region_el is not None else ""
        if nuts_code:
            criteria_flags["place_of_execution_not_asturias"] = {
                "applies": nuts_code != "ES120",
                "evidence": f"{region} ({nuts_code})" if region else nuts_code,
                "pages": None,
            }

    # execution_period_under_2_months — checked per original unit (no conversion)
    if dur_n is not None and dur_unit is not None:
        under: Optional[bool] = None
        if dur_unit == "MON":
            under = dur_n < 2
        elif dur_unit == "DAY":
            under = dur_n < 60
        elif dur_unit == "WEE":
            under = dur_n < 8
        elif dur_unit == "ANN":
            under = False
        if under is not None:
            criteria_flags["execution_period_under_2_months"] = {
                "applies": under,
                "evidence": execution_period or "",
                "pages": None,
            }

    # economic_offer_weight_over_70_points
    # If all OBJ criteria combined < 70, price weight can't exceed 70.
    # If explicit price criteria (SubTypeCode=1) alone > 70, flag it directly.
    if total_weight > 0:
        if price_weight > 70:
            applies_eco: Optional[bool] = True
            evidence_eco = f"Criterios de precio: {price_weight:.0f}/{total_weight:.0f} pts"
        elif obj_weight < 70:
            applies_eco = False
            evidence_eco = (
                f"Total criterios objetivos (límite superior precio): "
                f"{obj_weight:.0f}/{total_weight:.0f} pts"
            )
        else:
            applies_eco = None
            evidence_eco = (
                f"Criterios precio explícitos: {price_weight:.0f}/{total_weight:.0f} pts "
                f"(revisión PCAP necesaria)"
            )
        criteria_flags["economic_offer_weight_over_70_points"] = {
            "applies": applies_eco,
            "evidence": evidence_eco,
            "pages": None,
        }

    if criteria_flags:
        enrichment["discard_review"] = {"criteria_flags": criteria_flags}

    return enrichment


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
        "submission_deadline": extract_submission_deadline_from_entry(entry_el),
        "pcap_url": None,
        "ppt_url": None,
        "cpv_codes": [],
    }

    id_el = entry_el.find(f".//{ATOM}id")
    if id_el is not None:
        raw_id = (id_el.text or "").strip()
        data["id"] = raw_id.rsplit("/", 1)[-1] if "/" in raw_id else raw_id

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

    docs_by_name = {name: url for name, url in extract_technical_documents_from_entry(entry_el)}
    data["pcap_url"] = docs_by_name.get("PCAP.pdf")
    data["ppt_url"] = docs_by_name.get("PPT.pdf")

    cpv_codes = []
    for elem in entry_el.iter():
        if _xml_local_name(elem.tag or "") == "ItemClassificationCode":
            code = (elem.text or "").strip()
            if code and code not in cpv_codes:
                cpv_codes.append(code)
    data["cpv_codes"] = cpv_codes

    data["atom_enrichment"] = extract_enrichment_from_atom(entry_el)

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
    seen_folders: Dict[str, int] = {}  # folder_name -> doc count of accepted entry

    hit_limit = False
    for atom_source in config.atom_sources:
        if hit_limit:
            break
        for feed_source, root, feed_updated in iter_feed_documents(
            atom_source,
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

            for entry in entries:
                if not entry_has_allowed_contract_folder_status(entry, config.allowed_statuses):
                    continue
                if not entry_has_allowed_type_and_subtype(entry, config.allowed_type_codes, config.allowed_subtype_codes):
                    continue
                if not entry_has_it_services_cpv(entry, config.cpv_filters):
                    continue

                folder_name = get_entry_folder_name(entry)
                docs = extract_technical_documents_from_entry(entry)
                prev_doc_count = seen_folders.get(folder_name, -1)

                if prev_doc_count >= len(docs):
                    logger.info(
                        "Skipping duplicate tender [%s] (%d doc(s); already accepted with %d)",
                        folder_name,
                        len(docs),
                        prev_doc_count,
                    )
                    continue

                if prev_doc_count >= 0:
                    # Replace the weaker entry in tenders_data with this better one
                    tenders_data = [t for t in tenders_data if t.get("id") != folder_name]

                seen_folders[folder_name] = len(docs)

                tender_info = extract_tender_data(entry)
                tenders_data.append(tender_info)
                entry_dir = config.output_dir / folder_name
                detail_url = get_entry_detail_link(entry)
                logger.info(
                    "Processing tender [%s] (%d doc(s)) title=%r",
                    folder_name,
                    len(docs),
                    (tender_info.get("title") or "")[:120],
                )
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
                    logger.info("Downloading [%s] %s -> %s", folder_name, name, dest)
                    success, msg = try_download(url, dest)
                    if success:
                        ok += 1
                        if verbose:
                            print(f"[OK] {name} -> {dest}")
                        logger.info("[OK] [%s] %s", folder_name, name)
                    else:
                        if verbose:
                            print(f"[FAIL] {name}: {msg}")
                        logger.warning("[FAIL] [%s] %s: %s", folder_name, name, msg)
                        if detail_url:
                            failed_with_detail.append((name, detail_url))
                if hit_limit:
                    break
            if hit_limit:
                if verbose and config.max_tries:
                    print(
                        f"\nStopped: --try limit ({config.max_tries} PDFs). "
                        "Next Atom pages in the chain were not fetched. "
                        "Use --try 0 to download without this cap.",
                        file=sys.stdout,
                    )
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
