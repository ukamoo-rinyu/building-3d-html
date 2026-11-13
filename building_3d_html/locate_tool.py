"""地図をクリックした場所を、Google マップ・ストリートビュー・地理院地図で開く道具。
(EN) Map tool: click the map to open that place in Google Maps / Street View / GSI Maps
in the system web browser. Nothing is sent unless the user picks a menu item.

メニューの「クリックした場所を地図で確かめる」を押してから地図をクリックすると、
その場所で何を開くかを選ぶメニューが出る。別の道具（移動など）を選ぶと終わる。
"""

from qgis.gui import QgsMapToolEmitPoint
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QCursor, QDesktopServices
from qgis.PyQt.QtWidgets import QMenu

from .core.links import MAPS, to_lonlat, url_for


class LocateTool(QgsMapToolEmitPoint):
    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.canvasClicked.connect(self.on_click)

    def on_click(self, point, button=None):
        lon, lat = to_lonlat(point, self.canvas.mapSettings().destinationCrs())
        menu = QMenu(self.canvas)
        title = menu.addAction(f"この場所（{lat:.5f}, {lon:.5f}）を開く")
        title.setEnabled(False)
        menu.addSeparator()
        for kind, text in MAPS:
            menu.addAction(text).setData(kind)
        chosen = menu.exec(QCursor.pos()) if hasattr(menu, "exec") else menu.exec_(QCursor.pos())
        if chosen is not None and chosen.data():
            QDesktopServices.openUrl(QUrl(url_for(chosen.data(), lon, lat)))
