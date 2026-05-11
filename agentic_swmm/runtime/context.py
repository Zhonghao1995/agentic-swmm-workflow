from __future__ import annotations

from pathlib import Path

from agentic_swmm.utils.paths import resource_root
from agentic_swmm.runtime.registry import enabled_skill_files, enabled_startup_memory_files


DEFAULT_CONTEXT_FILES = [
    Path("skills/swmm-end-to-end/SKILL.md"),
    Path("docs/openclaw-execution-path.md"),
]


BASE_SYSTEM_PROMPT = """You are Agentic SWMM, a local stormwater modeling assistant.
Use the provided project memory and skills as operating context.
Keep SWMM execution claims evidence-based: distinguish completed local runs, parsed artifacts, QA warnings, and recommendations.
When a user asks to run or audit a model, prefer the local aiswmm CLI and existing skills instead of inventing unsupported behavior.
"""


def build_system_prompt(max_chars: int = 20000) -> str:
    root = resource_root()
    sections = [BASE_SYSTEM_PROMPT.strip()]
    remaining = max(0, max_chars - len(sections[0]))
    context_paths = enabled_startup_memory_files()
    for relative in DEFAULT_CONTEXT_FILES:
        path = root / relative
        if path not in context_paths:
            context_paths.append(path)
    for skill_file in enabled_skill_files():
        if skill_file not in context_paths:
            context_paths.append(skill_file)

    for path in context_paths:
        try:
            relative = path.resolve().relative_to(root.resolve())
        except ValueError:
            relative = path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        header = f"\n\n---\nContext file: {relative}\n---\n"
        chunk = header + text
        if len(chunk) > remaining:
            if remaining > len(header) + 200:
                sections.append(header + text[: remaining - len(header)].rstrip() + "\n[truncated]")
            break
        sections.append(chunk)
        remaining -= len(chunk)
    return "\n".join(sections)
