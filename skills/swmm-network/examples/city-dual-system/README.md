# City Dual-System Structured Network Benchmark

This example validates a practical city-network configuration path:

```text
structured pipe/surface asset tables
-> inferred nodes and dual-system metadata
-> network.json
-> network QA
-> SWMM INP build
```

It is intentionally not a CAD drawing recognizer. CAD or GIS data should first be exported to structured CSV/GeoJSON/GeoPackage layers with pipe endpoints, sizes, roughness, and outlet records.

The demo includes both `minor_pipe` and `major_surface` conduits. The adapter preserves these layers in `network.json` and QA summaries while the current SWMM builder exports them as standard one-dimensional SWMM conduit sections.
