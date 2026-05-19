"""Tests for ``estimate_resources`` + CLI gating (PRD-06 Phase B §8)."""

from __future__ import annotations

import importlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.swmm_runtime.uncertainty_plan import (
    ResourceEstimate,
    UncertaintyPlan,
    estimate_resources,
    format_estimate_block,
)


def _has_salib() -> bool:
    return importlib.util.find_spec("SALib") is not None


def _fake_plan(n_runs: int, names: list[str] | None = None) -> UncertaintyPlan:
    names = names or [f"p{i}" for i in range(3)]
    samples = [
        {name: 0.5 for name in names} for _ in range(n_runs)
    ]
    return UncertaintyPlan(
        samples=samples,
        method="morris",
        n_samples_requested=max(1, n_runs // 4 or 1),
        n_samples_actual=len(samples),
        parameter_names=names,
        provenance={"salib_version": "test", "method": "morris"},
    )


class BaseRunSecondsPrecedenceTests(unittest.TestCase):
    def test_user_supplied_wins_over_history(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            store.write_text(
                json.dumps(
                    {
                        "case_name": "case-a",
                        "wall_time_s": 60.0,
                        "qa_metrics": {},
                        "performance_metrics": {},
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "case_name": "case-a",
                        "wall_time_s": 80.0,
                        "qa_metrics": {},
                        "performance_metrics": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            plan = _fake_plan(n_runs=10)
            est = estimate_resources(
                plan,
                base_run_seconds=15.0,
                parametric_store=store,
                case_name="case-a",
            )
        self.assertEqual(est.base_run_seconds_source, "user_supplied")
        self.assertAlmostEqual(est.base_run_seconds_estimated, 15.0, places=5)

    def test_history_wins_over_default(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            store.write_text(
                json.dumps({"case_name": "case-a", "wall_time_s": 40.0}) + "\n",
                encoding="utf-8",
            )
            plan = _fake_plan(n_runs=20)
            est = estimate_resources(
                plan,
                parametric_store=store,
                case_name="case-a",
            )
        self.assertEqual(est.base_run_seconds_source, "parametric_memory_median")
        self.assertAlmostEqual(est.base_run_seconds_estimated, 40.0, places=5)

    def test_default_when_nothing_else_supplied(self) -> None:
        plan = _fake_plan(n_runs=5)
        est = estimate_resources(plan)
        self.assertEqual(est.base_run_seconds_source, "conservative_default")
        self.assertGreater(est.base_run_seconds_estimated, 0.0)

    def test_history_median_with_three_values(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            lines = "\n".join(
                json.dumps({"case_name": "c1", "wall_time_s": v})
                for v in (10.0, 50.0, 30.0)
            )
            store.write_text(lines + "\n", encoding="utf-8")
            plan = _fake_plan(n_runs=4)
            est = estimate_resources(plan, parametric_store=store, case_name="c1")
        self.assertAlmostEqual(est.base_run_seconds_estimated, 30.0, places=5)

    def test_negative_base_run_seconds_rejected(self) -> None:
        plan = _fake_plan(n_runs=1)
        with self.assertRaises(ValueError):
            estimate_resources(plan, base_run_seconds=-1.0)


class WallClockAndDiskTests(unittest.TestCase):
    def test_wall_clock_is_n_runs_times_per_run(self) -> None:
        plan = _fake_plan(n_runs=20)
        est = estimate_resources(plan, base_run_seconds=10.0)
        self.assertAlmostEqual(est.wall_clock_seconds_estimated, 200.0, places=5)
        self.assertEqual(est.n_runs_estimated, 20)

    def test_disk_bytes_scales_with_runs(self) -> None:
        plan = _fake_plan(n_runs=100)
        est = estimate_resources(plan, base_run_seconds=1.0)
        # Each run contributes ~800 KB (250+500+50). 100 runs = 80,000 KB.
        expected = 100 * (250 + 500 + 50) * 1024
        self.assertEqual(est.disk_bytes_estimated, expected)
        # And disk_bytes scales linearly: doubling runs doubles disk.
        plan2 = _fake_plan(n_runs=200)
        est2 = estimate_resources(plan2, base_run_seconds=1.0)
        self.assertEqual(est2.disk_bytes_estimated, 2 * expected)

    def test_assumptions_list_nonempty(self) -> None:
        plan = _fake_plan(n_runs=3)
        est = estimate_resources(plan)
        self.assertTrue(est.assumptions)
        self.assertGreaterEqual(len(est.assumptions), 3)


class LlmTokenTests(unittest.TestCase):
    def test_llm_in_loop_off_yields_zero_tokens(self) -> None:
        plan = _fake_plan(n_runs=8)
        est = estimate_resources(plan, llm_in_loop=False, avg_llm_tokens_per_run=2000)
        self.assertEqual(est.llm_tokens_estimated, 0)

    def test_llm_in_loop_on_multiplies_tokens(self) -> None:
        plan = _fake_plan(n_runs=8)
        est = estimate_resources(
            plan, llm_in_loop=True, avg_llm_tokens_per_run=2000
        )
        self.assertEqual(est.llm_tokens_estimated, 16000)

    def test_negative_avg_tokens_rejected(self) -> None:
        plan = _fake_plan(n_runs=1)
        with self.assertRaises(ValueError):
            estimate_resources(plan, llm_in_loop=True, avg_llm_tokens_per_run=-5)


class MorrisAndSobolCountsTests(unittest.TestCase):
    """When SALib is present, the n_runs estimate matches the plan size."""

    @unittest.skipUnless(_has_salib(), "SALib not installed")
    def test_morris_n50_k5(self) -> None:
        from agentic_swmm.agent.swmm_runtime.uncertainty_plan import (
            plan_uncertainty_run,
        )

        with TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text("[TITLE]\n", encoding="utf-8")
            plan = plan_uncertainty_run(
                inp,
                {f"p{i}": (0.0, 1.0) for i in range(5)},
                method="morris",
                n_samples=50,
            )
        est = estimate_resources(plan, base_run_seconds=10.0)
        # Morris: trajectories * (k+1) = 50 * 6 = 300 evaluations.
        self.assertEqual(est.n_runs_estimated, plan.n_samples_actual)
        self.assertEqual(est.n_runs_estimated, 50 * (5 + 1))

    @unittest.skipUnless(_has_salib(), "SALib not installed")
    def test_sobol_n128_k8(self) -> None:
        from agentic_swmm.agent.swmm_runtime.uncertainty_plan import (
            plan_uncertainty_run,
        )

        with TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text("[TITLE]\n", encoding="utf-8")
            plan = plan_uncertainty_run(
                inp,
                {f"p{i}": (0.0, 1.0) for i in range(8)},
                method="sobol",
                n_samples=128,
            )
        est = estimate_resources(plan, base_run_seconds=5.0)
        # Sobol' with second-order: N * (2k + 2) = 128 * 18 = 2304.
        self.assertEqual(est.n_runs_estimated, plan.n_samples_actual)


class FormatEstimateBlockTests(unittest.TestCase):
    def test_contains_all_lines(self) -> None:
        plan = _fake_plan(n_runs=4)
        est = estimate_resources(plan, base_run_seconds=20.0)
        block = format_estimate_block(est)
        self.assertIn("Resource estimate", block)
        self.assertIn("n_runs", block)
        self.assertIn("wall_clock_seconds", block)
        self.assertIn("disk_bytes", block)
        self.assertIn("llm_tokens", block)
        self.assertIn("Assumptions:", block)


class CliGatingTests(unittest.TestCase):
    @unittest.skipUnless(_has_salib(), "SALib not installed")
    def test_no_estimate_flag_skips_estimate(self) -> None:
        from agentic_swmm.cli import main as cli_main

        with TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text("[TITLE]\n", encoding="utf-8")
            out = Path(tmp) / "plan.json"
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                rc = cli_main(
                    [
                        "uncertainty",
                        "plan",
                        "--base-inp",
                        str(inp),
                        "--param",
                        "manning_n=0.01,0.03",
                        "--method",
                        "morris",
                        "--n-samples",
                        "3",
                        "--out",
                        str(out),
                        "--no-estimate",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertNotIn("Resource estimate", buf.getvalue())
            self.assertTrue(out.exists())

    @unittest.skipUnless(_has_salib(), "SALib not installed")
    def test_abort_on_estimate_exits_without_writing(self) -> None:
        from agentic_swmm.cli import main as cli_main

        with TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text("[TITLE]\n", encoding="utf-8")
            out = Path(tmp) / "plan.json"
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                rc = cli_main(
                    [
                        "uncertainty",
                        "plan",
                        "--base-inp",
                        str(inp),
                        "--param",
                        "manning_n=0.01,0.03",
                        "--method",
                        "morris",
                        "--n-samples",
                        "3",
                        "--out",
                        str(out),
                        "--abort-on-estimate",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertIn("Resource estimate", buf.getvalue())
            self.assertFalse(out.exists())

    @unittest.skipUnless(_has_salib(), "SALib not installed")
    def test_tty_prompts_and_y_proceeds(self) -> None:
        from agentic_swmm.cli import main as cli_main

        with TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text("[TITLE]\n", encoding="utf-8")
            out = Path(tmp) / "plan.json"
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                with mock.patch(
                    "agentic_swmm.commands.uncertainty._stdin_is_tty",
                    return_value=True,
                ):
                    with mock.patch(
                        "agentic_swmm.commands.uncertainty._prompt_proceed",
                        return_value=True,
                    ):
                        rc = cli_main(
                            [
                                "uncertainty",
                                "plan",
                                "--base-inp",
                                str(inp),
                                "--param",
                                "manning_n=0.01,0.03",
                                "--method",
                                "morris",
                                "--n-samples",
                                "3",
                                "--out",
                                str(out),
                            ]
                        )
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists())

    @unittest.skipUnless(_has_salib(), "SALib not installed")
    def test_tty_prompts_and_n_aborts(self) -> None:
        from agentic_swmm.cli import main as cli_main

        with TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text("[TITLE]\n", encoding="utf-8")
            out = Path(tmp) / "plan.json"
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                with mock.patch(
                    "agentic_swmm.commands.uncertainty._stdin_is_tty",
                    return_value=True,
                ):
                    with mock.patch(
                        "agentic_swmm.commands.uncertainty._prompt_proceed",
                        return_value=False,
                    ):
                        rc = cli_main(
                            [
                                "uncertainty",
                                "plan",
                                "--base-inp",
                                str(inp),
                                "--param",
                                "manning_n=0.01,0.03",
                                "--method",
                                "morris",
                                "--n-samples",
                                "3",
                                "--out",
                                str(out),
                            ]
                        )
            self.assertEqual(rc, 0)
            self.assertIn("aborted", buf.getvalue())
            self.assertFalse(out.exists())

    @unittest.skipUnless(_has_salib(), "SALib not installed")
    def test_yes_flag_skips_prompt(self) -> None:
        from agentic_swmm.cli import main as cli_main

        with TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text("[TITLE]\n", encoding="utf-8")
            out = Path(tmp) / "plan.json"
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                with mock.patch(
                    "agentic_swmm.commands.uncertainty._stdin_is_tty",
                    return_value=True,
                ):
                    with mock.patch(
                        "agentic_swmm.commands.uncertainty._prompt_proceed"
                    ) as prompt_mock:
                        rc = cli_main(
                            [
                                "uncertainty",
                                "plan",
                                "--base-inp",
                                str(inp),
                                "--param",
                                "manning_n=0.01,0.03",
                                "--method",
                                "morris",
                                "--n-samples",
                                "3",
                                "--out",
                                str(out),
                                "--yes",
                            ]
                        )
                        prompt_mock.assert_not_called()
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists())

    @unittest.skipUnless(_has_salib(), "SALib not installed")
    def test_llm_in_loop_flag_yields_token_estimate(self) -> None:
        from agentic_swmm.cli import main as cli_main

        with TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text("[TITLE]\n", encoding="utf-8")
            out = Path(tmp) / "plan.json"
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                with mock.patch(
                    "agentic_swmm.commands.uncertainty._stdin_is_tty",
                    return_value=False,
                ):
                    rc = cli_main(
                        [
                            "uncertainty",
                            "plan",
                            "--base-inp",
                            str(inp),
                            "--param",
                            "manning_n=0.01,0.03",
                            "--method",
                            "morris",
                            "--n-samples",
                            "3",
                            "--out",
                            str(out),
                            "--llm-in-loop",
                            "--avg-llm-tokens-per-run",
                            "2000",
                            "--yes",
                        ]
                    )
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists())
            text = out.read_text(encoding="utf-8")
            payload = json.loads(text)
            self.assertIn("resource_estimate", payload)
            self.assertGreater(
                payload["resource_estimate"]["llm_tokens_estimated"], 0
            )


if __name__ == "__main__":
    unittest.main()
