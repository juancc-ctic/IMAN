#!/usr/bin/env python3
"""Fetch EU Funding & Tenders Search API results (topics and calls).

Replicates the three Postman requests in
``EU Search API (fetch_eu_topics & fetch_eu_calls).postman_collection.json``:
multipart form-data with each body part sent as ``application/json``.

Usage:
  python fetch_eu_search.py --output-dir ./out
  python fetch_eu_search.py --output-dir ./out --only horizon-topics
  python fetch_eu_search.py --output-dir ./out --raw

The API returns at most 100 results per HTTP request; the script paginates
until ``totalResults`` is reached.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlencode

try:
    import requests
except ImportError:
    print("Install requests: pip install requests", file=sys.stderr)
    sys.exit(1)

DEFAULT_BASE_URL = (
    "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
)
JSON_PART = "application/json"

# The Search API returns at most this many hits per page (regardless of pageSize).
API_MAX_PAGE_SIZE = 100

# Metadata keys whose values are HTML fragments in the API (topics + calls).
METADATA_HTML_FIELDS = frozenset(
    {
        "description",
        "furtherInformation",
        "missionDescription",
        "missionDetails",
        "destinationDescription",
        "destinationDetails",
        "descriptionByte",
        "content",
    }
)

_TAG_RE = re.compile(r"<[a-zA-Z][\s\S]*?>")


# --- Request payloads (match Postman collection) ---

LANGUAGES = ["en"]

TOPIC_DISPLAY_FIELDS = [
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

CALL_DISPLAY_FIELDS = [
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

SORT_TOPICS = {"order": "ASC", "field": "title"}
SORT_CALLS = {"order": "ASC", "field": "caName"}

QUERY_HORIZON_TOPICS: Mapping[str, Any] = {
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

QUERY_NON_HORIZON_TOPICS: Mapping[str, Any] = {
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

QUERY_HORIZON_CALLS: Mapping[str, Any] = {
    "bool": {
        "must": [
            {"terms": {"type": ["8"]}},
            {"term": {"programmePeriod": "2021 - 2027"}},
            {"terms": {"frameworkProgramme": ["43108390"]}},
        ]
    }
}


@dataclass(frozen=True)
class DatasetConfig:
    """One Search API profile and output filename."""

    key: str
    filename: str
    query: Mapping[str, Any]
    display_fields: List[str]
    sort: Mapping[str, Any]
    source_label: str


DATASETS: Dict[str, DatasetConfig] = {
    "horizon-topics": DatasetConfig(
        key="horizon-topics",
        filename="Horizon-topics.json",
        query=QUERY_HORIZON_TOPICS,
        display_fields=list(TOPIC_DISPLAY_FIELDS),
        sort=SORT_TOPICS,
        source_label="fetch_horizon_topics",
    ),
    "non-horizon-topics": DatasetConfig(
        key="non-horizon-topics",
        filename="Non-Horizon-topics.json",
        query=QUERY_NON_HORIZON_TOPICS,
        display_fields=list(TOPIC_DISPLAY_FIELDS),
        sort=SORT_TOPICS,
        source_label="fetch_non_horizon_topics",
    ),
    "horizon-calls": DatasetConfig(
        key="horizon-calls",
        filename="Horizon-calls.json",
        query=QUERY_HORIZON_CALLS,
        display_fields=list(CALL_DISPLAY_FIELDS),
        sort=SORT_CALLS,
        source_label="fetch_eu_calls",
    ),
}


def _multipart_json_parts(
    query: Mapping[str, Any],
    languages: List[str],
    display_fields: List[str],
    sort: Mapping[str, Any],
) -> List[tuple]:
    """Build multipart parts with application/json content types."""
    return [
        ("query", (None, json.dumps(query, separators=(",", ":")), JSON_PART)),
        ("languages", (None, json.dumps(languages), JSON_PART)),
        (
            "displayFields",
            (None, json.dumps(display_fields), JSON_PART),
        ),
        ("sort", (None, json.dumps(sort, separators=(",", ":")), JSON_PART)),
    ]


def build_search_url(
    base_url: str,
    api_key: str,
    text: str,
    page_size: int,
    page_number: int,
) -> str:
    """Return POST URL with query string parameters.

    Args:
        base_url: Search API base URL (no query string).
        api_key: API key (Postman default is SEDIA).
        text: Search text (use ``***`` for wildcard).
        page_size: Requested ``pageSize`` (responses return at most 100 rows).
        page_number: 1-based page index.

    Returns:
        Full URL including query string.
    """
    qs = urlencode(
        {
            "apiKey": api_key,
            "text": text,
            "pageSize": str(page_size),
            "pageNumber": str(page_number),
        }
    )
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}{qs}"


def fetch_page(
    session: requests.Session,
    url: str,
    multipart_parts: List[tuple],
    timeout_s: float = 120.0,
) -> Dict[str, Any]:
    """POST one page and return parsed JSON.

    Args:
        session: Requests session.
        url: Full URL from ``build_search_url``.
        multipart_parts: Parts from ``_multipart_json_parts``.
        timeout_s: Request timeout in seconds.

    Returns:
        Parsed API JSON object.

    Raises:
        requests.HTTPError: If the response status is not OK.
        ValueError: If the body is not JSON.
    """
    headers = {"Accept": "application/json"}
    response = session.post(
        url,
        files=multipart_parts,
        headers=headers,
        timeout=timeout_s,
    )
    response.raise_for_status()
    try:
        data: Dict[str, Any] = response.json()
    except json.JSONDecodeError as exc:
        raise ValueError("Response is not JSON") from exc
    return data


def flatten_metadata_value(values: Any) -> Any:
    """Turn API metadata field (usually list of strings) into scalar or list."""
    if not isinstance(values, list):
        return values
    if len(values) == 0:
        return None
    if len(values) == 1:
        return values[0]
    return values


class _HTMLTextExtractor(HTMLParser):
    """Collect visible text; add spaces after common block-level tags."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: List[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in {
            "br",
            "p",
            "div",
            "li",
            "tr",
            "td",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        }:
            self._chunks.append(" ")

    def get_text(self) -> str:
        return "".join(self._chunks)


