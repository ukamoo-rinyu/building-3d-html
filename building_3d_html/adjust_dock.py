"""照合の確認・調整（地図上で直す）のパネル。

照合で「要確認」になった施設（近くの建物で代用・建物なし）を1件ずつ地図に出し、
  - 「この建物でよい」：候補の建物の中へ施設の点を動かす
  - 「地図をクリックして直す」：クリックした場所へ施設の点を動かす（建物の中なら、その建物に当たる）
直すのは元の施設レイヤ（編集モードにして点を動かすので、元に戻す・保存は QGIS のいつもの操作でできる）。
直したあとは「照合し直す」で結果のレイヤを作り直す。
"""

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
    QgsWkbTypes,
)
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .core.matcher import NEAR, _text, _to_point, match
from .dialog import _enum

TITLE = "照合の確認・調整"
FIXED = "✓ 直した"
NAME_FIELDS = ("名称", "施設名", "name", "NAME", "Name")

try:
    POLYGON = Qgis.GeometryType.Polygon
except AttributeError:
    POLYGON = QgsWkbTypes.PolygonGeometry

_current = None  # 開いているパネル（1つだけにする）


def open_dock(iface, params, result, layer_ids):
    """照合の結果を受け取ってパネルを開く。前のパネルがあれば閉じる。"""
    global _current
    if _current is not None:
        _current.close()
    _current = AdjustDock(iface, params, result, layer_ids)
    iface.addDockWidget(_enum(Qt, "DockWidgetArea", "RightDockWidgetArea"), _current)
    _current.show()
    _current.raise_()
    return _current


