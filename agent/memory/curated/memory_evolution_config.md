---
# ME-2 (#62) bounded-forgetting knobs.
# half_life_days controls how fast confidence_score decays:
#   confidence = evidence_count * exp(-age_days / half_life_days)
# active_threshold / dormant_threshold partition the score axis:
#   score >= active_threshold        -> status = active
#   dormant_threshold <= score < active_threshold -> dormant
#   score < dormant_threshold        -> retired (moved to
#                                       memory/modeling-memory/lessons_archived.md)
half_life_days: 90
active_threshold: 1.0
dormant_threshold: 0.2
---

# Memory evolution config

These knobs govern how the agent's failure-pattern memory decays over
time. Edit the YAML front-matter above and run ``aiswmm memory
compact`` to apply a fresh decay pass with the new thresholds.

## Defaults

- ``half_life_days = 90`` — a pattern observed once decays to half
  confidence after 90 days, ``exp(-2) ≈ 0.135`` confidence after 180
  days, and slides into ``retired`` at ~234 days for a single-evidence
  pattern.
- ``active_threshold = 1.0`` — the same effective bar as "at least one
  recent observation worth of evidence".
- ``dormant_threshold = 0.2`` — anything below this is considered
  archive-worthy. A single-evidence pattern crosses it at roughly
  ``90 * ln(5) ≈ 145`` days old.

## Tuning hints

- Halving ``half_life_days`` to 45 doubles forget speed (useful for
  short, fast-iteration projects).
- Raising ``active_threshold`` to 2.0 enforces "two observations or one
  very recent observation" before a pattern leaves dormant; this
  shrinks the live RAG corpus to high-signal entries.
- ``dormant_threshold`` is the hardest knob to change retroactively —
  patterns retired under the old threshold are physically moved to
  ``lessons_archived.md`` and have to be revived by hand.
