from qgis.PyQt.QtCore import Qt


def _qt(name, enum_name):
    value = getattr(Qt, name, None)
    if value is not None:
        return value
    enum = getattr(Qt, enum_name)
    return getattr(enum, name)


def enum_value(owner, name, enum_name):
    value = getattr(owner, name, None)
    if value is not None:
        return value
    enum = getattr(owner, enum_name)
    return getattr(enum, name)


def enum_int(value):
    """Convert a PyQt5 int-like enum or a PyQt6 Python enum to int."""
    value = getattr(value, "value", value)
    try:
        return int(value)
    except Exception:
        return 0


CHECKED = _qt("Checked", "CheckState")
PARTIALLY_CHECKED = _qt("PartiallyChecked", "CheckState")
UNCHECKED = _qt("Unchecked", "CheckState")

USER_ROLE = _qt("UserRole", "ItemDataRole")
DISPLAY_ROLE = _qt("DisplayRole", "ItemDataRole")
TOOLTIP_ROLE = _qt("ToolTipRole", "ItemDataRole")

ITEM_IS_ENABLED = _qt("ItemIsEnabled", "ItemFlag")
ITEM_IS_SELECTABLE = _qt("ItemIsSelectable", "ItemFlag")
ITEM_IS_USER_CHECKABLE = _qt("ItemIsUserCheckable", "ItemFlag")
ITEM_IS_AUTO_TRISTATE = getattr(
    getattr(Qt, "ItemFlag", Qt),
    "ItemIsAutoTristate",
    getattr(Qt, "ItemIsTristate", 0),
)

HORIZONTAL = _qt("Horizontal", "Orientation")
VERTICAL = _qt("Vertical", "Orientation")
WINDOW_MODAL = _qt("WindowModal", "WindowModality")

ASCENDING_ORDER = _qt("AscendingOrder", "SortOrder")
DESCENDING_ORDER = _qt("DescendingOrder", "SortOrder")

CUSTOM_CONTEXT_MENU = _qt("CustomContextMenu", "ContextMenuPolicy")
TOOL_BUTTON_TEXT_BESIDE_ICON = _qt("ToolButtonTextBesideIcon", "ToolButtonStyle")

WIDGET_SHORTCUT = _qt("WidgetShortcut", "ShortcutContext")
WIDGET_WITH_CHILDREN_SHORTCUT = _qt("WidgetWithChildrenShortcut", "ShortcutContext")
WINDOW_MAXIMIZE_HINT = _qt("WindowMaximizeButtonHint", "WindowType")
WINDOW_CONTEXT_HELP_HINT = _qt("WindowContextHelpButtonHint", "WindowType")
WINDOW_MAXIMIZED = _qt("WindowMaximized", "WindowState")
WINDOW_MINIMIZED = _qt("WindowMinimized", "WindowState")
WINDOW_FULLSCREEN = _qt("WindowFullScreen", "WindowState")
WAIT_CURSOR = _qt("WaitCursor", "CursorShape")
ALIGN_CENTER = _qt("AlignCenter", "AlignmentFlag")
ALIGN_RIGHT_VCENTER = _qt("AlignRight", "AlignmentFlag") | _qt("AlignVCenter", "AlignmentFlag")
RICH_TEXT = _qt("RichText", "TextFormat")


def item_flags(*flags):
    clean = [flag for flag in flags if flag]
    if not clean:
        return 0
    result = clean[0]
    for flag in clean[1:]:
        result = result | flag
    return result
