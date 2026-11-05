from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from qgis.core import QgsApplication, QgsLayerTreeGroup, QgsLayerTreeLayer, QgsMapThemeCollection, QgsProject

from .progress import LazyProgress
from .qt_compat import (
    CHECKED,
    ITEM_IS_AUTO_TRISTATE,
    ITEM_IS_ENABLED,
    ITEM_IS_SELECTABLE,
    ITEM_IS_USER_CHECKABLE,
    UNCHECKED,
    USER_ROLE,
    enum_value,
    item_flags,
)
from .theme_utils import (
    clone_layer_record,
    current_visible_layer_ids,
    layer_tree_index,
    record_by_layer_id,
    record_checked_groups,
    theme_visible_layer_ids,
)
from .window_state import WindowStateKeeper

try:
    from qgis.core import QgsIconUtils
except ImportError:  # QGIS < 3.20
    QgsIconUtils = None


ROLE_TYPE = USER_ROLE
ROLE_LAYER_ID = USER_ROLE + 1
ROLE_GROUP_PATH = USER_ROLE + 2

_LAYER_FLAGS = item_flags(ITEM_IS_ENABLED, ITEM_IS_SELECTABLE, ITEM_IS_USER_CHECKABLE)
_GROUP_FLAGS = item_flags(ITEM_IS_ENABLED, ITEM_IS_SELECTABLE, ITEM_IS_USER_CHECKABLE, ITEM_IS_AUTO_TRISTATE)


def _theme_icon(name):
    try:
        return QgsApplication.getThemeIcon(name)
    except Exception:
        return None