class AdjustDock(QDockWidget):
    def __init__(self, iface, params, result, layer_ids):
        super().__init__(TITLE, iface.mainWindow())
        self.setAttribute(_enum(Qt, "WidgetAttribute", "WA_DeleteOnClose"))
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.params = params          # match() に渡したもの（施設・建物レイヤ、距離、選択中だけ）
        self.layer_ids = layer_ids    # 照合結果のレイヤ（照合し直すときに入れ替える）
        self.points = params["points"]
        self.buildings = params["buildings"]
        self.name_index = _name_field(self.points)
        self.checks = []
        self.fixed = set()            # 直した施設の fid

        # 地図の上の目印：候補の建物（オレンジの枠）と施設の点（赤い丸）
        self.band = QgsRubberBand(self.canvas, POLYGON)
        self.band.setColor(QColor(238, 154, 79, 200))
        self.band.setFillColor(QColor(238, 154, 79, 60))
        self.band.setWidth(3)
        self.marker = QgsVertexMarker(self.canvas)
        self.marker.setIconType(_enum(QgsVertexMarker, "IconType", "ICON_CIRCLE"))
        self.marker.setColor(QColor("#cf3f4f"))
        self.marker.setFillColor(QColor(207, 63, 79, 120))
        self.marker.setIconSize(16)
        self.marker.setPenWidth(3)
        self.marker.hide()

        self.tool = QgsMapToolEmitPoint(self.canvas)
        self.tool.canvasClicked.connect(self.on_map_click)
        self.prev_tool = None

        # 画面
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.show_item)
        self.detail = QLabel()
        self.detail.setWordWrap(True)

        self.prev_btn = QPushButton("◀ 前へ")
        self.next_btn = QPushButton("次へ ▶")
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn.clicked.connect(lambda: self.step(1))
        nav = QHBoxLayout()
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.next_btn)

        self.accept_btn = QPushButton("この建物でよい（点を建物の中へ動かす）")
        self.accept_btn.clicked.connect(self.accept_candidate)
        self.click_btn = QPushButton("地図をクリックして直す")
        self.click_btn.setCheckable(True)
        self.click_btn.setToolTip("押してから地図をクリックすると、施設の点がその場所へ動きます。"
                                  "建物の中をクリックすれば、その建物に当たります。")
        self.click_btn.toggled.connect(self.toggle_tool)
        self.canvas.mapToolSet.connect(self.on_tool_changed)

        self.rematch_btn = QPushButton("照合し直して結果を更新")
        self.rematch_btn.clicked.connect(self.rematch)

        note = QLabel(
            "点を動かすと、施設レイヤが編集中になります。残すときは QGIS の「編集内容を保存」を押してください"
            "（取り消しは Ctrl+Z）。直したあとは「照合し直して結果を更新」を押すと、結果のレイヤが新しくなります。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #555;")

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(self.summary)
        layout.addWidget(self.list, 1)
        layout.addLayout(nav)
        layout.addWidget(self.detail)
        layout.addWidget(self.accept_btn)
        layout.addWidget(self.click_btn)
        layout.addSpacing(8)
        layout.addWidget(self.rematch_btn)
        layout.addWidget(note)
        self.setWidget(body)

        self.set_result(result)

    # ---- 一覧 -------------------------------------------------------------

    def set_result(self, result):
        self.checks = list(result.checks)
        self.fixed = set()
        self.list.blockSignals(True)
        self.list.clear()
        for c in self.checks:
            self.list.addItem(self.item_text(c))
        self.list.blockSignals(False)
        self.update_summary()
        if self.checks:
            self.list.setCurrentRow(0)
        else:
            self.show_item(-1)

    def item_text(self, c):
        name = self.facility_name(c.point_fid)
        if c.point_fid in self.fixed:
            return f"{FIXED}　{name}"
        if c.status == NEAR:
            return f"{name}　（近くの建物で代用・{c.distance:.0f} m）"
        return f"{name}　（建物なし）"

    def facility_name(self, fid):
        f = self.points.getFeature(fid)
        name = _text(f.attribute(self.name_index)) if self.name_index is not None else ""
        return name or f"施設 {fid}"

    def update_summary(self):
        n = len(self.checks)
        if n == 0:
            self.summary.setText("要確認の施設はありません。")
        else:
            self.summary.setText(f"要確認の施設：{n:,} 件（直した {len(self.fixed):,} 件）")

    def step(self, d):
        row = self.list.currentRow() + d
        if 0 <= row < self.list.count():
            self.list.setCurrentRow(row)

    def current(self):
        row = self.list.currentRow()
        return self.checks[row] if 0 <= row < len(self.checks) else None

    # ---- 地図に出す ----------------------------------------------------------

    def show_item(self, row):
        c = self.current()
        self.prev_btn.setEnabled(row > 0)
        self.next_btn.setEnabled(0 <= row < len(self.checks) - 1)
        self.click_btn.setEnabled(c is not None)
        self.band.reset(POLYGON)
        self.marker.hide()
        if c is None:
            self.detail.setText("")
            self.accept_btn.setEnabled(False)
            self.canvas.refresh()
            return

        building = self.building_geometry(c)
        self.accept_btn.setEnabled(building is not None)
        if c.point_fid in self.fixed:
            self.detail.setText("直しました。ほかの場所がよければ、もう一度地図をクリックしてください。")
        elif c.status == NEAR:
            self.detail.setText(
                f"施設の点（赤い丸）が建物に入っていないため、{c.distance:.0f} m 先のいちばん近い建物"
                "（オレンジの枠）を当てています。正しければ「この建物でよい」、違えば「地図をクリックして直す」。")
        else:
            self.detail.setText(
                "施設の点（赤い丸）の近くに建物がありません。「地図をクリックして直す」で、"
                "正しい建物の中をクリックしてください。")

        if building is not None:
            self.band.setToGeometry(building, self.buildings)
        pt = self.point_on_canvas(c.point_fid)
        extent = QgsRectangle(pt, pt) if pt is not None else None
        if pt is not None:
            self.marker.setCenter(pt)
            self.marker.show()
        if building is not None:
            box = self.to_canvas(self.buildings).transformBoundingBox(building.boundingBox())
            extent = box if extent is None else _combined(extent, box)
        if extent is not None:
            self.zoom_to(extent)

    def building_geometry(self, c):
        if c.building_fid is None:
            return None
        f = self.buildings.getFeature(c.building_fid)
        g = f.geometry()
        return None if g is None or g.isNull() else QgsGeometry(g)

    def point_on_canvas(self, fid):
        p = _to_point(self.points.getFeature(fid).geometry())
        if p is None:
            return None
        return self.to_canvas(self.points).transform(p)

    def to_canvas(self, layer):
        return QgsCoordinateTransform(layer.crs(), self.canvas.mapSettings().destinationCrs(),
                                      QgsProject.instance())

    def zoom_to(self, extent):
        # 小さすぎると周りが見えないので、建物数棟が入るくらいまで広げる
        crs = self.canvas.mapSettings().destinationCrs()
        least = 0.0015 if crs.isGeographic() else 150.0
        c = extent.center()
        w = max(extent.width() * 2.0, least)
        h = max(extent.height() * 2.0, least)
        self.canvas.setExtent(QgsRectangle(c.x() - w / 2, c.y() - h / 2, c.x() + w / 2, c.y() + h / 2))
        self.canvas.refresh()

    # ---- 直す ----------------------------------------------------------------

    def accept_candidate(self):
        c = self.current()
        building = self.building_geometry(c) if c is not None else None
        if building is None:
            return
        p = building.pointOnSurface()
        p.transform(QgsCoordinateTransform(self.buildings.crs(), self.points.crs(), QgsProject.instance()))
        self.move_point(c, p.asPoint())

    def toggle_tool(self, on):
        if on:
            current = self.canvas.mapTool()
            if current is not self.tool:
                self.prev_tool = current
            self.canvas.setMapTool(self.tool)
        elif self.canvas.mapTool() is self.tool:
            if self.prev_tool is not None:
                self.canvas.setMapTool(self.prev_tool)
            else:
                self.canvas.unsetMapTool(self.tool)

    def on_tool_changed(self, new_tool, old_tool=None):
        if new_tool is not self.tool and self.click_btn.isChecked():
            self.click_btn.blockSignals(True)
            self.click_btn.setChecked(False)
            self.click_btn.blockSignals(False)

    def on_map_click(self, point, button=None):
        c = self.current()
        if c is None:
            return
        xform = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), self.points.crs(),
                                       QgsProject.instance())
        self.move_point(c, xform.transform(point))

    def move_point(self, c, xy):
        """施設の点を xy（施設レイヤの座標）へ動かす。元に戻せるよう、編集モードの中で動かす。"""
        layer = self.points
        if not layer.isEditable() and not layer.startEditing():
            QMessageBox.warning(
                self, TITLE,
                f"「{layer.name()}」は編集できない形式のため、点を動かせません。\n"
                "右クリック →「エクスポート」→「地物の保存」で GeoPackage に保存し、"
                "保存したレイヤで照合し直してください。")
            return
        g = QgsGeometry.fromPointXY(xy)
        if QgsWkbTypes.isMultiType(layer.wkbType()):
            g.convertToMultiType()
        layer.beginEditCommand("施設の位置を直す")
        ok = layer.changeGeometry(c.point_fid, g)
        if ok:
            layer.endEditCommand()
        else:
            layer.destroyEditCommand()
            QMessageBox.warning(self, TITLE, "点を動かせませんでした。")
            return
        layer.triggerRepaint()

        self.fixed.add(c.point_fid)
        row = self.list.currentRow()
        self.list.item(row).setText(self.item_text(c))
        self.update_summary()
        self.show_item(row)
        if self.building_at(xy) is None:
            self.detail.setText("点を動かしました。ただし、そこは建物の外です。"
                                "照合し直すと「近くの建物で代用」か「建物なし」になります。")
        else:
            self.detail.setText("点を建物の中へ動かしました。照合し直すと「建物の中」になります。")

    def building_at(self, xy):
        """xy（施設レイヤの座標）が入る建物の fid。どの建物にも入らなければ None。"""
        g = QgsGeometry.fromPointXY(xy)
        g.transform(QgsCoordinateTransform(self.points.crs(), self.buildings.crs(), QgsProject.instance()))
        req = QgsFeatureRequest().setFilterRect(g.boundingBox()).setNoAttributes()
        for f in self.buildings.getFeatures(req):
            if f.geometry().contains(g):
                return f.id()
        return None

    # ---- 照合し直す ------------------------------------------------------------

    def rematch(self):
        from .match_dialog import add_result_layers  # 互いに読み込み合うので、ここで読む

        QApplication.setOverrideCursor(_enum(Qt, "CursorShape", "WaitCursor"))
        try:
            p = self.params
            r = match(p["points"], p["buildings"], p["distance"], p["selected_only"])
        except Exception as e:  # 失敗の理由をそのまま見せる
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, TITLE, "照合に失敗しました。\n\n" + str(e))
            return
        QApplication.restoreOverrideCursor()

        project = QgsProject.instance()
        for lid in self.layer_ids:
            if project.mapLayer(lid) is not None:
                project.removeMapLayer(lid)
        self.layer_ids = add_result_layers(r)
        self.set_result(r)
        QMessageBox.information(
            self, TITLE,
            f"照合し直しました。\n\n　建物の中：{r.inside:,} 件\n　近くの建物で代用：{r.near:,} 件\n"
            f"　建物なし：{r.none:,} 件\n\n結果のレイヤを新しくしました。")

    # ---- 閉じる ------------------------------------------------------------------

    def closeEvent(self, event):
        global _current
        if self.click_btn.isChecked():
            self.click_btn.setChecked(False)
        try:
            self.canvas.mapToolSet.disconnect(self.on_tool_changed)
        except TypeError:
            pass
        self.canvas.scene().removeItem(self.band)
        self.canvas.scene().removeItem(self.marker)
        self.canvas.refresh()
        if _current is self:
            _current = None
        super().closeEvent(event)


def _name_field(layer):
    """一覧に出す施設の名前の列。よくある列名、なければ最初の文字の列。"""
    fields = layer.fields()
    for n in NAME_FIELDS:
        i = fields.indexOf(n)
        if i >= 0:
            return i
    for i, f in enumerate(fields):
        if not f.isNumeric() and not f.isDateOrTime() and f.name().lower() != "fid":
            return i
    return None


def _combined(a, b):
    r = QgsRectangle(a)
    r.combineExtentWith(b)
    return r
