"""Download PDFs and tender JSON from Plataformas Agregadas Sin Menores ATOM feeds."""

from __future__ import annotations

import codecs
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
TOMBSTONES_NS = "http://purl.org/atompub/tombstones/1.0"

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

    atom_source: str
    output_dir: Path
    json_out: Path
    cutoff_utc: Optional[datetime] = None
    max_tries: int = 0
    no_download: bool = False
    allowed_statuses: frozenset = field(
        default_factory=lambda: ALLOWED_CONTRACT_FOLDER_STATUSES
    )
    allowed_type_code: str = ALLOWED_TYPE_CODE
    allowed_subtype_codes: frozenset = field(
        default_factory=lambda: ALLOWED_SUBTYPE_CODES
    )
    cpv_prefix: str = CPV_IT_SERVICES_PREFIX
    # Tracked Tender ids: their entries bypass the discovery filters and are
    # never re-downloaded. Known Tender ids: entries failing the filters still
    # produce a feed version so the current Tender Status stays up to date.
    tracked_ids: frozenset = field(default_factory=frozenset)
    known_ids: frozenset = field(default_factory=frozenset)


@dataclass
class IngestionResult:
    """Outcome of :func:`run_ingestion`.

    ``tenders_data`` holds newly discovered (filter-passing, untracked) entries,
    as before. ``versions`` holds every feed version relevant to the Tender
    Lifecycle, in feed order (newest first), each with ``updated`` (ISO),
    ``status``, ``discovered`` and ``deleted`` keys.
    """

    tenders_data: List[Dict[str, Any]]
    total: int
    ok: int
    failed_with_detail: List[Tuple[str, str]]
    json_out: Optional[Path] = None
    versions: List[Dict[str, Any]] = field(default_factory=list)


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

        # Network/IO errors propagate so the run fails visibly (and can be retried).
        # A malformed page mid-chain ends pagination; a malformed first page is fatal.
        try:
            tree = load_atom_tree(current)
        except ET.ParseError as exc:
            if len(seen) == 1:
                raise
            logger.error(
                "Unparseable feed page, stopping pagination; later pages are NOT ingested: %s — %s",
                current,
                exc,
            )
            break
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


# Matches the encoding="..." attribute in the XML prolog.
_XML_ENCODING_RE = re.compile(rb'(<\?xml\b[^?]*?\bencoding\s*=\s*)["\']([^"\']*)["\']')
# Control characters illegal in XML 1.0 (tab/LF/CR are kept).
_INVALID_XML10_CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _cp1252_fallback_handler(err: UnicodeDecodeError) -> Tuple[str, int]:
    """Decode a byte that is invalid UTF-8 as Windows-1252 (Latin-1 for its 5 holes)."""
    b = err.object[err.start : err.start + 1]
    try:
        ch = b.decode("cp1252")
    except UnicodeDecodeError:
        ch = b.decode("latin-1")
    return ch, err.start + 1


codecs.register_error("iman_cp1252_fallback", _cp1252_fallback_handler)


def _sanitize_xml_bytes(data: bytes) -> bytes:
    """Fix invalid encoding bytes and illegal control chars in XML before parsing.

    Feeds sometimes declare UTF-8 but contain ISO-8859-1/Windows-1252 high bytes
    (e.g. 0x96 en-dash, 0x85 ellipsis) that are invalid UTF-8 lead/continuation
    bytes and make expat raise 'not well-formed (invalid token)'. Valid UTF-8
    sequences are kept; each invalid byte is decoded as Windows-1252.
    """
    m = _XML_ENCODING_RE.search(data[:256])
    declared_enc = m.group(2).decode("ascii", errors="replace").strip() if m else ""
    try:
        codec_name = codecs.lookup(declared_enc or "utf-8").name
    except LookupError:
        logger.warning("Unknown XML encoding %r; decoding as UTF-8.", declared_enc)
        codec_name = "utf-8"
    if codec_name == "utf-8":
        text = data.decode("utf-8", errors="iman_cp1252_fallback")
    else:
        text = data.decode(codec_name, errors="replace")
    text = _INVALID_XML10_CTRL_RE.sub("", text)
    clean = text.encode("utf-8")
    if m:
        clean = _XML_ENCODING_RE.sub(rb'\1"UTF-8"', clean, count=1)
    return clean


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
        data = response.content
    else:
        path = Path(stripped)
        if not path.exists():
            raise FileNotFoundError(str(path))
        data = path.read_bytes()
    return ET.parse(io.BytesIO(_sanitize_xml_bytes(data)))


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
    type_code: str = ALLOWED_TYPE_CODE,
    subtype_codes: frozenset = ALLOWED_SUBTYPE_CODES,
) -> bool:
    """True if entry has the expected TypeCode and a SubTypeCode in the allowed set."""
    has_type = False
    has_subtype = False
    for elem in entry_el.iter():
        local = _xml_local_name(elem.tag or "")
        if local == "SubTypeCode":
            val = _normalize_cbc_code_text(elem)
            if val in subtype_codes:
                has_subtype = True
        elif local == "TypeCode":
            val = _normalize_cbc_code_text(elem)
            if val == type_code:
                has_type = True
    return has_type and has_subtype


