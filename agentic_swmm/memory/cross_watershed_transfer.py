"""Cross-watershed transfer learning (PRD-07 Phase 5).

When the modeler points the agent at a *new* INP — no prior runs for
this case yet — the agent should not start from generic defaults.
Instead it should look at the calibration history for *other* cases,
pick the most similar watershed by attribute distance, and propose
that case's accepted parameter set as a warm start.

What this module is
-------------------
A pure-function recommender on top of two existing layers:

- :mod:`watershed_similarity` extracts a feature vector from an INP
  and scores two attribute bags against each other in ``[0, 1]``.
- :mod:`calibration_memory` persists one ``CalibrationRecord`` per
  accepted calibration, joined to a stable ``case_name``.

The recommender pulls every unique ``case_name`` from the calibration
store, ranks those candidate cases by similarity to the target, and
returns the *best* (highest ``objective_value``) calibration record
for each of the top-k matches as a :class:`TransferRecommendation`.

What this module is **not**
---------------------------
- A calibration runner. We never edit an INP, never invoke SWMM,
  never overwrite a parameter file. The recommendation is *advisory*
  — the caller surfaces it to the user (CLI table, chat prompt) and
  the user confirms before any irreversible action.
- A learner. Feature weights and the similarity score live in
  :mod:`watershed_similarity`. This module is composition only.
- A case-discovery oracle. When the caller does not pass
  ``candidate_attributes`` we walk a small list of conventional INP
  locations (``cases/<case>/<case>.inp``, ``runs/<date>/*<case>*.inp``,
  project root). Cases whose INP cannot be found are skipped with a
  logged note rather than raising — the agent should still surface
  the recommendations it *could* compute.

Memory transparency contract
----------------------------
When the caller provides ``run_dir``, exactly one ``memory_trace.jsonl``
line lands via :func:`log_memory_decision` with ``decision_point=
"cross_watershed_transfer_recommendation"``. The confidence label is
``memory_informed`` when at least one recommendation was produced;
``llm`` when zero candidates exist anywhere in the calibration store
(no transfer is possible, defer to the existing LLM/keyword paths).
``run_dir=None`` is the explicit "no trace" mode used by the CLI's
``--json`` smoke surface so a one-shot inspection does not pollute
the trace dir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agentic_swmm.memory.calibration_memory import recall_calibration, CalibrationRecord
from agentic_swmm.memory.watershed_similarity import (
    WatershedAttributes,
    extract_attributes_from_inp,
    similarity_score,
)


logger = logging.getLogger(__name__)


# Decision-point label written to memory_trace.jsonl. Pinned to a
# constant so consumers (this module's tests, downstream filters,
# Phase D dashboards) can grep without hard-coding the string.
DECISION_POINT = "cross_watershed_transfer_recommendation"


@dataclass(frozen=True)
class TransferRecommendation:
    """One transfer-learning recommendation for a new case.

    Frozen so callers cannot scribble on a recommendation between the
    recommender producing it and the CLI / planner surfacing it. The
    fields are flat so JSON serialisation is mechanical (the CLI's
    ``--json`` mode dumps these directly).

    Round-3 fields (``recommended_design_storm``,
    ``recommended_manning_n``, ``known_failure_patterns``) are
    additive: a caller that does not pass the new stores still gets a
    valid recommendation with the original fields populated and the
    new ones defaulting to ``None`` / ``{}`` / ``[]``.

    Attributes:
        target_case: The new case being recommended *for*. Today this
            is always the INP filename stem; future callers may pass
            an explicit ``case_id`` slug.
        source_case: The historical case the recommendation borrows
            from. Joins back to ``calibration_memory.jsonl`` via
            ``case_name``.
        similarity: ``[0, 1]`` similarity score between target and
            source. ``1.0`` = identical attribute vector.
        source_calibration_record: The best (highest ``objective_value``)
            ``CalibrationRecord`` for ``source_case``. Carries the
            algorithm + parameter set + goodness-of-fit.
        proposed_parameters: A shallow copy of
            ``source_calibration_record.parameters`` so the caller can
            mutate freely without touching the underlying record.
        rationale: One-line user-facing explanation. Format:
            "<source_case> (sim=<x.xx>, NSE=<y.yy>)" so it fits in a
            CLI table cell without wrapping.
        confidence: One of the trace-friendly labels — today always
            ``"memory_informed"`` since the recommender only produces
            an entry when a real calibration record is on file.
        n_alternatives: How many other source cases were considered
            during ranking. Lets the user understand whether the top-1
            was the only option or one of many close matches.
        recommended_design_storm: When the source case's calibration
            row carries ``metadata.case_design_storm_key`` and that
            key resolves against the project storm library, the
            resolved spec dict (with ``key`` echoed back so the CLI
            can name it). ``None`` when the source has no storm key
            or the library does not resolve it.
        recommended_manning_n: Calibrated Manning's *n* values from
            ``source_calibration_record.parameters`` whose key matches
            a known ``manning_n_*`` prefix from
            ``reference_benchmarks.yaml``. Empty dict when nothing
            matches.
        known_failure_patterns: Lessons from ``negative_lessons.jsonl``
            associated with the source case. Each entry is a flat dict
            of ``{lesson_type, parameters_tried, note}`` so a CLI
            consumer can render without instantiating dataclasses.
            Empty list when no lessons or the store is missing.
    """

    target_case: str
    source_case: str
    similarity: float
    source_calibration_record: CalibrationRecord
    proposed_parameters: dict[str, float] = field(default_factory=dict)
    rationale: str = ""
    confidence: str = "memory_informed"
    n_alternatives: int = 0
    recommended_design_storm: dict[str, Any] | None = None
    recommended_manning_n: dict[str, float] = field(default_factory=dict)
    known_failure_patterns: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe shape for CLI / trace consumers.

        ``source_calibration_record`` is summarised in place rather
        than nested as the record's full ``to_dict`` so the CLI
        ``--json`` output stays narrow. Callers that need the full
        record reconstruct it via :mod:`calibration_memory`.
        """
        rec = self.source_calibration_record
        return {
            "target_case": self.target_case,
            "source_case": self.source_case,
            "similarity": round(float(self.similarity), 6),
            "objective_name": rec.objective_name,
            "objective_value": rec.objective_value,
            "algorithm": rec.algorithm,
            "swmm5_version": rec.swmm5_version,
            "proposed_parameters": dict(self.proposed_parameters),
            "rationale": self.rationale,
            "confidence": self.confidence,
            "n_alternatives": self.n_alternatives,
            "recommended_design_storm": (
                dict(self.recommended_design_storm)
                if self.recommended_design_storm is not None
                else None
            ),
            "recommended_manning_n": dict(self.recommended_manning_n),
            "known_failure_patterns": [
                dict(item) for item in self.known_failure_patterns
            ],
        }