class ThemeEditDialog(QDialog):
    def __init__(self, iface, theme_name, record, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.theme_name = theme_name
        self.original_record = record
        self.project = QgsProject.instance()
        self.layer_items = []
        self.group_items = []
        self._group_icon = _theme_icon("/mActionFolder.svg")
        self._layer_icon = _theme_icon("/mIconLayer.png")

        self.setWindowTitle("Edit Theme - {0}".format(theme_name))
        self.setMinimumSize(380, 360)
        self.setSizeGripEnabled(True)

        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.setInterval(60)
        self._count_timer.timeout.connect(self._update_count)

        self._build_ui()
        self.window_state = WindowStateKeeper(self, "LayerThemeManager/edit_window", (520, 640))
        self._populate_from_theme()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        title = QLabel("<b>{0}</b> &mdash; tick the layers and groups that should be visible.".format(
            self.theme_name.replace("&", "&amp;").replace("<", "&lt;")), self)
        title.setWordWrap(True)
        layout.addWidget(title)

        search_row = QHBoxLayout()
        self.filter_edit = QLineEdit(self)
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setPlaceholderText("Filter layers and groups...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.filter_edit, 1)
        self.expand_button = QToolButton(self)
        self.expand_button.setIcon(_theme_icon("/mActionExpandTree.svg"))
        self.expand_button.setToolTip("Expand all")
        self.expand_button.clicked.connect(lambda: self.tree.expandAll())
        self.collapse_button = QToolButton(self)
        self.collapse_button.setIcon(_theme_icon("/mActionCollapseTree.svg"))
        self.collapse_button.setToolTip("Collapse all")
        self.collapse_button.clicked.connect(lambda: self.tree.collapseAll())
        self.check_all_button = QToolButton(self)
        self.check_all_button.setIcon(_theme_icon("/mActionShowAllLayers.svg"))
        self.check_all_button.setToolTip("Check all layers")
        self.check_all_button.clicked.connect(lambda: self._set_all(CHECKED))
        self.uncheck_all_button = QToolButton(self)
        self.uncheck_all_button.setIcon(_theme_icon("/mActionHideAllLayers.svg"))
        self.uncheck_all_button.setToolTip("Uncheck all layers")
        self.uncheck_all_button.clicked.connect(lambda: self._set_all(UNCHECKED))
        for button in (self.expand_button, self.collapse_button, self.check_all_button, self.uncheck_all_button):
            button.setAutoRaise(True)
            search_row.addWidget(button)
        layout.addLayout(search_row)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(enum_value(QAbstractItemView, "ExtendedSelection", "SelectionMode"))
        self.tree.itemChanged.connect(lambda *a: self._count_timer.start())
        layout.addWidget(self.tree, 1)

        tools = QHBoxLayout()
        self.current_button = QPushButton("Use Current Map", self)
        self.current_button.setToolTip("Set the check boxes from the current QGIS layer tree visibility.")
        self.current_button.clicked.connect(self.apply_current_visibility)
        self.reset_button = QPushButton("Reset", self)
        self.reset_button.setToolTip("Discard changes made in this dialog.")
        self.reset_button.clicked.connect(self._populate_from_theme)
        for button in (self.current_button, self.reset_button):
            button.setAutoDefault(False)
        tools.addWidget(self.current_button)
        tools.addWidget(self.reset_button)
        tools.addStretch(1)
        self.count_label = QLabel(self)
        tools.addWidget(self.count_label)
        layout.addLayout(tools)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        ok_button = enum_value(QDialogButtonBox, "Ok", "StandardButton")
        cancel_button = enum_value(QDialogButtonBox, "Cancel", "StandardButton")
        buttons = QDialogButtonBox(ok_button | cancel_button, self)
        buttons.button(ok_button).setText("Save Theme")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)

    # ------------------------------------------------------------------ populate
    def _populate_from_theme(self):
        index = layer_tree_index()
        visible = theme_visible_layer_ids(self.original_record, index)
        self._populate_tree(visible, record_checked_groups(self.original_record))

    def apply_current_visibility(self):
        root = self.project.layerTreeRoot()
        checked_groups = set()
        self._collect_checked_groups(root, "", checked_groups)
        self._populate_tree(current_visible_layer_ids(root), checked_groups)

    def _collect_checked_groups(self, node, path, result):
        for child in node.children():
            if isinstance(child, QgsLayerTreeGroup):
                child_path = child.name() if not path else "{0}/{1}".format(path, child.name())
                if child.itemVisibilityChecked():
                    result.add(child_path)
                self._collect_checked_groups(child, child_path, result)

    def _populate_tree(self, checked_layer_ids, checked_groups=None):
        with LazyProgress(self, "Edit Theme", "Building layer tree...") as progress:
            self.tree.setUpdatesEnabled(False)
            self.tree.blockSignals(True)
            try:
                self.tree.clear()
                self.layer_items = []
                self.group_items = []
                root = self.project.layerTreeRoot()
                top_items = []
                for child in root.children():
                    item = self._make_item(child, "", checked_layer_ids, checked_groups, progress)
                    if item is not None:
                        top_items.append(item)
                self.tree.addTopLevelItems(top_items)
                self.tree.expandAll()
                self._apply_filter()
            finally:
                self.tree.blockSignals(False)
                self.tree.setUpdatesEnabled(True)
        self._update_count()

    def _layer_icon_for(self, layer):
        if layer is not None and QgsIconUtils is not None:
            try:
                return QgsIconUtils.iconForLayer(layer)
            except Exception:
                pass
        return self._layer_icon

    def _make_item(self, node, parent_path, checked_layer_ids, checked_groups, progress):
        progress.step(len(self.layer_items) + len(self.group_items), "Building layer tree...")
        if isinstance(node, QgsLayerTreeLayer):
            layer = node.layer()
            layer_id = node.layerId()
            name = layer.name() if layer is not None else node.name()
            item = QTreeWidgetItem([name])
            item.setData(0, ROLE_TYPE, "layer")
            item.setData(0, ROLE_LAYER_ID, layer_id)
            item.setFlags(_LAYER_FLAGS)
            icon = self._layer_icon_for(layer)
            if icon is not None:
                item.setIcon(0, icon)
            if layer is None:
                item.setToolTip(0, "Layer is not available")
            item.setCheckState(0, CHECKED if layer_id in checked_layer_ids else UNCHECKED)
            self.layer_items.append(item)
            return item

        if isinstance(node, QgsLayerTreeGroup):
            group_path = node.name() if not parent_path else "{0}/{1}".format(parent_path, node.name())
            item = QTreeWidgetItem([node.name()])
            item.setData(0, ROLE_TYPE, "group")
            item.setData(0, ROLE_GROUP_PATH, group_path)
            if self._group_icon is not None:
                item.setIcon(0, self._group_icon)
            children = []
            for child in node.children():
                child_item = self._make_item(child, group_path, checked_layer_ids, checked_groups, progress)
                if child_item is not None:
                    children.append(child_item)
            if children:
                item.addChildren(children)
                item.setFlags(_GROUP_FLAGS)  # tristate state is derived from the children
            else:
                item.setFlags(_LAYER_FLAGS)
                checked = checked_groups is None or group_path in checked_groups
                item.setCheckState(0, CHECKED if checked else UNCHECKED)
            self.group_items.append(item)
            return item

        return None

    # ------------------------------------------------------------------ helpers
    def _set_all(self, state):
        self.tree.setUpdatesEnabled(False)
        try:
            for index in range(self.tree.topLevelItemCount()):
                self.tree.topLevelItem(index).setCheckState(0, state)
        finally:
            self.tree.setUpdatesEnabled(True)
        self._update_count()

    def _apply_filter(self):
        text = self.filter_edit.text().strip().lower()

        def visit(item):
            match = not text or text in item.text(0).lower()
            child_match = False
            for index in range(item.childCount()):
                if visit(item.child(index)):
                    child_match = True
            show = match or child_match
            item.setHidden(not show)
            if text and child_match:
                item.setExpanded(True)
            return show

        self.tree.setUpdatesEnabled(False)
        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))
        self.tree.setUpdatesEnabled(True)

    def _update_count(self):
        total = len(self.layer_items)
        visible = sum(1 for item in self.layer_items if self._item_effectively_checked(item))
        self.count_label.setText("{0} / {1} visible".format(visible, total))
        self.count_label.setToolTip("{0} of {1} layer(s) will be visible".format(visible, total))

    def _item_effectively_checked(self, item):
        if item.checkState(0) != CHECKED:
            return False
        parent = item.parent()
        while parent is not None:
            if parent.checkState(0) == UNCHECKED:
                return False
            parent = parent.parent()
        return True

    # ------------------------------------------------------------------ result
    def edited_record(self):
        old_records = record_by_layer_id(self.original_record)
        record = QgsMapThemeCollection.MapThemeRecord()
        new_layer_records = []
        for item in self.layer_items:
            layer = self.project.mapLayer(item.data(0, ROLE_LAYER_ID))
            if layer is None:
                continue
            old_record = old_records.get(layer.id())
            visible = self._item_effectively_checked(item)
            if not visible:
                # Newer QGIS versions keep hidden layers (isVisible=False) to
                # preserve their style; keep that information if present.
                if old_record is not None and hasattr(old_record, "isVisible"):
                    layer_record = clone_layer_record(old_record)
                    try:
                        layer_record.isVisible = False
                        new_layer_records.append(layer_record)
                    except Exception:
                        pass
                continue
            if old_record is not None:
                layer_record = clone_layer_record(old_record)
            else:
                layer_record = QgsMapThemeCollection.MapThemeLayerRecord(layer)
            try:
                layer_record.isVisible = True
            except Exception:
                pass
            new_layer_records.append(layer_record)
        record.setLayerRecords(new_layer_records)

        if hasattr(record, "setHasCheckedStateInfo"):
            # A partially checked group must be switched on, otherwise its
            # visible children would be hidden when the theme is applied.
            checked_groups = set()
            for item in self.group_items:
                if item.checkState(0) != UNCHECKED:
                    checked_groups.add(item.data(0, ROLE_GROUP_PATH))
            try:
                record.setHasCheckedStateInfo(True)
                record.setCheckedGroupNodes(checked_groups)
            except Exception:
                pass

        if hasattr(record, "setHasExpandedStateInfo"):
            try:
                has_info = False
                expanded = set()
                if hasattr(self.original_record, "hasExpandedStateInfo"):
                    has_info = bool(self.original_record.hasExpandedStateInfo())
                    if has_info:
                        expanded = set(self.original_record.expandedGroupNodes())
                record.setHasExpandedStateInfo(has_info)
                if has_info and hasattr(record, "setExpandedGroupNodes"):
                    record.setExpandedGroupNodes(expanded)
            except Exception:
                pass

        return record

    def done(self, result):
        self.window_state.save()
        self.window_state.cleanup()
        super().done(result)
