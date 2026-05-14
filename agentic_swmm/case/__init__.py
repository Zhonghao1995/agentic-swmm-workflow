"""Package boundary for the ``case`` namespace (PRD-CASE-ID).

Every case-aware feature in aiswmm — gap-fill promotion, calibration
acceptance, modeling-memory clustering — funnels through the two
modules in this package:

* :mod:`agentic_swmm.case.case_id` — resolver + validator for the
  ``case_id`` slug. One canonical function, ``resolve_case_id(...)``,
  so all features share the same precedence rules and the same error
  type when a case is missing.
* :mod:`agentic_swmm.case.case_registry` — list / read / write
  ``cases/<id>/case_meta.yaml``. The registry is intentionally tiny:
  no migrations, no caching, just a readonly facade plus an explicit
  writer used by ``aiswmm case init``.

This PRD establishes the namespace only. Downstream PRDs
(PRD-GF-PROMOTE, future calibration-accept) populate ``cases/<id>/``
with feature-specific artefacts; they are forbidden from inventing
their own slug rules or directory layout.
"""