# Conventional locations a candidate case's INP may live at. Checked
# in order; the first hit wins. Kept tiny on purpose — adding more
# paths grows the "files we might pretend exist" surface area; the
# caller can always pass ``candidate_attributes`` explicitly when
# their layout is unusual.
def _candidate_inp_locations(case_name: str, repo_root: Path) -> list[Path]:
    return [
        repo_root / "cases" / case_name / f"{case_name}.inp",
        repo_root / "cases" / case_name / "model.inp",
        repo_root / "examples" / case_name / f"{case_name}.inp",
        repo_root / f"{case_name}.inp",
    ]


def _locate_inp_for_case(case_name: str, repo_root: Path) -> Path | None:
    """Return the first existing INP path under conventional locations.

    Returns ``None`` (rather than raising) when no file is found so the
    recommender can skip-and-log instead of failing the whole call.
    """
    for path in _candidate_inp_locations(case_name, repo_root):
        if path.is_file():
            return path
    return None


def _pick_best_record(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the row with the highest ``objective_value``, or ``None``.

    ``objective_value`` is the primary fit metric ("higher is better"
    for NSE / KGE; we do not enumerate which direction is good for a
    given ``objective_name`` because the writer already accepted only
    the row the calibrator considered best). Rows with ``None`` or
    non-numeric values are deprioritised (sort to end) so a corrupt
    store never returns ``None`` when at least one usable row exists.
    """
    if not rows:
        return None

    def _key(row: dict[str, Any]) -> tuple[int, float]:
        # First component: 0 when the row has a parseable objective
        # value, 1 otherwise. We *invert* the negated float so a
        # straightforward ascending sort puts "has-value, highest"
        # first and "no-value" last. Using ``reverse=True`` with a
        # tuple key would also reverse the group flag, which would
        # promote no-value rows above usable ones — exactly the bug
        # this layout avoids.
        v = row.get("objective_value")
        try:
            return (0, -float(v))
        except (TypeError, ValueError):
            return (1, 0.0)

    sorted_rows = sorted(rows, key=_key)
    return sorted_rows[0]


def _record_from_row(row: dict[str, Any]) -> CalibrationRecord:
    """Project a stored JSON row back into a :class:`CalibrationRecord`.

    Mirrors the writer's dataclass field list. Unknown keys (e.g. the
    schema-version sentinel) are dropped on purpose — the consumer
    type does not version-stamp recommendations.
    """
    return CalibrationRecord(
        run_id=str(row.get("run_id") or ""),
        case_name=str(row.get("case_name") or ""),
        use_case=row.get("use_case"),
        algorithm=row.get("algorithm"),
        parameters=dict(row.get("parameters") or {}),
        objective_name=row.get("objective_name"),
        objective_value=row.get("objective_value"),
        secondary_metrics=dict(row.get("secondary_metrics") or {}),
        swmm5_version=row.get("swmm5_version"),
        n_evaluations=row.get("n_evaluations"),
        wall_time_s=row.get("wall_time_s"),
        created_at=row.get("created_at"),
    )


def _format_rationale(source_case: str, similarity: float, record: CalibrationRecord) -> str:
    """Build a one-line user-facing rationale.

    Includes similarity (always) and the objective metric (when the
    record carries one). Kept compact for terminal tables.
    """
    parts = [f"{source_case} (sim={similarity:.3f}"]
    if record.objective_name and record.objective_value is not None:
        try:
            parts[-1] += f", {record.objective_name}={float(record.objective_value):.3f}"
        except (TypeError, ValueError):
            pass
    parts[-1] += ")"
    return "".join(parts)


def _extract_storm_key(row: dict[str, Any]) -> str | None:
    """Return the calibrated design-storm key from a calibration row.

    Looks for ``metadata.case_design_storm_key`` (the Round 2 convention)
    on the raw row dict. Returns ``None`` when the field is absent or
    not a non-empty string. Tolerant: never raises.
    """
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return None
    key = metadata.get("case_design_storm_key")
    if not isinstance(key, str) or not key.strip():
        return None
    return key.strip()


def _storm_key_resolves(storm_key: str, repo_root: Path) -> bool:
    """Return True when ``storm_key`` exists in the project storm library.

    Best-effort: a missing storm_library file, a malformed YAML, or a
    key that does not appear in the chicago_hyetographs block all
    yield ``False`` without raising. Used as a guard so the rationale
    only mentions storm keys the user can actually act on.
    """
    try:
        # Lazy import: this module's other consumers should not pull
        # storm_library into their import graph.
        from agentic_swmm.memory.storm_library import recall_chicago_spec

        library_path = repo_root / "memory" / "modeling-memory" / "storm_library.yaml"
        spec = recall_chicago_spec(library_path, storm_key)
        return spec is not None
    except Exception:  # pragma: no cover - defensive
        return False


def _resolve_design_storm(
    storm_key: str | None, library_path: Path
) -> dict[str, Any] | None:
    """Return the resolved storm-library spec for ``storm_key`` or ``None``.

    The returned dict echoes the storm-library entry plus a ``key``
    field so a downstream CLI can name the recommendation without a
    second lookup. ``None`` when the key is empty, the library cannot
    be loaded, or the entry does not exist / is a schema-only
    placeholder.
    """
    if not storm_key:
        return None
    try:
        from agentic_swmm.memory.storm_library import recall_chicago_spec

        spec = recall_chicago_spec(library_path, storm_key)
    except Exception:  # pragma: no cover - defensive
        return None
    if spec is None:
        return None
    out = dict(spec)
    out.setdefault("key", storm_key)
    return out


def _known_manning_n_prefixes(benchmarks_path: Path) -> set[str]:
    """Return the set of top-level ``manning_n_*`` keys from the YAML.

    A SWMM project file typically uses parameter names like
    ``manning_n_overland_grass_short`` or ``manning_n_pipes_concrete``.
    The reference benchmarks YAML groups those under top-level blocks
    named with a ``manning_n_`` prefix (e.g. ``manning_n_overland``,
    ``manning_n_pipes``). The recommender uses those prefixes as the
    filter: any calibration parameter whose name starts with one of
    these prefixes is plausibly a Manning's *n* value worth surfacing.

    Returns the empty set when the YAML cannot be loaded or contains
    no ``manning_n_*`` keys — the caller then yields an empty Manning's
    block rather than erroring.
    """
    try:
        from agentic_swmm.memory.reference_benchmarks import (
            load_reference_benchmarks,
        )

        data = load_reference_benchmarks(benchmarks_path)
    except Exception:  # pragma: no cover - defensive
        return set()
    if not isinstance(data, dict):
        return set()
    return {
        key for key in data.keys() if isinstance(key, str) and key.startswith("manning_n_")
    }


def _extract_recommended_manning_n(
    parameters: dict[str, Any], benchmarks_path: Path
) -> dict[str, float]:
    """Filter ``parameters`` down to keys that look like Manning's *n*.

    Matches the calibration parameter name against the set of
    ``manning_n_*`` prefixes from ``reference_benchmarks.yaml``. A
    parameter named e.g. ``manning_n_overland_grass`` matches when
    ``manning_n_overland`` is one of the known prefixes. Non-numeric
    values are dropped so the returned dict is always
    ``str -> float``.
    """
    if not parameters:
        return {}
    prefixes = _known_manning_n_prefixes(benchmarks_path)
    if not prefixes:
        return {}
    out: dict[str, float] = {}
    for name, value in parameters.items():
        if not isinstance(name, str):
            continue
        if not any(name.startswith(p) for p in prefixes):
            continue
        try:
            out[name] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def _load_known_failure_patterns(
    source_case: str, store_path: Path
) -> list[dict[str, Any]]:
    """Return negative lessons for ``source_case`` as flat dicts.

    Returns the empty list when the store is missing, the case has
    no lessons, or any read error fires. We surface ``lesson_type``,
    ``parameters_tried``, ``note``, and ``recorded_at`` so a CLI
    consumer can rank the lessons by date without instantiating a
    :class:`NegativeLesson` dataclass.
    """
    try:
        from agentic_swmm.memory.negative_lessons import recall_negative_lessons

        lessons = recall_negative_lessons(store_path, {"case_name": source_case})
    except Exception:  # pragma: no cover - defensive
        return []
    out: list[dict[str, Any]] = []
    for lesson in lessons:
        out.append(
            {
                "lesson_type": lesson.lesson_type,
                "parameters_tried": dict(lesson.parameters_tried),
                "note": lesson.note,
                "recorded_at": lesson.recorded_at,
            }
        )
    # Newest first — calibration consumers want the most recent
    # observation when deciding whether a parameter region is still bad.
    out.sort(key=lambda item: (item.get("recorded_at") or ""), reverse=True)
    return out


def recommend_parameters_for_new_case(
    target_inp: Path,
    *,
    calibration_store: Path,
    candidate_attributes: dict[str, WatershedAttributes] | None = None,
    top_k: int = 1,
    attribute_extractor: Callable[[Path], WatershedAttributes] | None = None,
    run_dir: Path | None = None,
    repo_root: Path | None = None,
    storm_library_path: Path | None = None,
    negative_lessons_store: Path | None = None,
    benchmarks_path: Path | None = None,
) -> list[TransferRecommendation]:
    """Recommend warm-start parameters for ``target_inp`` from prior cases.

    Pipeline:

    1. Extract :class:`WatershedAttributes` from ``target_inp`` via
       ``attribute_extractor`` (defaults to the production extractor).
    2. Pull unique ``case_name`` values from ``calibration_store``.
    3. Resolve each candidate to a :class:`WatershedAttributes` —
       either from ``candidate_attributes`` (test injection point) or
       by locating a conventional INP path and extracting.
    4. Score every resolved candidate against the target and keep the
       top-``k`` by similarity descending.
    5. For each surviving source case, pull all matching rows from the
       store and pick the one with the highest ``objective_value`` as
       the seed record.
    6. Build a :class:`TransferRecommendation` per surviving source.

    Returns an empty list when no candidates have calibration history,
    when ``top_k <= 0``, or when no candidate's INP can be resolved.

    Trace contract:

    - ``run_dir is not None`` → exactly one ``memory_trace.jsonl``
      line per call (``decision_point="cross_watershed_transfer_recommendation"``).
      The confidence label is ``"memory_informed"`` when at least one
      recommendation was produced; ``"llm"`` when zero candidates
      existed (no transfer is possible).
    - ``run_dir is None`` → no trace line. Used by one-shot CLI calls
      that should not contaminate a trace dir.

    Arguments:
        target_inp: Path to the new case's INP file. Read once; never
            mutated.
        calibration_store: Path to ``calibration_memory.jsonl`` (the
            writer's canonical file under
            ``memory/modeling-memory/``).
        candidate_attributes: Optional pre-built map of
            ``case_name -> WatershedAttributes``. When provided the
            recommender skips the conventional-location lookup
            entirely — useful for tests and for callers that already
            cached attribute vectors.
        top_k: Maximum number of recommendations to return. Values
            ``<= 0`` short-circuit to an empty list.
        attribute_extractor: Test injection point. Defaults to
            ``extract_attributes_from_inp``.
        run_dir: When provided, a memory_trace line is written here.
            ``None`` is the no-trace mode.
        repo_root: Root directory the conventional-location lookup
            uses. Defaults to the calibration store's grandparent
            (so ``memory/modeling-memory/x.jsonl`` → project root).
        storm_library_path: Path to ``storm_library.yaml``. Default:
            ``<repo_root>/memory/modeling-memory/storm_library.yaml``.
            Used to resolve the source case's ``case_design_storm_key``
            into a ``recommended_design_storm`` payload.
        negative_lessons_store: Path to ``negative_lessons.jsonl``.
            Default: ``<repo_root>/memory/modeling-memory/negative_lessons.jsonl``.
            Used to populate ``known_failure_patterns`` for the source
            case.
        benchmarks_path: Path to ``reference_benchmarks.yaml``. Default:
            ``<repo_root>/memory/modeling-memory/reference_benchmarks.yaml``.
            Used to discover ``manning_n_*`` prefixes that select which
            calibration parameters land in ``recommended_manning_n``.

    Failure modes:
        * Missing ``calibration_store`` → empty list, optional ``llm``
          trace line.
        * Missing ``target_inp`` → the extractor returns zero-attribute
          features; recommendations may still be produced if any
          candidate happens to match the zero vector, but the more
          common outcome is a very low similarity score that still
          surfaces ranked candidates honestly.
        * Candidate INP cannot be located → logged as a debug note
          (``logger.debug``) and the candidate is dropped from the
          ranking. We never raise on a missing companion file.
    """
    extractor = attribute_extractor or extract_attributes_from_inp
    target_attrs = extractor(target_inp)

    if top_k <= 0:
        _maybe_log_trace(run_dir, decision="(none)", confidence="llm", evidence_count=0)
        return []

    # Pull all calibration rows and group by case_name. We do not
    # filter by use_case / algorithm here — Phase 5 is intentionally
    # permissive; richer filtering lands when callers ask for it.
    rows = recall_calibration(calibration_store, {})
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        name = str(row.get("case_name") or "").strip()
        if not name:
            continue
        by_case.setdefault(name, []).append(row)

    if not by_case:
        _maybe_log_trace(run_dir, decision="(none)", confidence="llm", evidence_count=0)
        return []

    # Resolve each candidate's WatershedAttributes. Sources of truth,
    # in order: explicit map → conventional location + extractor.
    repo_root_path = Path(repo_root) if repo_root else _default_repo_root(calibration_store)
    resolved: dict[str, WatershedAttributes] = {}
    if candidate_attributes:
        # Defensive copy + skip empty case names. Caller-provided
        # values win over conventional-location lookup so test fixtures
        # do not depend on filesystem layout.
        for name, attrs in candidate_attributes.items():
            name = (name or "").strip()
            if name:
                resolved[name] = attrs

    for case_name in by_case:
        if case_name in resolved:
            continue
        inp_path = _locate_inp_for_case(case_name, repo_root_path)
        if inp_path is None:
            logger.debug(
                "cross_watershed_transfer: skipping %r — no INP file under %s",
                case_name,
                repo_root_path,
            )
            continue
        try:
            resolved[case_name] = extractor(inp_path)
        except Exception:  # pragma: no cover - defensive: extractor must not break recommender
            logger.debug(
                "cross_watershed_transfer: extractor raised on %s; skipping",
                inp_path,
            )
            continue

    if not resolved:
        _maybe_log_trace(run_dir, decision="(none)", confidence="llm", evidence_count=0)
        return []

    # Score every resolved candidate; sort desc by score with case
    # name as a deterministic tiebreaker so two equally-similar
    # candidates always rank the same way across processes.
    scored: list[tuple[str, float]] = [
        (name, similarity_score(target_attrs, attrs)) for name, attrs in resolved.items()
    ]
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    top = scored[: max(0, int(top_k))]

    target_case_name = target_inp.stem if hasattr(target_inp, "stem") else str(target_inp)
    n_alternatives = max(0, len(scored) - len(top))

    # Resolve enrichment-store paths once per call so per-source-case
    # lookups stay cheap and the defaults derive from ``repo_root``.
    storm_library = (
        Path(storm_library_path)
        if storm_library_path is not None
        else repo_root_path / "memory" / "modeling-memory" / "storm_library.yaml"
    )
    negative_store = (
        Path(negative_lessons_store)
        if negative_lessons_store is not None
        else repo_root_path / "memory" / "modeling-memory" / "negative_lessons.jsonl"
    )
    benchmarks = (
        Path(benchmarks_path)
        if benchmarks_path is not None
        else repo_root_path / "memory" / "modeling-memory" / "reference_benchmarks.yaml"
    )

    recommendations: list[TransferRecommendation] = []
    for source_case, sim in top:
        best_row = _pick_best_record(by_case.get(source_case, []))
        if best_row is None:
            # A case had rows mapped but every row was unusable; skip
            # rather than fabricate. Logging here is debug-only since
            # the caller already sees a missing entry in the output.
            logger.debug(
                "cross_watershed_transfer: %r had no usable calibration row",
                source_case,
            )
            continue
        record = _record_from_row(best_row)
        rationale = _format_rationale(source_case, float(sim), record)
        # Round 2: when the source case has a recorded design-storm
        # key in metadata that resolves to a storm_library entry,
        # surface it in the rationale so the user can re-run the
        # transfer against the same storm.
        storm_key = _extract_storm_key(best_row)
        if storm_key and _storm_key_resolves(storm_key, repo_root_path):
            rationale = (
                f"{rationale} — {source_case} calibrated against "
                f"storm_library.chicago_hyetographs.{storm_key}"
            )

        # Round 3: enrichment. None-tolerant: each helper degrades to
        # the empty value when its underlying store is missing.
        design_storm = _resolve_design_storm(storm_key, storm_library)
        manning_n = _extract_recommended_manning_n(record.parameters, benchmarks)
        failure_patterns = _load_known_failure_patterns(source_case, negative_store)

        recommendations.append(
            TransferRecommendation(
                target_case=target_case_name,
                source_case=source_case,
                similarity=float(sim),
                source_calibration_record=record,
                proposed_parameters=dict(record.parameters),
                rationale=rationale,
                confidence="memory_informed",
                n_alternatives=n_alternatives,
                recommended_design_storm=design_storm,
                recommended_manning_n=manning_n,
                known_failure_patterns=failure_patterns,
            )
        )

    if recommendations:
        _maybe_log_trace(
            run_dir,
            decision=recommendations[0].source_case,
            confidence="memory_informed",
            evidence_count=len(recommendations),
        )
    else:
        _maybe_log_trace(run_dir, decision="(none)", confidence="llm", evidence_count=0)
    return recommendations


def _default_repo_root(calibration_store: Path) -> Path:
    """Best-effort repo root for the conventional-location lookup.

    The canonical layout is
    ``<repo>/memory/modeling-memory/calibration_memory.jsonl`` so the
    grandparent of the store is a safe default. Callers can always
    override via the ``repo_root`` argument.
    """
    store = Path(calibration_store)
    return store.parent.parent.parent if store.parent.parent.parent != Path("/") else Path.cwd()


def _maybe_log_trace(
    run_dir: Path | None,
    *,
    decision: str,
    confidence: str,
    evidence_count: int,
) -> None:
    """Write one memory_trace line when ``run_dir`` is provided.

    Done as a private helper so the policy logic above stays linear
    and easy to read; the trace is *adjunct*, not part of the
    decision. Any I/O failure here is swallowed — a memory store that
    cannot be appended to must never break the recommendation path.
    """
    if run_dir is None:
        return
    try:
        # Lazy import keeps the agent layer optional for pure-memory
        # callers (e.g. a future MCP tool that wants recommendations
        # without pulling the planner's transitive deps).
        from agentic_swmm.agent.memory_context import MemoryContext
        from agentic_swmm.agent.memory_trace import log_memory_decision

        context = MemoryContext(
            summary=f"cross-watershed transfer: {evidence_count} recommendation(s)",
            provenance={"decision_point": DECISION_POINT},
        )
        log_memory_decision(
            run_dir=Path(run_dir),
            decision_point=DECISION_POINT,
            context=context,
            decision=decision,
            confidence=confidence,
        )
    except Exception:  # pragma: no cover - audit must never break dispatch
        logger.debug("cross_watershed_transfer: trace logging failed", exc_info=True)


__all__ = [
    "DECISION_POINT",
    "TransferRecommendation",
    "recommend_parameters_for_new_case",
]
