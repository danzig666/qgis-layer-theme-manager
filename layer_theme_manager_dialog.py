import json
import os

try:
    from qgis.PyQt.QtWidgets import QAction
except ImportError:
    from qgis.PyQt.QtGui import QAction
from qgis.PyQt.QtCore import QSettings, QSize, Qt, QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from qgis.core import QgsApplication, QgsProject, Qgis

from .batch_rename_dialog import BatchRenameDialog
from .progress import LazyProgress
from .qt_compat import (
    ALIGN_CENTER,
    ALIGN_RIGHT_VCENTER,
    ASCENDING_ORDER,
    CUSTOM_CONTEXT_MENU,
    DESCENDING_ORDER,
    TOOL_BUTTON_TEXT_BESIDE_ICON,
    USER_ROLE,
    VERTICAL,
    WIDGET_SHORTCUT,
    WINDOW_CONTEXT_HELP_HINT,
    WINDOW_MAXIMIZE_HINT,
    enum_int,
    enum_value,
)
from .theme_edit_dialog import ThemeEditDialog
from .theme_io import FORMAT, dumps_payload, export_payload, import_payload, loads_payload
from .theme_utils import (
    apply_theme_to_project,
    collection,
    current_state_record,
    current_visible_layer_ids,
    duplicate_name,
    has_theme,
    insert_theme,
    layer_tree_index,
    log,
    map_theme_names,
    remove_theme,
    rename_theme,
    theme_summary,
    unique_name,
    update_theme,
)
from .window_state import WindowStateKeeper


NAME_ROLE = USER_ROLE
FILTER_ROLE = USER_ROLE + 1

COL_NAME, COL_LAYERS, COL_LEGEND, COL_STYLES, COL_NOTES = range(5)
HEADERS = ["Theme", "Layers", "Legend items", "Style overrides", "Notes"]

PLUGIN_DIR = os.path.dirname(__file__)