def plain_text_from_html(html_str: str) -> str:
    """Strip tags and HTML entities; collapse whitespace to single spaces.

    Args:
        html_str: HTML or plain text from the API.

    Returns:
        Plain text suitable for JSON export.
    """
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
    text = re.sub(r"\s+", " ", text).strip()
    return text


def looks_like_html(text: str) -> bool:
    """Return True if ``text`` appears to contain an HTML tag."""
    return bool(_TAG_RE.search(text))


def strip_html_field_value(val: Any) -> Any:
    """Strip HTML from a flattened metadata value (str or list of str)."""
    if isinstance(val, str):
        return plain_text_from_html(val)
    if isinstance(val, list):
        return [
            plain_text_from_html(x) if isinstance(x, str) else x for x in val
        ]
    return val


def strip_html_in_budget_overview_parsed(obj: Any) -> Any:
    """Strip HTML from string leaves inside parsed ``budgetOverview`` JSON."""
    if isinstance(obj, str):
        return (
            plain_text_from_html(obj) if looks_like_html(obj) else obj
        )
    if isinstance(obj, list):
        return [strip_html_in_budget_overview_parsed(x) for x in obj]
    if isinstance(obj, dict):
        return {
            k: strip_html_in_budget_overview_parsed(v) for k, v in obj.items()
        }
    return obj


def parse_budget_overview(raw: Any) -> Optional[Any]:
    """Parse ``budgetOverview`` string into an object, or None."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def parse_budget_scalar(raw: Any) -> Optional[int]:
    """Parse call ``budget`` string to int if possible."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def normalize_hit(
    hit: Mapping[str, Any],
    *,
    parse_budget_overview_field: bool,
    strip_html_fields: bool = True,
) -> Dict[str, Any]:
    """Merge top-level hit fields with flattened metadata.

    Args:
        hit: One element of API ``results``.
        parse_budget_overview_field: If True, add ``budgetOverviewParsed``.
        strip_html_fields: If True, convert HTML metadata to plain text.

    Returns:
        Normalized record.
    """
    meta = hit.get("metadata")
    if not isinstance(meta, dict):
        meta = {}

    flat: Dict[str, Any] = {}
    for key, val in meta.items():
        flat[key] = flatten_metadata_value(val)

    if strip_html_fields:
        for key in METADATA_HTML_FIELDS:
            if key in flat:
                flat[key] = strip_html_field_value(flat[key])

    if parse_budget_overview_field and "budgetOverview" in flat:
        parsed = parse_budget_overview(flat.get("budgetOverview"))
        if parsed is not None:
            if strip_html_fields:
                parsed = strip_html_in_budget_overview_parsed(parsed)
            flat["budgetOverviewParsed"] = parsed

    if "budget" in flat:
        parsed_b = parse_budget_scalar(flat.get("budget"))
        if parsed_b is not None:
            flat["budgetParsed"] = parsed_b

    out: Dict[str, Any] = {
        "reference": hit.get("reference"),
        "url": hit.get("url"),
        "summary": hit.get("summary"),
        "metadata": flat,
    }
    return out


