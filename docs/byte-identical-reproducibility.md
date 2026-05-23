# Byte-identical SWMM reproducibility across environments (Tecnopolo)

The Tecnopolo INP (`examples/tecnopolo/tecnopolo_r1_199401.inp`) was executed through three independent stacks — the full aiswmm chain on macOS driven by a natural-language prompt (`Run the Tecnopolo (Rome 1994) demo`), the aiswmm skill inside a Docker container, and bare `swmm5` invoked directly inside the same container. All three produced a byte-identical SWMM binary output.

## What was compared

| Path | Stack | Invocation |
| --- | --- | --- |
| macOS, full aiswmm chain | aiswmm 0.7.0a1 (editable) · SWMM 5.2.4 (Homebrew) | Natural-language prompt `Run the Tecnopolo (Rome 1994) demo` in `aiswmm interactive` → LLM agent → MCP server → swmm-runner skill → `swmm5` |
| Docker, aiswmm runner | aiswmm 0.6.4 · SWMM 5.2.4 (compiled from `USEPA/Stormwater-Management-Model@v5.2.4`) | `tecnopolo` entrypoint → swmm-runner skill → `swmm5` |
| Docker, direct | Same container build | `tecnopolo` entrypoint → `swmm5` invoked directly |

## Result

All three paths emit a `model.out` with the same SHA256:

```
85c5514a81ea745ebb0c1c3e2aebb0c2cc0d5a6aa3ef00a0fa6c8f7b760be38c
```

Continuity errors agree exactly: runoff quantity `-0.13 %`, flow routing `-0.004 %`. Peak flow at the outfall `OUT_0` is `0.061 CMS @ 03:15`; peak at internal junction `J22` is `0.007 CMS @ 03:15`. The 8 640-point inflow series at `J22` matches between the runner-mediated and direct invocations.

## What this proves

Across **operating system** (macOS native vs Linux container), **SWMM provenance** (Homebrew binary vs source build), **aiswmm version** (0.6.4 vs 0.7.0a1), and **invocation path** (full LLM-agent chain vs bare `swmm5`):

1. **The full aiswmm chain reproduces the bare-`swmm5` result byte-for-byte from a natural-language prompt.** Issuing `Run the Tecnopolo (Rome 1994) demo` to `aiswmm interactive` drives the LLM-agent-mediated pipeline (LLM agent → MCP → swmm-runner skill → SWMM) to the same `model.out`, the same continuity errors, and the same hydrograph that bare `swmm5` produces on this INP.
2. **The swmm-runner skill is a transparent pass-through.** The Docker-runner vs Docker-direct comparison isolates the skill layer in a single environment; both produce identical output.
3. **The MCP layer is a transparent pass-through.** The macOS path goes through the MCP server; the Docker paths do not — and all three results match.
4. **SWMM 5.2.4 itself is numerically deterministic for this INP** across the Homebrew binary and the in-container source build.

## Scope

This evidence covers the SWMM execution layer — for a given INP and goal, every orchestration layer in the aiswmm chain is verifiably transparent. Reproducibility of LLM agent decision-making, of upstream stages (GIS→INP construction, calibration, uncertainty quantification), and byte-equivalence of audit and plot artifacts (which legitimately embed timestamps and git HEAD) are separate questions not addressed here.

## How to reproduce locally

```bash
# Path A: macOS, natural-language driven (requires Homebrew SWMM, a local
# aiswmm install, and an LLM provider configured for `aiswmm interactive`)
aiswmm interactive
#   you> Run the Tecnopolo (Rome 1994) demo
# Locate the resulting run directory under runs/<date>/<id>_tecnopolo_run/
shasum -a 256 "runs/<date>/<id>_tecnopolo_run/model.out"

# Paths B + C: Docker (the tecnopolo entrypoint runs both the runner-mediated and direct invocations)
docker run --rm -v "$PWD/docker-runs:/app/runs" \
  ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.6.4 tecnopolo
shasum -a 256 docker-runs/benchmarks/tecnopolo-199401-prepared/06_runner/model.out
shasum -a 256 docker-runs/benchmarks/tecnopolo-199401-prepared/10_direct/model.out
```

All three SHA256 outputs should equal `85c5514a81ea745ebb0c1c3e2aebb0c2cc0d5a6aa3ef00a0fa6c8f7b760be38c`.
