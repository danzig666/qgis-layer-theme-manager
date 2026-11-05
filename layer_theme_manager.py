import os

try:
    from qgis.PyQt.QtWidgets import QAction
except ImportError:
    from qgis.PyQt.QtGui import QAction
from qgis.PyQt.QtGui import QIcon

from qgis.core import Qgis, QgsMessageLog


class LayerThemeManagerPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None
        self.menu_name = "&Layer Theme Manager"

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "Layer Theme Manager", self.iface.mainWindow())
        self.action.setObjectName("LayerThemeManagerAction")
        self.action.setToolTip("Manage layer and map themes in the current project")
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(self.menu_name, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu(self.menu_name, self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.dialog is not None:
            try:
                self.dialog.cleanup()
                self.dialog.close()
                self.dialog.deleteLater()
            except Exception:
                pass
            self.dialog = None

    def run(self):
        try:
            from .layer_theme_manager_dialog import LayerThemeManagerDialog

            if self.dialog is None:
                self.dialog = LayerThemeManagerDialog(self.iface, self.iface.mainWindow())
            # The dialog keeps itself up to date through project signals and
            # refreshes on show only when something changed while hidden.
            if self.dialog.isMinimized():
                self.dialog.showNormal()
            self.dialog.show()
            self.dialog.raise_()
            self.dialog.activateWindow()
        except Exception as exc:
            QgsMessageLog.logMessage(str(exc), "Layer Theme Manager", Qgis.Critical)
            self.iface.messageBar().pushCritical("Layer Theme Manager", str(exc))
