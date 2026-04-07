#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def patch_inp_text(text: str, patch_map: dict, params: dict) -> str:
    lines = text.splitlines()
    current_section = None
    touched = set()

    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped.upper()
            continue
        if stripped.startswith(";"):
            continue

        code, *comment = raw.split(";", 1)
        tokens = code.split()
        if not tokens:
            continue

        for key, value in params.items():
            spec = patch_map.get(key)
            if not spec:
                continue
            if current_section != str(spec["section"]).upper():
                continue
            if tokens[0] != str(spec["object"]):
                continue
            idx = int(spec["field_index"])
            if idx >= len(tokens):
                raise IndexError(f"Field index {idx} out of range for {key} on line: {raw}")
            tokens[idx] = str(value)
            new_code = "  ".join(tokens)
            if comment:
                new_code += " ;" + comment[0]
            lines[i] = new_code
            touched.add(key)

    missing = sorted(set(params) - touched)
    if missing:
        raise KeyError(f"Did not patch parameter(s): {missing}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", required=True, type=Path)
    ap.add_argument("--patch-map", required=True, type=Path)
    ap.add_argument("--params", required=True, type=Path, help="JSON object of parameter values")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    patch_map = json.loads(args.patch_map.read_text())
    params = json.loads(args.params.read_text())
    patched = patch_inp_text(args.inp.read_text(errors="ignore"), patch_map, params)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(patched, encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(args.out)}, indent=2))


if __name__ == "__main__":
    main()
