#!/usr/bin/env python3
"""
Download PDFs from the Plataformas Agregadas Sin Menores ATOM feed.

Downloads PDFs under <cac:TechnicalDocumentReference> and <cac:LegalDocumentReference>
for entries where <cbc-place-ext:ContractFolderStatusCode> is PRE, PUB or EV.
Document URIs may point to external platforms (e.g. contractaciopublica.cat,
contratos-publicos.comunidad.madrid), not only contrataciondelestado.es.

Usage:
  python download_aggregated_docs.py <path_to.atom> [--try N] [--output DIR]
  python download_aggregated_docs.py PlataformasAgregadasSinMenores_20260206_040054_1.atom
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional, Tuple

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


def get_document_url_from_uri_text(uri_text: str) -> str:
    """Decode XML entity &amp; to & so the URL is valid."""
    if not uri_text:
        return ""
    return uri_text.strip().replace("&amp;", "&")


ALLOWED_CONTRACT_FOLDER_STATUSES = frozenset({"PRE", "PUB", "EV"})


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n")[1])
    parser.add_argument("atom_file", type=Path, help="Path to .atom file")
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
    args = parser.parse_args()

    if not args.atom_file.exists():
        print(f"File not found: {args.atom_file}", file=sys.stderr)
        return 1

    tree = ET.parse(args.atom_file)
    root = tree.getroot()
    # Handle default namespace: tag is {http://...}entry
    entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    if not entries:
        entries = list(root)
    total = 0
    ok = 0
    failed_with_detail = []

    for entry in entries:
        if not entry_has_allowed_contract_folder_status(entry):
            continue
        folder_name = get_entry_folder_name(entry)
        entry_dir = args.output / folder_name
        detail_url = get_entry_detail_link(entry)
        docs = extract_technical_documents_from_entry(entry)
        for name, url in docs:
            if not url:
                continue
            if args.max_tries and total >= args.max_tries:
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
        if args.max_tries and total >= args.max_tries:
            break

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
