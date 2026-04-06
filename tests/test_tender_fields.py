"""Unit tests for tender field satisfaction and merge (no live LLM)."""

from __future__ import annotations

import pytest

from iman_ingestion.llm import tender_fields as tf


def _full_discard_flags() -> dict:
    return {
        k: {"applies": False, "evidence": ""}
        for k in tf.CRITERIA_FLAG_KEYS
    }


def test_all_required_satisfied_empty() -> None:
    assert not tf.all_required_satisfied({})


def test_all_required_satisfied_full() -> None:
    acc = {
        "object_of_the_contract": "x",
        "scope_of_the_work": "y",
        "packages": [],
        "economic_solvency": "z",
        "required_profiles": "p",
        "assessment_criteria": "a",
        "outsourcing": {"exists": False, "percentage": None, "notes": ""},
        "discard_review": {
            "summary": "ok",
            "potential_discard": False,
            "reasons_for_manual_review": [],
            "criteria_flags": _full_discard_flags(),
        },
    }
    assert tf.all_required_satisfied(acc)


def test_packages_null_not_satisfied() -> None:
    acc = {
        "object_of_the_contract": "x",
        "scope_of_the_work": "y",
        "packages": None,
        "economic_solvency": "z",
        "required_profiles": "p",
        "assessment_criteria": "a",
        "outsourcing": {"exists": True, "percentage": None, "notes": ""},
        "discard_review": {
            "summary": "s",
            "criteria_flags": _full_discard_flags(),
        },
    }
    assert not tf.all_required_satisfied(acc)


def test_flag_applies_null_not_satisfied() -> None:
    flags = _full_discard_flags()
    flags["iso_certification_required"] = {"applies": None, "evidence": ""}
    acc = {
        "object_of_the_contract": "x",
        "scope_of_the_work": "y",
        "packages": [],
        "economic_solvency": "z",
        "required_profiles": "p",
        "assessment_criteria": "a",
        "outsourcing": {"exists": False, "percentage": None, "notes": ""},
        "discard_review": {"summary": "s", "criteria_flags": flags},
    }
    assert not tf.all_required_satisfied(acc)


def test_list_missing_field_labels_order() -> None:
    missing = tf.list_missing_field_labels({})
    assert "Object of the contract" in missing
    assert missing.index("Object of the contract") < missing.index(
        "Packages / lots and budgets"
    )


def test_merge_tender_partial_strings() -> None:
    acc: dict = {}
    tf.merge_tender_partial(acc, {"object_of_the_contract": "  a  "})
    assert acc["object_of_the_contract"] == "a"
    tf.merge_tender_partial(acc, {"object_of_the_contract": "b"})
    assert acc["object_of_the_contract"] == "a"


def test_merge_tender_partial_packages_union_by_label() -> None:
    acc = {
        "packages": [
            {"label": "Lot 1", "description": "d1", "budget": None},
        ],
    }
    tf.merge_tender_partial(
        acc,
        {
            "packages": [
                {"label": "lot 1", "description": None, "budget": "100"},
                {"label": "Lot 2", "description": None, "budget": None},
            ],
        },
    )
    labels = {p["label"] for p in acc["packages"]}
    assert "Lot 1" in labels or "lot 1" in {p["label"].lower() for p in acc["packages"]}
    assert len(acc["packages"]) == 2


def test_merge_outsourcing_fill_missing() -> None:
    acc = {"outsourcing": {"exists": None, "percentage": None, "notes": ""}}
    tf.merge_tender_partial(
        acc,
        {"outsourcing": {"exists": True, "percentage": "10%", "notes": "n"}},
    )
    assert acc["outsourcing"]["exists"] is True
    assert acc["outsourcing"]["percentage"] == "10%"


def test_merge_discard_criteria_flags() -> None:
    acc = {
        "discard_review": {
            "summary": "",
            "criteria_flags": {
                "iso_certification_required": {
                    "applies": None,
                    "evidence": "",
                },
            },
        },
    }
    tf.merge_tender_partial(
        acc,
        {
            "discard_review": {
                "summary": "review",
                "criteria_flags": {
                    "iso_certification_required": {
                        "applies": True,
                        "evidence": "ISO 9001",
                    },
                },
            },
        },
    )
    dr = acc["discard_review"]
    assert dr["summary"] == "review"
    assert dr["criteria_flags"]["iso_certification_required"]["applies"] is True


def test_multimodal_images_per_request_clamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAN_MULTIMODAL_IMAGES_PER_REQUEST", "99")
    assert tf.multimodal_images_per_request() == 15
    monkeypatch.setenv("IMAN_MULTIMODAL_IMAGES_PER_REQUEST", "0")
    assert tf.multimodal_images_per_request() == 1
    monkeypatch.delenv("IMAN_MULTIMODAL_IMAGES_PER_REQUEST", raising=False)
    assert tf.multimodal_images_per_request() == 12


def test_partial_json_for_prompt_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    huge = {"k": "x" * 5000}
    s = tf.partial_json_for_prompt(huge, max_chars=100)
    assert len(s) <= 100
    assert "truncated" in s


def test_merge_reasons_unique() -> None:
    acc = {
        "discard_review": {
            "reasons_for_manual_review": ["a"],
        },
    }
    tf.merge_tender_partial(
        acc,
        {
            "discard_review": {
                "reasons_for_manual_review": ["a", "b"],
            },
        },
    )
    assert acc["discard_review"]["reasons_for_manual_review"] == ["a", "b"]
