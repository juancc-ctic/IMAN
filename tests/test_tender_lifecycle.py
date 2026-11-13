"""Tender Lifecycle: fixture Atom feeds -> ingestion -> lifecycle module -> Postgres.

All assertions go through the lifecycle module's read interface.
Requires ``IMAN_TEST_DATABASE_URL`` (skipped otherwise).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import pytest

from iman_ingestion import lifecycle
from iman_ingestion.aggregated.ingestion import IngestionConfig, run_ingestion
from tests.feed_builder import Deleted, Entry, Lot, Result, write_feed_chain

REAL_AWARDS_FEED = Path(__file__).parent / "fixtures" / "feeds" / "real_awards.atom"


@pytest.fixture
def ingest(db_session, tmp_path):
    """Run one pipeline pass over a feed chain given newest-first pages."""
    runs = {"n": 0}

    def _ingest(*pages: Sequence[Entry | Deleted] | Path) -> lifecycle.ApplySummary:
        runs["n"] += 1
        run_dir = tmp_path / f"run{runs['n']}"
        if len(pages) == 1 and isinstance(pages[0], Path):
            first = pages[0]
        else:
            first = write_feed_chain(run_dir, pages)
        scope = lifecycle.ingestion_scope(db_session)
        config = IngestionConfig(
            atom_source=str(first),
            output_dir=run_dir / "downloads",
            json_out=run_dir / "tenders.json",
            no_download=True,
            cutoff_utc=datetime(2000, 1, 1, tzinfo=timezone.utc),
            tracked_ids=scope.tracked_ids,
            known_ids=scope.known_ids,
        )
        result = run_ingestion(config)
        return lifecycle.apply_feed_versions(db_session, result.versions)

    return _ingest


def test_discovered_tender_shows_its_current_status(db_session, ingest):
    ingest([Entry(id="100", updated="2026-11-01T10:00:00+01:00", status="PUB")])

    tender = lifecycle.get_tender(db_session, "100")
    assert tender is not None
    assert tender.status == "PUB"
    assert tender.tracked is False


def test_known_tender_status_follows_feed_even_when_filtered_out(db_session, ingest):
    ingest([Entry(id="100", updated="2026-11-01T10:00:00+01:00", status="PUB")])

    ingest([Entry(id="100", updated="2026-11-05T10:00:00+01:00", status="EV")])

    assert lifecycle.get_tender(db_session, "100").status == "EV"


def test_unknown_tender_in_later_stage_is_not_discovered(db_session, ingest):
    ingest([Entry(id="200", updated="2026-11-05T10:00:00+01:00", status="EV")])

    assert lifecycle.get_tender(db_session, "200") is None


def test_older_version_never_overwrites_newer_status(db_session, ingest):
    ingest(
        [Entry(id="100", updated="2026-11-05T10:00:00+01:00", status="EV")],
        [Entry(id="100", updated="2026-11-01T10:00:00+01:00", status="PUB")],
    )

    assert lifecycle.get_tender(db_session, "100").status == "EV"


# ---------------------------------------------------------------------------
# Starting and stopping tracking
# ---------------------------------------------------------------------------


def _published(ingest, tender_id="100", **kw):
    ingest([Entry(id=tender_id, updated="2026-11-01T10:00:00+01:00", status="PUB", **kw)])


def test_start_tracking_records_who_and_a_snapshot(db_session, ingest):
    _published(ingest, title="Gemelo digital", deadline="2026-12-01")

    lifecycle.start_tracking(db_session, "100", by="Ana")

    tender = lifecycle.get_tender(db_session, "100")
    assert tender.tracked is True
    assert tender.tracked_by == "Ana"
    [started] = lifecycle.timeline(db_session, "100")
    assert started.kind == "tracking_started"
    assert started.by == "Ana"
    assert started.snapshot["status"] == "PUB"
    assert started.snapshot["title"] == "Gemelo digital"
    assert started.snapshot["submission_deadline"] == "2026-12-01"


def test_cannot_track_unknown_tender(db_session):
    with pytest.raises(lifecycle.TenderNotFound):
        lifecycle.start_tracking(db_session, "nope", by="Ana")


@pytest.mark.parametrize("terminal", ["RES", "ANUL"])
def test_cannot_track_tender_that_is_already_finished(db_session, ingest, terminal):
    _published(ingest)
    ingest([Entry(id="100", updated="2026-11-05T10:00:00+01:00", status=terminal)])

    with pytest.raises(lifecycle.TrackingRefused):
        lifecycle.start_tracking(db_session, "100", by="Ana")
    assert lifecycle.get_tender(db_session, "100").tracked is False


def test_starting_tracking_twice_is_harmless(db_session, ingest):
    _published(ingest)

    lifecycle.start_tracking(db_session, "100", by="Ana")
    lifecycle.start_tracking(db_session, "100", by="Luis")

    assert [e.kind for e in lifecycle.timeline(db_session, "100")] == ["tracking_started"]
    assert lifecycle.get_tender(db_session, "100").tracked_by == "Ana"


def test_stop_tracking_records_who(db_session, ingest):
    _published(ingest)
    lifecycle.start_tracking(db_session, "100", by="Ana")

    lifecycle.stop_tracking(db_session, "100", by="Luis")

    tender = lifecycle.get_tender(db_session, "100")
    assert tender.tracked is False
    assert tender.tracking_end_reason == "manual"
    stopped = lifecycle.timeline(db_session, "100")[-1]
    assert (stopped.kind, stopped.by, stopped.end_reason) == ("tracking_stopped", "Luis", "manual")


def test_cannot_stop_tracking_an_untracked_tender(db_session, ingest):
    _published(ingest)

    with pytest.raises(lifecycle.NotTracked):
        lifecycle.stop_tracking(db_session, "100", by="Luis")


def test_tracking_again_continues_the_same_timeline(db_session, ingest):
    _published(ingest)
    lifecycle.start_tracking(db_session, "100", by="Ana")
    lifecycle.stop_tracking(db_session, "100", by="Ana")
    ingest([Entry(id="100", updated="2026-11-05T10:00:00+01:00", status="EV")])

    lifecycle.start_tracking(db_session, "100", by="Luis")

    entries = lifecycle.timeline(db_session, "100")
    assert [e.kind for e in entries] == ["tracking_started", "tracking_stopped", "tracking_started"]
    assert entries[-1].snapshot["status"] == "EV"
    tender = lifecycle.get_tender(db_session, "100")
    assert (tender.tracked, tender.tracked_by, tender.tracking_end_reason) == (True, "Luis", None)


def test_lists_tracked_and_ended_tenders(db_session, ingest):
    ingest(
        [
            Entry(id="1", updated="2026-11-01T10:00:00+01:00"),
            Entry(id="2", updated="2026-11-01T10:00:00+01:00"),
            Entry(id="3", updated="2026-11-01T10:00:00+01:00"),
        ]
    )
    lifecycle.start_tracking(db_session, "1", by="Ana")
    lifecycle.start_tracking(db_session, "2", by="Ana")
    lifecycle.stop_tracking(db_session, "2", by="Ana")

    assert [t.id for t in lifecycle.list_tracked(db_session)] == ["1"]
    assert [t.id for t in lifecycle.list_tracked(db_session, ended=True)] == ["2"]


# ---------------------------------------------------------------------------
# Tender Changes from the feed
# ---------------------------------------------------------------------------


def _tracked(db_session, ingest, tender_id="100", **kw):
    _published(ingest, tender_id, **kw)
    lifecycle.start_tracking(db_session, tender_id, by="Ana")


def _changes(db_session, tender_id="100"):
    return [e for e in lifecycle.timeline(db_session, tender_id) if e.kind == "change"]


def test_status_transition_is_recorded_with_feed_date(db_session, ingest):
    _tracked(db_session, ingest)

    summary = ingest([Entry(id="100", updated="2026-11-10T09:30:00+01:00", status="EV")])

    [change] = _changes(db_session)
    assert (change.status_before, change.status_after) == ("PUB", "EV")
    assert change.occurred_at == datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc)
    assert change.changes["status"] == {"before": "PUB", "after": "EV"}
    assert (summary.tracked_versions, summary.changes_recorded) == (1, 1)


def test_tracked_tender_bypasses_all_discovery_filters(db_session, ingest):
    _tracked(db_session, ingest)

    ingest(
        [
            Entry(
                id="100",
                updated="2026-11-10T09:30:00+01:00",
                status="EV",
                subtype_code="99",
                cpv="45000000",
            )
        ]
    )

    assert [c.status_after for c in _changes(db_session)] == ["EV"]


def test_several_versions_in_one_run_are_recorded_oldest_first(db_session, ingest):
    _tracked(db_session, ingest)

    ingest(
        [Entry(id="100", updated="2026-11-20T10:00:00+01:00", status="ADJ")],
        [Entry(id="100", updated="2026-11-10T10:00:00+01:00", status="EV")],
    )

    assert [(c.status_before, c.status_after) for c in _changes(db_session)] == [
        ("PUB", "EV"),
        ("EV", "ADJ"),
    ]
    assert lifecycle.get_tender(db_session, "100").status == "ADJ"


def test_processing_the_same_feed_twice_records_nothing_new(db_session, ingest):
    _tracked(db_session, ingest)
    page = [Entry(id="100", updated="2026-11-10T10:00:00+01:00", status="EV")]

    ingest(page)
    summary = ingest(page)

    assert len(_changes(db_session)) == 1
    assert summary.changes_recorded == 0


def test_missed_versions_collapse_into_one_change(db_session, ingest):
    _tracked(db_session, ingest)

    ingest([Entry(id="100", updated="2026-12-01T10:00:00+01:00", status="ADJ", budget="90000")])

    [change] = _changes(db_session)
    assert change.changes["status"] == {"before": "PUB", "after": "ADJ"}
    assert change.changes["tax_exclusive_amount"] == {"before": "100000", "after": "90000"}


@pytest.mark.parametrize("status,reason", [("RES", "resolved"), ("ANUL", "cancelled")])
def test_terminal_status_ends_tracking(db_session, ingest, status, reason):
    _tracked(db_session, ingest)

    ingest([Entry(id="100", updated="2026-12-01T10:00:00+01:00", status=status)])

    tender = lifecycle.get_tender(db_session, "100")
    assert (tender.tracked, tender.tracking_end_reason) == (False, reason)
    kinds = [e.kind for e in lifecycle.timeline(db_session, "100")]
    assert kinds == ["tracking_started", "change", "tracking_ended"]
    assert lifecycle.timeline(db_session, "100")[-1].end_reason == reason
    assert [t.id for t in lifecycle.list_tracked(db_session, ended=True)] == ["100"]


def test_untracked_tender_keeps_no_history(db_session, ingest):
    _published(ingest)

    ingest([Entry(id="100", updated="2026-11-10T10:00:00+01:00", status="EV")])

    assert lifecycle.timeline(db_session, "100") == []


# ---------------------------------------------------------------------------
# Amendments
# ---------------------------------------------------------------------------


def test_deadline_extension_is_recorded_and_details_refreshed(db_session, ingest):
    _tracked(db_session, ingest, deadline="2026-12-01")

    ingest([Entry(id="100", updated="2026-11-10T10:00:00+01:00", status="PUB", deadline="2026-12-15")])

    [change] = _changes(db_session)
    assert change.changes == {"submission_deadline": {"before": "2026-12-01", "after": "2026-12-15"}}
    assert (change.status_before, change.status_after) == ("PUB", "PUB")
    assert lifecycle.get_tender(db_session, "100").details["submission_deadline"] == "2026-12-15"


def test_replaced_documents_are_recorded(db_session, ingest):
    _tracked(db_session, ingest, pcap_url="https://example.org/pcap-v1.pdf")

    ingest(
        [
            Entry(
                id="100",
                updated="2026-11-10T10:00:00+01:00",
                pcap_url="https://example.org/pcap-v2.pdf",
            )
        ]
    )

    [change] = _changes(db_session)
    assert change.changes["pcap_url"] == {
        "before": "https://example.org/pcap-v1.pdf",
        "after": "https://example.org/pcap-v2.pdf",
    }


def test_refresh_keeps_existing_enrichment_and_triage(db_session, ingest):
    from iman_ingestion.db.models import Tender

    _tracked(db_session, ingest)
    t = db_session.get(Tender, "100")
    t.enrichment = {"contract_scope": "Plataforma"}
    t.triage = {"overall_score": 4.2}
    t.triage_score = 4.2
    db_session.flush()

    ingest([Entry(id="100", updated="2026-11-10T10:00:00+01:00", title="Nuevo título")])

    tender = lifecycle.get_tender(db_session, "100")
    assert tender.details["title"] == "Nuevo título"
    assert tender.enrichment == {"contract_scope": "Plataforma"}
    assert (tender.triage, tender.triage_score) == ({"overall_score": 4.2}, 4.2)


# ---------------------------------------------------------------------------
# Withdrawal from the feed
# ---------------------------------------------------------------------------


def test_withdrawal_from_feed_ends_tracking_without_claiming_cancellation(db_session, ingest):
    _tracked(db_session, ingest)

    ingest([Deleted(id="100", when="2026-11-12T00:00:10+01:00")])

    tender = lifecycle.get_tender(db_session, "100")
    assert (tender.tracked, tender.tracking_end_reason, tender.status) == (False, "withdrawn", "PUB")
    ended = lifecycle.timeline(db_session, "100")[-1]
    assert (ended.kind, ended.end_reason) == ("tracking_ended", "withdrawn")
    assert ended.occurred_at == datetime(2026, 11, 11, 23, 0, 10, tzinfo=timezone.utc)


def test_withdrawal_of_untracked_tender_records_nothing(db_session, ingest):
    _published(ingest)

    ingest([Deleted(id="100", when="2026-11-12T00:00:10+01:00")])

    assert lifecycle.timeline(db_session, "100") == []
    assert lifecycle.get_tender(db_session, "100").status == "PUB"


# ---------------------------------------------------------------------------
# Awards per Lot (real feed entries captured 2026-11-13)
# ---------------------------------------------------------------------------


def _awards_by_lot(db_session, tender_id):
    return {a.lot_id: a for a in lifecycle.awards(db_session, tender_id)}


def test_multi_lot_award_is_recorded_per_lot(db_session, ingest):
    _tracked(db_session, ingest, tender_id="20438334")

    ingest(REAL_AWARDS_FEED)

    awards = _awards_by_lot(db_session, "20438334")
    assert set(awards) == {"1", "2"}
    lot1 = awards["1"]
    assert lot1.lot_name.startswith("Delimitació de propietat")
    assert (lot1.winner_name, lot1.winner_nif) == ("Acsa, Obras e Infraestructuras, S.A.U.", "A08112716")
    assert (lot1.award_amount, lot1.bids_received, lot1.void) == ("102684.87", 1, False)
    assert awards["2"].award_amount == "57960.76"
    tender = lifecycle.get_tender(db_session, "20438334")
    assert (tender.status, tender.tracking_end_reason) == ("RES", "resolved")
    [change] = _changes(db_session, "20438334")
    assert change.changes["status"] == {"before": "PUB", "after": "RES"}
    assert "awards" in change.changes


def test_void_lot_is_recorded_as_void(db_session, ingest):
    _tracked(db_session, ingest, tender_id="15173741")

    ingest(REAL_AWARDS_FEED)

    awards = _awards_by_lot(db_session, "15173741")
    assert len(awards) == 10
    void = awards["660712"]
    assert (void.void, void.winner_name, void.bids_received) == (True, None, 0)
    assert void.lot_name == "Lote 10 - Alimentación diversa"
    assert awards["660703"].winner_name == "BURUTXAGA HARAKINTZAK,S.L."
    assert awards["660703"].void is False


def test_tender_without_lots_is_awarded_as_a_single_lot(db_session, ingest):
    _tracked(db_session, ingest, tender_id="20666652")

    ingest(REAL_AWARDS_FEED)

    [award] = lifecycle.awards(db_session, "20666652")
    assert (award.lot_id, award.winner_name, award.award_amount, award.bids_received) == (
        "1",
        "AUDENIS, S.L.",
        "28921.19",
        2,
    )
    assert lifecycle.get_tender(db_session, "20666652").status == "ADJ"


def test_lots_added_by_amendment_appear_in_the_change(db_session, ingest):
    _tracked(db_session, ingest)

    ingest(
        [
            Entry(
                id="100",
                updated="2026-11-10T10:00:00+01:00",
                lots=[Lot(id="1", name="Lote 1 - Software", amount="60000"), Lot(id="2", name="Lote 2 - Formación")],
            )
        ]
    )

    [change] = _changes(db_session)
    assert change.changes["lots"]["before"] == []
    assert [lot["id"] for lot in change.changes["lots"]["after"]] == ["1", "2"]
    assert change.changes["lots"]["after"][0] == {"id": "1", "name": "Lote 1 - Software", "amount": "60000"}


def test_award_changes_are_updated_not_duplicated(db_session, ingest):
    _tracked(db_session, ingest)
    ingest(
        [
            Entry(
                id="100",
                updated="2026-11-10T10:00:00+01:00",
                status="ADJ",
                results=[Result(code="8", winner="Datos Norte S.L.", amount="80000", bids=3)],
            )
        ]
    )

    ingest(
        [
            Entry(
                id="100",
                updated="2026-11-20T10:00:00+01:00",
                status="ADJ",
                results=[Result(code="9", winner="Datos Norte S.L.", amount="79500", bids=3)],
            )
        ]
    )

    [award] = lifecycle.awards(db_session, "100")
    assert (award.result_code, award.award_amount) == ("9", "79500")
    assert len(_changes(db_session)) == 2


def test_untracked_tenders_get_no_awards(db_session, ingest):
    _published(ingest, "20666652")

    ingest(REAL_AWARDS_FEED)

    assert lifecycle.awards(db_session, "20666652") == []


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------


def test_old_withdrawal_does_not_end_tracking_started_afterwards(db_session, ingest):
    _tracked(db_session, ingest)
    tombstone = [Deleted(id="100", when="2026-11-12T00:00:10+01:00")]
    ingest(tombstone)
    lifecycle.start_tracking(db_session, "100", by="Luis")

    ingest(tombstone)

    assert lifecycle.get_tender(db_session, "100").tracked is True
    kinds = [e.kind for e in lifecycle.timeline(db_session, "100")]
    assert kinds == ["tracking_started", "tracking_ended", "tracking_started"]


def test_award_for_a_lot_no_longer_in_the_feed_is_dropped(db_session, ingest):
    _tracked(db_session, ingest)
    ingest(
        [
            Entry(
                id="100",
                updated="2026-11-10T10:00:00+01:00",
                status="ADJ",
                lots=[Lot(id="1"), Lot(id="2")],
                results=[
                    Result(lot_id="1", winner="Datos Norte S.L.", amount="50000"),
                    Result(lot_id="2", winner="Formación Sur S.L.", amount="20000"),
                ],
            )
        ]
    )

    ingest(
        [
            Entry(
                id="100",
                updated="2026-11-20T10:00:00+01:00",
                status="ADJ",
                lots=[Lot(id="1")],
                results=[Result(lot_id="1", winner="Datos Norte S.L.", amount="50000")],
            )
        ]
    )

    assert [a.lot_id for a in lifecycle.awards(db_session, "100")] == ["1"]
    last = _changes(db_session)[-1]
    assert [a["lot_id"] for a in last.changes["awards"]["after"]] == ["1"]


def test_filtered_out_entry_only_refreshes_status_of_untracked_tender(db_session, ingest):
    _published(ingest, pcap_url="https://example.org/pcap-v1.pdf")

    ingest(
        [
            Entry(
                id="100",
                updated="2026-11-10T10:00:00+01:00",
                status="EV",
                pcap_url="https://example.org/pcap-v2.pdf",
            )
        ]
    )

    tender = lifecycle.get_tender(db_session, "100")
    assert tender.status == "EV"
    assert tender.details["pcap_url"] == "https://example.org/pcap-v1.pdf"
