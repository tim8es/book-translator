"""Reproducible analysis of externally observed manual chat-session runs.

No model calls, synthetic performance claims, or writes. Every run must cover
the same corpus with the same evaluation protocol and complete independent
review before throughput or quality comparisons are reported.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class BenchmarkError(ValueError):
    """Benchmark observations cannot support an apples-to-apples comparison."""


_METRICS = ("github_requests", "manual_interventions", "major_findings", "critical_findings")


def _integer(value: Any, key: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise BenchmarkError(f"{key} must be an integer >= {minimum}")
    return value


def _positive_number(value: Any, key: str) -> float:
    if type(value) not in (int, float) or not 0 < value < 1e12:
        raise BenchmarkError(f"{key} must be a finite positive number")
    return float(value)


def evaluate(observations: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(observations, Mapping) or observations.get("schema_version") != 1:
        raise BenchmarkError("benchmark schema_version must be 1")
    dataset = observations.get("dataset_id")
    protocol = observations.get("evaluation_protocol")
    if not isinstance(dataset, str) or not dataset.strip():
        raise BenchmarkError("dataset_id must identify the same source corpus")
    if not isinstance(protocol, str) or not protocol.strip():
        raise BenchmarkError("evaluation_protocol is required for comparable quality checks")
    runs = observations.get("runs")
    if not isinstance(runs, list) or not 2 <= len(runs) <= 100:
        raise BenchmarkError("provide between 2 and 100 recorded runs")
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    canonical_units: set[str] | None = None
    canonical_words: int | None = None
    for index, item in enumerate(runs):
        prefix = f"runs[{index}]"
        if not isinstance(item, Mapping):
            raise BenchmarkError(f"{prefix} must be an object")
        run_id = item.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip() or run_id in names:
            raise BenchmarkError(f"{prefix}.run_id is missing or duplicated")
        names.add(run_id)
        sessions = _integer(item.get("sessions"), f"{prefix}.sessions", minimum=1)
        words = _integer(item.get("source_words"), f"{prefix}.source_words", minimum=1)
        seconds = _positive_number(item.get("elapsed_seconds"), f"{prefix}.elapsed_seconds")
        units = item.get("unit_ids")
        if (not isinstance(units, list) or not units
                or any(not isinstance(u, str) or not u.strip() for u in units)
                or len(set(units)) != len(units)):
            raise BenchmarkError(f"{prefix}.unit_ids must be unique non-empty strings")
        complete = item.get("reviewed_unit_ids")
        if (not isinstance(complete, list) or len(complete) != len(units)
                or any(not isinstance(u, str) for u in complete)
                or set(complete) != set(units)):
            raise BenchmarkError(
                f"{prefix} must have independently reviewed every assigned unit"
            )
        if canonical_units is None:
            canonical_units, canonical_words = set(units), words
        if set(units) != canonical_units or words != canonical_words:
            raise BenchmarkError("runs must use identical unit identities and source word counts")
        values = {key: _integer(item.get(key), f"{prefix}.{key}") for key in _METRICS}
        normalized.append({
            "run_id": run_id, "sessions": sessions, "source_words": words,
            "elapsed_seconds": seconds, **values,
            "reviewed_units": len(units),
            "words_per_hour": round(words * 3600.0 / seconds, 2),
            "_raw_words_per_hour": words * 3600.0 / seconds,
        })
    grouped: dict[int, list[dict[str, Any]]] = {}
    for run in normalized:
        grouped.setdefault(run["sessions"], []).append(run)
    if 1 not in grouped or not any(count > 1 for count in grouped):
        raise BenchmarkError("benchmark requires a single-chat baseline and a multi-chat candidate")

    summary: list[dict[str, Any]] = []
    for count, samples in sorted(grouped.items()):
        summary.append({
            "sessions": count, "samples": len(samples),
            "median_words_per_hour": round(statistics.median(x["_raw_words_per_hour"] for x in samples), 2),
            **{
                "median_" + key: statistics.median(x[key] for x in samples)
                for key in _METRICS
            },
        })
    baseline = next(row for row in summary if row["sessions"] == 1)
    comparisons = []
    for candidate in summary:
        if candidate["sessions"] == 1:
            continue
        raw_baseline = statistics.median(x["_raw_words_per_hour"] for x in grouped[1])
        raw_candidate = statistics.median(
            x["_raw_words_per_hour"] for x in grouped[candidate["sessions"]]
        )
        rate = raw_candidate / raw_baseline
        comparisons.append({
            "sessions": candidate["sessions"],
            "throughput_speedup": round(rate, 3),
            "github_request_change": candidate["median_github_requests"] - baseline["median_github_requests"],
            "manual_intervention_change": (
                candidate["median_manual_interventions"] - baseline["median_manual_interventions"]
            ),
            "major_finding_change": candidate["median_major_findings"] - baseline["median_major_findings"],
            "critical_finding_change": candidate["median_critical_findings"] - baseline["median_critical_findings"],
            "meets_provisional_2x_target": (
                rate >= 2 and
                candidate["median_major_findings"] <= baseline["median_major_findings"] and
                candidate["median_critical_findings"] <= baseline["median_critical_findings"]
            ),
        })
    return {
        "schema_version": 1,
        "dataset_id": dataset,
        "evaluation_protocol": protocol,
        "complete_review_required": True,
        "runs": sorted(
            [{key: value for key, value in run.items() if not key.startswith("_")}
             for run in normalized],
            key=lambda x: (x["sessions"], x["run_id"]),
        ),
        "summary": summary,
        "comparisons": comparisons,
        "qualification": (
            "This report summarizes supplied observations, not automatic timing or "
            "a statistically validated quality improvement. Literary quality requires "
            "blind independent evaluation and repeated comparable runs."
        ),
    }


def evaluate_file(path: Path, *, max_bytes: int = 1_000_000) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise BenchmarkError("benchmark file missing or exceeds 1 MB")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise BenchmarkError(f"invalid benchmark JSON: {exc}") from exc
    return evaluate(doc)