def _escape(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _SortItem(QTableWidgetItem):
    """Table item that sorts by a Python key (case-insensitive names, numeric counts)."""

    def __init__(self, text, key):
        super().__init__(text)
        self.sort_key = key

    def __lt__(self, other):
        try:
            return self.sort_key < other.sort_key
        except Exception:
            return QTableWidgetItem.__lt__(self, other)


class LayerThemeManagerDialog(QDialog):
    SETTINGS = "LayerThemeManager"

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.settings = QSettings()
        self._connections = []
        self._project_connections = []
        self._summaries = {}
        self._active_names = set()
        self._dirty = True
        self._busy = 0
        self._refreshing = False

        self.setWindowTitle("Layer Theme Manager")
        self.setWindowIcon(QIcon(os.path.join(PLUGIN_DIR, "icon.svg")))
        try:
            self.setWindowFlags((self.windowFlags() | WINDOW_MAXIMIZE_HINT) & ~WINDOW_CONTEXT_HELP_HINT)
        except Exception:
            pass
        self.setMinimumSize(560, 340)
        self.setSizeGripEnabled(True)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(200)
        self._refresh_timer.timeout.connect(self._auto_refresh)
        self._active_timer = QTimer(self)
        self._active_timer.setSingleShot(True)
        self._active_timer.setInterval(250)
        self._active_timer.timeout.connect(self._update_active_marker)

        self._build_ui()
        self._restore_settings()
        self.window_state = WindowStateKeeper(self, self._key("window"), (940, 600))
        self._connect_project_signals()
        self._update_paste_action()
        self.refresh()

    # ================================================================== UI
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        self.toolbar = QToolBar(self)
        self.toolbar.setToolButtonStyle(TOOL_BUTTON_TEXT_BESIDE_ICON)
        self.toolbar.setIconSize(QSize(18, 18))
        layout.addWidget(self.toolbar)

        self.actions = {}
        add = self._add_action
        add("refresh", "Refresh", "/mActionRefresh.svg", self.refresh, "Reload the theme list", "F5", toolbar=False)
        add("new", "New", "/mActionAdd.svg", self.new_from_current, "Create a new theme from the current map visibility", "Ctrl+N")
        add("activate", "Activate", "/mActionShowAllLayers.svg", self.activate_theme, "Apply the selected theme to the map (Enter)")
        add("edit", "Edit", "/mActionToggleEditing.svg", self.edit_theme, "Choose visible layers and groups for the selected theme", "Ctrl+E")
        add("replace", "Update", "/mActionFileSave.svg", self.replace_from_current, "Overwrite the selected theme with the current map visibility", "Ctrl+U")
        self.toolbar.addSeparator()
        add("rename", "Rename", "/mIconFieldText.svg", self.rename_theme, "Rename the selected theme", "F2")
        add("duplicate", "Duplicate", "/mActionDuplicateLayer.svg", self.duplicate_themes, "Duplicate the selected themes", "Ctrl+D")
        add("delete", "Delete", "/mActionDeleteSelected.svg", self.delete_themes, "Delete the selected themes (Del)")
        add("batch", "Batch Rename...", "/mActionCalculateField.svg", self.batch_rename, "Find/replace, prefix and suffix for the selected theme names", "Ctrl+B", toolbar=False)
        add("export", "Export to File...", "/mActionFileSaveAs.svg", self.export_themes, "Export the selected themes to a JSON file", "Ctrl+S", toolbar=False)
        add("import", "Import from File...", "/mActionFileOpen.svg", self.import_themes, "Import themes from a JSON file", "Ctrl+O", toolbar=False)
        add("copy", "Copy", "/mActionEditCopy.svg", self.copy_themes, "Copy the selected themes to the clipboard", "Ctrl+C", toolbar=False)
        add("paste", "Paste", "/mActionEditPaste.svg", self.paste_themes, "Paste themes from the clipboard", "Ctrl+V", toolbar=False)

        # Delete / Enter only act when the table has focus so they never
        # interfere with typing in the search field.
        for key, shortcut in (("delete", "Del"), ("activate", "Return")):
            self.actions[key].setShortcut(shortcut)
            self.actions[key].setShortcutContext(WIDGET_SHORTCUT)

        self.confirm_delete_action = QAction("Confirm before deleting", self)
        self.confirm_delete_action.setCheckable(True)
        self.confirm_delete_action.setChecked(self.settings.value(self._key("confirm_delete"), True, type=bool))
        self.confirm_delete_action.toggled.connect(lambda value: self.settings.setValue(self._key("confirm_delete"), value))
        self.dblclick_activate_action = QAction("Double-click activates theme (instead of edit)", self)
        self.dblclick_activate_action.setCheckable(True)
        self.dblclick_activate_action.setChecked(self.settings.value(self._key("dblclick_activate"), False, type=bool))
        self.dblclick_activate_action.toggled.connect(lambda value: self.settings.setValue(self._key("dblclick_activate"), value))

        self.more_menu = QMenu(self)
        for key in ("batch",):
            self.more_menu.addAction(self.actions[key])
        self.more_menu.addSeparator()
        for key in ("copy", "paste", "export", "import"):
            self.more_menu.addAction(self.actions[key])
        self.more_menu.addSeparator()
        self.more_menu.addAction(self.confirm_delete_action)
        self.more_menu.addAction(self.dblclick_activate_action)
        more_button = QToolButton(self)
        more_button.setText("More")
        more_button.setIcon(self._icon("/mActionOptions.svg"))
        more_button.setToolButtonStyle(TOOL_BUTTON_TEXT_BESIDE_ICON)
        more_button.setPopupMode(enum_value(QToolButton, "InstantPopup", "ToolButtonPopupMode"))
        more_button.setMenu(self.more_menu)
        more_button.setToolTip("Import/export, clipboard, batch rename and options")
        self.toolbar.addSeparator()
        self.toolbar.addWidget(more_button)

        spacer = QWidget(self)
        spacer.setSizePolicy(enum_value(QSizePolicy, "Expanding", "Policy"),
                             enum_value(QSizePolicy, "Preferred", "Policy"))
        self.toolbar.addWidget(spacer)
        self.toolbar.addAction(self.actions["refresh"])
        # Secondary actions as compact icon buttons so the toolbar fits narrow windows.
        icon_only = enum_value(Qt, "ToolButtonIconOnly", "ToolButtonStyle")
        for key in ("rename", "duplicate", "delete", "refresh"):
            button = self.toolbar.widgetForAction(self.actions[key])
            if button is not None:
                button.setToolButtonStyle(icon_only)

        # ---- search row
        filters = QHBoxLayout()
        self.filter_edit = QLineEdit(self)
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setPlaceholderText("Search themes or layers...  (Enter = activate)")
        try:
            self.filter_edit.addAction(self._icon("/search.svg"), enum_value(QLineEdit, "LeadingPosition", "ActionPosition"))
        except Exception:
            pass
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.filter_edit.returnPressed.connect(self._filter_return)
        filters.addWidget(self.filter_edit, 1)
        self.layer_search_check = QCheckBox("Match layer names", self)
        self.layer_search_check.setToolTip("Also find themes that contain a layer with a matching name")
        self.layer_search_check.setChecked(self.settings.value(self._key("search_layers"), True, type=bool))
        self.layer_search_check.toggled.connect(self._layer_search_toggled)
        filters.addWidget(self.layer_search_check)
        layout.addLayout(filters)

        # ---- table + details
        self.splitter = QSplitter(VERTICAL, self)
        self.splitter.setChildrenCollapsible(False)

        self.stack = QStackedWidget(self)
        self.table = QTableWidget(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(enum_value(QAbstractItemView, "SelectRows", "SelectionBehavior"))
        self.table.setSelectionMode(enum_value(QAbstractItemView, "ExtendedSelection", "SelectionMode"))
        self.table.setEditTriggers(enum_value(QAbstractItemView, "NoEditTriggers", "EditTrigger"))
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 10)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(COL_NAME, enum_value(QHeaderView, "Stretch", "ResizeMode"))
        for col in (COL_LAYERS, COL_LEGEND, COL_STYLES):
            header.setSectionResizeMode(col, enum_value(QHeaderView, "ResizeToContents", "ResizeMode"))
        header.setSectionResizeMode(COL_NOTES, enum_value(QHeaderView, "Interactive", "ResizeMode"))
        header.setStretchLastSection(False)
        header.resizeSection(COL_NOTES, 160)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(self._double_clicked)
        self.table.setContextMenuPolicy(CUSTOM_CONTEXT_MENU)
        self.table.customContextMenuRequested.connect(self._context_menu)
        for key in ("delete", "activate"):
            self.table.addAction(self.actions[key])
        self.stack.addWidget(self.table)

        self.empty_label = QLabel(self)
        self.empty_label.setAlignment(ALIGN_CENTER)
        self.empty_label.setWordWrap(True)
        self.empty_label.setText(
            "<p style='font-size:13pt'><b>No map themes yet</b></p>"
            "<p>Set up layer visibility in the QGIS Layers panel,<br>"
            "then click <b>New</b> to save it as a theme.</p>"
            "<p>You can also <b>Import</b> or <b>Paste</b> themes from the <b>More</b> menu.</p>"
        )
        self.stack.addWidget(self.empty_label)
        self.splitter.addWidget(self.stack)

        self.details = QTextBrowser(self)
        self.details.setOpenLinks(False)
        self.details.setPlaceholderText("Select a theme to see its layers.")
        self.splitter.addWidget(self.details)
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([420, 140])
        layout.addWidget(self.splitter, 1)

        # ---- status row
        status = QHBoxLayout()
        self.status_label = QLabel(self)
        self.report_label = QLabel(self)
        self.report_label.setAlignment(ALIGN_RIGHT_VCENTER)
        self.report_label.setWordWrap(False)
        ignored = enum_value(QSizePolicy, "Ignored", "Policy")
        preferred = enum_value(QSizePolicy, "Preferred", "Policy")
        self.status_label.setSizePolicy(ignored, preferred)
        self.report_label.setSizePolicy(ignored, preferred)
        status.addWidget(self.status_label, 3)
        status.addWidget(self.report_label, 2)
        layout.addLayout(status)

        try:
            QApplication.clipboard().dataChanged.connect(self._update_paste_action)
            self._connections.append((QApplication.clipboard().dataChanged, self._update_paste_action))
        except Exception:
            pass

        self._active_icon = self._icon("/mActionShowAllLayers.svg")
        self._warning_icon = self._icon("/mIconWarning.svg")

    def _add_action(self, key, text, qgis_icon, callback, tooltip, shortcut=None, toolbar=True):
        action = QAction(self._icon(qgis_icon), text, self)
        tip = tooltip
        if shortcut:
            action.setShortcut(shortcut)
            tip = "{0} ({1})".format(tooltip, shortcut)
        action.setToolTip(tip)
        action.setStatusTip(tooltip)
        action.triggered.connect(lambda checked=False, cb=callback: cb())
        self.addAction(action)
        if toolbar:
            self.toolbar.addAction(action)
        self.actions[key] = action
        return action

    def _icon(self, name):
        try:
            icon = QgsApplication.getThemeIcon(name)
            if not icon.isNull():
                return icon
        except Exception:
            pass
        return QIcon.fromTheme(name.strip("/").replace(".svg", ""))

    def _context_menu(self, pos):
        menu = QMenu(self)
        for key in ("activate", "edit", "replace", None, "rename", "duplicate", "batch", "delete", None, "copy", "paste", "export"):
            if key is None:
                menu.addSeparator()
            else:
                menu.addAction(self.actions[key])
        menu.exec_(self.table.viewport().mapToGlobal(pos)) if hasattr(menu, "exec_") else menu.exec(
            self.table.viewport().mapToGlobal(pos))

    def _double_clicked(self, *args):
        if self.dblclick_activate_action.isChecked():
            self.activate_theme()
        else:
            self.edit_theme()

    # ================================================================== settings
    def _key(self, name):
        return "{0}/{1}".format(self.SETTINGS, name)

    def _restore_settings(self):
        splitter_state = self.settings.value(self._key("splitter"))
        if splitter_state:
            try:
                self.splitter.restoreState(splitter_state)
            except Exception:
                pass
        column = self.settings.value(self._key("sort_column"), COL_NAME, type=int)
        descending = self.settings.value(self._key("sort_descending"), False, type=bool)
        if not 0 <= column < len(HEADERS):
            column = COL_NAME
        self.table.sortByColumn(column, DESCENDING_ORDER if descending else ASCENDING_ORDER)
        notes_width = self.settings.value(self._key("notes_width"), 160, type=int)
        if 40 <= notes_width <= 2000:
            self.table.horizontalHeader().resizeSection(COL_NOTES, notes_width)

    def _save_settings(self):
        header = self.table.horizontalHeader()
        self.settings.setValue(self._key("splitter"), self.splitter.saveState())
        self.settings.setValue(self._key("sort_column"), header.sortIndicatorSection())
        self.settings.setValue(self._key("sort_descending"), enum_int(header.sortIndicatorOrder()) == enum_int(DESCENDING_ORDER))
        self.settings.setValue(self._key("notes_width"), header.sectionSize(COL_NOTES))
        selected = self.selected_names()
        if selected:
            self.settings.setValue(self._key("last_selected_theme"), selected[0])
        self.window_state.save()

    def showEvent(self, event):
        super().showEvent(event)
        if self._dirty:
            self._refresh_timer.start(0)
        self.filter_edit.setFocus()

    def hideEvent(self, event):
        self._save_settings()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._save_settings()
        super().closeEvent(event)

    def cleanup(self):
        """Disconnect everything; called when the plugin unloads."""
        try:
            self._save_settings()
        except Exception:
            pass
        self._refresh_timer.stop()
        self._active_timer.stop()
        self._disconnect(self._project_connections)
        self._disconnect(self._connections)
        self.window_state.cleanup()

    # ================================================================== project signals
    @staticmethod
    def _disconnect(connections):
        for signal, slot in connections:
            try:
                signal.disconnect(slot)
            except Exception:
                pass
        del connections[:]

    @staticmethod
    def _link(connections, signal, slot):
        try:
            signal.connect(slot)
            connections.append((signal, slot))
        except Exception:
            pass

    def _connect_project_signals(self):
        prj = QgsProject.instance()
        for name in ("cleared", "readProject", "mapThemeCollectionChanged"):
            signal = getattr(prj, name, None)
            if signal is not None:
                self._link(self._connections, signal, self._project_changed)
        self._connect_collection_signals()

    def _connect_collection_signals(self):
        self._disconnect(self._project_connections)
        prj = QgsProject.instance()
        coll = prj.mapThemeCollection()
        for name in ("mapThemesChanged", "mapThemeChanged", "mapThemeRenamed"):
            signal = getattr(coll, name, None)
            if signal is not None:
                self._link(self._project_connections, signal, self._schedule_refresh)
        for name in ("layersAdded", "layersRemoved"):
            self._link(self._project_connections, getattr(prj, name), self._schedule_refresh)
        root = prj.layerTreeRoot()
        for name in ("addedChildren", "removedChildren", "nameChanged"):
            signal = getattr(root, name, None)
            if signal is not None:
                self._link(self._project_connections, signal, self._schedule_refresh)
        signal = getattr(root, "visibilityChanged", None)
        if signal is not None:
            self._link(self._project_connections, signal, self._schedule_active)

    def _project_changed(self, *args):
        self._connect_collection_signals()
        self._schedule_refresh()

    def _schedule_refresh(self, *args):
        self._dirty = True
        if self.isVisible() and not self._busy:
            self._refresh_timer.start()

    def _schedule_active(self, *args):
        if self.isVisible() and not self._busy:
            self._active_timer.start()

    def _auto_refresh(self):
        if self._busy or not self.isVisible():
            return
        self.refresh()

    # ================================================================== table
    def refresh(self, *args, select=None):
        if self._refreshing:
            return
        self._refreshing = True
        self._refresh_timer.stop()
        try:
            if select is None:
                select = self.selected_names()
                if not select:
                    last = self.settings.value(self._key("last_selected_theme"), "")
                    select = [last] if last else []
            scroll = self.table.verticalScrollBar().value()

            coll = collection()
            names = map_theme_names(coll)
            tree_index = layer_tree_index()

            with LazyProgress(self, "Layer Theme Manager", "Reading map themes...", len(names)) as progress:
                summaries = {}
                for index, name in enumerate(names, 1):
                    summaries[name] = theme_summary(name, coll, tree_index)
                    progress.step(index, "Reading theme {0} of {1}".format(index, len(names)))
            self._summaries = summaries
            self._tree_order = {layer_id: i for i, (layer_id, _) in enumerate(tree_index)}
            self._fill_table(names)

            self._apply_filter()
            self._restore_selection(select, scroll_to=False)
            self.table.verticalScrollBar().setValue(scroll)
            self._update_active_marker()
            self.stack.setCurrentIndex(0 if names else 1)
            self._dirty = False
        finally:
            self._refreshing = False
        self._selection_changed()

    def _fill_table(self, names):
        table = self.table
        header = table.horizontalHeader()
        sort_column = max(0, header.sortIndicatorSection())
        sort_order = header.sortIndicatorOrder()
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        table.setSortingEnabled(False)
        try:
            table.clearContents()
            table.setRowCount(len(names))
            for row, name in enumerate(names):
                info = self._summaries[name]
                layers = len(info["layer_ids"])
                name_item = _SortItem(name, name.lower())
                name_item.setData(NAME_ROLE, name)
                filter_text = name.lower()
                layer_text = "\n".join(info["layer_names"]).lower()
                name_item.setData(FILTER_ROLE, filter_text + "\n" + layer_text)

                notes = []
                if info["error"]:
                    notes.append("read error: {0}".format(info["error"]))
                if info["missing"]:
                    notes.append("{0} missing layer(s)".format(info["missing"]))
                hidden_by_group = layers - len(info["visible_ids"])
                if hidden_by_group > 0:
                    notes.append("{0} hidden by group".format(hidden_by_group))
                if not layers and not info["error"]:
                    notes.append("no visible layers")
                notes_item = _SortItem(", ".join(notes), ", ".join(notes))
                if info["error"] or info["missing"]:
                    notes_item.setIcon(self._warning_icon)
                notes_item.setToolTip("\n".join(notes))

                cells = [
                    name_item,
                    _SortItem(str(layers), layers),
                    _SortItem(str(info["legend"]) if info["legend"] else "", info["legend"]),
                    _SortItem(str(info["styles"]) if info["styles"] else "", info["styles"]),
                    notes_item,
                ]
                for col in (COL_LAYERS, COL_LEGEND, COL_STYLES):
                    cells[col].setTextAlignment(ALIGN_RIGHT_VCENTER)
                for col, item in enumerate(cells):
                    table.setItem(row, col, item)
        finally:
            table.setSortingEnabled(True)
            table.sortByColumn(sort_column, sort_order)
            table.blockSignals(False)
            table.setUpdatesEnabled(True)

    def _update_active_marker(self):
        """Mark the theme(s) whose layer visibility matches the current map."""
        try:
            current = frozenset(current_visible_layer_ids())
        except Exception:
            current = None
        active = set()
        if current is not None:
            for name, info in self._summaries.items():
                if info["visible_ids"] == current and not info["error"]:
                    active.add(name)
        self._active_names = active
        table = self.table
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        try:
            for row in range(table.rowCount()):
                item = table.item(row, COL_NAME)
                if item is None:
                    continue
                is_active = item.data(NAME_ROLE) in active
                font = item.font()
                if font.bold() != is_active:
                    font.setBold(is_active)
                    item.setFont(font)
                item.setIcon(self._active_icon if is_active else QIcon())
                item.setToolTip("Matches the current map visibility" if is_active else "")
        finally:
            table.blockSignals(False)
            table.setUpdatesEnabled(True)
        self._update_status()

    def _apply_filter(self, *args):
        text = self.filter_edit.text().strip().lower()
        search_layers = self.layer_search_check.isChecked()
        terms = text.split()
        table = self.table
        table.setUpdatesEnabled(False)
        try:
            for row in range(table.rowCount()):
                item = table.item(row, COL_NAME)
                if item is None:
                    continue
                if not terms:
                    hidden = False
                else:
                    haystack = item.data(FILTER_ROLE) if search_layers else item.data(NAME_ROLE).lower()
                    hidden = not all(term in haystack for term in terms)
                table.setRowHidden(row, hidden)
        finally:
            table.setUpdatesEnabled(True)
        self._update_status()

    def _layer_search_toggled(self, value):
        self.settings.setValue(self._key("search_layers"), value)
        self._apply_filter()

    def _visible_rows(self):
        return [row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)]

    def _filter_return(self):
        rows = self._visible_rows()
        if len(rows) == 1:
            self.table.selectRow(rows[0])
        if len(self.selected_names()) == 1:
            self.activate_theme()
        elif rows:
            self.table.setFocus()

    def _restore_selection(self, names, scroll_to=True):
        names = set(names or [])
        table = self.table
        model = table.selectionModel()
        table.blockSignals(True)
        try:
            table.clearSelection()
            first = None
            flags = enum_value(type(model), "Select", "SelectionFlag") | enum_value(type(model), "Rows", "SelectionFlag")
            for row in range(table.rowCount()):
                item = table.item(row, COL_NAME)
                if item is not None and item.data(NAME_ROLE) in names:
                    model.select(table.model().index(row, 0), flags)
                    if first is None:
                        first = item
            if first is not None:
                current_flags = enum_value(type(model), "NoUpdate", "SelectionFlag")
                model.setCurrentIndex(table.indexFromItem(first), current_flags)
                if scroll_to:
                    table.scrollToItem(first)
        finally:
            table.blockSignals(False)
        self._selection_changed()

    def selected_names(self):
        names = []
        for index in self.table.selectionModel().selectedRows(COL_NAME):
            item = self.table.item(index.row(), COL_NAME)
            if item is not None:
                names.append(item.data(NAME_ROLE))
        names.sort(key=lambda value: value.lower())
        return names

    def _selection_changed(self):
        if self._refreshing:
            return
        selected = self.selected_names()
        count = len(selected)
        for key in ("edit", "activate", "rename", "replace"):
            self.actions[key].setEnabled(count == 1)
        for key in ("duplicate", "delete", "export", "copy", "batch"):
            self.actions[key].setEnabled(count >= 1)
        self.actions["batch"].setText("Batch Rename..." if count != 1 else "Batch Rename (1 theme)...")
        self._update_details(selected)
        self._update_status()

    def _update_status(self):
        total = self.table.rowCount()
        shown = len(self._visible_rows()) if self.filter_edit.text().strip() else total
        selected = len(self.table.selectionModel().selectedRows(COL_NAME))
        parts = ["{0} theme(s)".format(total)]
        if shown != total:
            parts.append("{0} shown".format(shown))
        if selected:
            parts.append("{0} selected".format(selected))
        if self._active_names:
            parts.append("current map = " + ", ".join(sorted(self._active_names, key=str.lower)))
        self.status_label.setText("  ·  ".join(parts))
        self.status_label.setToolTip("\n".join(parts))

    def _update_details(self, selected):
        if not selected:
            self.details.clear()
            return
        if len(selected) > 1:
            items = "".join("<li>{0}</li>".format(_escape(n)) for n in selected[:200])
            more = "<p>... and {0} more</p>".format(len(selected) - 200) if len(selected) > 200 else ""
            self.details.setHtml("<b>{0} themes selected</b><ul style='margin-top:2px'>{1}</ul>{2}".format(
                len(selected), items, more))
            return
        name = selected[0]
        info = self._summaries.get(name)
        if info is None:
            self.details.clear()
            return
        order = getattr(self, "_tree_order", {})
        pairs = sorted(zip(info["layer_ids"], info["layer_names"]), key=lambda p: order.get(p[0], 1 << 30))
        rows = []
        for layer_id, layer_name in pairs[:500]:
            if layer_id in info["visible_ids"]:
                rows.append("<li>{0}</li>".format(_escape(layer_name)))
            else:
                rows.append("<li><span style='color:gray'>{0} (group off)</span></li>".format(_escape(layer_name)))
        if len(pairs) > 500:
            rows.append("<li>... and {0} more</li>".format(len(pairs) - 500))
        badges = []
        if name in self._active_names:
            badges.append("<span style='color:#2e7d32'>&#10004; matches current map</span>")
        if info["missing"]:
            badges.append("<span style='color:#c62828'>{0} missing layer(s)</span>".format(info["missing"]))
        if info["styles"]:
            badges.append("{0} style override(s)".format(info["styles"]))
        if info["legend"]:
            badges.append("{0} checked legend item(s)".format(info["legend"]))
        html = "<b>{0}</b> &nbsp; {1} layer(s)".format(_escape(name), len(pairs))
        if badges:
            html += " &nbsp;&middot;&nbsp; " + " &nbsp;&middot;&nbsp; ".join(badges)
        html += "<ul style='margin-top:2px'>{0}</ul>".format("".join(rows)) if rows else "<p><i>No visible layers.</i></p>"
        self.details.setHtml(html)
        self.settings.setValue(self._key("last_selected_theme"), name)

    def _update_paste_action(self, *args):
        try:
            text = QApplication.clipboard().text()
        except Exception:
            text = ""
        looks_ok = bool(text) and FORMAT in text[:2000000]
        self.actions["paste"].setEnabled(looks_ok)
        self.actions["paste"].setToolTip(
            "Paste themes from the clipboard (Ctrl+V)" if looks_ok else "The clipboard does not contain copied themes")

    # ================================================================== messages
    def _message(self, text, level=None, duration=4):
        if level is None:
            level = getattr(Qgis, "Info", 0)
        try:
            self.iface.messageBar().pushMessage("Layer Theme Manager", text, level=level, duration=duration)
        except Exception:
            pass
        self.report_label.setText(text if len(text) < 140 else text[:137] + "...")
        self.report_label.setToolTip(text)
        log(text, level)

    def _error(self, text):
        self._message(text, getattr(Qgis, "Critical", 2), 8)
        QMessageBox.critical(self, "Layer Theme Manager", text)

    def _input_name(self, title, label, default, existing):
        name = default
        while True:
            name, ok = QInputDialog.getText(self, title, label, text=name)
            if not ok:
                return None
            name = name.strip()
            if not name:
                QMessageBox.warning(self, title, "Theme name cannot be empty.")
                name = default
                continue
            if name in existing:
                QMessageBox.warning(self, title, "A theme named '{0}' already exists. Please choose another name.".format(name))
                continue
            return name

    def _single(self):
        selected = self.selected_names()
        return selected[0] if len(selected) == 1 else None

    def _ensure_exists(self, name, coll):
        if coll.hasMapTheme(name):
            return True
        self._message("Theme no longer exists: {0}".format(name), getattr(Qgis, "Warning", 1))
        self.refresh()
        return False

    def _begin(self):
        self._busy += 1

    def _end(self, select=None):
        self._busy = max(0, self._busy - 1)
        self._active_timer.stop()
        self.refresh(select=select)

    # ================================================================== actions
    def new_from_current(self):
        existing = set(map_theme_names())
        name = self._input_name("New Theme", "Save the current map visibility as:", unique_name("New theme", existing), existing)
        if not name:
            return
        self._begin()
        try:
            with LazyProgress(self, "New Theme", "Reading current layer visibility..."):
                insert_theme(name, current_state_record(self.iface))
            self._message("Created theme '{0}' from current visibility.".format(name))
        except Exception as exc:
            self._error("Could not create theme: {0}".format(exc))
        finally:
            self._end(select=[name])

    def rename_theme(self):
        old_name = self._single()
        if not old_name:
            return
        existing = set(map_theme_names()) - {old_name}
        name = self._input_name("Rename Theme", "New name:", old_name, existing)
        if not name or name == old_name:
            return
        self._begin()
        try:
            rename_theme(old_name, name)
            self._message("Renamed '{0}' to '{1}'.".format(old_name, name))
        except Exception as exc:
            name = old_name
            self._error("Could not rename theme: {0}".format(exc))
        finally:
            self._end(select=[name])

    def activate_theme(self):
        name = self._single()
        if not name:
            return
        coll = collection()
        if not self._ensure_exists(name, coll):
            return
        self._busy += 1
        try:
            with LazyProgress(self, "Activate Theme", "Applying theme '{0}'...".format(name)):
                apply_theme_to_project(name, self.iface, coll)
            self._message("Activated theme '{0}'.".format(name), duration=2)
        except Exception as exc:
            self._error("Could not activate theme: {0}".format(exc))
        finally:
            self._busy -= 1
        self._update_active_marker()
        self._update_details(self.selected_names())

    def duplicate_themes(self):
        selected = self.selected_names()
        if not selected:
            return
        coll = collection()
        existing = set(map_theme_names(coll))
        pattern = self.settings.value(self._key("duplicate_pattern"), "{name} copy")
        if len(selected) == 1:
            new_name = self._input_name("Duplicate Theme", "New theme name:",
                                        duplicate_name(selected[0], existing, pattern), existing)
            if not new_name:
                return
            plan = [(selected[0], new_name)]
        else:
            plan = []
            for old_name in selected:
                new_name = duplicate_name(old_name, existing, pattern)
                existing.add(new_name)
                plan.append((old_name, new_name))
        created = []
        self._begin()
        try:
            with LazyProgress(self, "Duplicate Themes", "Duplicating themes...", len(plan)) as progress:
                for index, (old_name, new_name) in enumerate(plan, 1):
                    progress.step(index, "Duplicating '{0}'".format(old_name))
                    if has_theme(old_name, coll):
                        insert_theme(new_name, coll.mapThemeState(old_name), coll)
                        created.append(new_name)
            self._message("Duplicated {0} theme(s).".format(len(created)))
        except Exception as exc:
            self._error("Could not duplicate themes: {0}".format(exc))
        finally:
            self._end(select=created)

    def delete_themes(self):
        selected = self.selected_names()
        if not selected:
            return
        if self.confirm_delete_action.isChecked():
            box = QMessageBox(self)
            box.setIcon(enum_value(QMessageBox, "Question", "Icon"))
            box.setWindowTitle("Delete Themes")
            if len(selected) == 1:
                box.setText("Delete theme '{0}'?".format(selected[0]))
            else:
                box.setText("Delete {0} themes?".format(len(selected)))
                box.setDetailedText("\n".join(selected))
            yes = enum_value(QMessageBox, "Yes", "StandardButton")
            no = enum_value(QMessageBox, "No", "StandardButton")
            box.setStandardButtons(yes | no)
            box.setDefaultButton(no)
            dont_ask = QCheckBox("Don't ask again", box)
            box.setCheckBox(dont_ask)
            result = box.exec_() if hasattr(box, "exec_") else box.exec()
            if enum_int(result) != enum_int(yes):
                return
            if dont_ask.isChecked():
                self.confirm_delete_action.setChecked(False)

        # Select the row after the deleted ones so keyboard deletion flows nicely.
        rows = sorted(index.row() for index in self.table.selectionModel().selectedRows(COL_NAME))
        next_name = None
        for row in range(rows[-1] + 1, self.table.rowCount()) if rows else []:
            if not self.table.isRowHidden(row):
                next_name = self.table.item(row, COL_NAME).data(NAME_ROLE)
                break
        if next_name is None and rows:
            for row in range(rows[0] - 1, -1, -1):
                if not self.table.isRowHidden(row):
                    next_name = self.table.item(row, COL_NAME).data(NAME_ROLE)
                    break

        deleted = []
        self._begin()
        try:
            with LazyProgress(self, "Delete Themes", "Deleting themes...", len(selected)) as progress:
                coll = collection()
                for index, name in enumerate(selected, 1):
                    progress.step(index, "Deleting '{0}'".format(name))
                    if remove_theme(name, coll):
                        deleted.append(name)
            self._message("Deleted {0} theme(s).".format(len(deleted)))
        except Exception as exc:
            self._error("Could not delete themes: {0}".format(exc))
        finally:
            self._end(select=[next_name] if next_name else [])

    def edit_theme(self):
        name = self._single()
        if not name:
            return
        coll = collection()
        if not self._ensure_exists(name, coll):
            return
        try:
            dialog = ThemeEditDialog(self.iface, name, coll.mapThemeState(name), self)
        except Exception as exc:
            self._error("Could not open theme editor: {0}".format(exc))
            return
        result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
        if enum_int(result) != enum_int(enum_value(QDialog, "Accepted", "DialogCode")):
            dialog.deleteLater()
            return
        self._begin()
        try:
            update_theme(name, dialog.edited_record(), coll)
            self._message("Updated theme '{0}'.".format(name))
        except Exception as exc:
            self._error("Could not save theme: {0}".format(exc))
        finally:
            dialog.deleteLater()
            self._end(select=[name])

    def replace_from_current(self):
        name = self._single()
        if not name:
            return
        if name in self._active_names:
            self._message("'{0}' already matches the current map visibility; updated styles/legend state.".format(name),
                          duration=3)
        self._begin()
        try:
            with LazyProgress(self, "Update Theme", "Reading current layer visibility..."):
                update_theme(name, current_state_record(self.iface))
            self._message("Updated '{0}' from the current map.".format(name))
        except Exception as exc:
            self._error("Could not update theme: {0}".format(exc))
        finally:
            self._end(select=[name])

    def batch_rename(self):
        selected = self.selected_names()
        if not selected:
            return
        dialog = BatchRenameDialog(selected, map_theme_names(), self)
        result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
        planned = dialog.planned()
        dialog.deleteLater()
        if enum_int(result) != enum_int(enum_value(QDialog, "Accepted", "DialogCode")):
            return
        planned = [(old, new) for old, new, _ in planned if old != new]
        renamed = []
        self._begin()
        try:
            coll = collection()
            # Two-phase rename so swaps like "A"->"B", "B"->"A" cannot collide.
            temp = []
            taken = set(map_theme_names(coll))
            with LazyProgress(self, "Batch Rename", "Renaming themes...", len(planned) * 2) as progress:
                for index, (old, new) in enumerate(planned, 1):
                    progress.step(index)
                    if not has_theme(old, coll):
                        continue
                    if new in taken:
                        tmp = unique_name("{0} __ltm_tmp".format(old), taken)
                        rename_theme(old, tmp, coll)
                        taken.discard(old)
                        taken.add(tmp)
                        temp.append((tmp, new))
                    else:
                        rename_theme(old, new, coll)
                        taken.discard(old)
                        taken.add(new)
                        renamed.append(new)
                for index, (tmp, new) in enumerate(temp, len(planned) + 1):
                    progress.step(index)
                    final = new if new not in taken else unique_name(new, taken)
                    rename_theme(tmp, final, coll)
                    taken.discard(tmp)
                    taken.add(final)
                    renamed.append(final)
            self._message("Renamed {0} theme(s).".format(len(renamed)))
        except Exception as exc:
            self._error("Could not batch rename themes: {0}".format(exc))
        finally:
            self._end(select=renamed)

    # ---------------------------------------------------------------- import / export
    def _default_export_name(self):
        try:
            base = QgsProject.instance().baseName()
        except Exception:
            base = ""
        return "{0}_themes.json".format(base) if base else "layer_themes.json"

    def export_themes(self):
        selected = self.selected_names()
        if not selected:
            return
        last_dir = self.settings.value(self._key("last_io_dir"), "") or QgsProject.instance().homePath() or os.path.expanduser("~")
        default_path = os.path.join(last_dir, self._default_export_name())
        path, _ = QFileDialog.getSaveFileName(self, "Export Themes", default_path, "JSON files (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            with LazyProgress(self, "Export Themes", "Exporting themes...", len(selected)) as progress:
                payload = export_payload(selected, collection(), progress.callback())
                progress.step(len(selected), "Writing JSON file...", force=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(dumps_payload(payload))
            self.settings.setValue(self._key("last_io_dir"), os.path.dirname(path))
            self._message("Exported {0} theme(s) to {1}.".format(len(payload["themes"]), path))
        except Exception as exc:
            self._error("Could not export themes: {0}".format(exc))

    def import_themes(self):
        last_dir = self.settings.value(self._key("last_io_dir"), "") or QgsProject.instance().homePath() or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, "Import Themes", last_dir, "JSON files (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = loads_payload(handle.read())
        except Exception as exc:
            self._error("Could not read {0}: {1}".format(path, exc))
            return
        self.settings.setValue(self._key("last_io_dir"), os.path.dirname(path))
        self._import(payload, "Imported from {0}".format(os.path.basename(path)))

    def copy_themes(self):
        selected = self.selected_names()
        if not selected:
            return
        try:
            with LazyProgress(self, "Copy Themes", "Preparing themes...", len(selected)) as progress:
                payload = export_payload(selected, collection(), progress.callback())
                QApplication.clipboard().setText(dumps_payload(payload))
            self._message("Copied {0} theme(s) to the clipboard.".format(len(payload["themes"])), duration=2)
        except Exception as exc:
            self._error("Could not copy themes: {0}".format(exc))

    def paste_themes(self):
        text = QApplication.clipboard().text()
        try:
            payload = loads_payload(text)
        except json.JSONDecodeError as exc:
            self._error("The clipboard does not contain valid JSON: {0}".format(exc))
            return
        except Exception as exc:
            self._error("Could not paste themes: {0}".format(exc))
            return
        self._import(payload, "Pasted from clipboard")

    def _import(self, payload, title):
        report = None
        self._begin()
        try:
            themes = payload.get("themes", [])
            with LazyProgress(self, "Import Themes", "Importing themes...", len(themes)) as progress:
                report = import_payload(payload, collection(), progress_callback=progress.callback())
            self._show_import_report(report, title)
        except Exception as exc:
            self._error("Could not import themes: {0}".format(exc))
        finally:
            self._end(select=report["imported"] if report else None)

    def _show_import_report(self, report, title):
        lines = ["{0}: {1} theme(s).".format(title, len(report["imported"]))]
        if report["renamed"]:
            lines.append("Renamed on import: {0}.".format(", ".join("{0} -> {1}".format(a, b) for a, b in report["renamed"])))
        if report["repaired"]:
            repaired_count = sum(len(v) for v in report["repaired"].values())
            lines.append("Re-linked {0} layer reference(s) by name/source.".format(repaired_count))
        if report["missing"]:
            missing_count = sum(len(v) for v in report["missing"].values())
            lines.append("Warning: {0} layer reference(s) could not be matched.".format(missing_count))
        level = getattr(Qgis, "Warning", 1) if report["missing"] else getattr(Qgis, "Info", 0)
        self._message(" ".join(lines), level, 8)
