"""``aiswmm memory promote-facts`` CLI behaviour.

Stubbing ``$EDITOR=true`` is a no-op; the CLI must append staging to
``facts.md`` then truncate staging. Stubbing ``$EDITOR=false`` is a
clean abort: neither file changes.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _setup_staging(curated_dir: Path) -> tuple[Path, Path]:
    curated_dir.mkdir(parents=True, exist_ok=True)
    facts_md = curated_dir / "facts.md"
    staging_md = curated_dir / "facts_staging.md"
    facts_md.write_text(
        "<!-- header -->\n# Project facts (curated)\n",
        encoding="utf-8",
    )
    staging_md.write_text(
        (
            "§\n"
            "text: user prefers metric units\n"
            "source_session: s1\n"
            "proposed_utc: 2026-05-13T00:00:00+00:00\n"
            "§\n"
        ),
        encoding="utf-8",
    )
    return facts_md, staging_md


def test_promote_facts_appends_then_clears_staging(tmp_path: Path) -> None:
    from agentic_swmm.commands.memory import promote_facts_main

    curated_dir = tmp_path / "curated"
    facts_md, staging_md = _setup_staging(curated_dir)
    facts_before = facts_md.read_text(encoding="utf-8")

    os.environ["AISWMM_FACTS_DIR"] = str(curated_dir)
    try:
        rc = promote_facts_main(argparse.Namespace(editor="true"))
    finally:
        os.environ.pop("AISWMM_FACTS_DIR", None)

    assert rc == 0
    facts_after = facts_md.read_text(encoding="utf-8")
    assert "user prefers metric units" in facts_after
    assert facts_after.startswith(facts_before)
    assert staging_md.read_text(encoding="utf-8") == ""


def test_promote_facts_aborts_when_editor_exits_nonzero(tmp_path: Path) -> None:
    from agentic_swmm.commands.memory import promote_facts_main

    curated_dir = tmp_path / "curated"
    facts_md, staging_md = _setup_staging(curated_dir)
    facts_before = facts_md.read_text(encoding="utf-8")
    staging_before = staging_md.read_text(encoding="utf-8")

    os.environ["AISWMM_FACTS_DIR"] = str(curated_dir)
    try:
        rc = promote_facts_main(argparse.Namespace(editor="false"))
    finally:
        os.environ.pop("AISWMM_FACTS_DIR", None)

    assert rc != 0
    assert facts_md.read_text(encoding="utf-8") == facts_before
    assert staging_md.read_text(encoding="utf-8") == staging_before


def test_promote_facts_is_a_noop_when_staging_is_empty(tmp_path: Path) -> None:
    from agentic_swmm.commands.memory import promote_facts_main

    curated_dir = tmp_path / "curated"
    curated_dir.mkdir()
    (curated_dir / "facts_staging.md").write_text("", encoding="utf-8")
    facts_md = curated_dir / "facts.md"
    facts_md.write_text("# Project facts (curated)\n", encoding="utf-8")
    snapshot = facts_md.read_text(encoding="utf-8")

    os.environ["AISWMM_FACTS_DIR"] = str(curated_dir)
    try:
        rc = promote_facts_main(argparse.Namespace(editor="true"))
    finally:
        os.environ.pop("AISWMM_FACTS_DIR", None)
    assert rc == 0
    assert facts_md.read_text(encoding="utf-8") == snapshot
