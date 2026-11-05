"""Persist and restore a top-level window's size and position.

Besides the plain geometry it remembers which screen the window was on and that
screen's available area. When the monitor setup or the resolution changes, the
window is moved proportionally onto the same (or the nearest) screen and kept
fully visible. It also reacts to monitors being plugged in/out or resized while
the window is open.
"""

from qgis.PyQt.QtCore import QEvent, QObject, QRect, QSettings, QTimer
from qgis.PyQt.QtWidgets import QApplication

from .qt_compat import WINDOW_FULLSCREEN, WINDOW_MAXIMIZED, WINDOW_MINIMIZED, enum_value

try:
    from qgis.PyQt.QtGui import QGuiApplication
except ImportError:  # pragma: no cover
    QGuiApplication = QApplication

_EV_MOVE = enum_value(QEvent, "Move", "Type")
_EV_RESIZE = enum_value(QEvent, "Resize", "Type")
_EV_SHOW = enum_value(QEvent, "Show", "Type")
_EV_HIDE = enum_value(QEvent, "Hide", "Type")
_EV_CLOSE = enum_value(QEvent, "Close", "Type")
_EV_STATE = enum_value(QEvent, "WindowStateChange", "Type")

# Height of the area under the top edge that must stay reachable (title bar).
_MIN_MARGIN = 8


def _rect_to_str(rect):
    return "{0},{1},{2},{3}".format(rect.x(), rect.y(), rect.width(), rect.height())


def _str_to_rect(value):
    try:
        parts = [int(float(p)) for p in str(value).split(",")]
        if len(parts) == 4 and parts[2] > 0 and parts[3] > 0:
            return QRect(parts[0], parts[1], parts[2], parts[3])
    except Exception:
        pass
    return None


def _area(rect):
    if rect is None or rect.isEmpty():
        return 0
    return rect.width() * rect.height()


def _screens():
    try:
        return list(QGuiApplication.screens())
    except Exception:
        return []


def _primary_screen():
    try:
        return QGuiApplication.primaryScreen()
    except Exception:
        return None


def _screen_for_rect(rect):
    """Screen with the largest overlap with rect, or the nearest one."""
    best = None
    best_area = 0
    for screen in _screens():
        overlap = _area(screen.geometry().intersected(rect))
        if overlap > best_area:
            best, best_area = screen, overlap
    if best is not None:
        return best
    center = rect.center()
    best_dist = None
    for screen in _screens():
        c = screen.geometry().center()
        dist = (c.x() - center.x()) ** 2 + (c.y() - center.y()) ** 2
        if best_dist is None or dist < best_dist:
            best, best_dist = screen, dist
    return best or _primary_screen()


def _widget_screen(widget):
    if widget is None:
        return None
    try:
        screen = widget.screen()  # Qt >= 5.14
        if screen is not None:
            return screen
    except Exception:
        pass
    try:
        return _screen_for_rect(widget.frameGeometry())
    except Exception:
        return None


