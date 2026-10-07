import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

MENU = "&Building 3D HTML"


class Building3DHtml:
    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "3D地図をHTMLに書き出す…", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToWebMenu(MENU, self.action)
        self.iface.addWebToolBarIcon(self.action)

    def unload(self):
        self.iface.removePluginWebMenu(MENU, self.action)
        self.iface.removeWebToolBarIcon(self.action)
        self.action = None

    def run(self):
        from .dialog import Building3DHtmlDialog
        dlg = Building3DHtmlDialog(self.iface, self.iface.mainWindow())
        dlg.exec()
