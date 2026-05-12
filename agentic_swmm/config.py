from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_DIR_ENV = "AISWMM_CONFIG_DIR"
DEFAULT_PROVIDER = "openai"
DEFAULT_OPENAI_MODEL = "gpt-5.5"


@dataclass(frozen=True)
class AiswmmConfig:
    path: Path
    values: dict[str, Any]

    def get(self, dotted_key: str, default: Any = None) -> Any:
        current: Any = self.values
        for part in dotted_key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current


def config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".aiswmm"


def config_path() -> Path:
    return config_dir() / "config.toml"


def skills_registry_path() -> Path:
    return config_dir() / "skills.json"


def mcp_registry_path() -> Path:
    return config_dir() / "mcp.json"


def memory_registry_path() -> Path:
    return config_dir() / "memory.json"


def setup_state_path() -> Path:
    return config_dir() / "setup_state.json"


def mcp_schema_cache_dir() -> Path:
    return config_dir() / "mcp_schema_cache"


def default_values() -> dict[str, Any]:
    return {
        "provider": {
            "default": DEFAULT_PROVIDER,
        },
        "openai": {
            "model": os.environ.get("AISWMM_OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        },
        "runtime": {
            "context_max_chars": 20000,
        },
    }


def load_config(path: Path | None = None) -> AiswmmConfig:
    target = path or config_path()
    values = default_values()
    if target.exists():
        loaded = _parse_simple_toml(target.read_text(encoding="utf-8"))
        _deep_update(values, loaded)
    return AiswmmConfig(path=target, values=values)


def write_config(values: dict[str, Any], path: Path | None = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_to_toml(values), encoding="utf-8")
    return target


def set_config_value(dotted_key: str, value: str, path: Path | None = None) -> AiswmmConfig:
    config = load_config(path)
    values = config.values
    current = values
    parts = dotted_key.split(".")
    if not all(parts):
        raise ValueError("config key must be a dotted path like openai.model")
    for part in parts[:-1]:
        next_value = current.setdefault(part, {})
        if not isinstance(next_value, dict):
            raise ValueError(f"cannot set {dotted_key}; {part} is not a section")
        current = next_value
    current[parts[-1]] = _coerce_value(value)
    write_config(values, config.path)
    return load_config(config.path)


def _deep_update(base: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def _coerce_value(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return value


def _to_toml(values: dict[str, Any]) -> str:
    lines: list[str] = []
    scalar_items = {key: value for key, value in values.items() if not isinstance(value, dict)}
    for key, value in sorted(scalar_items.items()):
        lines.append(f"{key} = {_format_toml_value(value)}")
    if scalar_items:
        lines.append("")
    for section, section_values in sorted((k, v) for k, v in values.items() if isinstance(v, dict)):
        lines.append(f"[{section}]")
        for key, value in sorted(section_values.items()):
            if isinstance(value, dict):
                continue
            lines.append(f"{key} = {_format_toml_value(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_simple_toml(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    current = values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            current = values.setdefault(section, {})
            if not isinstance(current, dict):
                raise ValueError(f"invalid config section: {section}")
            continue
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        current[key.strip()] = _parse_simple_toml_value(raw_value.strip())
    return values


def _parse_simple_toml_value(value: str) -> Any:
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith('"') and value.endswith('"'):
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
