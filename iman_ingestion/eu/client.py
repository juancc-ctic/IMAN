"""EU Funding & Tenders Search API client for Dagster ingestion.

Library version of data-sources/Europe/fetch_eu_search.py — no CLI, no argparse.
Exposes :func:`fetch_eu_datasets` which returns a flat list of normalized items
from the three datasets (horizon-topics, non-horizon-topics, horizon-calls).
"""

from __future__ import annotations

import html
import json
import re
import time
from html.parser import HTMLParser
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlencode

import requests

DEFAULT_BASE_URL = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
API_MAX_PAGE_SIZE = 100
_JSON_PART = "application/json"
_LANGUAGES = ["en"]

# Fields whose values are HTML fragments in the API response.
_HTML_FIELDS = frozenset(
    {
        "description",
        "furtherInformation",
        "missionDescription",
        "missionDetails",
        "destinationDescription",
        "destinationDetails",
        "descriptionByte",
        "topicConditions",
        "content",
    }
)

_TAG_RE = re.compile(r"<[a-zA-Z][\s\S]*?>")

# ---------------------------------------------------------------------------
# Query payloads (mirror of fetch_eu_search.py)
# ---------------------------------------------------------------------------

_TOPIC_DISPLAY_FIELDS = [
    "identifier",
    "title",
    "callTitle",
    "description",
    "furtherInformation",
    "missionDescription",
    "missionDetails",
    "destinationDescription",
    "destinationDetails",
    "duration",
    "summary",
    "reference",
    "callccm2Id",
    "status",
    "projectAcronym",
    "startDate",
    "deadlineDate",
    "deadlineModel",
    "frameworkProgramme",
    "typesOfAction",
    "keywords",
    "tags",
    "programmeDivision",
    "budgetOverview",
    "descriptionByte",
]

_CALL_DISPLAY_FIELDS = [
    "identifier",
    "budget",
    "title",
    "callccm2Id",
    "content",
    "description",
    "status",
    "caName",
    "startDate",
    "deadlineDate",
    "deadlineModel",
    "esDA_FirstIngestDate",
    "furtherInformation",
]

_SORT_TOPICS: Mapping[str, Any] = {"order": "ASC", "field": "title"}
_SORT_CALLS: Mapping[str, Any] = {"order": "ASC", "field": "caName"}

_QUERY_HORIZON_TOPICS: Mapping[str, Any] = {
    "bool": {
        "must": [
            {"terms": {"type": ["1"]}},
            {"terms": {"status": ["31094501", "31094502", "31094503"]}},
            {"term": {"programmePeriod": "2021 - 2027"}},
            {"terms": {"frameworkProgramme": ["43108390"]}},
            {
                "terms": {
                    "programmeDivision": [
                        43108557,
                        43118846,
                        43118971,
                        43120193,
                        43120821,
                        43121563,
                        43108541,
                        43121707,
                        43108514,
                    ]
                }
            },
        ]
    }
}

_QUERY_NON_HORIZON_TOPICS: Mapping[str, Any] = {
    "bool": {
        "must": [
            {"terms": {"type": ["1"]}},
            {"terms": {"status": ["31094501", "31094502", "31094503"]}},
            {"term": {"programmePeriod": "2021 - 2027"}},
            {
                "terms": {
                    "frameworkProgramme": [
                        "43152860",
                        "44181033",
                        "43252405",
                        "43251589",
                    ]
                }
            },
        ]
    }
}

_QUERY_HORIZON_CALLS: Mapping[str, Any] = {
    "bool": {
        "must": [
            {"terms": {"type": ["8"]}},
            {"term": {"programmePeriod": "2021 - 2027"}},
            {"terms": {"frameworkProgramme": ["43108390"]}},
        ]
    }
}


# (key, query, display_fields, sort)
_DATASETS = [
    ("horizon-topic", _QUERY_HORIZON_TOPICS, _TOPIC_DISPLAY_FIELDS, _SORT_TOPICS),
    ("non-horizon-topic", _QUERY_NON_HORIZON_TOPICS, _TOPIC_DISPLAY_FIELDS, _SORT_TOPICS),
    ("horizon-call", _QUERY_HORIZON_CALLS, _CALL_DISPLAY_FIELDS, _SORT_CALLS),
]

# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: List[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in {"br", "p", "div", "li", "tr", "td", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._chunks.append(" ")

    def get_text(self) -> str:
        return "".join(self._chunks)


def _plain_text(html_str: str) -> str:
    if not html_str:
        return html_str
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html_str)
        parser.close()
        text = parser.get_text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html_str)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_html(val: Any) -> Any:
    if isinstance(val, str):
        return _plain_text(val)
    if isinstance(val, list):
        return [_plain_text(x) if isinstance(x, str) else x for x in val]
    return val


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _multipart_parts(
    query: Mapping[str, Any],
    display_fields: List[str],
    sort: Mapping[str, Any],
) -> List[tuple]:
    return [
        ("query", (None, json.dumps(query, separators=(",", ":")), _JSON_PART)),
        ("languages", (None, json.dumps(_LANGUAGES), _JSON_PART)),
        ("displayFields", (None, json.dumps(display_fields), _JSON_PART)),
        ("sort", (None, json.dumps(sort, separators=(",", ":")), _JSON_PART)),
    ]


