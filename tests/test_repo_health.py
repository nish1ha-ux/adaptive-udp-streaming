"""Repository health checks run by CI.

Deliberately light: they catch broken merges (conflict markers), modules that no longer
import, and a missing or mis-targeted workflow, without restricting how code is written.
"""

import importlib
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _tracked(*patterns):
    out = subprocess.run(["git", "ls-files", *patterns], cwd=ROOT, capture_output=True,
                         text=True, check=False).stdout
    return [ROOT / line for line in out.splitlines() if line]


def test_no_merge_conflict_markers_in_tracked_source():
    offenders = []
    for path in _tracked("*.py", "*.md", "*.js", "*.html", "*.css", "*.yml"):
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if line.startswith(("<<<<<<< ", ">>>>>>> ")) or line == "=======":
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, f"Unresolved conflict markers: {offenders}"


@pytest.mark.parametrize("module", sorted(p.stem for p in (ROOT / "src").glob("*.py") if p.stem != "__init__"))
def test_every_src_module_imports(module):
    importlib.import_module(f"src.{module}")


def test_requirements_list_pytest():
    assert "pytest" in (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()


def test_workflows_exist_and_target_development():
    for name in ("ci.yml", "auto-merge.yml", "promote.yml"):
        assert (WORKFLOWS / name).is_file(), f"missing workflow {name}"
    auto = (WORKFLOWS / "auto-merge.yml").read_text(encoding="utf-8")
    assert "branches: [development]" in auto and "gh pr merge" in auto


def test_nothing_auto_merges_into_main():
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert "gh pr merge" not in text or path.name == "auto-merge.yml"
