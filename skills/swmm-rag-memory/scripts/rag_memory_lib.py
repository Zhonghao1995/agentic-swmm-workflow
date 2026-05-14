from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+|[\u4e00-\u9fff]+")
EMBEDDING_DIMENSIONS = 384
HYBRID_KEYWORD_WEIGHT = 0.62
HYBRID_SEMANTIC_WEIGHT = 0.28
HYBRID_METADATA_WEIGHT = 0.10
QUERY_EXPANSIONS = {
    "洪峰": ["peak", "flow", "peak_flow", "peak_flow_parse_missing"],
    "峰值": ["peak", "flow", "peak_flow"],
    "流量": ["flow", "inflow", "outflow"],
    "没读到": ["parse", "missing", "parse_missing"],
    "没有读到": ["parse", "missing", "parse_missing"],
    "解析不到": ["parse", "missing", "parse_missing"],
    "水量平衡": ["continuity", "continuity_error", "flow_routing", "runoff_quantity"],
    "连续性": ["continuity", "continuity_error"],
    "管道坡度": ["conduit_slope", "conduit_slope_suspicious"],
    "坡度": ["slope", "conduit_slope_suspicious"],
    "缺证据": ["missing", "missing_evidence", "partial_run"],
    "证据不足": ["missing", "missing_evidence", "partial_run"],
    "失败": ["failure", "failed", "failure_patterns"],
}
STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "for",
    "with",
    "is",
    "are",
    "this",
    "that",
    "what",
    "why",
    "how",
    "怎么办",
    "为什么",
    "这个",
    "那个",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def relpath(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def tokenize(text: str) -> list[str]:
    tokens = [match.group(0).lower() for match in TOKEN_RE.finditer(text)]
    out: list[str] = []
    for token in tokens:
        token = token.strip("._-:/")
        if not token or token in STOPWORDS:
            continue
        out.append(token)
        if "_" in token:
            out.extend(part for part in token.split("_") if part and part not in STOPWORDS)
        if "-" in token:
            out.extend(part for part in token.split("-") if part and part not in STOPWORDS)
    return out


def expand_query_tokens(query: str, tokens: list[str]) -> list[str]:
    expanded = list(tokens)
    lowered = query.lower()
    for phrase, additions in QUERY_EXPANSIONS.items():
        if phrase.lower() in lowered:
            expanded.extend(additions)
    token_set = set(tokens)
    if {"peak", "flow"} <= token_set:
        expanded.append("peak_flow_parse_missing")
    if {"parse", "missing"} <= token_set:
        expanded.extend(["parse_missing", "peak_flow_parse_missing", "continuity_parse_missing"])
    if "continuity" in token_set and ("high" in token_set or "error" in token_set):
        expanded.append("continuity_error_high")
    return expanded


def stable_hash(value: str) -> int:
    h = 2166136261
    for byte in value.encode("utf-8"):
        h ^= byte
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def char_ngrams(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", text.lower()).strip()
    grams: list[str] = []
    for n in (3, 4, 5):
        if len(clean) < n:
            continue
        grams.extend(clean[i : i + n] for i in range(len(clean) - n + 1))
    return grams


def embedding_features(text: str) -> list[str]:
    tokens = tokenize(text)
    features: list[str] = []
    features.extend(f"tok:{token}" for token in tokens)
    for left, right in zip(tokens, tokens[1:]):
        features.append(f"bi:{left}_{right}")
    features.extend(f"char:{gram}" for gram in char_ngrams(text))
    return features


def hashed_embedding(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> dict[str, float]:
    counts: Counter[int] = Counter()
    for feature in embedding_features(text):
        idx = stable_hash(feature) % dimensions
        sign = -1.0 if stable_hash("sign:" + feature) % 2 else 1.0
        counts[idx] += sign
    norm = math.sqrt(sum(float(value) * float(value) for value in counts.values()))
    if not norm:
        return {}
    return {str(idx): round(float(value) / norm, 6) for idx, value in sorted(counts.items()) if value}


def cosine_sparse(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(key, 0.0) for key, value in left.items())


def compact_json(value: Any, max_chars: int = 1800) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def excerpt(text: str, terms: set[str], max_chars: int = 600) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= max_chars:
        return clean
    lower = clean.lower()
    positions = [lower.find(term.lower()) for term in terms if term and lower.find(term.lower()) >= 0]
    start = max(0, min(positions) - 160) if positions else 0
    end = min(len(clean), start + max_chars)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(clean) else ""
    return prefix + clean[start:end] + suffix


def listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def infer_source_type(path: Path) -> str:
    name = path.name
    if name == "resolution_memory.json":
        return "resolution_memory"
    if name == "failure_advice.json":
        return "failure_advice"
    if name == "failure_advice.md":
        return "failure_advice_note"
    if name == "memory_summary.json":
        return "run_memory"
    if name == "modeling_memory_index.json":
        return "global_memory_index"
    if name == "run_memory_summaries.json":
        return "run_memory_index"
    if name == "project_memory.json":
        return "project_memory"
    if name == "experiment_note.md":
        return "experiment_note"
    if name == "model_diagnostics.json":
        return "model_diagnostics"
    if name.endswith(".md"):
        return "memory_note"
    return "memory_artifact"


def entry_from_record(record: dict[str, Any], source_path: Path, repo_root: Path, source_type: str) -> dict[str, Any]:
    fields = [
        record.get("run_id"),
        record.get("case_name"),
        record.get("project_key"),
        record.get("workflow_mode"),
        record.get("objective"),
        " ".join(listify(record.get("failure_patterns"))),
        " ".join(listify(record.get("model_diagnostic_ids"))),
        " ".join(listify(record.get("warnings"))),
        " ".join(listify(record.get("evidence_boundary_notes"))),
        " ".join(listify(record.get("next_run_cautions"))),
        compact_json(record.get("metrics", {}), max_chars=900),
    ]
    text = "\n".join(str(item) for item in fields if item)
    return {
        "schema_version": "1.0",
        "source_type": source_type,
        "source_path": relpath(source_path, repo_root),
        "run_id": record.get("run_id"),
        "project_key": record.get("project_key"),
        "case_name": record.get("case_name"),
        "workflow_mode": record.get("workflow_mode"),
        "qa_status": record.get("qa_status"),
        "failure_patterns": listify(record.get("failure_patterns")),
        "model_diagnostic_ids": listify(record.get("model_diagnostic_ids")),
        "next_run_cautions": listify(record.get("next_run_cautions")),
        "text": text,
    }


def entry_from_file(path: Path, repo_root: Path, source_type: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = normalize_entry_metadata(metadata or {})
    if path.suffix == ".json":
        parsed = read_json(path)
        text = compact_json(parsed if parsed is not None else {}, max_chars=3000)
    else:
        text = read_text(path)
    return {
        "schema_version": "1.0",
        "source_type": source_type,
        "source_path": relpath(path, repo_root),
        "run_id": metadata.get("run_id"),
        "project_key": metadata.get("project_key"),
        "case_name": metadata.get("case_name"),
        "workflow_mode": metadata.get("workflow_mode"),
        "qa_status": metadata.get("qa_status"),
        "failure_patterns": listify(metadata.get("failure_patterns")),
        "model_diagnostic_ids": listify(metadata.get("model_diagnostic_ids")),
        "next_run_cautions": listify(metadata.get("next_run_cautions")),
        "retrieval_grounded": metadata.get("retrieval_grounded"),
        "human_reviewed": metadata.get("human_reviewed"),
        "benchmark_verified": metadata.get("benchmark_verified"),
        "text": text,
    }


def normalize_entry_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    if "current_run_problem" in metadata and isinstance(metadata["current_run_problem"], dict):
        problem = metadata["current_run_problem"]
        out = dict(metadata)
        for key in ("run_id", "project_key", "case_name", "workflow_mode", "qa_status", "failure_patterns", "model_diagnostic_ids", "next_run_cautions"):
            if out.get(key) in (None, [], "") and problem.get(key) not in (None, [], ""):
                out[key] = problem.get(key)
        return out
    if "problem" in metadata and isinstance(metadata["problem"], dict):
        problem = metadata["problem"]
        out = dict(metadata)
        if out.get("failure_patterns") in (None, [], ""):
            out["failure_patterns"] = problem.get("failure_patterns")
        if out.get("model_diagnostic_ids") in (None, [], ""):
            out["model_diagnostic_ids"] = problem.get("diagnostics")
        return out
    return metadata


def load_modeling_index(memory_dir: Path) -> list[dict[str, Any]]:
    parsed = read_json(memory_dir / "modeling_memory_index.json")
    if isinstance(parsed, dict) and isinstance(parsed.get("records"), list):
        return [item for item in parsed["records"] if isinstance(item, dict)]
    return []


def records_by_run(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        run_id = record.get("run_id")
        if run_id:
            out[str(run_id)] = record
    return out


def build_corpus(memory_dir: Path, runs_dir: Path, repo_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    records = load_modeling_index(memory_dir)
    run_records = records_by_run(records)
    for record in records:
        entries.append(entry_from_record(record, memory_dir / "modeling_memory_index.json", repo_root, "run_record"))

    for path in sorted(memory_dir.glob("*.md")):
        entries.append(entry_from_file(path, repo_root, infer_source_type(path)))

    for path in sorted(memory_dir.glob("projects/*/project_memory.json")):
        parsed = read_json(path)
        metadata = parsed if isinstance(parsed, dict) else {}
        entries.append(entry_from_file(path, repo_root, "project_memory", metadata))

    for path in sorted(memory_dir.glob("projects/*/project_memory.md")):
        project_key = path.parent.name
        entries.append(entry_from_file(path, repo_root, "project_memory_note", {"project_key": project_key}))

    if runs_dir.exists():
        wanted = {"memory_summary.json", "experiment_note.md", "model_diagnostics.json", "failure_advice.json", "failure_advice.md", "resolution_memory.json"}
        for path in sorted(p for p in runs_dir.rglob("*") if p.is_file() and p.name in wanted):
            parsed = read_json(path) if path.suffix == ".json" else None
            metadata = parsed if isinstance(parsed, dict) else {}
            run_id = metadata.get("run_id")
            if run_id and str(run_id) in run_records:
                merged = {**run_records[str(run_id)], **metadata}
            else:
                merged = metadata
            entries.append(entry_from_file(path, repo_root, infer_source_type(path), merged))

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str]] = set()
    for entry in entries:
        key = (str(entry.get("source_path")), entry.get("run_id"), str(entry.get("source_type")))
        if key in seen:
            continue
        seen.add(key)
        entry["tokens"] = sorted(set(tokenize(" ".join([entry.get("text", ""), compact_json(entry, max_chars=1200)]))))
        deduped.append(entry)
    return deduped


def write_corpus(entries: list[dict[str, Any]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "corpus.jsonl").open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
    index: dict[str, list[int]] = {}
    for idx, entry in enumerate(entries):
        for token in entry.get("tokens", []):
            index.setdefault(str(token), []).append(idx)
    write_json(
        out_dir / "keyword_index.json",
        {
            "schema_version": "1.0",
            "generated_by": "swmm-rag-memory",
            "generated_at_utc": now_utc(),
            "entry_count": len(entries),
            "token_count": len(index),
            "index": index,
        },
    )
    write_json(
        out_dir / "embedding_index.json",
        {
            "schema_version": "1.0",
            "generated_by": "swmm-rag-memory",
            "backend": "local-hashed-token-char-ngram",
            "dimensions": EMBEDDING_DIMENSIONS,
            "generated_at_utc": now_utc(),
            "entry_count": len(entries),
            "vectors": [hashed_embedding(str(entry.get("text") or "")) for entry in entries],
        },
    )


def load_corpus(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries


def load_embedding_vectors(path: Path) -> list[dict[str, float]]:
    parsed = read_json(path)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("vectors"), list):
        return []
    vectors: list[dict[str, float]] = []
    for vector in parsed["vectors"]:
        if isinstance(vector, dict):
            vectors.append({str(key): float(value) for key, value in vector.items()})
    return vectors


def score_entry(entry: dict[str, Any], query_tokens: list[str], project: str | None = None) -> tuple[float, list[str]]:
    entry_tokens = set(str(token) for token in entry.get("tokens", []))
    query_set = set(query_tokens)
    matched = sorted(query_set & entry_tokens)
    if not matched:
        return 0.0, []

    token_score = sum(1.0 + math.log1p(len(token)) / 4.0 for token in matched)
    tag_text = " ".join(
        listify(entry.get("failure_patterns"))
        + listify(entry.get("model_diagnostic_ids"))
        + listify(entry.get("project_key"))
        + listify(entry.get("run_id"))
        + listify(entry.get("case_name"))
    ).lower()
    tag_matches = [token for token in query_set if token in tag_text]
    score = token_score + 2.5 * len(tag_matches)
    if entry.get("source_type") in {"run_record", "run_memory", "project_memory"}:
        score += 1.0
    if project and str(entry.get("project_key") or "").lower() == project.lower():
        score += 4.0
    elif project:
        score *= 0.35
    return score, sorted(set(matched + tag_matches))


def metadata_score(entry: dict[str, Any], query_tokens: list[str], project: str | None = None) -> float:
    query_set = set(query_tokens)
    tags = set(
        tokenize(
            " ".join(
                listify(entry.get("failure_patterns"))
                + listify(entry.get("model_diagnostic_ids"))
                + listify(entry.get("project_key"))
                + listify(entry.get("run_id"))
                + listify(entry.get("case_name"))
                + listify(entry.get("workflow_mode"))
            )
        )
    )
    score = min(1.0, len(query_set & tags) / 4.0)
    if project and str(entry.get("project_key") or "").lower() == project.lower():
        score = min(1.0, score + 0.5)
    return score


def retrieve(
    entries: list[dict[str, Any]],
    query: str,
    top_k: int,
    project: str | None = None,
    retriever: str = "keyword",
    embedding_vectors: list[dict[str, float]] | None = None,
) -> list[dict[str, Any]]:
    query_tokens = expand_query_tokens(query, tokenize(query))
    query_vector = hashed_embedding(" ".join([query, " ".join(query_tokens)])) if retriever == "hybrid" else {}
    scored: list[dict[str, Any]] = []
    max_keyword = 1.0
    raw_scores: list[tuple[dict[str, Any], float, list[str], float, float]] = []
    for idx, entry in enumerate(entries):
        score, matched_terms = score_entry(entry, query_tokens, project=project)
        semantic = 0.0
        if retriever == "hybrid":
            if embedding_vectors and idx < len(embedding_vectors):
                entry_vector = embedding_vectors[idx]
            else:
                entry_vector = hashed_embedding(str(entry.get("text") or ""))
            semantic = max(0.0, cosine_sparse(query_vector, entry_vector))
        if score <= 0 and semantic <= 0:
            continue
        meta = metadata_score(entry, query_tokens, project=project)
        max_keyword = max(max_keyword, score)
        raw_scores.append((entry, score, matched_terms, semantic, meta))

    for entry, keyword_score, matched_terms, semantic, meta in raw_scores:
        if retriever == "hybrid":
            normalized_keyword = keyword_score / max_keyword if max_keyword else 0.0
            final_score = (
                HYBRID_KEYWORD_WEIGHT * normalized_keyword
                + HYBRID_SEMANTIC_WEIGHT * semantic
                + HYBRID_METADATA_WEIGHT * meta
            ) * 100.0
        else:
            final_score = keyword_score
        result = {key: value for key, value in entry.items() if key not in {"tokens", "text"}}
        result["score"] = round(final_score, 3)
        result["keyword_score"] = round(keyword_score, 3)
        result["semantic_score"] = round(semantic, 3)
        result["metadata_score"] = round(meta, 3)
        result["matched_terms"] = matched_terms
        result["excerpt"] = excerpt(str(entry.get("text") or ""), set(matched_terms))
        scored.append(result)
    scored.sort(key=lambda item: (-float(item["score"]), str(item.get("source_path"))))
    return scored[:top_k]


def render_context_pack(query: str, matches: list[dict[str, Any]]) -> str:
    lines = [
        "# Retrieved Agentic SWMM Memory Context",
        "",
        "## User Question",
        query,
        "",
        "## Relevant Historical Evidence",
    ]
    if not matches:
        lines.append("- No relevant Agentic SWMM memory was retrieved.")
    for idx, match in enumerate(matches, start=1):
        lines.extend(
            [
                f"{idx}. Source: `{match.get('source_path')}`",
                f"   - Type: `{match.get('source_type')}`",
                f"   - Run: `{match.get('run_id') or 'n/a'}`",
                f"   - Project: `{match.get('project_key') or 'n/a'}`",
                f"   - Failure patterns: `{', '.join(match.get('failure_patterns') or []) or 'none'}`",
                f"   - Diagnostics: `{', '.join(match.get('model_diagnostic_ids') or []) or 'none'}`",
                f"   - Matched terms: `{', '.join(match.get('matched_terms') or [])}`",
                f"   - Review state: retrieval_grounded=`{match.get('retrieval_grounded')}`, human_reviewed=`{match.get('human_reviewed')}`, benchmark_verified=`{match.get('benchmark_verified')}`",
                f"   - Evidence excerpt: {match.get('excerpt') or 'n/a'}",
            ]
        )
    lines.extend(
        [
            "",
            "## Answer Constraints",
            "- Use retrieved Agentic SWMM memory as historical context, not as proof of a new model result.",
            "- Distinguish confirmed audit evidence from inference.",
            "- Cite source paths and run ids when making a memory-grounded claim.",
            "- Keep missing evidence and QA limitations visible.",
            "",
        ]
    )
    return "\n".join(lines)


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.lower()).strip("-._")
    return slug[:80] or "rag-query"


def read_run_evidence(run_dir: Path) -> dict[str, Any]:
    memory_summary = read_json(run_dir / "memory_summary.json")
    provenance = read_json(run_dir / "experiment_provenance.json")
    comparison = read_json(run_dir / "comparison.json")
    diagnostics = read_json(run_dir / "model_diagnostics.json")
    note_text = read_text(run_dir / "experiment_note.md")
    return {
        "memory_summary": memory_summary if isinstance(memory_summary, dict) else {},
        "provenance": provenance if isinstance(provenance, dict) else {},
        "comparison": comparison if isinstance(comparison, dict) else {},
        "diagnostics": diagnostics if isinstance(diagnostics, dict) else {},
        "experiment_note": note_text,
    }


def extract_run_problem(evidence: dict[str, Any], run_dir: Path, repo_root: Path) -> dict[str, Any]:
    memory_summary = evidence.get("memory_summary") or {}
    provenance = evidence.get("provenance") or {}
    comparison = evidence.get("comparison") or {}
    diagnostics = evidence.get("diagnostics") or {}
    artifacts = provenance.get("artifacts") if isinstance(provenance.get("artifacts"), dict) else {}
    missing_evidence = listify(memory_summary.get("missing_evidence"))
    if not missing_evidence and artifacts:
        for artifact_id, record in artifacts.items():
            if isinstance(record, dict) and record.get("exists") is False:
                missing_evidence.append(str(artifact_id))

    stderr_excerpt = ""
    stderr_record = artifacts.get("runner_stderr") if isinstance(artifacts, dict) else None
    stderr_path = None
    if isinstance(stderr_record, dict):
        stderr_value = stderr_record.get("absolute_path") or stderr_record.get("relative_path")
        if stderr_value:
            candidate = Path(str(stderr_value))
            stderr_path = candidate if candidate.is_absolute() else repo_root / candidate
    if stderr_path and stderr_path.exists():
        stderr_excerpt = excerpt(read_text(stderr_path), set(), max_chars=800)

    diagnostic_ids = listify(memory_summary.get("model_diagnostic_ids"))
    if not diagnostic_ids:
        diagnostic_ids = [str(item.get("id")) for item in diagnostics.get("diagnostics", []) if isinstance(item, dict) and item.get("id")]

    failure_patterns = listify(memory_summary.get("failure_patterns"))
    if not failure_patterns:
        metrics = provenance.get("metrics") if isinstance(provenance.get("metrics"), dict) else {}
        if metrics.get("peak_flow") is None:
            failure_patterns.append("peak_flow_parse_missing")
        if metrics.get("continuity_error") is None:
            failure_patterns.append("continuity_parse_missing")
        if missing_evidence:
            failure_patterns.append("partial_run")
        if not failure_patterns:
            failure_patterns.append("no_detected_failure")

    qa_status = memory_summary.get("qa_status") or ((provenance.get("qa") or {}).get("status") if isinstance(provenance.get("qa"), dict) else None) or "unknown"
    diagnostic_status = memory_summary.get("model_diagnostics_status") or diagnostics.get("status") or "unknown"
    comparison_status = memory_summary.get("comparison_status")
    if not comparison_status:
        comparison_status = "mismatch" if any(isinstance(c, dict) and c.get("same") is False for c in comparison.get("checks", []) or []) else "unknown"

    return {
        "run_id": memory_summary.get("run_id") or provenance.get("run_id") or run_dir.name,
        "run_dir": relpath(run_dir, repo_root),
        "project_key": memory_summary.get("project_key"),
        "case_name": memory_summary.get("case_name") or provenance.get("case_name") or run_dir.name,
        "workflow_mode": memory_summary.get("workflow_mode") or provenance.get("workflow_mode"),
        "audit_status": memory_summary.get("audit_status") or provenance.get("status"),
        "qa_status": qa_status,
        "comparison_status": comparison_status,
        "failure_patterns": sorted(set(failure_patterns)),
        "model_diagnostic_ids": sorted(set(diagnostic_ids)),
        "missing_evidence": sorted(set(missing_evidence)),
        "warnings": sorted(set(listify(memory_summary.get("warnings")) + listify(provenance.get("warnings")))),
        "next_run_cautions": listify(memory_summary.get("next_run_cautions")),
        "stderr_excerpt": stderr_excerpt,
        "model_diagnostics_status": diagnostic_status,
    }


def should_generate_failure_advice(problem: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if str(problem.get("audit_status") or "").lower() not in {"", "none", "pass"}:
        reasons.append("audit_status_not_pass")
    if str(problem.get("qa_status") or "").lower() not in {"", "none", "pass"}:
        reasons.append("qa_status_not_pass")
    if str(problem.get("comparison_status") or "").lower() == "mismatch":
        reasons.append("comparison_mismatch")
    if str(problem.get("model_diagnostics_status") or "").lower() in {"warning", "fail", "error"}:
        reasons.append("model_diagnostics_not_pass")
    patterns = set(problem.get("failure_patterns") or [])
    if patterns and patterns != {"no_detected_failure"}:
        reasons.append("failure_patterns_detected")
    if problem.get("missing_evidence"):
        reasons.append("missing_evidence_detected")
    if problem.get("warnings"):
        reasons.append("warnings_detected")
    if problem.get("stderr_excerpt"):
        reasons.append("stderr_present")
    return bool(reasons), sorted(set(reasons))


def build_failure_advice_query(problem: dict[str, Any]) -> str:
    parts = [
        str(problem.get("case_name") or ""),
        str(problem.get("workflow_mode") or ""),
        " ".join(problem.get("failure_patterns") or []),
        " ".join(problem.get("model_diagnostic_ids") or []),
        " ".join(problem.get("missing_evidence") or []),
        " ".join(problem.get("warnings") or []),
        str(problem.get("stderr_excerpt") or ""),
    ]
    return " ".join(part for part in parts if part).strip() or str(problem.get("run_id") or "failed run")


def render_failure_advice(advice: dict[str, Any]) -> str:
    problem = advice["current_run_problem"]
    lines = [
        "# Failure Advice",
        "",
        "This file is retrieval-grounded advice. It is not proof of model correctness and it did not modify model files.",
        "",
        "## Current Run Evidence",
        f"- Run ID: `{problem.get('run_id')}`",
        f"- Run directory: `{problem.get('run_dir')}`",
        f"- Project: `{problem.get('project_key') or 'n/a'}`",
        f"- Audit status: `{problem.get('audit_status')}`",
        f"- QA status: `{problem.get('qa_status')}`",
        f"- Diagnostic status: `{problem.get('model_diagnostics_status')}`",
        f"- Failure patterns: `{', '.join(problem.get('failure_patterns') or []) or 'none'}`",
        f"- Model diagnostics: `{', '.join(problem.get('model_diagnostic_ids') or []) or 'none'}`",
        f"- Missing evidence: `{', '.join(problem.get('missing_evidence') or []) or 'none'}`",
        f"- Trigger reasons: `{', '.join(advice.get('trigger_reasons') or [])}`",
        "",
    ]
    if problem.get("warnings"):
        lines.extend(["## Warnings", *[f"- {item}" for item in problem["warnings"]], ""])
    if problem.get("stderr_excerpt"):
        lines.extend(["## Stderr Excerpt", "```text", str(problem["stderr_excerpt"]), "```", ""])

    lines.append("## Retrieved Similar Memory")
    matches = advice.get("retrieved_memory") or []
    if not matches:
        lines.append("- No similar historical memory was retrieved.")
    for idx, match in enumerate(matches, start=1):
        lines.extend(
            [
                f"{idx}. `{match.get('source_path')}`",
                f"   - Run: `{match.get('run_id') or 'n/a'}`",
                f"   - Project: `{match.get('project_key') or 'n/a'}`",
                f"   - Source type: `{match.get('source_type')}`",
                f"   - Failure patterns: `{', '.join(match.get('failure_patterns') or []) or 'none'}`",
                f"   - Diagnostics: `{', '.join(match.get('model_diagnostic_ids') or []) or 'none'}`",
                f"   - Review state: retrieval_grounded=`{match.get('retrieval_grounded')}`, human_reviewed=`{match.get('human_reviewed')}`, benchmark_verified=`{match.get('benchmark_verified')}`",
                f"   - Matched terms: `{', '.join(match.get('matched_terms') or [])}`",
                f"   - Excerpt: {match.get('excerpt') or 'n/a'}",
            ]
        )

    lines.extend(["", "## Suggested Next Checks"])
    suggestions = advice.get("suggested_next_checks") or []
    if suggestions:
        lines.extend(f"- {item}" for item in suggestions)
    else:
        lines.append("- Inspect current run evidence manually before changing model inputs or workflow code.")

    lines.extend(
        [
            "",
            "## Boundary",
            "- This advice is generated from current audit evidence plus retrieved historical memory.",
            "- It does not edit SWMM model files, workflow code, or skill definitions.",
            "- Scientific modeling changes require human review and benchmark verification.",
            "- A repair should only become `resolution_memory.json` after verification evidence exists.",
            "",
        ]
    )
    return "\n".join(lines)


def suggested_checks(problem: dict[str, Any], matches: list[dict[str, Any]]) -> list[str]:
    checks: list[str] = []
    patterns = set(problem.get("failure_patterns") or [])
    diagnostics = set(problem.get("model_diagnostic_ids") or [])
    missing = set(problem.get("missing_evidence") or [])
    if "peak_flow_parse_missing" in patterns or "peak_qa" in missing:
        checks.append("Check whether the SWMM report contains `Node Inflow Summary`; if not, document the fallback section before accepting peak-flow evidence.")
    if "continuity_parse_missing" in patterns or "continuity_qa" in missing:
        checks.append("Check whether continuity tables are present in the runner report and referenced by the run manifest.")
    if "missing_inp" in patterns or "model_inp" in missing:
        checks.append("Record the runnable SWMM INP handoff before treating the run as reproducible.")
    if "continuity_error_high" in diagnostics:
        checks.append("Inspect routing step, storage, and external inflow/outflow accounting before treating the result as hydrologic evidence.")
    if "conduit_slope_suspicious" in diagnostics:
        checks.append("Check node invert elevations, conduit direction, and conduit length before changing hydrologic parameters.")
    if "partial_run" in patterns:
        checks.append("Keep this as partial-run evidence until the missing artifacts are produced or explicitly waived.")
    for match in matches[:3]:
        for caution in match.get("next_run_cautions") or []:
            if caution not in checks:
                checks.append(str(caution))
    if not checks:
        checks.append("Use the retrieved source paths to inspect the closest historical runs before deciding on a repair.")
    return checks
