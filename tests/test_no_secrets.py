"""Run the CI secret scan locally.

The AGRARIAN CI template greps src/ for four literals and fails the build on any
match. CI should never be the first place we learn one slipped in — especially
since the obvious redaction regex would contain one of them.
"""

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"

# Exactly the four patterns from .github/workflows/ci.yml, split so this file
# does not itself contain them.
PATTERNS = [
    "pass" + "word=",
    "sec" + "ret=",
    "api" + "_key",
    "to" + "ken=",
]


@pytest.mark.parametrize("pattern", PATTERNS)
def test_src_has_no_secret_literals(pattern):
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern in line:
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"CI secret scan would fail on {pattern!r}: {offenders}"
