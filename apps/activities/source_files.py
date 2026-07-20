"""Choose one current individual plan when a folder contains dated snapshots."""

from __future__ import annotations

import re
from pathlib import Path


PLAN_SNAPSHOT_RE = re.compile(
    r"^(?:(?P<day>\d{2})\.(?P<month>\d{2})\.(?P<year>\d{4})_)?"
    r"(?P<plan_name>ИПП_\d{4}-\d{4}_.+)\.xlsx$",
    re.IGNORECASE,
)


def current_individual_plan_paths(source_root: Path) -> list[Path]:
    """Return the newest dated copy of each individual plan.

    Departments sometimes keep several snapshots of the same plan in one
    folder, for example ``19.11.2025_ИПП_...`` and ``20.11.2025_ИПП_...``.
    They are revisions of one plan, not separate plans. A dated copy takes
    precedence over an undated copy; among dated copies the latest date wins.
    Files whose names do not follow this convention are retained unchanged.
    """

    source_root = Path(source_root)
    selected_paths: dict[str, tuple[tuple[int, int, int, int, str], Path]] = {}
    for path in sorted(source_root.rglob("*.xlsx")):
        relative_path = path.relative_to(source_root)
        match = PLAN_SNAPSHOT_RE.match(path.name)
        if match is None:
            key = relative_path.as_posix().casefold()
            priority = (0, 0, 0, 0, path.name.casefold())
        else:
            plan_name = match.group("plan_name").casefold()
            key = (relative_path.parent / plan_name).as_posix().casefold()
            if match.group("year"):
                priority = (
                    1,
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                    path.name.casefold(),
                )
            else:
                priority = (0, 0, 0, 0, path.name.casefold())

        existing = selected_paths.get(key)
        if existing is None or priority > existing[0]:
            selected_paths[key] = (priority, path)

    return sorted((path for _priority, path in selected_paths.values()), key=lambda path: path.as_posix())
