"""CLI entry point for aggregated procurement ingestion."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from iman_ingestion.aggregated.ingestion import (
    IngestionConfig,
    parse_cutoff_datetime,
    run_ingestion,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download PDFs from the Plataformas Agregadas Sin Menores ATOM feed."
        ),
    )
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

    config = IngestionConfig(
        atom_source=args.atom_source,
        output_dir=args.output,
        json_out=args.json_out,
        cutoff_utc=cutoff_utc,
        max_tries=args.max_tries,
        no_download=args.no_download,
    )

    try:
        result = run_ingestion(config, verbose=True)
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

    if result.failed_with_detail:
        print("\n--- Documents that failed (500) ---")
        print("Download them from the tender page in your browser:")
        seen_urls = set()
        for name, detail_url in result.failed_with_detail:
            if detail_url not in seen_urls:
                seen_urls.add(detail_url)
                print(f"  {detail_url}")
        print(
            "\n(You can try opening the tender page in your browser to download "
            "documents manually.)",
        )

    print(f"\nDownloaded {result.ok}/{result.total} documents.")
    if result.json_out:
        print(f"Data exported to JSON in: {result.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
