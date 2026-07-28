"""Proves FINDING 1 from the whole-branch review: dragging the assumed-life
slider must re-sort the Top Picks strip, not just the browse cards.

A Python test cannot execute `_JS` — it is a string embedded in the page,
never imported or run by anything in this process. The only way to prove
the slider actually reorders Top Picks is to run the real client-side code.

jsdom is not installed in this project (`npm ls jsdom` -> empty), so rather
than add a dependency this feeds the *literal* `_JS` string from
marketsearch.dashboard to Node, against a small hand-rolled DOM
(tests/js/dom_shim.js) that implements just the API surface `_JS` calls:
querySelector(All), dataset, classList, appendChild, addEventListener,
textContent/value/hidden. tests/js/assert_footer.js then mutates the life
input exactly the way a browser would after a drag and calls the script's
own `apply()` again — the very function the 'input' listener invokes.

This is real end-to-end coverage of the shipped code, not a reimplementation
of its logic under test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from marketsearch.dashboard import _JS, value_per_remaining_hour

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="Node is required to execute the dashboard's client-side _JS "
    "directly (see this file's module docstring for why a Python test "
    "cannot substitute).",
)

_JS_DIR = Path(__file__).parent / "js"

# Same three machines and hours/price figures as
# test_ranking_inverts_at_ten_thousand_hours in test_dashboard_ranking.py —
# the fixture proving the assumed-life figure inverts the ranking rather
# than merely adjusting it. Kept in one place (tests/js/dom_shim.js) so this
# file and that one describe the same numbers without silently drifting.
_MACHINES = {
    "svl95": (3_200_000, 2984),
    "svl90": (4_500_000, 1005),
    "t770": (3_950_000, 2200),
}


def _label(price_cents: int, hours: int, life_hours: int) -> str:
    value = value_per_remaining_hour(price_cents, hours, life_hours)
    assert value is not None
    return f"${value:,.2f}/hr"


def _run_apply_js() -> dict:
    combined = "\n".join([
        (_JS_DIR / "dom_shim.js").read_text(encoding="utf-8"),
        _JS,
        (_JS_DIR / "assert_footer.js").read_text(encoding="utf-8"),
    ])
    result = subprocess.run(
        [shutil.which("node"), "-"],
        input=combined, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"node exited {result.returncode}\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_slider_actually_reorders_top_picks():
    """Drag the slider from 6,000 to 10,000 hours: the three machines must
    rank in exactly the opposite order, proving `apply()` re-sorts
    `.picks` — not just the browse `.card`s, as it did before this fix.
    """
    data = _run_apply_js()

    assert data["order6000"] == ["svl90", "t770", "svl95"]
    assert data["order10000"] == ["svl95", "svl90", "t770"]


def test_the_relabelled_pick_values_match_the_python_formula():
    """Not just the order — the $/hr text on each pick must also update,
    using the exact formula value_per_remaining_hour uses in Python.
    """
    data = _run_apply_js()

    for machine_id, (price_cents, hours) in _MACHINES.items():
        assert data["values6000"][machine_id] == _label(price_cents, hours, 6000)
        assert data["values10000"][machine_id] == _label(price_cents, hours, 10000)