def fetch_all_pages(
    session: requests.Session,
    cfg: DatasetConfig,
    *,
    base_url: str,
    api_key: str,
    text: str,
    page_size: int,
    page_delay_s: float,
    max_pages: int,
) -> tuple[int, List[Dict[str, Any]]]:
    """Fetch every page for one dataset.

    Args:
        session: Requests session.
        cfg: Dataset configuration.
        base_url: API base URL.
        api_key: API key.
        text: Search text.
        page_size: Page size.
        page_delay_s: Sleep between pages (0 to disable).
        max_pages: Safety cap on page count.

    Returns:
        Tuple of (total_results, list of raw page payloads merged results).

    Raises:
        RuntimeError: If total_results is inconsistent or max_pages exceeded.
    """
    parts = _multipart_json_parts(
        cfg.query, LANGUAGES, cfg.display_fields, cfg.sort
    )
    all_results: List[Dict[str, Any]] = []
    total_results: Optional[int] = None
    page_number = 1

    while page_number <= max_pages:
        url = build_search_url(base_url, api_key, text, page_size, page_number)
        payload = fetch_page(session, url, parts)
        tr = payload.get("totalResults")
        if not isinstance(tr, int):
            raise RuntimeError(
                f"Missing or invalid totalResults: {tr!r}"
            )
        if total_results is None:
            total_results = tr
        elif total_results != tr:
            raise RuntimeError(
                f"totalResults changed: {total_results} -> {tr}"
            )

        batch = payload.get("results")
        if not isinstance(batch, list):
            raise RuntimeError("results is not a list")
        all_results.extend(batch)

        # Stop when we have all hits (responses cap at API_MAX_PAGE_SIZE rows).
        if len(all_results) >= total_results:
            break
        if len(batch) == 0:
            break
        if page_delay_s > 0:
            time.sleep(page_delay_s)
        page_number += 1

    if total_results is None:
        total_results = 0

    if len(all_results) < total_results:
        raise RuntimeError(
            f"Stopped at max_pages={max_pages}; "
            f"got {len(all_results)} of {total_results} results."
        )

    return total_results, all_results


def build_output_document(
    cfg: DatasetConfig,
    total_results: int,
    raw_results: List[Dict[str, Any]],
    *,
    raw: bool,
    parse_budget_overview_field: bool,
    strip_html_fields: bool = True,
) -> Dict[str, Any]:
    """Build the JSON object written to disk.

    Args:
        cfg: Dataset configuration.
        total_results: API totalResults.
        raw_results: Merged ``results`` from all pages.
        raw: If True, embed raw API hits without normalization.
        parse_budget_overview_field: Passed to ``normalize_hit``.
        strip_html_fields: Passed to ``normalize_hit``.

    Returns:
        Envelope dict for JSON output.
    """
    now = datetime.now(timezone.utc).isoformat()
    if raw:
        items: Any = raw_results
    else:
        items = [
            normalize_hit(
                hit,
                parse_budget_overview_field=parse_budget_overview_field,
                strip_html_fields=strip_html_fields,
            )
            for hit in raw_results
        ]
    return {
        "source": cfg.source_label,
        "fetchedAt": now,
        "totalResults": total_results,
        "items": items,
    }


def write_json(path: Path, data: Mapping[str, Any]) -> None:
    """Write pretty-printed UTF-8 JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=4)
        handle.write("\n")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Download EU Search API topics/calls (three Postman profiles)."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for JSON output files.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=API_MAX_PAGE_SIZE,
        help=(
            f"pageSize query parameter (default {API_MAX_PAGE_SIZE}). "
            "The API only returns up to 100 hits per page; use pagination "
            "to fetch the rest (this script loops automatically)."
        ),
    )
    parser.add_argument(
        "--page-delay",
        type=float,
        default=0.0,
        help="Seconds to sleep between page requests (default 0).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10_000,
        help="Safety limit on number of pages per dataset.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Write raw API hits (no metadata flattening).",
    )
    parser.add_argument(
        "--no-budget-overview-parse",
        action="store_true",
        help="Do not add budgetOverviewParsed for topics.",
    )
    parser.add_argument(
        "--no-strip-html",
        action="store_true",
        help=(
            "Keep raw HTML in metadata (default: strip to plain text "
            "for known rich-text fields)."
        ),
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=sorted(DATASETS.keys()),
        dest="only_datasets",
        help=(
            "Fetch only this dataset (repeatable). "
            "Default: all three."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success).
    """
    args = parse_args(argv)
    if args.page_size < 1 or args.page_size > API_MAX_PAGE_SIZE:
        print(
            f"page-size must be between 1 and {API_MAX_PAGE_SIZE} "
            "(API maximum per page).",
            file=sys.stderr,
        )
        return 1

    base_url = os.environ.get("EU_SEARCH_BASE_URL", DEFAULT_BASE_URL).rstrip(
        "/"
    )
    api_key = os.environ.get("EU_SEARCH_API_KEY", "SEDIA")
    text = os.environ.get("EU_SEARCH_TEXT", "***")

    if args.only_datasets:
        keys: Iterable[str] = args.only_datasets
    else:
        keys = ("horizon-topics", "non-horizon-topics", "horizon-calls")

    parse_budget = not args.no_budget_overview_parse
    strip_html = not args.no_strip_html

    with requests.Session() as session:
        for key in keys:
            cfg = DATASETS[key]
            total_results, raw_hits = fetch_all_pages(
                session,
                cfg,
                base_url=base_url,
                api_key=api_key,
                text=text,
                page_size=args.page_size,
                page_delay_s=args.page_delay,
                max_pages=args.max_pages,
            )
            doc = build_output_document(
                cfg,
                total_results,
                raw_hits,
                raw=args.raw,
                parse_budget_overview_field=parse_budget,
                strip_html_fields=strip_html,
            )
            out_path = args.output_dir / cfg.filename
            write_json(out_path, doc)
            print(
                f"Wrote {out_path} ({len(raw_hits)} items, "
                f"totalResults={total_results})",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
