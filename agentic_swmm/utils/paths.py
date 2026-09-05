from __future__ import annotations

import os
import sysconfig
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_memory_dir(explicit: Path | None = None) -> Path:
    """Resolve the modeling-memory directory.

    Precedence: ``explicit`` argument -> ``AISWMM_MEMORY_DIR`` env var ->
    ``<repo>/memory/modeling-memory``. Explicit and env values are
    expanduser()+resolve()d.
    """
    if explicit is not None:
        return explicit.expanduser().resolve()
    override = os.environ.get("AISWMM_MEMORY_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return resource_root() / "memory" / "modeling-memory"


def resolve_runs_dir(explicit: Path | None = None) -> Path:
    """Resolve the runs root.

    Precedence: ``explicit`` argument -> ``AISWMM_RUNS_ROOT`` env var ->
    ``<repo>/runs``. Explicit and env values are expanduser()+resolve()d.
    """
    if explicit is not None:
        return explicit.expanduser().resolve()
    override = os.environ.get("AISWMM_RUNS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    root = repo_root()
    if is_checkout(root):
        return root / "runs"
    # Live finding F-137 (2026-09-04): on a pip install repo_root() is
    # site-packages, and the default runs root landed inside it. A pip
    # user's runs belong next to where they work.
    return Path.cwd().resolve() / "runs"


def is_checkout(root: Path | None = None) -> bool:
    """True when ``root`` (default ``repo_root()``) is a source checkout, not
    the site-packages directory a wheel resolves ``repo_root()`` to."""
    root = repo_root() if root is None else root
    return (root / "skills").exists() and _has_agent_resources(root)


_EXTRA_WORKSPACE_ROOTS: list[Path] = []


def register_workspace_root(path: Path) -> Path:
    """Add a directory the agent may address (the session base dir).

    Live finding F-135 (2026-09-04, S59 on the released wheel): list_dir,
    read_file and every other path-sandboxed tool required paths inside
    ``repo_root()``, which on a pip install is site-packages, so a user could
    not list their own run directory. The shell registers its session base
    directory here when it starts.
    """
    resolved = path.expanduser().resolve()
    if resolved not in _EXTRA_WORKSPACE_ROOTS:
        _EXTRA_WORKSPACE_ROOTS.append(resolved)
    return resolved


def workspace_roots(root: Path | None = None) -> list[Path]:
    """The directories a path-sandboxed tool may address, primary root first.

    ``root`` is the caller's ``repo_root()`` (handlers pass their own module
    name so tests that patch it keep working): the checkout, or
    site-packages on a wheel, as before. Then the runs root, the packaged
    resources (skills, examples, mcp), the user's working directory when the
    primary root is not a checkout (a pip install), and every registered
    session base directory.
    """
    primary = (repo_root() if root is None else root).resolve()
    roots: list[Path] = [primary]
    extras = [resolve_runs_dir(), resource_root().resolve()]
    if not is_checkout(primary):
        extras.append(Path.cwd().resolve())
    for candidate in (*extras, *_EXTRA_WORKSPACE_ROOTS):
        if candidate not in roots:
            roots.append(candidate)
    return roots


def resolve_workspace_path(value: str, root: Path | None = None) -> Path | None:
    """Resolve a user or planner path against the workspace, or ``None``.

    Absolute paths must lie under one of :func:`workspace_roots`. Relative
    paths resolve against the first root under which they exist, else
    against the primary root (so a file about to be created lands in the
    repository on a checkout, exactly as before).
    """
    raw = Path(value).expanduser()
    roots = workspace_roots(root)
    if raw.is_absolute():
        candidate = raw.resolve()
        return candidate if _under_any(candidate, roots) else None
    for base in roots:
        candidate = (base / raw).resolve()
        if candidate.exists() and _under_any(candidate, roots):
            return candidate
    candidate = (roots[0] / raw).resolve()
    return candidate if _under_any(candidate, roots) else None


def workspace_relative(path: Path, root: Path | None = None) -> str:
    """``path`` rendered relative to the workspace root that holds it, else
    absolute. Replaces ``path.relative_to(repo_root())`` in tool summaries,
    which raised on every pip install (F-135)."""
    resolved = Path(path).expanduser().resolve()
    for base in workspace_roots(root):
        try:
            return str(resolved.relative_to(base))
        except ValueError:
            continue
    return str(resolved)


def _under_any(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def packaged_resource_root() -> Path:
    return Path(sysconfig.get_path("data")) / "aiswmm"


def resource_root() -> Path:
    source_root = repo_root()
    if (source_root / "skills").exists() and _has_agent_resources(source_root):
        return source_root
    installed_root = packaged_resource_root()
    if installed_root.exists():
        return installed_root
    return source_root


def resource_path(*parts: str) -> Path:
    root = resource_root()
    source_path = root.joinpath(*parts)
    if source_path.exists():
        return source_path

    installed_path = packaged_resource_root().joinpath(*parts)
    if installed_path.exists():
        return installed_path

    raise FileNotFoundError(
        "Required Agentic SWMM resource is missing. Checked source path "
        f"{source_path} and installed package path {installed_path}."
    )


def _has_agent_resources(root: Path) -> bool:
    return any(
        path.exists()
        for path in (
            root / "agent" / "memory",
            root / "agent" / "config",
            root / "agent" / "identification_memory.md",
        )
    )


def require_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(f"{label} does not exist or is not a file: {resolved}")
    return resolved


def require_dir(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise FileNotFoundError(f"{label} does not exist or is not a directory: {resolved}")
    return resolved


def script_path(*parts: str) -> Path:
    return resource_path(*parts)
