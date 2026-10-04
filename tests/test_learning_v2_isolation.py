"""Isolation invariant, repository-wide: no module under air/learning_v2
may import the allocator, the v1 learning engine, or the policy store,
nor define any activation/mutation entrypoint. The path Learner ->
Candidate -> Evaluation -> Assurance -> Promotion -> PolicyVersion ->
Allocator is one-directional; the last arrow is owned by allocator-side
code this package never touches."""

import re
from pathlib import Path

PKG = Path(__file__).parent.parent / "src" / "air" / "learning_v2"

FORBIDDEN_IMPORTS = (
    "air.allocation",
    "air.policy",
    "air.learning.engine",
    "air.learning.policies",
    "air.learning.bridge",
)

FORBIDDEN_TOKENS = (
    "def activate",
    "def apply_policy",
    "def set_active",
    "def mutate_allocator",
    ".current_version =",
)


def _sources():
    return [p for p in PKG.glob("*.py") if p.name != "__pycache__"]


def test_package_exists_and_is_self_contained():
    assert PKG.is_dir()
    assert len(_sources()) >= 7


def test_no_forbidden_imports():
    for src in _sources():
        text = src.read_text()
        for token in FORBIDDEN_IMPORTS:
            # Match real imports, not mentions in docstrings about the
            # invariant itself.
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")) \
                        and token in stripped:
                    raise AssertionError(
                        f"{src.name}: forbidden import {token!r}")


def test_no_activation_entrypoint():
    for src in _sources():
        text = src.read_text()
        for token in FORBIDDEN_TOKENS:
            assert token not in text, f"{src.name}: {token!r}"


def test_learner_never_names_the_active_policy():
    # The learner produces candidates and versions; it never resolves,
    # reads, or writes "the active policy".
    for src in _sources():
        text = src.read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') \
                    or stripped.startswith("'''"):
                continue
            if re.search(r"\bactive_policy\b", stripped):
                raise AssertionError(f"{src.name}: {stripped!r}")
