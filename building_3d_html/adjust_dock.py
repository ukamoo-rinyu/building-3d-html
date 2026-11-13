"""照合の確認・調整（地図上で直す）のパネル。
(EN) Dock panel to review facility-building matches one by one and fix them on the map.

照合した施設を1件ずつ地図に出す。「表示」で、要確認（近くの建物で代用・建物なし）・
照合できた施設（建物の中・敷地の中）・すべて、を切り替えられる。
  - 「この建物でよい」：候補の建物の中へ施設の点を動かす
  - 「地図をクリックして直す」：クリックした場所へ施設の点を動かす（建物の中なら、その建物に当たる）
  - 「建物を選んで当てる」：地図で建物を（何棟でも）クリックして選び、その施設に当てる。
    選んだ建物のIDは施設レイヤの「指定した建物」の列に入り、照合し直すと「手で指定」になる
  - 「場所を確かめる」：施設の場所を Google マップ・ストリートビュー・地理院地図で開く、名前で検索する
直すのは元の施設レイヤ（編集モードにして点を動かすので、元に戻す・保存は QGIS のいつもの操作でできる）。
直したあとは「照合し直す」で結果のレイヤを作り直す。敷地（面）の施設は点ではないので、点は動かさない
（建物を選んで当てることはできる）。
"""

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsField,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
    QgsWkbTypes,
)
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QColor, QDesktopServices
from qgis.PyQt.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .core.links import search_url, to_lonlat, url_for
from .core.matcher import (
    INSIDE,
    KEY_SEP,
    MANUAL,
    MANUAL_FIELD,
    NEAR,
    SITE,
    T_STRING,
    _text,
    _to_point,
    building_key,
    building_key_field,
    match,
    parse_keys,
)
from .dialog import _enum