def entry_has_it_services_cpv(
    entry_el: ET.Element,
    cpv_prefix: str = CPV_IT_SERVICES_PREFIX,
) -> bool:
    """True if entry has at least one CPV code starting with cpv_prefix."""
    if not cpv_prefix:
        return True
    for elem in entry_el.iter():
        if _xml_local_name(elem.tag or "") == "ItemClassificationCode":
            code = (elem.text or "").strip()
            if code.startswith(cpv_prefix):
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


def entry_passes_discovery_filters(entry_el: ET.Element, config: "IngestionConfig") -> bool:
    """True if the entry passes the status, type/subtype and CPV discovery filters."""
    return (
        entry_has_allowed_contract_folder_status(entry_el, config.allowed_statuses)
        and entry_has_allowed_type_and_subtype(
            entry_el, config.allowed_type_code, config.allowed_subtype_codes
        )
        and entry_has_it_services_cpv(entry_el, config.cpv_prefix)
    )


def extract_tender_status(entry_el: ET.Element) -> Optional[str]:
    """Return the entry's Tender Status code (PRE, PUB, EV, ADJ, RES, ANUL), or None."""
    for elem in entry_el.iter():
        if _xml_local_name(elem.tag or "") == "ContractFolderStatusCode":
            return (elem.text or "").strip() or None
    return None


# TenderResultCode values meaning the Lot was declared void (desierto).
# Other codes are kept as-is on the Award: 8 awarded, 9 formalised, and
# 4 (desistimiento) / 5 (renuncia), which end the Lot with neither a winner
# nor a void declaration.
VOID_RESULT_CODES = frozenset({"3", "6", "7"})
# Lot id used for a Tender that is not split into lots.
SINGLE_LOT_ID = "1"


def _child_text(elem: Optional[ET.Element], path: str) -> Optional[str]:
    if elem is None:
        return None
    found = elem.find(path)
    if found is None:
        return None
    return (found.text or "").strip() or None


def extract_lots(entry_el: ET.Element) -> List[Dict[str, Any]]:
    """Return the entry's Lots as ``{"id", "name", "amount"}`` dicts (empty if not split)."""
    CAC = f"{{{CAC_NS}}}"
    CBC = f"{{{CBC_NS}}}"
    lots: List[Dict[str, Any]] = []
    for lot_el in entry_el.iter(f"{CAC}ProcurementProjectLot"):
        lot_id = _child_text(lot_el, f"{CBC}ID")
        if not lot_id:
            continue
        project = lot_el.find(f"{CAC}ProcurementProject")
        lots.append(
            {
                "id": lot_id,
                "name": _child_text(project, f"{CBC}Name"),
                "amount": _child_text(project, f"{CAC}BudgetAmount/{CBC}TaxExclusiveAmount"),
            }
        )
    return lots


