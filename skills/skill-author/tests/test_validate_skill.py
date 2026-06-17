"""Contract tests for validate_skill.py — a well-formed skill passes, malformed ones fail."""
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "validate_skill.py"
_spec = importlib.util.spec_from_file_location("validate_skill", _SCRIPT)
validate_skill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_skill)


def _write_skill(parent, name="demo-skill", description=None, body="# Demo\n\nDoes a thing."):
    if description is None:
        description = "A demo skill that does a thing and triggers when the user asks for that thing."
    skill = parent / name
    skill.mkdir(parents=True, exist_ok=True)
    front = ["---", f"name: {name}"]
    if description is not False:
        front.append(f"description: {description}")
    front.append("---")
    (skill / "SKILL.md").write_text("\n".join(front) + "\n\n" + body, encoding="utf-8")
    return skill


def test_wellformed_skill_passes(tmp_path):
    skill = _write_skill(tmp_path)
    assert validate_skill.validate(skill) == []


def test_missing_skill_md_fails(tmp_path):
    empty = tmp_path / "empty-skill"
    empty.mkdir()
    problems = validate_skill.validate(empty)
    assert problems and "no SKILL.md" in problems[0]


def test_missing_description_fails(tmp_path):
    skill = _write_skill(tmp_path, description=False)  # omit the description line
    assert any("description" in p for p in validate_skill.validate(skill))


def test_name_must_match_folder(tmp_path):
    skill = _write_skill(tmp_path, name="demo-skill")
    renamed = skill.parent / "other-folder"
    skill.rename(renamed)
    assert any("match the folder name" in p for p in validate_skill.validate(renamed))


def test_real_skill_author_passes():
    # dogfood: the skill-author skill itself must be well-formed
    skill_author = _SCRIPT.resolve().parent.parent
    assert validate_skill.validate(skill_author) == []
