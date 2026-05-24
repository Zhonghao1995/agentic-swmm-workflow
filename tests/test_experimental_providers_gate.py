"""Unit tests for the ``claude_sdk`` env gate (issue #182).

The gate is the single predicate every user-facing surface consults
before exposing the ``claude_sdk`` provider. When the env var
``AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS`` is unset (or set to a non-
truthy value), ``claude_sdk`` must be invisible in argparse choices,
absent from the welcome banner, and silently downgraded to ``openai``
at runtime.

This module pins the helper API (`claude_sdk_enabled`,
`available_provider_choices`, `gate_notice_for_legacy_config`) so
that subsequent surface-level commits can lean on a stable contract.
"""
from __future__ import annotations

import pytest

from agentic_swmm.agent import experimental_providers


_ENV_VAR = "AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS"


class TestClaudeSdkEnabled:
    def test_unset_returns_false(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        assert experimental_providers.claude_sdk_enabled() is False

    @pytest.mark.parametrize(
        "value",
        ["1", "true", "TRUE", "True", "yes", "YES", "Yes", "on", "ON", "On"],
    )
    def test_truthy_values_return_true(self, monkeypatch, value):
        monkeypatch.setenv(_ENV_VAR, value)
        assert experimental_providers.claude_sdk_enabled() is True

    @pytest.mark.parametrize(
        "value",
        ["", "0", "false", "FALSE", "no", "NO", "off", "anything-else"],
    )
    def test_non_truthy_values_return_false(self, monkeypatch, value):
        monkeypatch.setenv(_ENV_VAR, value)
        assert experimental_providers.claude_sdk_enabled() is False


class TestAvailableProviderChoices:
    def test_gate_off_returns_openai_only(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        assert experimental_providers.available_provider_choices() == ["openai"]

    def test_gate_on_returns_both(self, monkeypatch):
        monkeypatch.setenv(_ENV_VAR, "1")
        assert experimental_providers.available_provider_choices() == [
            "openai",
            "claude_sdk",
        ]


class TestGateNoticeForLegacyConfig:
    def test_notice_mentions_env_var(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        notice = experimental_providers.gate_notice_for_legacy_config()
        assert "AISWMM_ENABLE_EXPERIMENTAL_PROVIDERS" in notice

    def test_notice_mentions_config_set_command(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        notice = experimental_providers.gate_notice_for_legacy_config()
        assert "aiswmm config set provider.default openai" in notice

    def test_notice_is_non_empty_string(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        notice = experimental_providers.gate_notice_for_legacy_config()
        assert isinstance(notice, str)
        assert notice.strip()


class TestIsTruthyHelper:
    """The truthy-string contract lives in ``feature_flags.is_truthy``
    and is consumed by every ``AISWMM_*`` boolean env var.

    Merged from former ``TestSharedTruthyHelper`` /
    ``TestPublicIsTruthyHelper`` (issue #200): both classes asserted
    overlapping concerns at slightly different layers. One class with
    subtests keeps the helper / consumer contract in one place.
    """

    # --- Helper is public on feature_flags --------------------------

    def test_feature_flags_exposes_public_is_truthy(self):
        from agentic_swmm.agent import feature_flags

        assert callable(getattr(feature_flags, "is_truthy", None))

    def test_public_helper_accepts_all_truthy_values(self):
        from agentic_swmm.agent import feature_flags

        for value in ("1", "true", "TRUE", "yes", "Yes", "on", "ON"):
            assert feature_flags.is_truthy(value) is True, value

    def test_public_helper_rejects_non_truthy_values(self):
        from agentic_swmm.agent import feature_flags

        for value in (None, "", "0", "false", "no", "off", "maybe", "  "):
            assert feature_flags.is_truthy(value) is False, value

    # --- Gate predicate consumes the shared helper ------------------

    def test_module_does_not_define_local_truthy_set(self):
        # Module attribute is the contract: no local ``_TRUTHY`` set
        # to drift out of sync with ``feature_flags``.
        assert not hasattr(experimental_providers, "_TRUTHY")

    def test_claude_sdk_enabled_delegates_to_feature_flags_helper(
        self, monkeypatch
    ):
        # Patch the shared helper at its definition site and observe
        # that ``claude_sdk_enabled`` routes through it. If the gate
        # ever reverts to a local lookup, the patched stub would be
        # bypassed and this test would fail.
        from agentic_swmm.agent import feature_flags

        calls: list[str | None] = []

        def _spy(value: str | None) -> bool:
            calls.append(value)
            return True

        monkeypatch.setattr(feature_flags, "is_truthy", _spy, raising=True)
        monkeypatch.setenv(_ENV_VAR, "anything-the-spy-returns-true")
        assert experimental_providers.claude_sdk_enabled() is True
        assert calls, "claude_sdk_enabled did not consult feature_flags.is_truthy"


class TestSupportedProvidersSingleSourceOfTruth:
    """Issue #191: provider names live in one tuple, derived elsewhere.

    ``agentic_swmm.providers.factory`` owns the canonical tuple of
    supported provider names. The gate helper must derive its choices
    from that tuple rather than hand-roll the same literal — otherwise
    a third provider lands in one place and the two lists drift.
    """

    def test_factory_exposes_public_supported_providers(self):
        from agentic_swmm.providers import factory

        names = getattr(factory, "SUPPORTED_PROVIDERS", None)
        assert names is not None, (
            "factory must expose a public SUPPORTED_PROVIDERS tuple"
        )
        assert isinstance(names, tuple)
        assert "openai" in names
        assert "claude_sdk" in names

    def test_supported_providers_in_factory_dunder_all(self):
        from agentic_swmm.providers import factory

        assert "SUPPORTED_PROVIDERS" in getattr(factory, "__all__", ())

    def test_available_choices_gate_on_match_supported_providers(
        self, monkeypatch
    ):
        from agentic_swmm.providers import factory

        monkeypatch.setenv(_ENV_VAR, "1")
        # The gate-ON choice list is the canonical tuple in list form.
        assert experimental_providers.available_provider_choices() == list(
            factory.SUPPORTED_PROVIDERS
        )

    def test_available_choices_gate_off_filters_from_supported_providers(
        self, monkeypatch
    ):
        from agentic_swmm.providers import factory

        monkeypatch.delenv(_ENV_VAR, raising=False)
        choices = experimental_providers.available_provider_choices()
        # Every gate-OFF choice must come from the canonical tuple —
        # the gate-OFF helper filters out claude_sdk; everything else
        # in the canonical tuple survives.
        for name in choices:
            assert name in factory.SUPPORTED_PROVIDERS
        assert "claude_sdk" not in choices

    def test_adding_a_provider_to_supported_propagates_to_gate_on_choices(
        self, monkeypatch
    ):
        """Mutating SUPPORTED_PROVIDERS at runtime exposes the new
        provider through the gate-ON helper without editing
        ``available_provider_choices``. This pins the single-source-
        of-truth contract: a third provider lands in factory only.
        """
        from agentic_swmm.providers import factory

        monkeypatch.setenv(_ENV_VAR, "1")
        monkeypatch.setattr(
            factory,
            "SUPPORTED_PROVIDERS",
            ("openai", "claude_sdk", "future_provider"),
            raising=True,
        )
        choices = experimental_providers.available_provider_choices()
        assert "future_provider" in choices


class TestProviderHelpText:
    """Issue #191: gate-aware ``--provider`` help text helper.

    Today ``setup.py`` and ``agent.py`` build dynamic help text that
    names ``claude_sdk`` when the gate is ON; ``chat.py`` and
    ``model.py`` use static text that never names ``claude_sdk`` even
    when the gate is ON. The helper unifies the pattern so a single
    edit propagates to all four commands.
    """

    def test_helper_exists(self):
        assert callable(getattr(experimental_providers, "provider_help_text", None))

    def test_gate_off_returns_base_unchanged(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)
        base = "Default provider."
        assert experimental_providers.provider_help_text(base) == base

    def test_gate_on_appends_claude_sdk_hint(self, monkeypatch):
        monkeypatch.setenv(_ENV_VAR, "1")
        base = "Default provider."
        out = experimental_providers.provider_help_text(base)
        assert out.startswith(base)
        assert "claude_sdk" in out
        assert "Claude Pro/Max" in out

    def test_gate_on_keeps_caller_base_text_verbatim(self, monkeypatch):
        # The helper appends, never rewrites — each command keeps its
        # own role-specific base sentence (provider for chat, planner-
        # only for agent, etc.).
        monkeypatch.setenv(_ENV_VAR, "1")
        base = "Provider to use with --planner openai. Defaults to config provider.default."
        out = experimental_providers.provider_help_text(base)
        assert base in out


class TestProviderHelpAcrossCommands:
    """Issue #191: every --provider flag must render the dynamic hint
    when the gate is ON. Today chat/model render only the static base.

    We assert against the ``help`` string on the argparse action
    itself, not the rendered ``--help`` output, because argparse
    auto-prints the choice set (``{openai,claude_sdk}``) regardless of
    the help text — and we want the *help string* to carry the WHY.
    """

    @pytest.fixture(autouse=True)
    def _gate_on(self, monkeypatch):
        monkeypatch.setenv(_ENV_VAR, "1")

    @pytest.mark.parametrize(
        "command_name",
        ["setup", "chat", "model", "agent"],
    )
    def test_each_command_help_string_mentions_claude_sdk_when_gate_on(
        self, command_name
    ):
        import argparse
        from agentic_swmm.commands import agent as agent_cmd
        from agentic_swmm.commands import chat as chat_cmd
        from agentic_swmm.commands import model as model_cmd
        from agentic_swmm.commands import setup as setup_cmd

        modules = {
            "setup": setup_cmd,
            "chat": chat_cmd,
            "model": model_cmd,
            "agent": agent_cmd,
        }
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        modules[command_name].register(sub)
        # Walk the subparser to find the --provider action.
        subparser = sub.choices[command_name]
        provider_action = next(
            a for a in subparser._actions if "--provider" in a.option_strings
        )
        assert "claude_sdk" in (provider_action.help or ""), (
            f"{command_name} --provider help string did not mention "
            "claude_sdk when gate ON"
        )


class TestProviderHelpAcrossCommandsGateOff:
    """Issue #200: gate-OFF symmetric coverage for the same four commands.

    With the env gate UNSET, every command's ``--provider`` help string
    must reduce to the role-specific base sentence — no ``claude_sdk``
    leakage, no ``Claude Pro/Max`` mention. This pins the omission
    contract so a regression to a static help string that always names
    claude_sdk would be caught here, symmetric to ``TestProviderHelp
    AcrossCommands`` for the gate-ON path.
    """

    @pytest.fixture(autouse=True)
    def _gate_off(self, monkeypatch):
        monkeypatch.delenv(_ENV_VAR, raising=False)

    @pytest.mark.parametrize(
        "command_name",
        ["setup", "chat", "model", "agent"],
    )
    def test_each_command_help_string_omits_claude_sdk_when_gate_off(
        self, command_name
    ):
        import argparse
        from agentic_swmm.commands import agent as agent_cmd
        from agentic_swmm.commands import chat as chat_cmd
        from agentic_swmm.commands import model as model_cmd
        from agentic_swmm.commands import setup as setup_cmd

        modules = {
            "setup": setup_cmd,
            "chat": chat_cmd,
            "model": model_cmd,
            "agent": agent_cmd,
        }
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        modules[command_name].register(sub)
        subparser = sub.choices[command_name]
        provider_action = next(
            a for a in subparser._actions if "--provider" in a.option_strings
        )
        help_text = provider_action.help or ""
        assert "claude_sdk" not in help_text, (
            f"{command_name} --provider help string leaked claude_sdk "
            "when gate OFF"
        )
        assert "Claude Pro/Max" not in help_text, (
            f"{command_name} --provider help string leaked Claude Pro/Max "
            "hint when gate OFF"
        )


class TestNoIssue182CommentsAtArgparseSites:
    """Issue #191: ``# Issue #182:`` narrative comments at argparse
    sites in commands/*.py were just restating the helper name. Drop
    them so the source reads cleanly. The substantive comments in
    ``provider_preflight.py`` (real non-obvious WHY) stay put.
    """

    @pytest.mark.parametrize(
        "module_path",
        [
            "agentic_swmm/commands/setup.py",
            "agentic_swmm/commands/chat.py",
            "agentic_swmm/commands/model.py",
            "agentic_swmm/commands/agent.py",
        ],
    )
    def test_command_module_has_no_issue_182_comment(self, module_path):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        text = (repo_root / module_path).read_text(encoding="utf-8")
        assert "# Issue #182" not in text, (
            f"{module_path}: drop the narrative # Issue #182 comment "
            "(refer to issue tracker, not inline)"
        )
