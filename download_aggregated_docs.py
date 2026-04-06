#!/usr/bin/env python3
"""Thin wrapper for the aggregated procurement downloader CLI.

See iman_ingestion.aggregated.cli for implementation.
"""

from iman_ingestion.aggregated.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
