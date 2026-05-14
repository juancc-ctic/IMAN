#!/usr/bin/env python3
"""Generate seeds/seed.sql from the live database.

Usage:
    IMAN_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/iman python seeds/generate_seed.py
    # or with docker-compose running on default port:
    python seeds/generate_seed.py
"""
import json
import os
import sys
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2 not found. Install with: pip install psycopg2-binary")
    sys.exit(1)

EU_ITEMS_TOP_N = 20
OUTPUT = Path(__file__).parent / "seed.sql"
DATABASE_URL = os.getenv(
    "IMAN_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/iman"
)

COMPANY_PROFILE_COLS = [
    "id", "interest_areas", "company_fields", "past_tender_categories",
    "triage_dimensions", "tender_filters", "action_plan_text",
    "action_plan_embedding", "updated_at",
]
TENDERS_COLS = [
    "id", "link", "title", "party_name", "tax_exclusive_amount",
    "estimated_overall_contract_amount", "enrichment", "summary",
    "summary_embedding", "triage", "triage_score", "execution_period",
    "pcap_url", "ppt_url", "submission_deadline", "created_at", "updated_at",
]
EU_ITEMS_COLS = [
    "reference", "kind", "url", "identifier", "title", "status",
    "start_date", "deadline_date", "metadata", "embed_text", "embedding",
    "triage", "triage_score", "framework_programme", "programme_period",
    "programme_division", "programme_part", "mission_group",
    "created_at", "updated_at",
]

def literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (dict, list)):
        escaped = json.dumps(value, ensure_ascii=False).replace("'", "''")
        return f"'{escaped}'"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def row_to_insert(table: str, pk_cols: list[str], columns: list[str], row) -> str:
    col_list = ", ".join(columns)
    val_list = ", ".join(literal(row[c]) for c in columns)
    update_set = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in columns if c not in pk_cols
    )
    conflict_target = ", ".join(pk_cols)
    return (
        f"INSERT INTO {table} ({col_list}) VALUES ({val_list})\n"
        f"  ON CONFLICT ({conflict_target}) DO UPDATE SET {update_set};"
    )


def fetch_and_write(conn) -> str:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    lines: list[str] = [
        "-- Auto-generated seed data. Re-generate with: python seeds/generate_seed.py",
        f"-- company_profile, tenders (all), eu_items (top {EU_ITEMS_TOP_N} by triage_score)",
        "-- CORDIS tables loaded separately via: load-cordis-data --limit 100",
        "",
        "SET client_encoding = 'UTF8';",
        "SET standard_conforming_strings = on;",
        "",
    ]

    # ── company_profile ──────────────────────────────────────────────────────
    lines.append("-- company_profile")
    cur.execute(f"SELECT {', '.join(COMPANY_PROFILE_COLS)} FROM company_profile LIMIT 1")
    for row in cur.fetchall():
        lines.append(row_to_insert("company_profile", ["id"], COMPANY_PROFILE_COLS, row))
    lines.append("")

    # ── tenders ──────────────────────────────────────────────────────────────
    lines.append("-- tenders (all rows, ordered by triage_score DESC)")
    cur.execute(f"SELECT {', '.join(TENDERS_COLS)} FROM tenders ORDER BY triage_score DESC NULLS LAST")
    for row in cur.fetchall():
        lines.append(row_to_insert("tenders", ["id"], TENDERS_COLS, row))
    lines.append("")

    # ── eu_items ─────────────────────────────────────────────────────────────
    lines.append(f"-- eu_items (top {EU_ITEMS_TOP_N} by triage_score)")
    cur.execute(
        f"SELECT {', '.join(EU_ITEMS_COLS)} FROM eu_items WHERE triage_score IS NOT NULL "
        "ORDER BY triage_score DESC NULLS LAST LIMIT %s",
        (EU_ITEMS_TOP_N,),
    )
    for row in cur.fetchall():
        lines.append(row_to_insert("eu_items", ["reference"], EU_ITEMS_COLS, row))
    lines.append("")

    cur.close()
    return "\n".join(lines)


def main():
    print(f"Connecting to {DATABASE_URL} …")
    conn = psycopg2.connect(DATABASE_URL)
    try:
        sql = fetch_and_write(conn)
    finally:
        conn.close()

    OUTPUT.write_text(sql, encoding="utf-8")
    size_kb = OUTPUT.stat().st_size // 1024
    print(f"Wrote {OUTPUT} ({size_kb:,} KB)")


if __name__ == "__main__":
    main()
