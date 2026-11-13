"""Build small Atom feed chains on disk for ingestion + Tender Lifecycle tests.

Entries mirror the structure of the real Plataformas Agregadas feed (CODICE
namespaces, ContractFolderStatus, ProcurementProjectLot, TenderResult).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence
from xml.sax.saxutils import escape

ENTRY_URI = "https://contrataciondelestado.es/sindicacion/PlataformasAgregadasSinMenores/"

_FEED_OPEN = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<feed xmlns="http://www.w3.org/2005/Atom" '
    'xmlns:cbc="urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2" '
    'xmlns:cac="urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2" '
    'xmlns:cac-place-ext="urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2" '
    'xmlns:cbc-place-ext="urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2" '
    'xmlns:at="http://purl.org/atompub/tombstones/1.0">\n'
)


@dataclass
class Lot:
    id: str
    name: str = "Lote"
    amount: Optional[str] = None


@dataclass
class Result:
    """One TenderResult block. ``code``: 8 awarded, 9 formalised, 3 void."""

    code: str = "8"
    lot_id: Optional[str] = None
    winner: Optional[str] = None
    winner_nif: Optional[str] = None
    amount: Optional[str] = None
    bids: Optional[int] = None


@dataclass
class Entry:
    id: str
    updated: str
    status: str = "PUB"
    title: str = "Plataforma de datos abiertos"
    party: str = "Ayuntamiento de Gijón"
    budget: str = "100000"
    estimated: str = "120000"
    deadline: Optional[str] = "2026-12-01"
    type_code: str = "2"
    subtype_code: str = "5"
    cpv: str = "72000000"
    pcap_url: Optional[str] = None
    ppt_url: Optional[str] = None
    lots: List[Lot] = field(default_factory=list)
    results: List[Result] = field(default_factory=list)


@dataclass
class Deleted:
    id: str
    when: str


def _lot_xml(lot: Lot) -> str:
    amount = (
        f"<cac:BudgetAmount><cbc:TaxExclusiveAmount currencyID=\"EUR\">{lot.amount}"
        "</cbc:TaxExclusiveAmount></cac:BudgetAmount>"
        if lot.amount
        else ""
    )
    return (
        "<cac:ProcurementProjectLot>"
        f'<cbc:ID schemeName="ID_LOTE">{escape(lot.id)}</cbc:ID>'
        f"<cac:ProcurementProject><cbc:Name>{escape(lot.name)}</cbc:Name>{amount}"
        "</cac:ProcurementProject></cac:ProcurementProjectLot>"
    )


def _result_xml(r: Result) -> str:
    parts = [f"<cac:TenderResult><cbc:ResultCode>{r.code}</cbc:ResultCode>"]
    if r.bids is not None:
        parts.append(f"<cbc:ReceivedTenderQuantity>{r.bids}</cbc:ReceivedTenderQuantity>")
    if r.winner:
        nif = (
            f'<cac:PartyIdentification><cbc:ID schemeName="NIF">{r.winner_nif}</cbc:ID>'
            "</cac:PartyIdentification>"
            if r.winner_nif
            else ""
        )
        parts.append(
            f"<cac:WinningParty>{nif}<cac:PartyName><cbc:Name>{escape(r.winner)}"
            "</cbc:Name></cac:PartyName></cac:WinningParty>"
        )
    if r.lot_id or r.amount:
        parts.append("<cac:AwardedTenderedProject>")
        if r.lot_id:
            parts.append(f"<cbc:ProcurementProjectLotID>{r.lot_id}</cbc:ProcurementProjectLotID>")
        if r.amount:
            parts.append(
                "<cac:LegalMonetaryTotal><cbc:TaxExclusiveAmount currencyID=\"EUR\">"
                f"{r.amount}</cbc:TaxExclusiveAmount></cac:LegalMonetaryTotal>"
            )
        parts.append("</cac:AwardedTenderedProject>")
    parts.append("</cac:TenderResult>")
    return "".join(parts)


def _doc_xml(tag: str, url: Optional[str]) -> str:
    if not url:
        return ""
    return (
        f"<cac:{tag}><cbc:ID>doc.pdf</cbc:ID><cac:Attachment><cac:ExternalReference>"
        f"<cbc:URI>{escape(url)}</cbc:URI></cac:ExternalReference></cac:Attachment></cac:{tag}>"
    )


def entry_xml(e: Entry) -> str:
    deadline = (
        "<cac:TenderingProcess><cac:TenderSubmissionDeadlinePeriod>"
        f"<cbc:EndDate>{e.deadline}</cbc:EndDate></cac:TenderSubmissionDeadlinePeriod>"
        "</cac:TenderingProcess>"
        if e.deadline
        else ""
    )
    return (
        "<entry>"
        f"<id>{ENTRY_URI}{e.id}</id>"
        f'<link href="https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion&amp;idEvl={e.id}"/>'
        f"<title>{escape(e.title)}</title>"
        f"<updated>{e.updated}</updated>"
        "<cac-place-ext:ContractFolderStatus>"
        f"<cbc:ContractFolderID>EXP-{e.id}</cbc:ContractFolderID>"
        f"<cbc-place-ext:ContractFolderStatusCode>{e.status}</cbc-place-ext:ContractFolderStatusCode>"
        "<cac-place-ext:LocatedContractingParty><cac:Party><cac:PartyName>"
        f"<cbc:Name>{escape(e.party)}</cbc:Name></cac:PartyName></cac:Party>"
        "</cac-place-ext:LocatedContractingParty>"
        "<cac:ProcurementProject>"
        f"<cbc:Name>{escape(e.title)}</cbc:Name>"
        f"<cbc:TypeCode>{e.type_code}</cbc:TypeCode>"
        f"<cbc:SubTypeCode>{e.subtype_code}</cbc:SubTypeCode>"
        "<cac:BudgetAmount>"
        f"<cbc:EstimatedOverallContractAmount currencyID=\"EUR\">{e.estimated}</cbc:EstimatedOverallContractAmount>"
        f"<cbc:TaxExclusiveAmount currencyID=\"EUR\">{e.budget}</cbc:TaxExclusiveAmount>"
        "</cac:BudgetAmount>"
        "<cac:RequiredCommodityClassification>"
        f"<cbc:ItemClassificationCode>{e.cpv}</cbc:ItemClassificationCode>"
        "</cac:RequiredCommodityClassification>"
        "</cac:ProcurementProject>"
        + "".join(_lot_xml(lot) for lot in e.lots)
        + "".join(_result_xml(r) for r in e.results)
        + deadline
        + _doc_xml("LegalDocumentReference", e.pcap_url)
        + _doc_xml("TechnicalDocumentReference", e.ppt_url)
        + "</cac-place-ext:ContractFolderStatus></entry>"
    )


def feed_xml(
    items: Sequence[Entry | Deleted],
    *,
    updated: str = "2026-11-13T03:00:00+01:00",
    next_href: Optional[str] = None,
) -> str:
    body = [_FEED_OPEN, f"<id>test-feed</id><title>Test</title><updated>{updated}</updated>"]
    if next_href:
        body.append(f'<link href="{next_href}" rel="next"/>')
    for item in items:
        if isinstance(item, Deleted):
            body.append(f'<at:deleted-entry ref="{ENTRY_URI}{item.id}" when="{item.when}"/>')
        else:
            body.append(entry_xml(item))
    body.append("</feed>\n")
    return "".join(body)


def write_feed_chain(directory: Path, pages: Sequence[Sequence[Entry | Deleted]], name: str = "feed") -> Path:
    """Write pages newest-first as a rel=next chain; return the first page path."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = [directory / f"{name}_{i}.atom" for i in range(len(pages))]
    for i, items in enumerate(pages):
        nxt = paths[i + 1].name if i + 1 < len(paths) else None
        paths[i].write_text(feed_xml(items, next_href=nxt), encoding="utf-8")
    return paths[0]
