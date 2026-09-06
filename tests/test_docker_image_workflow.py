"""The published image is checked in CI, not on the developer's laptop (2026-09-05).

Docker Desktop on the developer's Mac could not start its engine VM (S60,
three attempts), so the image smoke is a workflow: pull what the Docker
workflow pushed and drive it as a user would. The file is docker-image.yml, not
*smoke*.yml: the installer smoke-matrix lock (tests/test_install_smoke_matrix.bash)
treats every *smoke*.yml as an installer smoke that must run install.sh.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "docker-image.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(data: dict) -> dict:
    # PyYAML reads the bare key ``on`` as the boolean True (YAML 1.1).
    return data.get("on") or data.get(True)


def test_it_runs_after_the_docker_workflow_on_demand_and_weekly() -> None:
    triggers = _triggers(_workflow())
    assert triggers["workflow_run"]["workflows"] == ["Docker"]
    assert triggers["workflow_dispatch"]["inputs"]["image_tag"]["default"] == "latest"
    assert "schedule" in triggers


def test_it_drives_the_published_image_as_a_user_would() -> None:
    data = _workflow()
    job = data["jobs"]["smoke"]
    assert job["env"]["IMAGE_REPO"] == "ghcr.io/zhonghao1995/agentic-swmm-workflow"
    commands = " ".join(step.get("run", "") for step in job["steps"])
    assert "docker pull" in commands
    assert "python3 -m agentic_swmm.cli --version" in commands
    assert "swmm5 --version" in commands
    assert "python3 -m agentic_swmm.cli doctor" in commands
    assert commands.rstrip().endswith("acceptance")


def test_a_failed_docker_workflow_is_not_smoked() -> None:
    job = _workflow()["jobs"]["smoke"]
    assert "workflow_run.conclusion == 'success'" in job["if"]


def test_the_file_name_stays_out_of_the_installer_smoke_matrix() -> None:
    # CI went red on #547: docker-smoke.yml matched tests/test_install_smoke_matrix.bash's
    # *smoke*.yml glob and was required to run an installer entrypoint.
    assert "smoke" not in WORKFLOW.name
    assert WORKFLOW.exists()
