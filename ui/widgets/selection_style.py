"""Shared checkbox indicators for forms, item views and menus."""

from utils.app_paths import resource_root


def checkbox_style() -> str:
    indicators = (
        "QCheckBox::indicator", "QGroupBox::indicator", "QListView::indicator",
        "QTableView::indicator", "QTreeView::indicator", "QMenu::indicator:non-exclusive",
    )
    assets = resource_root() / "assets"
    states = {
        "": "width:15px; height:15px; background:#1a2030; border:1px solid #707d97; border-radius:4px;",
        ":hover": "border-color:#b4a5ff;",
        ":checked": (
            "background:#393057; border-color:#b4a5ff;"
            f'image:url("{(assets / "checkbox-check.svg").as_posix()}");'
        ),
        ":indeterminate": (
            "background:#393057; border-color:#b4a5ff;"
            f'image:url("{(assets / "checkbox-mixed.svg").as_posix()}");'
        ),
        ":disabled": "background:#242938; border-color:#50596d;",
    }
    return "\n".join(
        f"{', '.join(selector + state for selector in indicators)} {{ {declarations} }}"
        for state, declarations in states.items()
    )
