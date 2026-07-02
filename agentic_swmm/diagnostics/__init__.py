"""Diagnostics: the ``aiswmm doctor`` data layer.

``doctor_report`` collects and renders the health report (pure — no
IO beyond reading the stores it inspects); ``fixes`` owns the ``--fix``
remediation actions (interactive prompt + subprocess, dependency-
injected for tests). The ``commands/doctor.py`` verb is the thin
dispatcher that prints what these modules return.
"""
