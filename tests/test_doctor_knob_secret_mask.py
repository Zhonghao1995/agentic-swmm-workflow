"""Security: `aiswmm doctor` must not print secret env-var values.

The "Runtime knobs" section rendered ``SET=<raw value>`` for every set var.
For ANTHROPIC_API_KEY (a secret) that leaks the user's API key in plaintext to
stdout. Non-secret config knobs (AISWMM_DISABLE_*, AISWMM_MEMORY_DIR) should
still show their value — it's useful to see what you exported.
"""

from __future__ import annotations

import unittest

from agentic_swmm.commands.doctor_extension import (
    OptOutFlagStatus,
    render_runtime_knobs_section,
)


def _render(name: str, value: str | None) -> str:
    return render_runtime_knobs_section(
        [OptOutFlagStatus(env_name=name, current_value=value, description="x")]
    )


class KnobSecretMaskTests(unittest.TestCase):
    def test_secret_value_is_masked(self) -> None:
        body = _render("ANTHROPIC_API_KEY", "sk-ant-supersecret-abc123")
        self.assertNotIn("sk-ant-supersecret-abc123", body)  # never leak the key
        self.assertIn("ANTHROPIC_API_KEY", body)
        self.assertIn("SET", body)  # still shows it is configured

    def test_secret_empty_value_reads_as_empty_not_set_value(self) -> None:
        body = _render("ANTHROPIC_API_KEY", "")
        self.assertNotIn("sk-", body)
        self.assertIn("empty", body.lower())

    def test_secret_unset_is_unset(self) -> None:
        self.assertIn("UNSET", _render("ANTHROPIC_API_KEY", None))

    def test_non_secret_value_is_still_shown(self) -> None:
        # Config flags are not secret; seeing the value is the point.
        self.assertIn("SET=1", _render("AISWMM_DISABLE_HONESTY_LAYER", "1"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
