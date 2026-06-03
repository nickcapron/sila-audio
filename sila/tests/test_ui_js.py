"""
Static-analysis tests for sila/ui/app.js.

These catch whole-class bugs (duplicate declarations, missing wiring) that
wouldn't surface in API tests but would silently break the UI.
"""

import re
from collections import Counter
from pathlib import Path

APP_JS = Path(__file__).parent.parent / "ui" / "app.js"


def _source():
    return APP_JS.read_text(encoding="utf-8")


def test_no_duplicate_top_level_function_declarations():
    """Two declarations of the same function name means the first is silently
    overwritten. This is what killed selectStep's panel-switch: a second
    definition without _inspectorSetMode replaced the correct one."""
    src = _source()
    names = re.findall(r"^function (\w+)\s*\(", src, re.MULTILINE)
    counts = Counter(names)
    duplicates = {name: count for name, count in counts.items() if count > 1}
    assert not duplicates, (
        f"Duplicate top-level function declarations found in app.js: {duplicates}. "
        "The later definition silently overwrites the earlier one."
    )


def test_select_step_calls_inspector_set_mode():
    """selectStep must call _inspectorSetMode to switch the right panel from
    the track inspector to the step inspector. Without this call the track
    panel stays visible and the step controls are unreachable."""
    src = _source()
    # Extract the body of selectStep (everything between its opening { and the
    # matching closing }).
    match = re.search(r"^function selectStep\s*\([^)]*\)\s*\{", src, re.MULTILINE)
    assert match, "selectStep not found in app.js"
    start = match.end()
    depth = 1
    pos = start
    while pos < len(src) and depth:
        if src[pos] == "{":
            depth += 1
        elif src[pos] == "}":
            depth -= 1
        pos += 1
    body = src[start : pos - 1]
    assert "_inspectorSetMode" in body, (
        "selectStep does not call _inspectorSetMode — the step inspector panel "
        "will never become visible when a step is clicked."
    )
