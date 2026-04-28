"""Dagster assets: EU topics and calls ingestion, persistence, embeddings, and triage."""

from __future__ import annotations

import os
import time
from collections import Counter
from typing import Any, Dict, List

from dagster import asset
from sqlalchemy import select

from iman_ingestion.db.models import EuItem
from iman_ingestion.db.session import session_scope
from iman_ingestion.eu.client import DEFAULT_BASE_URL, fetch_eu_datasets
from iman_ingestion.llm.client import chat_model_name, embed_texts, get_embeddings_client, get_llm_client
from iman_ingestion.triage import evaluate_eu_item, load_company_profile


@asset(group_name="eu", compute_kind="python")
def raw_eu_ingestion(context) -> List[Dict[str, Any]]:
    """Fetch EU topics and calls from the Search API; return normalized item list."""
    base_url = os.environ.get("EU_SEARCH_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    api_key = os.environ.get("EU_SEARCH_API_KEY", "SEDIA")
    text = os.environ.get("EU_SEARCH_TEXT", "***")
    page_delay_s = float(os.environ.get("EU_PAGE_DELAY", "0.0"))

    items = fetch_eu_datasets(
        base_url=base_url,
        api_key=api_key,
        text=text,
        page_delay_s=page_delay_s,
    )

    counts = Counter(item["kind"] for item in items)
    context.log.info(
        "raw_eu_ingestion: fetched %d items total — %s",
        len(items),
        dict(counts),
    )
    context.add_output_metadata({"total_items": len(items), **{k: v for k, v in counts.items()}})
    return items


@asset(group_name="eu", compute_kind="postgres")
def persist_eu_items(context, raw_eu_ingestion: List[Dict[str, Any]]) -> int:
    """Upsert EU items into the ``eu_items`` table."""
    count = 0
    with session_scope() as session:
        for row in raw_eu_ingestion:
            ref = row.get("reference")
            if not ref:
                continue
            item = EuItem(
                reference=ref,
                kind=row["kind"],
                url=row.get("url"),
                identifier=row.get("identifier"),
                title=row.get("title"),
                status=row.get("status"),
                start_date=row.get("start_date"),
                deadline_date=row.get("deadline_date"),
                item_metadata=row.get("metadata"),
                embed_text=row.get("embed_text") or None,
            )
            session.merge(item)
            count += 1
    context.log.info("persist_eu_items: upserted %d rows", count)
    context.add_output_metadata({"upserted": count})
    return count


@asset(group_name="eu", compute_kind="openai")
def eu_item_embeddings(
    context,
    persist_eu_items: int,
    raw_eu_ingestion: List[Dict[str, Any]],
) -> int:
    """Generate and store one embedding per EU item (descriptionByte / description)."""
    if os.environ.get("IMAN_SKIP_EMBEDDINGS", "").lower() in ("1", "true", "yes"):
        context.log.info("IMAN_SKIP_EMBEDDINGS set; skipping EU embeddings.")
        return 0

    batch_size = int(os.environ.get("IMAN_EMBED_BATCH_SIZE", "16"))
    embeddings_client = get_embeddings_client()

    # Character limit to stay within the embedding model's token budget.
    # Arctic Embed L v2.0 caps at 8192 tokens; ~4 chars/token → 16 000 chars is safe.
    max_chars = int(os.environ.get("EU_EMBED_MAX_CHARS", "16000"))

    embeddable = [r for r in raw_eu_ingestion if r.get("embed_text")]
    context.log.info(
        "eu_item_embeddings: %d of %d items have embed_text (max_chars=%d)",
        len(embeddable),
        len(raw_eu_ingestion),
        max_chars,
    )

    embedded = 0
    with session_scope() as session:
        # Load all EuItem rows into identity map so updates are tracked.
        refs = [r["reference"] for r in embeddable]
        db_items: Dict[str, EuItem] = {
            item.reference: item
            for item in session.scalars(
                select(EuItem).where(EuItem.reference.in_(refs))
            ).all()
        }

        for off in range(0, len(embeddable), batch_size):
            batch = embeddable[off : off + batch_size]
            texts = [r["embed_text"][:max_chars] for r in batch]
            vecs = embed_texts(embeddings_client, texts)
            for row, vec in zip(batch, vecs):
                db_item = db_items.get(row["reference"])
                if db_item is not None:
                    db_item.embedding = vec
                    embedded += 1

    context.log.info("eu_item_embeddings: embedded %d items", embedded)
    context.add_output_metadata({"embedded": embedded})
    return embedded


@asset(group_name="eu", compute_kind="openai")
def eu_item_triage(
    context,
    eu_item_embeddings: int,
) -> int:
    """Evaluate each EU item against the company profile and assign a triage status."""
    if os.environ.get("IMAN_SKIP_TRIAGE", "").lower() in ("1", "true", "yes"):
        context.log.info("IMAN_SKIP_TRIAGE is set; skipping EU item triage.")
        return 0

    company_profile = load_company_profile()
    llm_client = get_llm_client()
    pipeline_start = time.perf_counter()
    counters: Dict[str, int] = {"recommended": 0, "neutral": 0, "potential_discard": 0, "skipped": 0}

    TRIAGE_STATUSES = ("31094502", "31094501")  # Open for submission, Forthcoming

    with session_scope() as session:
        items = session.scalars(
            select(EuItem).where(
                EuItem.embed_text.isnot(None),
                EuItem.status.in_(TRIAGE_STATUSES),
            )
        ).all()
        n = len(items)
        context.log.info(
            "eu_item_triage: evaluating %d EU item(s) with embed_text and active status; model=%r",
            n,
            chat_model_name(),
        )
        for i, item in enumerate(items, start=1):
            t0 = time.perf_counter()
            try:
                result = evaluate_eu_item(
                    reference=item.reference,
                    title=item.title or "",
                    kind=item.kind,
                    url=item.url,
                    deadline_date=item.deadline_date,
                    embed_text=item.embed_text,
                    llm_client=llm_client,
                    company_profile=company_profile,
                )
            except Exception as exc:
                context.log.warning("[%d/%d] triage failed for %r: %s", i, n, item.reference, exc)
                counters["skipped"] += 1
                continue
            item.triage = result
            item.triage_status = result.get("status")
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            context.log.info(
                "[%d/%d] ref=%r status=%r score=%s dims=%d elapsed=%.0f ms",
                i,
                n,
                item.reference,
                result.get("status"),
                result.get("overall_score"),
                len(result.get("dimensions") or []),
                elapsed_ms,
            )
            status_key = result.get("status", "neutral")
            counters[status_key] = counters.get(status_key, 0) + 1

    total_s = time.perf_counter() - pipeline_start
    context.log.info("eu_item_triage finished in %.2f s: %s", total_s, counters)
    context.add_output_metadata(
        {
            "recommended": counters["recommended"],
            "neutral": counters["neutral"],
            "potential_discard": counters["potential_discard"],
            "skipped": counters["skipped"],
            "total_seconds": round(total_s, 3),
        }
    )
    return counters["recommended"] + counters["neutral"] + counters["potential_discard"]