TITLE = "照合の確認・調整 / Review matches"
FIXED = "✓ 直した"
NAME_FIELDS = ("名称", "施設名", "施設名称", "name", "NAME", "Name")
ADDRESS_FIELDS = ("所在地", "住所", "address", "ADDRESS", "Address")
CHECK, MATCHED, ALL = "check", "matched", "all"

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
        self.address_index = _first_field(self.points, ADDRESS_FIELDS)
        self.checks = []              # 要確認の施設
        self.matched = []             # 照合できた施設
        self.items = []               # 一覧に出している施設（「表示」で切り替える）
        self.fixed = set()            # 直した施設の fid
        self.manual_set = {}          # 建物を選んで当てた施設の fid → 棟数
        self.pending = None           # 建物を選んでいる途中の建物の fid（選んでいないときは None）
        self.key_field = building_key_field(self.buildings)

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
        self.pick_tool = QgsMapToolEmitPoint(self.canvas)
        self.pick_tool.canvasClicked.connect(self.on_pick_click)
        self.prev_tool = None

        # 画面
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.view = QComboBox()
        self.view.addItem("要確認の施設", CHECK)
        self.view.addItem("照合できた施設（建物の中・敷地の中・手で指定）", MATCHED)
        self.view.addItem("すべて", ALL)
        self.view.currentIndexChanged.connect(lambda _: self.fill_list())
        view_box = QHBoxLayout()
        view_box.addWidget(QLabel("表示"))
        view_box.addWidget(self.view, 1)
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

        # 建物を選んで当てる（何棟でも）
        self.pick_btn = QPushButton("建物を選んで当てる（複数の棟も可）")
        self.pick_btn.setCheckable(True)
        self.pick_btn.setToolTip("押してから地図の建物をクリックすると、選ぶ・外すを切り替えられます。"
                                 "選び終わったら「選んだ建物に決める」。")
        self.pick_btn.toggled.connect(self.toggle_pick)
        self.pick_done = QPushButton("選んだ建物に決める")
        self.pick_done.clicked.connect(self.save_pick)
        self.pick_clear = QPushButton("手での指定を外す")
        self.pick_clear.setToolTip("選んで当てた建物を消して、施設の位置で照合するように戻す")
        self.pick_clear.clicked.connect(self.clear_manual)
        pick_box = QHBoxLayout()
        pick_box.addWidget(self.pick_done)
        pick_box.addWidget(self.pick_clear)
        self.canvas.mapToolSet.connect(self.on_tool_changed)

        # 場所を確かめる：施設の点（敷地なら中心）の場所を、ブラウザの地図で開く
        self.look = QGroupBox("場所を確かめる（ブラウザで開く） / Check location in web browser")
        look_grid = QGridLayout(self.look)
        self.look_btns = []
        for i, (text, kind, tip) in enumerate([
                ("Google マップ", "google", "施設の点の場所にピンを立てて開く"),
                ("航空写真", "photo", "Google マップの航空写真で開く"),
                ("ストリートビュー", "street", "施設の点のいちばん近くのストリートビューを開く"),
                ("地理院地図", "gsi", "国土地理院の地図で開く"),
                ("名前で検索", "search", "施設の名前（と住所）で Google マップを検索する。"
                                        "Google が示す場所と、施設の点の場所を見比べられます")]):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=kind: self.open_map(k))
            look_grid.addWidget(b, i // 2, i % 2)
            self.look_btns.append(b)

        self.rematch_btn = QPushButton("照合し直して結果を更新 / Re-run matching")
        self.rematch_btn.clicked.connect(self.rematch)

        note = QLabel(
            "点を動かす・建物を選んで当てると、施設レイヤが編集中になります。残すときは QGIS の「編集内容を保存」を"
            "押してください（取り消しは Ctrl+Z）。直したあとは「照合し直して結果を更新」を押すと、結果のレイヤが新しくなります。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #555;")

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(self.summary)
        layout.addLayout(view_box)
        layout.addWidget(self.list, 1)
        layout.addLayout(nav)
        layout.addWidget(self.detail)
        layout.addWidget(self.accept_btn)
        layout.addWidget(self.click_btn)
        layout.addWidget(self.pick_btn)
        layout.addLayout(pick_box)
        layout.addWidget(self.look)
        layout.addSpacing(8)
        layout.addWidget(self.rematch_btn)
        layout.addWidget(note)
        self.setWidget(body)

        self.set_result(result)

    # ---- 一覧 -------------------------------------------------------------

    def set_result(self, result):
        self.checks = list(result.checks)
        self.matched = list(getattr(result, "matched", []))
        self.fixed = set()
        self.manual_set = {}
        # 要確認がなければ、照合できた施設を出しておく（照合し直したあとは、選んでいた表示のまま）
        if not self.checks and self.view.currentData() == CHECK:
            self.view.blockSignals(True)
            self.view.setCurrentIndex(self.view.findData(MATCHED))
            self.view.blockSignals(False)
        self.fill_list()

    def fill_list(self):
        mode = self.view.currentData()
        if mode == CHECK:
            self.items = list(self.checks)
        elif mode == MATCHED:
            self.items = list(self.matched)
        else:  # すべて：施設の順に並べる
            self.items = sorted(self.checks + self.matched, key=lambda c: c.point_fid)
        self.list.blockSignals(True)
        self.list.clear()
        for c in self.items:
            self.list.addItem(self.item_text(c))
        self.list.blockSignals(False)
        self.update_summary()
        if self.items:
            self.list.setCurrentRow(0)
        else:
            self.show_item(-1)

    def item_text(self, c):
        name = self.facility_name(c.point_fid)
        if c.point_fid in self.manual_set:
            return f"{FIXED}　{name}　（建物を {self.manual_set[c.point_fid]:,} 棟指定）"
        if c.point_fid in self.fixed:
            return f"{FIXED}　{name}"
        if c.status == MANUAL:
            return f"{name}　（手で指定・{len(c.building_fids):,} 棟）"
        if c.status == INSIDE:
            return f"{name}　（建物の中）"
        if c.status == SITE:
            return f"{name}　（敷地の中・{len(c.building_fids):,} 棟）"
        if c.status == NEAR:
            return f"{name}　（近くの建物で代用・{c.distance:.0f} m）"
        return f"{name}　（建物なし）"

    def facility_name(self, fid, fallback=True):
        f = self.points.getFeature(fid)
        name = _text(f.attribute(self.name_index)) if self.name_index is not None else ""
        return name or (f"施設 {fid}" if fallback else "")

    def facility_address(self, fid):
        if self.address_index is None:
            return ""
        return _text(self.points.getFeature(fid).attribute(self.address_index))

    def update_summary(self):
        n = len(self.checks)
        head = f"要確認の施設：{n:,} 件" if n else "要確認の施設はありません"
        if self.fixed:
            head += f"（直した {len(self.fixed):,} 件）"
        self.summary.setText(f"{head}。照合できた施設：{len(self.matched):,} 件。")

    def step(self, d):
        row = self.list.currentRow() + d
        if 0 <= row < self.list.count():
            self.list.setCurrentRow(row)

    def current(self):
        row = self.list.currentRow()
        return self.items[row] if 0 <= row < len(self.items) else None

    # ---- 地図に出す ----------------------------------------------------------

    def show_item(self, row):
        if self.pick_btn.isChecked():  # 別の施設に移ったら、選んでいる途中の建物は捨てる
            self.pick_btn.blockSignals(True)
            self.pick_btn.setChecked(False)
            self.pick_btn.blockSignals(False)
            self.end_pick()
        c = self.current()
        self.prev_btn.setEnabled(row > 0)
        self.next_btn.setEnabled(0 <= row < len(self.items) - 1)
        # 敷地（面）の施設は点ではないので、ここでは動かさない
        self.click_btn.setEnabled(c is not None and c.status != SITE)
        self.pick_btn.setEnabled(c is not None)
        self.pick_done.setEnabled(False)
        self.pick_clear.setEnabled(c is not None and bool(self.manual_value(c)))
        self.look.setEnabled(c is not None)
        self.band.reset(POLYGON)
        self.marker.hide()
        if c is None:
            self.detail.setText("")
            self.accept_btn.setEnabled(False)
            self.canvas.refresh()
            return

        buildings = self.building_geometries(c)
        building = _union(buildings)
        self.accept_btn.setEnabled(building is not None and c.status == NEAR)
        if c.point_fid in self.manual_set:
            self.detail.setText(f"建物を {self.manual_set[c.point_fid]:,} 棟指定しました（オレンジの枠）。"
                                "照合し直すと「手で指定」になります。")
        elif c.point_fid in self.fixed:
            self.detail.setText("直しました。ほかの場所がよければ、もう一度地図をクリックしてください。")
        elif c.status == MANUAL:
            self.detail.setText(
                f"手で指定した建物を {len(buildings):,} 棟当てています（オレンジの枠）。"
                "選び直すときは「建物を選んで当てる」、施設の位置で照合し直すときは「手での指定を外す」。")
        elif c.status == INSIDE:
            self.detail.setText(
                "施設の点（赤い丸）が建物（オレンジの枠）の中に入っているので、この建物を当てています。"
                "違う建物のときは「地図をクリックして直す」。迷ったら「場所を確かめる」で見比べてください。")
        elif c.status == SITE:
            self.detail.setText(
                f"敷地（施設の面）の中に建物が {len(buildings):,} 棟あり、すべて当てています（オレンジの枠）。"
                "違うときは、敷地の形を直してから照合し直してください。")
        elif c.status == NEAR:
            self.detail.setText(
                f"施設の点（赤い丸）が建物に入っていないため、{c.distance:.0f} m 先のいちばん近い建物"
                "（オレンジの枠）を当てています。正しければ「この建物でよい」、違えば「地図をクリックして直す」。")
        else:
            self.detail.setText(
                "施設の点（赤い丸）の近くに建物がありません。「地図をクリックして直す」で、"
                "正しい建物の中をクリックしてください。")

        for g in buildings:
            self.band.addGeometry(g, self.buildings)
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

    def building_geometries(self, c, fids=None):
        """当てはめた建物すべての形（敷地・手で指定のときは何棟もある）。"""
        out = []
        for fid in (c.building_fids if fids is None else fids):
            g = self.buildings.getFeature(fid).geometry()
            if g is not None and not g.isNull():
                out.append(QgsGeometry(g))
        return out

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

    # ---- 場所を確かめる --------------------------------------------------------

    def open_map(self, kind):
        c = self.current()
        if c is None:
            return
        if kind == "search":
            words = (self.facility_name(c.point_fid, fallback=False), self.facility_address(c.point_fid))
            text = " ".join(t for t in words if t)
            if not text:
                QMessageBox.information(self, TITLE, "この施設には名前の列がないため、検索できません。")
                return
            url = search_url(text)
        else:
            p = _to_point(self.points.getFeature(c.point_fid).geometry())
            if p is None:
                QMessageBox.information(self, TITLE, "この施設は位置が空のため、地図で開けません。")
                return
            lon, lat = to_lonlat(p, self.points.crs())
            url = url_for(kind, lon, lat)
        QDesktopServices.openUrl(QUrl(url))

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
        self.use_tool(self.tool, on)

    def use_tool(self, tool, on):
        """地図の道具を tool にする（on）／前の道具に戻す。"""
        if on:
            current = self.canvas.mapTool()
            if current not in (self.tool, self.pick_tool):
                self.prev_tool = current
            self.canvas.setMapTool(tool)
        elif self.canvas.mapTool() is tool:
            if self.prev_tool is not None:
                self.canvas.setMapTool(self.prev_tool)
            else:
                self.canvas.unsetMapTool(tool)

    def on_tool_changed(self, new_tool, old_tool=None):
        if new_tool is not self.tool and self.click_btn.isChecked():
            self.click_btn.blockSignals(True)
            self.click_btn.setChecked(False)
            self.click_btn.blockSignals(False)
        if new_tool is not self.pick_tool and self.pick_btn.isChecked():
            self.pick_btn.blockSignals(True)
            self.pick_btn.setChecked(False)
            self.pick_btn.blockSignals(False)
            self.end_pick()
            self.show_item(self.list.currentRow())

    # ---- 建物を選んで当てる -------------------------------------------------------

    def toggle_pick(self, on):
        c = self.current()
        if on and c is not None:
            self.pending = list(c.building_fids)  # 今当たっている建物から始める
            self.use_tool(self.pick_tool, True)
            self.show_pending()
        else:
            self.end_pick()
            self.show_item(self.list.currentRow())

    def end_pick(self):
        self.pending = None
        self.use_tool(self.pick_tool, False)

    def on_pick_click(self, point, button=None):
        if self.pending is None:
            return
        fid = self.building_at_canvas(point)
        if fid is None:
            return
        if fid in self.pending:
            self.pending.remove(fid)
        else:
            self.pending.append(fid)
        self.show_pending()

    def show_pending(self):
        self.band.reset(POLYGON)
        for g in self.building_geometries(None, self.pending):
            self.band.addGeometry(g, self.buildings)
        self.canvas.refresh()
        n = len(self.pending)
        self.pick_done.setEnabled(n > 0)
        self.pick_done.setText(f"選んだ建物に決める（{n:,} 棟）" if n else "選んだ建物に決める")
        self.detail.setText(f"選んでいる建物：{n:,} 棟（オレンジの枠）。地図の建物をクリックすると、選ぶ・外すを"
                            "切り替えられます。選び終わったら「選んだ建物に決める」。")

    def save_pick(self):
        c = self.current()
        if c is None or not self.pending:
            return
        req = QgsFeatureRequest().setFilterFids(self.pending)
        keys = {f.id(): building_key(f, self.key_field) for f in self.buildings.getFeatures(req)}
        value = KEY_SEP.join(keys[fid] for fid in self.pending if fid in keys)
        if not self.set_manual(c, value):
            return
        c.building_fids = list(self.pending)
        c.building_fid = self.pending[0]
        self.manual_set[c.point_fid] = len(self.pending)
        self.fixed.add(c.point_fid)
        self.pick_btn.setChecked(False)  # 選ぶのを終えて、表示を新しくする
        row = self.list.currentRow()
        self.list.item(row).setText(self.item_text(c))
        self.update_summary()
        self.show_item(row)

    def clear_manual(self):
        c = self.current()
        if c is None or not self.set_manual(c, ""):
            return
        self.manual_set.pop(c.point_fid, None)
        row = self.list.currentRow()
        self.list.item(row).setText(self.item_text(c))
        self.show_item(row)
        self.detail.setText("手での指定を外しました。照合し直すと、施設の位置で照合します。")

    def manual_value(self, c):
        i = self.points.fields().indexOf(MANUAL_FIELD)
        return _text(self.points.getFeature(c.point_fid).attribute(i)) if i >= 0 else ""

    def set_manual(self, c, value):
        """施設レイヤの「指定した建物」の列に value を入れる。列がなければ作る。"""
        layer = self.points
        if not self.start_editing("建物を当てられません"):
            return False
        i = layer.fields().indexOf(MANUAL_FIELD)
        if i < 0:
            if not value:
                return True
            if not layer.addAttribute(QgsField(MANUAL_FIELD, T_STRING)):
                QMessageBox.warning(self, TITLE, f"「{layer.name()}」に「{MANUAL_FIELD}」の列を作れませんでした。")
                return False
            layer.updateFields()
            i = layer.fields().indexOf(MANUAL_FIELD)
        layer.beginEditCommand("施設に建物を当てる")
        if layer.changeAttributeValue(c.point_fid, i, value or None):
            layer.endEditCommand()
            return True
        layer.destroyEditCommand()
        QMessageBox.warning(self, TITLE, "建物を当てられませんでした。")
        return False

    def start_editing(self, what):
        layer = self.points
        if layer.isEditable() or layer.startEditing():
            return True
        QMessageBox.warning(
            self, TITLE,
            f"「{layer.name()}」は編集できない形式のため、{what}。\n"
            "右クリック →「エクスポート」→「地物の保存」で GeoPackage に保存し、"
            "保存したレイヤで照合し直してください。")
        return False

    def building_at_canvas(self, point):
        """地図の点（地図の座標）が入る建物の fid。重なっていたら面積の小さいほう。"""
        g = QgsGeometry.fromPointXY(point)
        g.transform(QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), self.buildings.crs(),
                                           QgsProject.instance()))
        req = QgsFeatureRequest().setFilterRect(g.boundingBox()).setNoAttributes()
        hits = [f for f in self.buildings.getFeatures(req) if f.geometry().contains(g)]
        return min(hits, key=lambda f: f.geometry().area()).id() if hits else None

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
        if not self.start_editing("点を動かせません"):
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
        # 手で指定した建物があると点の位置が使われないので、外しておく
        if self.manual_value(c):
            self.set_manual(c, "")
        self.manual_set.pop(c.point_fid, None)

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
            f"照合し直しました。\n\n　建物の中：{r.inside:,} 件\n"
            + (f"　敷地の中：{r.site:,} 件\n" if r.site else "")
            + (f"　手で指定：{r.manual:,} 件\n" if r.manual else "")
            + f"　近くの建物で代用：{r.near:,} 件\n　建物なし：{r.none:,} 件\n\n結果のレイヤを新しくしました。")

    # ---- 閉じる ------------------------------------------------------------------

    def closeEvent(self, event):
        global _current
        if self.click_btn.isChecked():
            self.click_btn.setChecked(False)
        if self.pick_btn.isChecked():
            self.pick_btn.setChecked(False)
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


def _first_field(layer, names):
    """names のうち、レイヤにある最初の列の番号。なければ None。"""
    for n in names:
        i = layer.fields().indexOf(n)
        if i >= 0:
            return i
    return None


def _union(geoms):
    """形をまとめて1つにする（ズームの範囲を決めるのに使う）。なければ None。"""
    if not geoms:
        return None
    return geoms[0] if len(geoms) == 1 else QgsGeometry.unaryUnion(geoms)


def _combined(a, b):
    r = QgsRectangle(a)
    r.combineExtentWith(b)
    return r
