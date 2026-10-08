import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

MENU = "&Building 3D HTML"


class Building3DHtml:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.match_action = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "3D地図をHTMLに書き出す…", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToWebMenu(MENU, self.action)
        self.iface.addWebToolBarIcon(self.action)

        self.match_action = QAction("点と建物を照合する…", self.iface.mainWindow())
        self.match_action.triggered.connect(self.run_match)
        self.iface.addPluginToWebMenu(MENU, self.match_action)

    def unload(self):
        self.iface.removePluginWebMenu(MENU, self.action)
        self.iface.removePluginWebMenu(MENU, self.match_action)
        self.iface.removeWebToolBarIcon(self.action)
        self.action = None
        self.match_action = None

    def run(self):
        from .dialog import Building3DHtmlDialog
        dlg = Building3DHtmlDialog(self.iface, self.iface.mainWindow())
        dlg.exec()

    def run_match(self):
        from .match_dialog import MatchDialog
        dlg = MatchDialog(self.iface, self.iface.mainWindow())
        dlg.exec()
