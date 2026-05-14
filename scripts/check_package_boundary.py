from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path


FORBIDDEN_SUBSTRINGS = (
    "/skills/swmm-rag-memory/",
    "/skills/swmm-lid-optimization/",
    "/memory/rag-memory/",
    "/memory/modeling-memory/projects/",
    "/memory/modeling-memory/run_memory_summaries.json",
    "/skills/swmm-gis/scripts/flowpath_entropy_partition.py",
    "/skills/swmm-gis/scripts/cell_entropy_similarity_aggregation.py",
    "/skills/swmm-gis/scripts/plot_entropy_threshold_sensitivity.py",
    "/skills/swmm-gis/scripts/qgis_raw_to_entropy_partition.py",
    "/skills/swmm-gis/scripts/qgis_todcreek_raw_to_entropy_partition.py",
    "/skills/swmm-uncertainty/scripts/monte_carlo_propagate.py",
    "/skills/swmm-uncertainty/scripts/entropy_metrics.py",
    "/skills/swmm-uncertainty/scripts/parameter_recommender.py",
    "/skills/swmm-uncertainty/scripts/probabilistic_sampling.py",
    "/skills/swmm-uncertainty/tests/test_monte_carlo_propagate.py",
    "/skills/swmm-uncertainty/tests/test_entropy_metrics.py",
    "/skills/swmm-uncertainty/tests/test_parameter_recommender.py",
    "/skills/swmm-uncertainty/tests/test_probabilistic_sampling.py",
    "/skills/swmm-uncertainty/examples/entropy_ensemble.json",
    "/skills/swmm-uncertainty/examples/monte_carlo_space.json",
    "/scripts/benchmarks/run_tecnopolo_lid_placement_smoke.py",
    "/scripts/benchmarks/run_tecnopolo_mc_uncertainty_smoke.py",
    "/docs/qgis-entropy-subcatchment-workflow.md",
    "/docs/qgis-entropy-subcatchment-mcp-skill-plan.md",
    "/docs/lid-entropy-decision-support-plan.md",
    "/docs/lid-optimization-workflow.md",
    "/docs/obsidian-compatible-rag-memory.md",
    "/docs/calibration-uncertainty-workflow.md",
    "/docs/figs/tecnopolo_mc_entropy_curves.png",
    "/docs/figs/tecnopolo_lid_placement_smoke.png",
    "/docs/figs/tecnopolo_mc_uncertainty_flow_envelope.png",
    "/tests/test_swmm_rag_memory.py",
    "/tests/test_flowpath_entropy_partition.py",
    "/tests/test_cell_entropy_similarity_aggregation.py",
    "/tests/test_qgis_mcp_contracts.py",
    "corpus.jsonl",
    "embedding_index.json",
    "keyword_index.json",
)

REQUIRED_SUBSTRINGS = (
    "agentic_swmm/cli.py",
    "/agent/config/intent_map.json",
    "/skills/swmm-end-to-end/SKILL.md",
    "/mcp/swmm-runner/server.js",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check public aiswmm package artifacts for private-only files.")
    parser.add_argument("artifacts", nargs="+", type=Path, help="Wheel or sdist files to inspect.")
    args = parser.parse_args()

    failed = False
    for artifact in args.artifacts:
        names = _artifact_names(artifact)
        forbidden = _matching(names, FORBIDDEN_SUBSTRINGS)
        missing = [item for item in REQUIRED_SUBSTRINGS if not any(item in _logical_path(name) for name in names)]

        if forbidden or missing:
            failed = True
            print(f"FAIL {artifact}")
            if forbidden:
                print("  forbidden files:")
                for name in forbidden[:50]:
                    print(f"    {name}")
            if missing:
                print("  missing required package resources:")
                for item in missing:
                    print(f"    {item}")
        else:
            print(f"OK   {artifact} ({len(names)} files)")

    return 1 if failed else 0


def _artifact_names(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    if path.suffixes[-2:] == [".tar", ".gz"]:
        with tarfile.open(path, "r:gz") as archive:
            return archive.getnames()
    raise ValueError(f"Unsupported package artifact: {path}")


def _matching(names: list[str], substrings: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for name in names:
        normalized = _logical_path(name)
        if any(substring in normalized for substring in substrings):
            matches.append(name)
    return matches


def _logical_path(name: str) -> str:
    normalized = name.replace("\\", "/").lstrip("/")
    parts = normalized.split("/")
    for index in range(len(parts) - 3):
        if parts[index].endswith(".data") and parts[index + 1 : index + 3] == ["data", "aiswmm"]:
            return "/" + "/".join(parts[index + 3 :])
    if parts and parts[0].startswith("aiswmm-"):
        return "/" + "/".join(parts[1:])
    return "/" + normalized


if __name__ == "__main__":
    sys.exit(main())