def _search_url(base_url: str, api_key: str, text: str, page_size: int, page_number: int) -> str:
    qs = urlencode(
        {"apiKey": api_key, "text": text, "pageSize": str(page_size), "pageNumber": str(page_number)}
    )
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}{qs}"


def _fetch_page(session: requests.Session, url: str, parts: List[tuple]) -> Dict[str, Any]:
    resp = session.post(url, files=parts, headers={"Accept": "application/json"}, timeout=120.0)
    resp.raise_for_status()
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise ValueError("Response is not JSON") from exc


def _fetch_all_pages(
    session: requests.Session,
    query: Mapping[str, Any],
    display_fields: List[str],
    sort: Mapping[str, Any],
    *,
    base_url: str,
    api_key: str,
    text: str,
    page_delay_s: float,
    max_pages: int,
) -> List[Dict[str, Any]]:
    parts = _multipart_parts(query, display_fields, sort)
    all_results: List[Dict[str, Any]] = []
    total_results: Optional[int] = None

    for page_number in range(1, max_pages + 1):
        url = _search_url(base_url, api_key, text, API_MAX_PAGE_SIZE, page_number)
        payload = _fetch_page(session, url, parts)
        tr = payload.get("totalResults")
        if not isinstance(tr, int):
            raise RuntimeError(f"Missing or invalid totalResults: {tr!r}")
        if total_results is None:
            total_results = tr
        batch = payload.get("results")
        if not isinstance(batch, list):
            raise RuntimeError("results is not a list")
        all_results.extend(batch)
        if len(all_results) >= (total_results or 0) or not batch:
            break
        if page_delay_s > 0:
            time.sleep(page_delay_s)

    return all_results


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _flatten_metadata(meta: Any) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    flat: Dict[str, Any] = {}
    for key, val in meta.items():
        if isinstance(val, list):
            flat[key] = val[0] if len(val) == 1 else (None if not val else val)
        else:
            flat[key] = val
    for key in _HTML_FIELDS:
        if key in flat:
            flat[key] = _strip_html(flat[key])
    return flat


def _first_str(val: Any) -> Optional[str]:
    """Return the first element if val is a list, else val as-is (or None)."""
    if isinstance(val, list):
        return val[0] if val else None
    return val or None


_PORTAL_TOPIC_URL = (
    "https://ec.europa.eu/info/funding-tenders/opportunities/portal/"
    "screen/opportunities/topic-details/{identifier}"
)
_PORTAL_CALL_URL = (
    "https://ec.europa.eu/info/funding-tenders/opportunities/portal/"
    "screen/opportunities/competitive-calls-cs/{callccm2Id}"
)


def _normalize_hit(hit: Mapping[str, Any], kind: str) -> Dict[str, Any]:
    flat = _flatten_metadata(hit.get("metadata"))
    if "call" in kind:
        parts = [flat.get("description") or "", flat.get("furtherInformation") or ""]
        embed_text = "\n\n".join(p for p in parts if p).strip()
        callccm2id = _first_str(flat.get("callccm2Id"))
        url = _PORTAL_CALL_URL.format(callccm2Id=callccm2id) if callccm2id else None
    else:
        parts = [flat.get("descriptionByte") or "", flat.get("topicConditions") or ""]
        embed_text = "\n\n".join(p for p in parts if p).strip()
        url = _PORTAL_TOPIC_URL.format(identifier=_first_str(flat.get("identifier"))) if flat.get("identifier") else None
    identifier = _first_str(flat.get("identifier"))
    return {
        "reference": hit.get("reference"),
        "kind": kind,
        "url": url,
        "identifier": identifier,
        "title": _first_str(flat.get("title")),
        "status": _first_str(flat.get("status")),
        "start_date": _first_str(flat.get("startDate")),
        "deadline_date": _first_str(flat.get("deadlineDate")),
        "metadata": flat,
        "embed_text": embed_text,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_eu_datasets(
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = "SEDIA",
    text: str = "***",
    page_delay_s: float = 0.0,
    max_pages: int = 10_000,
) -> List[Dict[str, Any]]:
    """Fetch all three EU Search API datasets and return a flat list of normalized items.

    Each item dict has keys: reference, kind, url, identifier, title, status,
    start_date, deadline_date, metadata (JSONB), embed_text (plain text for embedding).
    """
    all_items: List[Dict[str, Any]] = []
    with requests.Session() as session:
        for kind, query, display_fields, sort in _DATASETS:
            raw_hits = _fetch_all_pages(
                session,
                query,
                display_fields,
                sort,
                base_url=base_url,
                api_key=api_key,
                text=text,
                page_delay_s=page_delay_s,
                max_pages=max_pages,
            )
            for hit in raw_hits:
                item = _normalize_hit(hit, kind)
                if item["reference"]:
                    all_items.append(item)
    return all_items
