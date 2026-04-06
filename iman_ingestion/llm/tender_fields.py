"""Field satisfaction, missing-field labels, and merge for batched tender LLM."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Literal, Set

MergeMode = Literal["first_wins", "batch_overwrites"]

# Top-level string fields from the tender analysis schema.
TOP_LEVEL_STRING_KEYS = (
    "object_of_the_contract",
    "scope_of_the_work",
    "economic_solvency",
    "required_profiles",
    "assessment_criteria",
)

# Expected keys under discard_review.criteria_flags.
CRITERIA_FLAG_KEYS = (
    "place_of_execution_not_asturias",
    "execution_period_under_2_months",
    "maintenance_longer_than_1_year",
    "asks_technical_assistance_service",
    "iso_certification_required",
    "ens_certification_required",
    "pmi_certified_profile_required",
    "economic_offer_weight_over_70_points",
)

# Human-readable labels for prompts (key -> label).
_FIELD_LABELS: Dict[str, str] = {
    "object_of_the_contract": "Object of the contract",
    "scope_of_the_work": "Scope of the work",
    "packages": "Packages / lots and budgets",
    "economic_solvency": "Economic solvency requirements",
    "required_profiles": "Required profiles / team",
    "assessment_criteria": "Assessment criteria (incl. points)",
    "outsourcing": "Outsourcing (exists, %, notes)",
    "discard_review.summary": "Discard review summary",
    **{
        f"discard_review.criteria_flags.{k}": f"Criterion: {k}"
        for k in CRITERIA_FLAG_KEYS
    },
}


# Batching: PCAP pages are sent in chunks of this many ``image_url`` parts per request.
_DEFAULT_MULTIMODAL_IMAGES_PER_REQUEST = 20
# Vision APIs differ; keep an upper bound to avoid huge single payloads.
_MAX_MULTIMODAL_IMAGES_PER_REQUEST = 64


def multimodal_images_per_request() -> int:
    """Return max images per multimodal chat request from env.

    Reads ``IMAN_MULTIMODAL_IMAGES_PER_REQUEST`` (default 20). Invalid values
    fall back to the default. Result is clamped to ``[1, _MAX_MULTIMODAL_IMAGES_PER_REQUEST]``.

    Returns:
        Integer in ``[1, 64]`` (cap may change with ``_MAX_MULTIMODAL_IMAGES_PER_REQUEST``).
    """
    try:
        raw = int(
            os.environ.get(
                "IMAN_MULTIMODAL_IMAGES_PER_REQUEST",
                str(_DEFAULT_MULTIMODAL_IMAGES_PER_REQUEST),
            ),
        )
    except ValueError:
        raw = _DEFAULT_MULTIMODAL_IMAGES_PER_REQUEST
    return max(1, min(_MAX_MULTIMODAL_IMAGES_PER_REQUEST, raw))


def _nonempty_str(val: Any) -> bool:
    return isinstance(val, str) and bool(val.strip())


def _packages_satisfied(val: Any) -> bool:
    """Satisfied when value is a list (empty = explicit no packages)."""
    return isinstance(val, list)


def _outsourcing_satisfied(val: Any) -> bool:
    """Satisfied when exists is set or notes/percentage are meaningful."""
    if not isinstance(val, dict):
        return False
    if val.get("exists") is not None:
        return True
    if _nonempty_str(val.get("notes")):
        return True
    if _nonempty_str(val.get("percentage")):
        return True
    return False


def _flag_entry_satisfied(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    return entry.get("applies") is not None


def _discard_review_satisfied(dr: Any) -> bool:
    if not isinstance(dr, dict):
        return False
    if not _nonempty_str(dr.get("summary")):
        return False
    flags = dr.get("criteria_flags")
    if not isinstance(flags, dict):
        return False
    for key in CRITERIA_FLAG_KEYS:
        if not _flag_entry_satisfied(flags.get(key)):
            return False
    return True


def all_required_satisfied(accumulated: Dict[str, Any]) -> bool:
    """Whether the accumulated extraction is complete enough to stop batching.

    Args:
        accumulated: Merged partial JSON from prior batches.

    Returns:
        True if all top-level strings, ``packages`` (list, possibly empty),
        ``outsourcing``, and ``discard_review`` (summary + eight flags with
        non-null ``applies``) are satisfied.
    """
    for key in TOP_LEVEL_STRING_KEYS:
        if not _nonempty_str(accumulated.get(key)):
            return False
    if not _packages_satisfied(accumulated.get("packages")):
        return False
    if not _outsourcing_satisfied(accumulated.get("outsourcing")):
        return False
    if not _discard_review_satisfied(accumulated.get("discard_review")):
        return False
    return True


def list_missing_field_labels(accumulated: Dict[str, Any]) -> List[str]:
    """Human-readable labels for fields still missing (for batch prompts).

    Args:
        accumulated: Current merged state.

    Returns:
        Ordered list of labels matching :func:`all_required_satisfied` gaps.
    """
    missing: List[str] = []
    for key in TOP_LEVEL_STRING_KEYS:
        if not _nonempty_str(accumulated.get(key)):
            missing.append(_FIELD_LABELS[key])
    if not _packages_satisfied(accumulated.get("packages")):
        missing.append(_FIELD_LABELS["packages"])
    if not _outsourcing_satisfied(accumulated.get("outsourcing")):
        missing.append(_FIELD_LABELS["outsourcing"])
    dr = accumulated.get("discard_review")
    if not isinstance(dr, dict):
        dr = {}
    if not _nonempty_str(dr.get("summary")):
        missing.append(_FIELD_LABELS["discard_review.summary"])
    flags = dr.get("criteria_flags") if isinstance(dr.get("criteria_flags"), dict) else {}
    for key in CRITERIA_FLAG_KEYS:
        if not _flag_entry_satisfied(flags.get(key)):
            missing.append(_FIELD_LABELS[f"discard_review.criteria_flags.{key}"])
    return missing


def _merge_strings(
    acc: Dict[str, Any],
    batch: Dict[str, Any],
    keys: tuple[str, ...],
    *,
    merge_mode: MergeMode = "first_wins",
) -> None:
    for key in keys:
        if key not in batch:
            continue
        val = batch.get(key)
        if not _nonempty_str(val):
            continue
        if merge_mode == "batch_overwrites":
            acc[key] = val.strip()
            continue
        if _nonempty_str(acc.get(key)):
            continue
        acc[key] = val.strip()


def _merge_packages(acc: Dict[str, Any], batch: Dict[str, Any]) -> None:
    if "packages" not in batch:
        return
    b = batch["packages"]
    if b is None:
        return
    if not isinstance(b, list):
        return
    cur = acc.get("packages")
    if cur is None:
        acc["packages"] = list(b)
        return
    if not isinstance(cur, list):
        acc["packages"] = list(b)
        return
    by_label: Dict[str, Dict[str, Any]] = {}
    for item in cur:
        if isinstance(item, dict) and _nonempty_str(item.get("label")):
            by_label[item["label"].strip().lower()] = dict(item)
        elif isinstance(item, dict):
            by_label[f"__idx_{len(by_label)}"] = dict(item)
    for item in b:
        if not isinstance(item, dict):
            continue
        lab = item.get("label")
        k = lab.strip().lower() if _nonempty_str(lab) else f"__idx_{len(by_label)}"
        if k in by_label:
            for fk, fv in item.items():
                if fv is not None and (
                    fk not in by_label[k] or by_label[k][fk] in (None, "")
                ):
                    by_label[k][fk] = fv
        else:
            by_label[k] = dict(item)
    acc["packages"] = list(by_label.values())


def _merge_outsourcing(
    acc: Dict[str, Any],
    batch: Dict[str, Any],
    *,
    merge_mode: MergeMode = "first_wins",
) -> None:
    if "outsourcing" not in batch:
        return
    b = batch["outsourcing"]
    if not isinstance(b, dict):
        return
    cur = acc.get("outsourcing")
    if not isinstance(cur, dict):
        acc["outsourcing"] = dict(b)
        return
    merged = dict(cur)
    for k, v in b.items():
        if v is None:
            continue
        if merge_mode == "batch_overwrites":
            if k == "notes" and isinstance(v, str):
                prev = merged.get(k)
                if isinstance(prev, str) and prev.strip():
                    if v.strip() and v.strip() not in prev:
                        merged[k] = f"{prev.strip()}\n{v.strip()}"
                else:
                    merged[k] = v.strip()
            else:
                merged[k] = v
            continue
        if k not in merged or merged[k] is None or merged[k] == "":
            merged[k] = v
        elif k == "notes" and isinstance(v, str) and isinstance(merged.get(k), str):
            if v.strip() and v.strip() not in merged[k]:
                merged[k] = f"{merged[k].strip()}\n{v.strip()}"
    acc["outsourcing"] = merged


def _merge_discard_review(
    acc: Dict[str, Any],
    batch: Dict[str, Any],
    *,
    merge_mode: MergeMode = "first_wins",
) -> None:
    if "discard_review" not in batch:
        return
    b = batch["discard_review"]
    if not isinstance(b, dict):
        return
    cur = acc.get("discard_review")
    if not isinstance(cur, dict):
        cur = {}
    merged: Dict[str, Any] = dict(cur)

    if merge_mode == "batch_overwrites":
        if _nonempty_str(b.get("summary")):
            merged["summary"] = b["summary"].strip()
    else:
        if _nonempty_str(b.get("summary")) and not _nonempty_str(merged.get("summary")):
            merged["summary"] = b["summary"].strip()
        elif _nonempty_str(b.get("summary")) and _nonempty_str(merged.get("summary")):
            pass

    for key in ("potential_discard",):
        if key not in b or b[key] is None:
            continue
        if merge_mode == "batch_overwrites" or merged.get(key) is None:
            merged[key] = b[key]

    br = b.get("reasons_for_manual_review")
    if isinstance(br, list):
        existing: List[str] = merged.get("reasons_for_manual_review") or []
        if not isinstance(existing, list):
            existing = []
        seen: Set[str] = {x.strip() for x in existing if isinstance(x, str)}
        for r in br:
            if isinstance(r, str) and r.strip() and r.strip() not in seen:
                existing.append(r.strip())
                seen.add(r.strip())
        merged["reasons_for_manual_review"] = existing

    b_flags = b.get("criteria_flags")
    if isinstance(b_flags, dict):
        m_flags = merged.get("criteria_flags")
        if not isinstance(m_flags, dict):
            m_flags = {}
        m_flags = dict(m_flags)
        for key in CRITERIA_FLAG_KEYS:
            if key not in b_flags:
                continue
            be = b_flags[key]
            if not isinstance(be, dict):
                continue
            me = m_flags.get(key)
            if not isinstance(me, dict):
                me = {}
            applies = be.get("applies")
            if applies is not None and (
                merge_mode == "batch_overwrites" or me.get("applies") is None
            ):
                me["applies"] = applies
            ev_b = be.get("evidence")
            if _nonempty_str(ev_b):
                ev_m = me.get("evidence")
                if not _nonempty_str(ev_m):
                    me["evidence"] = ev_b.strip()
                elif ev_b.strip() not in ev_m:
                    me["evidence"] = f"{ev_m.strip()}\n{ev_b.strip()}"
            m_flags[key] = me
        merged["criteria_flags"] = m_flags

    acc["discard_review"] = merged


def merge_tender_partial(
    accumulated: Dict[str, Any],
    batch_partial: Dict[str, Any],
    *,
    merge_mode: MergeMode = "first_wins",
) -> None:
    """Merge one batch partial JSON into ``accumulated`` in place.

    ``merge_mode``:

    - ``first_wins`` (default): keep first non-empty strings; fill gaps only.
      Used for text gap-fill and single-shot merges.
    - ``batch_overwrites``: each non-empty batch value overwrites (later PCAP
      page batches refine cover-page guesses). Use for multimodal image batches.

    ``packages``: union by normalized label (fills empty fields on duplicate labels).
    ``discard_review``: reasons deduplicated; ``evidence`` appended when new.

    Args:
        accumulated: Running merge target; updated in place.
        batch_partial: Model output for one batch (may be empty).
        merge_mode: How to combine with prior merge state.
    """
    if not batch_partial:
        return
    _merge_strings(
        accumulated,
        batch_partial,
        TOP_LEVEL_STRING_KEYS,
        merge_mode=merge_mode,
    )
    _merge_packages(accumulated, batch_partial)
    _merge_outsourcing(accumulated, batch_partial, merge_mode=merge_mode)
    _merge_discard_review(accumulated, batch_partial, merge_mode=merge_mode)


def partial_json_for_prompt(
    accumulated: Dict[str, Any],
    max_chars: int | None = None,
) -> str:
    """Serialize accumulated state for inclusion in batch user messages.

    Args:
        accumulated: Current merged JSON.
        max_chars: Max length before truncation marker. When ``None``, uses
            ``IMAN_LLM_PARTIAL_JSON_MAX_CHARS`` (default 12000).

    Returns:
        Compact JSON string, possibly truncated.
    """
    if max_chars is None:
        try:
            max_chars = int(os.environ.get("IMAN_LLM_PARTIAL_JSON_MAX_CHARS", "12000"))
        except ValueError:
            max_chars = 12000
    try:
        s = json.dumps(accumulated, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 20] + "\n...[truncated]"