def extract_awards(entry_el: ET.Element) -> List[Dict[str, Any]]:
    """Return one Award dict per Lot from the entry's TenderResult blocks.

    Keys: ``lot_id``, ``result_code``, ``void``, ``winner_name``, ``winner_nif``,
    ``award_amount``, ``bids_received``. Results without a lot id belong to the
    single Lot of an undivided Tender.
    """
    CAC = f"{{{CAC_NS}}}"
    CBC = f"{{{CBC_NS}}}"
    by_lot: Dict[str, Dict[str, Any]] = {}
    for res in entry_el.iter(f"{CAC}TenderResult"):
        lot_id = _child_text(res, f"{CAC}AwardedTenderedProject/{CBC}ProcurementProjectLotID") or SINGLE_LOT_ID
        code = _child_text(res, f"{CBC}ResultCode")
        names = [
            (n.text or "").strip()
            for n in res.findall(f"{CAC}WinningParty/{CAC}PartyName/{CBC}Name")
            if (n.text or "").strip()
        ]
        nifs = [
            (n.text or "").strip()
            for n in res.findall(f"{CAC}WinningParty/{CAC}PartyIdentification/{CBC}ID")
            if (n.text or "").strip()
        ]
        bids_raw = _child_text(res, f"{CBC}ReceivedTenderQuantity")
        award = {
            "lot_id": lot_id,
            "result_code": code,
            "void": code in VOID_RESULT_CODES,
            "winner_name": "; ".join(names) or None,
            "winner_nif": "; ".join(nifs) or None,
            "award_amount": _child_text(
                res, f"{CAC}AwardedTenderedProject/{CAC}LegalMonetaryTotal/{CBC}TaxExclusiveAmount"
            ),
            "bids_received": int(bids_raw) if bids_raw and bids_raw.isdigit() else None,
        }
        existing = by_lot.get(lot_id)
        if existing is None:
            by_lot[lot_id] = award
        else:
            # Several results for one Lot (e.g. several winners): keep the first
            # value of each field and accumulate winners.
            for key in ("winner_name", "winner_nif"):
                if award[key] and award[key] != existing[key]:
                    existing[key] = "; ".join(x for x in (existing[key], award[key]) if x)
            for key, value in award.items():
                if existing.get(key) is None:
                    existing[key] = value
    return list(by_lot.values())


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
        "updated": None,
        "status": extract_tender_status(entry_el),
        "lots": extract_lots(entry_el),
        "awards": extract_awards(entry_el),
    }

    updated_el = entry_el.find(f"{ATOM}updated")
    if updated_el is not None and (updated_el.text or "").strip():
        data["updated"] = (updated_el.text or "").strip()

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
    versions: List[Dict[str, Any]] = []
    # Newer filtered-out versions of Tenders not yet known; kept in case an older
    # entry later in the (newest-first) walk discovers the Tender in this run.
    pending_versions: Dict[str, List[Dict[str, Any]]] = {}
    discovered_ids: set[str] = set()

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

        for deleted in root.findall(f".//{{{TOMBSTONES_NS}}}deleted-entry"):
            ref = (deleted.get("ref") or "").strip()
            tid = ref.rsplit("/", 1)[-1] if "/" in ref else ref
            if tid and tid in config.tracked_ids:
                versions.append(
                    {"id": tid, "updated": deleted.get("when"), "deleted": True, "discovered": False}
                )

        entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        if not entries:
            entries = list(root)

        hit_limit = False
        for entry in entries:
            discovered = entry_passes_discovery_filters(entry, config)
            tender_info = extract_tender_data(entry)
            tid = tender_info.get("id")
            if tid in config.tracked_ids:
                versions.append({**tender_info, "discovered": discovered, "deleted": False})
                continue
            if not discovered:
                if tid in config.known_ids:
                    versions.append({**tender_info, "discovered": False, "deleted": False})
                elif tid:
                    pending_versions.setdefault(tid, []).append(
                        {**tender_info, "discovered": False, "deleted": False}
                    )
                continue

            if tid:
                discovered_ids.add(tid)
            tenders_data.append(tender_info)
            versions.append({**tender_info, "discovered": True, "deleted": False})

            folder_name = get_entry_folder_name(entry)
            entry_dir = config.output_dir / folder_name
            detail_url = get_entry_detail_link(entry)
            docs = extract_technical_documents_from_entry(entry)
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

    for tid in discovered_ids:
        versions.extend(pending_versions.get(tid, []))

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
        versions=versions,
    )
