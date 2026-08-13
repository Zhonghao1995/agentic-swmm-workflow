# One prompt, a real municipal network, and a model that said not to trust it

**Machine:** Windows 11 on ARM · **Version:** aiswmm v0.9.2 · **Date:** 2026-08-13

A second downtown Victoria run, on a laptop rather than a workstation, against
the real municipal storm network rather than a synthesised one. It is worth
publishing for what it refused to claim as much as for what it produced.

## What was asked

> For Victoria, use the verified downtown demo AOI
> `[-123.370, 48.425, -123.360, 48.432]`, rainfall dates 2025-07-01 to
> 2025-07-07.

One request, in a chat window.

## What came back

The agent selected the SWMMCanada skill, fetched the network, ran EPA SWMM
5.2.4, and audited the result. Nine steps, no manual file handling.

| | |
|---|---|
| Network | Real municipal network, Victoria BC, via SWMMCanada |
| Model | 132 subcatchments, 528 conduits, 523 junctions, 28 outfalls |
| Storm subset | 307 storm conduits, 325 storm nodes, 11 tidal outfalls |
| Boundary | Predicted Victoria Harbour tide levels at the outfalls |
| Solver | SWMM 5.2.4, completed with no solver errors |
| Rainfall | 0.0 mm at Victoria Gonzales CS, 100% temporal coverage |

The combined `.inp` carries both the storm and sanitary networks, which is why
the totals exceed the storm subset.

## What it refused to claim

The week requested turned out to be dry. The engine reported:

```
Total Precipitation ......      0.000
Continuity Error (%) .....      0.000     <- runoff
Continuity Error (%) .....      6.111     <- flow routing
Flooding Loss ............      2.992 ML
```

A flooding volume with no rainfall, and a routing continuity error above the
threshold most practitioners would accept. The agent said so, unprompted:

> **Evidence boundary:** This is an uncalibrated first-pass model. The dry
> rainfall window cannot test rainfall-runoff capacity, and the 6.111% routing
> error plus dry-period flooding warrants hydraulic review before interpreting
> flood risk.
>
> **Next action:** Run a design storm and inspect the nodes responsible for
> dry-weather/tidal flooding.

It named the specific number that undermines its own output, explained what
that number rules out, and proposed the test that would settle it.

## Why that is the point

Assembling a model from open data is now the easy half. Any tool that
automates it will produce numbers, and numbers presented without their limits
are the failure mode that matters in engineering practice: a plausible figure
travels into a report, and nobody downstream can see what it rests on.

This run produced a peak, a flooding volume, and a continuity error, and then
told the reader which of those to distrust and why. That behaviour is designed
in, not incidental. A completed SWMM run is never described as calibrated or
validated unless observed data and the checks that go with it are present, and
every run folder repeats the boundary in writing:

> A completed run is not a calibrated or validated one. Numbers here are model
> outputs; treating them as predictions requires observed data and the checks
> that go with it.

## What the run left behind

```
013450_..._run/
├─ README.md              what this run was, and where to look
├─ final_report.md        the full account, including the boundary above
├─ 00_raw/                inputs as they arrived, plus the study-area map
├─ 05_builder/model.inp   the assembled model
├─ 06_runner/model.rpt    the engine's own report
├─ 09_audit/              diagnostics, provenance, the LLM call log
├─ 10_upstream/           what SWMMCanada returned, kept verbatim
└─ _agent/                the agent's record; nothing here is a result
```

The upstream response is kept byte for byte, and the audit records which
task id produced the network. The run is re-derivable rather than merely
described.

## On the machine

Windows 11 on ARM, an architecture where the geospatial Python stack has no
native wheels at all. That is a real constraint rather than a footnote, and it
is handled by the installer rather than by the user: `shapely` and `pyogrio`
have never shipped an ARM64 wheel, so the installer obtains an x64 interpreter
and runs the stack under emulation. The full chain above ran on that laptop.
