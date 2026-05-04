# SWMM Modeling Memory Example

Run modeling-memory summarization after one or more run directories have been audited by `swmm-experiment-audit`.

```bash
python3 skills/swmm-modeling-memory/scripts/summarize_memory.py \
  --runs-dir runs \
  --out-dir memory/modeling-memory
```

Optional Obsidian export:

```bash
python3 skills/swmm-modeling-memory/scripts/summarize_memory.py \
  --runs-dir runs \
  --out-dir memory/modeling-memory \
  --obsidian-dir "/path/to/Obsidian/Agentic SWMM/05_Modeling_Memory"
```

The skill reads existing audit artifacts and writes downstream memory files. It does not run SWMM, modify existing skills, or accept skill changes automatically.
