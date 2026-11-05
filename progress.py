"""Lightweight progress reporting.

The old implementation created a QProgressDialog for every operation and called
QApplication.processEvents() for every single item, which dominated run time on
projects with many themes or layers. LazyProgress only shows a dialog when an
operation is actually slow and throttles event processing to a few times per
second.
"""

import time

from qgis.PyQt.QtCore import QEventLoop
from qgis.PyQt.QtWidgets import QApplication, QProgressDialog

from .qt_compat import WAIT_CURSOR, WINDOW_MODAL, enum_value

try:
    from qgis.PyQt.QtGui import QCursor
except ImportError:  # pragma: no cover
    QCursor = None

_EXCLUDE_INPUT = enum_value(QEventLoop, "ExcludeUserInputEvents", "ProcessEventsFlag")


class LazyProgress:
    def __init__(self, parent, title, label="", maximum=0, delay=0.5, interval=0.1, wait_cursor=True):
        self.parent = parent
        self.title = title
        self.label = label
        self.maximum = max(0, int(maximum or 0))
        self.value = 0
        self.delay = delay
        self.interval = interval
        self.wait_cursor = wait_cursor
        self._dialog = None
        self._start = time.monotonic()
        self._last = 0.0
        self._cursor_set = False
        if wait_cursor and QCursor is not None:
            try:
                QApplication.setOverrideCursor(QCursor(WAIT_CURSOR))
                self._cursor_set = True
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def set_maximum(self, maximum):
        self.maximum = max(0, int(maximum or 0))
        if self._dialog is not None:
            self._dialog.setMaximum(self.maximum)

    def step(self, value=None, label=None, force=False):
        if label:
            self.label = label
        if value is not None:
            self.value = value
        now = time.monotonic()
        if not force and now - self._last < self.interval:
            return
        self._last = now
        if self._dialog is None:
            if now - self._start < self.delay:
                return
            self._create()
        if self.label:
            self._dialog.setLabelText(self.label)
        if self.maximum > 0:
            self._dialog.setValue(min(self.value, self.maximum))
        QApplication.processEvents(_EXCLUDE_INPUT)

    def callback(self):
        """Return a ``callback(index, total, label)`` usable by theme_io helpers."""

        def _callback(index, total, label):
            if total and total != self.maximum:
                self.set_maximum(total)
            self.step(index, label)

        return _callback

    def _create(self):
        dialog = QProgressDialog(self.label, "", 0, self.maximum, self.parent)
        dialog.setCancelButton(None)
        dialog.setWindowTitle(self.title)
        dialog.setWindowModality(WINDOW_MODAL)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumWidth(360)
        dialog.show()
        self._dialog = dialog

    def close(self):
        if self._dialog is not None:
            try:
                self._dialog.close()
                self._dialog.deleteLater()
            except Exception:
                pass
            self._dialog = None
        if self._cursor_set:
            self._cursor_set = False
            try:
                QApplication.restoreOverrideCursor()
            except Exception:
                pass