class WindowStateKeeper(QObject):
    def __init__(self, widget, settings_group, default_size=(900, 560)):
        super().__init__(widget)
        self.widget = widget
        self.group = settings_group
        self.default_size = default_size
        self._connections = []
        self._watched_screens = set()
        self._active = True

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self.save)

        self._fix_timer = QTimer(self)
        self._fix_timer.setSingleShot(True)
        self._fix_timer.setInterval(300)
        self._fix_timer.timeout.connect(self.ensure_on_screen)

        app = QGuiApplication.instance()
        if app is not None:
            self._link(app.screenAdded, self._screen_added)
            self._link(app.screenRemoved, self._screen_removed)
            if hasattr(app, "primaryScreenChanged"):
                self._link(app.primaryScreenChanged, self._schedule_fix)
        for screen in _screens():
            self._watch_screen(screen)

        self.restore()
        widget.installEventFilter(self)

    # ------------------------------------------------------------------ signals
    def _link(self, signal, slot):
        try:
            signal.connect(slot)
            self._connections.append((signal, slot))
        except Exception:
            pass

    def _watch_screen(self, screen):
        key = id(screen)
        if key in self._watched_screens:
            return
        self._watched_screens.add(key)
        for name in ("geometryChanged", "availableGeometryChanged", "logicalDotsPerInchChanged"):
            signal = getattr(screen, name, None)
            if signal is not None:
                self._link(signal, self._schedule_fix)

    def _screen_added(self, screen):
        self._watch_screen(screen)
        self._schedule_fix()

    def _screen_removed(self, screen):
        self._watched_screens.discard(id(screen))
        self._connections = [(s, f) for s, f in self._connections if getattr(s, "__self__", None) is not screen]
        self._schedule_fix()

    def _schedule_fix(self, *args):
        if self._active:
            self._fix_timer.start()

    def cleanup(self):
        self._active = False
        self._save_timer.stop()
        self._fix_timer.stop()
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except Exception:
                pass
        self._connections = []
        try:
            self.widget.removeEventFilter(self)
        except Exception:
            pass

    # ------------------------------------------------------------------ events
    def eventFilter(self, obj, event):
        if obj is self.widget and self._active:
            etype = event.type()
            if etype in (_EV_MOVE, _EV_RESIZE, _EV_STATE):
                if self.widget.isVisible():
                    self._save_timer.start()
            elif etype == _EV_SHOW:
                # Screens may have changed while the window was hidden.
                self._fix_timer.start(0)
            elif etype in (_EV_HIDE, _EV_CLOSE):
                self.save()
        return False

    # ------------------------------------------------------------------ settings
    def _key(self, name):
        return "{0}/{1}".format(self.group, name)

    def save(self):
        widget = self.widget
        try:
            state = widget.windowState()
            if state & WINDOW_MINIMIZED:
                return
            maximized = bool(state & (WINDOW_MAXIMIZED | WINDOW_FULLSCREEN))
            rect = widget.normalGeometry() if maximized else widget.geometry()
            if rect.isEmpty():
                rect = widget.geometry()
            if rect.isEmpty():
                return
            screen = _widget_screen(widget) or _screen_for_rect(rect)
            settings = QSettings()
            settings.setValue(self._key("rect"), _rect_to_str(rect))
            settings.setValue(self._key("maximized"), maximized)
            if screen is not None:
                settings.setValue(self._key("screen_name"), screen.name())
                settings.setValue(self._key("screen_area"), _rect_to_str(screen.availableGeometry()))
        except Exception:
            pass

    def restore(self):
        widget = self.widget
        settings = QSettings()
        rect = _str_to_rect(settings.value(self._key("rect"), ""))
        maximized = settings.value(self._key("maximized"), False, type=bool)
        if rect is None:
            self._place_default()
            return

        saved_name = settings.value(self._key("screen_name"), "") or ""
        saved_area = _str_to_rect(settings.value(self._key("screen_area"), ""))
        screens = _screens()

        target = None
        for screen in screens:
            if saved_name and screen.name() == saved_name:
                target = screen
                break
        if target is None:
            # The monitor is gone: if the rect still lies on a screen use that,
            # otherwise fall back to the screen QGIS itself is on.
            overlap_screen = None
            best = 0
            for screen in screens:
                area = _area(screen.availableGeometry().intersected(rect))
                if area > best:
                    overlap_screen, best = screen, area
            if overlap_screen is not None and best >= 0.5 * _area(rect):
                target = overlap_screen
                saved_area = None  # position is still valid, do not remap
            else:
                target = _widget_screen(widget.parentWidget()) or _primary_screen()

        if target is not None:
            new_area = target.availableGeometry()
            if saved_area is not None and saved_area != new_area:
                rect = self._map_rect(rect, saved_area, new_area)
            rect = self._clamp(rect, new_area)

        widget.setGeometry(rect)
        if maximized:
            widget.setWindowState(widget.windowState() | WINDOW_MAXIMIZED)

    def _place_default(self):
        widget = self.widget
        width, height = self.default_size
        screen = _widget_screen(widget.parentWidget()) or _primary_screen()
        if screen is None:
            widget.resize(width, height)
            return
        area = screen.availableGeometry()
        width = min(width, int(area.width() * 0.9))
        height = min(height, int(area.height() * 0.9))
        parent = widget.parentWidget()
        center = parent.frameGeometry().center() if parent is not None and parent.isVisible() else area.center()
        rect = QRect(0, 0, width, height)
        rect.moveCenter(center)
        widget.setGeometry(self._clamp(rect, area))

    # ------------------------------------------------------------------ geometry helpers
    @staticmethod
    def _map_rect(rect, old_area, new_area):
        """Map rect from old_area to new_area keeping its relative position."""
        if old_area.width() <= 0 or old_area.height() <= 0:
            return QRect(rect)
        sx = new_area.width() / float(old_area.width())
        sy = new_area.height() / float(old_area.height())
        width = rect.width()
        height = rect.height()
        # Only shrink the window when the new screen is smaller.
        if sx < 1.0:
            width = int(round(width * sx))
        if sy < 1.0:
            height = int(round(height * sy))
        # Keep the window center at the same relative position.
        cx = (rect.center().x() - old_area.x()) * sx + new_area.x()
        cy = (rect.center().y() - old_area.y()) * sy + new_area.y()
        mapped = QRect(0, 0, max(1, width), max(1, height))
        mapped.moveCenter(QRect(int(cx), int(cy), 1, 1).center())
        return mapped

    def _clamp(self, rect, area, margins=(0, 0, 0, 0)):
        """Keep a client rect (plus frame margins) fully inside area."""
        left, top, right, bottom = margins
        if top == 0:
            top = _MIN_MARGIN * 3  # unknown title bar height before first show
        min_size = self.widget.minimumSize()
        max_w = max(area.width() - left - right, 1)
        max_h = max(area.height() - top - bottom, 1)
        width = min(rect.width(), max_w)
        height = min(rect.height(), max_h)
        width = max(width, min(min_size.width(), max_w))
        height = max(height, min(min_size.height(), max_h))
        x = rect.x()
        y = rect.y()
        x = min(x, area.x() + area.width() - right - width)
        y = min(y, area.y() + area.height() - bottom - height)
        x = max(x, area.x() + left)
        y = max(y, area.y() + top)
        return QRect(x, y, width, height)

    def ensure_on_screen(self):
        widget = self.widget
        if not self._active or not widget.isVisible():
            return
        state = widget.windowState()
        if state & (WINDOW_MINIMIZED | WINDOW_MAXIMIZED | WINDOW_FULLSCREEN):
            return
        frame = widget.frameGeometry()
        geo = widget.geometry()
        screen = _screen_for_rect(frame)
        if screen is None:
            return
        area = screen.availableGeometry()
        margins = (
            max(0, geo.x() - frame.x()),
            max(0, geo.y() - frame.y()),
            max(0, frame.right() - geo.right()),
            max(0, frame.bottom() - geo.bottom()),
        )
        fixed = self._clamp(geo, area, margins)
        if fixed != geo:
            if fixed.size() != geo.size():
                widget.resize(fixed.size())
            widget.move(fixed.x() - margins[0], fixed.y() - margins[1])
            self._save_timer.start()
