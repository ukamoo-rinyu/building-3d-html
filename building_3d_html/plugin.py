"""Plugin entry: adds the menu items under Web > Building Connect & 3D Publish for PLATEAU.

プラグインの入口。「Web」メニューに項目を足す。
"""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

MENU = "&Building Connect && 3D Publish for PLATEAU"


class Building3DHtml:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.match_action = None
        self.geocode_action = None
        self.locate_action = None
        self.locate_tool = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "3D地図をHTMLに書き出す… / Export 3D map HTML…", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToWebMenu(MENU, self.action)
        self.iface.addWebToolBarIcon(self.action)

        self.geocode_action = QAction("住所から点を作る… / Points from addresses…", self.iface.mainWindow())
        self.geocode_action.triggered.connect(self.run_geocode)
        self.iface.addPluginToWebMenu(MENU, self.geocode_action)

        self.match_action = QAction("点と建物を照合する… / Match facilities to buildings…", self.iface.mainWindow())
        self.match_action.triggered.connect(self.run_match)
        self.iface.addPluginToWebMenu(MENU, self.match_action)

        # 地図をクリックした場所を Google マップなどで開く（押している間だけ有効な道具）
        self.locate_action = QAction("クリックした場所を地図で確かめる（Google マップなど） / Check clicked place on web map", self.iface.mainWindow())
        self.locate_action.setCheckable(True)
        self.locate_action.toggled.connect(self.toggle_locate)
        self.iface.addPluginToWebMenu(MENU, self.locate_action)

    def unload(self):
        self.iface.removePluginWebMenu(MENU, self.action)
        self.iface.removePluginWebMenu(MENU, self.match_action)
        self.iface.removePluginWebMenu(MENU, self.geocode_action)
        self.iface.removePluginWebMenu(MENU, self.locate_action)
        canvas = self.iface.mapCanvas()
        if self.locate_tool is not None and canvas.mapTool() is self.locate_tool:
            canvas.unsetMapTool(self.locate_tool)
        self.iface.removeWebToolBarIcon(self.action)
        self.action = None
        self.match_action = None
        self.geocode_action = None
        self.locate_action = None
        self.locate_tool = None

    def toggle_locate(self, on):
        canvas = self.iface.mapCanvas()
        if self.locate_tool is None:
            from .locate_tool import LocateTool
            self.locate_tool = LocateTool(canvas)
            # 別の道具に替えたら、メニューのチェックも外す
            self.locate_tool.setAction(self.locate_action)
        if on:
            canvas.setMapTool(self.locate_tool)
        elif canvas.mapTool() is self.locate_tool:
            canvas.unsetMapTool(self.locate_tool)

    def run(self):
        from .dialog import Building3DHtmlDialog
        dlg = Building3DHtmlDialog(self.iface, self.iface.mainWindow())
        dlg.exec()

    def run_match(self):
        from .match_dialog import MatchDialog
        dlg = MatchDialog(self.iface, self.iface.mainWindow())
        dlg.exec()

    def run_geocode(self):
        from .geocode_dialog import GeocodeDialog
        dlg = GeocodeDialog(self.iface, self.iface.mainWindow())
        dlg.exec()
