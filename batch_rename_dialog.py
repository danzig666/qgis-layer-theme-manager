from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QBrush, QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .qt_compat import enum_value
from .theme_utils import unique_name
from .window_state import WindowStateKeeper


def plan_renames(names, existing, find="", replace="", prefix="", suffix="", case_sensitive=True):
    """Return [(old, new, note)] for the requested batch rename."""
    taken = set(existing) - set(names)
    planned = []
    for old in names:
        new = old
        if find:
            if case_sensitive:
                new = new.replace(find, replace)
            else:
                lower = new.lower()
                needle = find.lower()
                out = []
                pos = 0
                while True:
                    hit = lower.find(needle, pos)
                    if hit < 0:
                        out.append(new[pos:])
                        break
                    out.append(new[pos:hit])
                    out.append(replace)
                    pos = hit + len(find)
                new = "".join(out)
        new = "{0}{1}{2}".format(prefix, new, suffix).strip()
        note = ""
        if not new:
            new, note = old, "empty name - kept"
        elif new in taken:
            unique = unique_name(new, taken)
            note = "name taken - using '{0}'".format(unique)
            new = unique
        taken.add(new)
        planned.append((old, new, note))
    return planned


class BatchRenameDialog(QDialog):
    def __init__(self, names, existing, parent=None):
        super().__init__(parent)
        self.names = list(names)
        self.existing = set(existing)
        self.setWindowTitle("Batch Rename Themes")
        self.setMinimumSize(440, 440)

        layout = QVBoxLayout(self)
        intro = QLabel("Rename {0} selected theme(s). The preview updates as you type.".format(len(self.names)), self)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.find_edit = QLineEdit(self)
        self.find_edit.setPlaceholderText("text to find (optional)")
        self.replace_edit = QLineEdit(self)
        self.replace_edit.setPlaceholderText("replacement")
        self.case_check = QCheckBox("Match case", self)
        self.case_check.setChecked(True)
        self.prefix_edit = QLineEdit(self)
        self.suffix_edit = QLineEdit(self)
        form.addRow("Find:", self.find_edit)
        form.addRow("Replace with:", self.replace_edit)
        form.addRow("", self.case_check)
        form.addRow("Add prefix:", self.prefix_edit)
        form.addRow("Add suffix:", self.suffix_edit)
        layout.addLayout(form)

        self.preview = QTreeWidget(self)
        self.preview.setHeaderLabels(["Current name", "New name", "Note"])
        self.preview.setRootIsDecorated(False)
        self.preview.setAlternatingRowColors(True)
        self.preview.setMinimumHeight(120)
        layout.addWidget(self.preview, 1)

        self.summary = QLabel(self)
        layout.addWidget(self.summary)

        ok = enum_value(QDialogButtonBox, "Ok", "StandardButton")
        cancel = enum_value(QDialogButtonBox, "Cancel", "StandardButton")
        self.buttons = QDialogButtonBox(ok | cancel, self)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.ok_button = self.buttons.button(ok)
        self.ok_button.setText("Rename")
        layout.addWidget(self.buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._update_preview)
        for edit in (self.find_edit, self.replace_edit, self.prefix_edit, self.suffix_edit):
            edit.textChanged.connect(self._timer.start)
        self.case_check.toggled.connect(self._timer.start)

        self.window_state = WindowStateKeeper(self, "LayerThemeManager/batch_window", (560, 460))
        self._update_preview()
        self.prefix_edit.setFocus()

    def planned(self):
        return plan_renames(
            self.names,
            self.existing,
            self.find_edit.text(),
            self.replace_edit.text(),
            self.prefix_edit.text(),
            self.suffix_edit.text(),
            self.case_check.isChecked(),
        )

    def _update_preview(self):
        planned = self.planned()
        changed = 0
        warn = QBrush(QColor(200, 120, 0))
        self.preview.setUpdatesEnabled(False)
        self.preview.clear()
        items = []
        for old, new, note in planned:
            item = QTreeWidgetItem([old, new, note])
            if old != new:
                changed += 1
                font = item.font(1)
                font.setBold(True)
                item.setFont(1, font)
            if note:
                item.setForeground(2, warn)
            items.append(item)
        self.preview.addTopLevelItems(items)
        self.preview.resizeColumnToContents(0)
        self.preview.resizeColumnToContents(1)
        self.preview.setUpdatesEnabled(True)
        self.summary.setText("{0} of {1} theme(s) will be renamed.".format(changed, len(planned)))
        self.ok_button.setEnabled(changed > 0)

    def done(self, result):
        self.window_state.save()
        self.window_state.cleanup()
        super().done(result)
