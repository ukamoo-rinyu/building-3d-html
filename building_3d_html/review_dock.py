"""照合の要確認（近くの建物で代用・建物なし）を、地図で1件ずつ確認しながら直すパネル。

直し方：施設の点を、正しい建物の中へ動かす（施設レイヤを編集する）。
動かした点は、最後に照合し直すと「建物の中」になる。元の位置には「元に戻す」で戻せる。
"""

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsWkbTypes,
)
from qgis.gui import QgsMapToolIdentifyFeature, QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .core.matcher import NEAR, match, move_into_building, restore_point
from .dialog import _enum

TITLE = "照合の確認・修正"
COLOR_POINT = QColor("#cf3f4f")
COLOR_BUILDING = QColor("#ee9a4f")

# 確認の結果
TODO = "未確認"
MOVED = "直した"
KEPT = "そのまま"

try:
    LINE, POLYGON = Qgis.GeometryType.Line, Qgis.GeometryType.Polygon
except AttributeError:
    LINE, POLYGON = QgsWkbTypes.LineGeometry, QgsWkbTypes.PolygonGeometry

_dock = None  # 開いているパネル（1つだけ）


def open_review(iface, points, buildings, distance, selected_only, result, result_ids):
    """パネルを開く。施設レイヤを編集できなければ知らせて開かない。"""
    close_review()
    started = False
    if not points.isEditable():
        if not points.startEditing():
            QMessageBox.warning(iface.mainWindow(), TITLE,
                                f"施設レイヤ「{points.name()}」は点を動かせない形式です（CSV など）。\n"
                                "GeoPackage などに保存してから、もう一度照合してください。")
            return None
        started = True
    global _dock
    _dock = ReviewDock(iface, points, buildings, distance, selected_only, result, result_ids, started)
    iface.addDockWidget(_enum(Qt, "DockWidgetArea", "RightDockWidgetArea"), _dock)
    _dock.show()
    _dock.go(0)
    return _dock


def close_review():
    global _dock
    if _dock is not None:
        _dock.close()
        _dock = None


def _name(layer, feature):
    """レイヤの「表示名」（レイヤのプロパティ →「表示」）で、地物の名前を出す。"""
    ctx = QgsExpressionContext()
    ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    ctx.setFeature(feature)
    value = QgsExpression(layer.displayExpression()).evaluate(ctx)
    text = str(value).strip() if value not in (None, "") else ""
    return text if text and text != "NULL" else f"#{feature.id()}"


class ReviewDock(QDockWidget):
    def __init__(self, iface, points, buildings, distance, selected_only, result, result_ids, started):
        super().__init__(TITLE, iface.mainWindow())
        self.setObjectName("Building3DHtmlReview")
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.points, self.buildings = points, buildings
        self.distance, self.selected_only = distance, selected_only
        self.result_ids = result_ids
        self.started = started   # このパネルが施設レイヤの編集を始めたか
        self.finished = False
        self.items = result.review
        for it in self.items:
            it.state = TODO
            it.chosen = it.building  # 当てはめる建物（地図で選び直すと変わる）
            it.before = None         # 動かす前の点の形
        self.index = 0
        self.prev_tool = None
        self.pick_tool = None

        # 地図の上の目印：赤い丸＝施設の点、オレンジの枠＝当てはめる建物、点線＝点から建物まで
        self.marker = QgsVertexMarker(self.canvas)
        self.marker.setIconType(_enum(QgsVertexMarker, "IconType", "ICON_CIRCLE"))
        self.marker.setIconSize(18)
        self.marker.setPenWidth(3)
        self.marker.setColor(COLOR_POINT)
        self.band = QgsRubberBand(self.canvas, POLYGON)
        self.band.setStrokeColor(COLOR_BUILDING)
        fill = QColor(COLOR_BUILDING)
        fill.setAlpha(70)
        self.band.setFillColor(fill)
        self.band.setWidth(3)
        self.line = QgsRubberBand(self.canvas, LINE)
        self.line.setColor(COLOR_POINT)
        self.line.setWidth(2)
        self.line.setLineStyle(_enum(Qt, "PenStyle", "DashLine"))

        self._build_ui()
        self.refresh_list()

        self.watch_ids = {points.id(), buildings.id()}
        QgsProject.instance().layersWillBeRemoved.connect(self._layers_removed)

    # ---- 画面 ----
    def _build_ui(self):
        body = QWidget()
        lay = QVBoxLayout(body)

        self.progress = QLabel()
        self.progress.setWordWrap(True)
        lay.addWidget(self.progress)

        self.list = QListWidget()
        self.list.setMinimumHeight(140)
        self.list.currentRowChanged.connect(self._row_changed)
        lay.addWidget(self.list, 1)

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(_enum(Qt, "TextFormat", "RichText"))
        lay.addWidget(self.info)

        self.decide_btn = QPushButton("この建物に決める（点を建物の中へ移す）")
        self.decide_btn.setToolTip("施設の点を、オレンジの建物の中に動かします。照合し直すと「建物の中」になります")
        self.decide_btn.clicked.connect(self.decide)
        self.pick_btn = QPushButton("地図で別の建物を選ぶ")
        self.pick_btn.setCheckable(True)
        self.pick_btn.setToolTip("押したあと、地図で正しい建物をクリックします")
        self.pick_btn.toggled.connect(self.toggle_pick)
        self.keep_btn = QPushButton("動かさない（このままにする）")
        self.keep_btn.clicked.connect(self.keep)
        self.undo_btn = QPushButton("元に戻す")
        self.undo_btn.setToolTip("動かした点を元の位置に戻し、未確認にします")
        self.undo_btn.clicked.connect(self.undo)
        for b in (self.decide_btn, self.pick_btn, self.keep_btn, self.undo_btn):
            lay.addWidget(b)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀ 前へ")
        self.next_btn = QPushButton("次へ ▶")
        self.prev_btn.clicked.connect(lambda: self.go(self.index - 1))
        self.next_btn.clicked.connect(lambda: self.go(self.index + 1))
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.next_btn)
        lay.addLayout(nav)

        note = QLabel("地図の赤い丸が施設の点、オレンジが当てはめる建物です。"
                      "動かした点は、最後に「終わって照合し直す」で結果に反映されます。")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray")
        lay.addWidget(note)

        self.finish_btn = QPushButton("終わって照合し直す")
        self.finish_btn.clicked.connect(self.finish)
        lay.addWidget(self.finish_btn)
        self.setWidget(body)

    def _label(self, i, it):
        f = self.points.getFeature(it.point_fid)
        name = _name(self.points, f) if f.isValid() else f"#{it.point_fid}"
        kind = f"代用 {it.distance:.1f} m" if it.status == NEAR else "建物なし"
        return f"{i + 1}. [{it.state}] {name}（{kind}）"

    def refresh_list(self):
        self.list.blockSignals(True)
        self.list.clear()
        for i, it in enumerate(self.items):
            self.list.addItem(self._label(i, it))
        self.list.setCurrentRow(self.index)
        self.list.blockSignals(False)
        n = len(self.items)
        moved = sum(1 for it in self.items if it.state == MOVED)
        kept = sum(1 for it in self.items if it.state == KEPT)
        self.progress.setText(f"要確認 {n:,} 件（直した {moved:,}・そのまま {kept:,}・未確認 {n - moved - kept:,}）")

    def _row_changed(self, row):
        if 0 <= row < len(self.items) and row != self.index:
            self.go(row)

    # ---- 1件を表示する ----
    def go(self, i):
        if not self.items:
            self.info.setText("要確認の施設はありません。")
            return
        self.index = max(0, min(i, len(self.items) - 1))
        self.pick_btn.setChecked(False)
        self.list.blockSignals(True)
        self.list.setCurrentRow(self.index)
        self.list.blockSignals(False)
        self.show_current(zoom=True)

    def show_current(self, zoom=False):
        it = self.items[self.index]
        pf = self.points.getFeature(it.point_fid)
        if not pf.isValid() or not pf.hasGeometry():
            self.info.setText("この施設が見つかりません（削除されたか、位置が空です）。")
            self._clear_marks()
            return
        # 点は元の位置（動かしたあとでも、どこから動かしたか分かるように）
        geom = QgsGeometry(it.before) if it.before is not None else QgsGeometry(pf.geometry())
        geom.transform(self._to_canvas(self.points))
        p = geom.centroid().asPoint()
        self.marker.setCenter(QgsPointXY(p))
        self.marker.show()

        extent = QgsRectangle(p.x(), p.y(), p.x(), p.y())
        bname = "（まだ選んでいません。「地図で別の建物を選ぶ」で選べます）"
        self.band.reset(POLYGON)
        self.line.reset(LINE)
        if it.chosen is not None:
            bf = self.buildings.getFeature(it.chosen)
            if bf.isValid() and bf.hasGeometry():
                self.band.setToGeometry(bf.geometry(), self.buildings)
                bg = QgsGeometry(bf.geometry())
                bg.transform(self._to_canvas(self.buildings))
                extent.combineExtentWith(bg.boundingBox())
                inside = bg.pointOnSurface().asPoint()
                self.line.addPoint(QgsPointXY(p), False)
                self.line.addPoint(QgsPointXY(inside), True)
                bname = _name(self.buildings, bf)
                if it.chosen != it.building:
                    bname += "（地図で選び直した建物）"

        kind = (f"近くの建物で代用（建物まで {it.distance:.1f} m）" if it.status == NEAR
                else f"建物なし（{self.distance:g} m 以内に建物がない）")
        self.info.setText(
            f"<b>{self.index + 1} / {len(self.items)}　{_esc(_name(self.points, pf))}</b><br>"
            f"判定：{_esc(kind)}<br>当てはめる建物：{_esc(bname)}<br>状態：<b>{it.state}</b>")
        self.decide_btn.setEnabled(it.chosen is not None)
        self.undo_btn.setEnabled(it.state != TODO)
        self.prev_btn.setEnabled(self.index > 0)
        self.next_btn.setEnabled(self.index < len(self.items) - 1)
        if zoom:
            self._zoom(extent)

    def _to_canvas(self, layer):
        return QgsCoordinateTransform(layer.crs(), self.canvas.mapSettings().destinationCrs(), QgsProject.instance())

    def _zoom(self, rect):
        """点と建物が両方入り、まわりの建物も見える広さにする。"""
        half_m = max(60.0, self.distance * 2.5)
        half = half_m / 111000.0 if self.canvas.mapSettings().destinationCrs().isGeographic() else half_m
        c = rect.center()
        w = max(rect.width() * 1.6, half * 2)
        h = max(rect.height() * 1.6, half * 2)
        self.canvas.setExtent(QgsRectangle(c.x() - w / 2, c.y() - h / 2, c.x() + w / 2, c.y() + h / 2))
        self.canvas.refresh()

    def _clear_marks(self):
        self.marker.hide()
        self.band.reset(POLYGON)
        self.line.reset(LINE)

    # ---- 直す ----
    def decide(self):
        it = self.items[self.index]
        if it.chosen is None:
            return
        if not self.points.isEditable() and not self.points.startEditing():
            QMessageBox.warning(self, TITLE, "施設レイヤを編集できません。")
            return
        before = move_into_building(self.points, it.point_fid, self.buildings, it.chosen)
        if before is None:
            QMessageBox.warning(self, TITLE, "点を動かせませんでした。")
            return
        if it.before is None:  # 2回目以降も、いちばん最初の位置を覚えておく
            it.before = before
        it.state = MOVED
        self.points.triggerRepaint()
        self._next_todo()

    def keep(self):
        it = self.items[self.index]
        if it.before is not None:
            restore_point(self.points, it.point_fid, it.before)
            it.before = None
            self.points.triggerRepaint()
        it.state = KEPT
        self._next_todo()

    def undo(self):
        it = self.items[self.index]
        if it.before is not None:
            restore_point(self.points, it.point_fid, it.before)
            it.before = None
            self.points.triggerRepaint()
        it.state = TODO
        it.chosen = it.building
        self.refresh_list()
        self.show_current(zoom=True)

    def _next_todo(self):
        """次の未確認へ進む（なければ今の場所のまま）。"""
        self.refresh_list()
        n = len(self.items)
        for k in range(1, n + 1):
            j = (self.index + k) % n
            if self.items[j].state == TODO:
                self.go(j)
                return
        self.show_current()
        QMessageBox.information(self, TITLE, "すべて確認しました。「終わって照合し直す」を押すと結果に反映されます。")

    # ---- 地図で建物を選ぶ ----
    def toggle_pick(self, on):
        if on:
            self.prev_tool = self.canvas.mapTool()
            self.pick_tool = QgsMapToolIdentifyFeature(self.canvas, self.buildings)
            self.pick_tool.featureIdentified.connect(self._picked)
            self.pick_tool.deactivated.connect(self._pick_deactivated)
            self.canvas.setMapTool(self.pick_tool)
            self.pick_btn.setText("地図で建物をクリックしてください（やめるときはもう一度押す）")
        else:
            self.pick_btn.setText("地図で別の建物を選ぶ")
            tool, self.pick_tool = self.pick_tool, None
            if tool is not None and self.canvas.mapTool() == tool:
                self.canvas.unsetMapTool(tool)
                if self.prev_tool is not None:
                    self.canvas.setMapTool(self.prev_tool)

    def _pick_deactivated(self):
        """ほかの道具（移動など）に切り替えられたとき。地図の道具はそのままにしてボタンだけ戻す。"""
        self.pick_tool = None
        self.pick_btn.setChecked(False)

    def _picked(self, feature):
        it = self.items[self.index]
        it.chosen = feature.id()
        self.pick_btn.setChecked(False)
        self.show_current()

    # ---- 終わる ----
    def finish(self):
        from .match_dialog import add_results, summary_lines

        self.pick_btn.setChecked(False)
        try:
            r = match(self.points, self.buildings, self.distance, self.selected_only)
        except Exception as e:  # 失敗の理由をそのまま見せる
            QMessageBox.critical(self, TITLE, "照合し直せませんでした。\n\n" + str(e))
            return
        project = QgsProject.instance()
        project.removeMapLayers([i for i in self.result_ids if project.mapLayer(i) is not None])
        add_results(r)
        lines = summary_lines(r)
        if r.review:
            lines += ["", f"まだ要確認が {len(r.review):,} 件あります（動かさなかった施設など）。",
                      "もう一度「点と建物を照合する…」から確認できます。"]
        moved = any(it.state == MOVED for it in self.items)
        if moved:
            lines += ["", f"動かした点を、施設レイヤ「{self.points.name()}」に保存しますか？",
                      "（保存しないと、施設レイヤは編集中のままです。あとでQGISの「編集内容を保存」でも保存できます）"]
            box = QMessageBox(_enum(QMessageBox, "Icon", "Question"), TITLE, "\n".join(lines),
                              parent=self.iface.mainWindow())
            save_btn = box.addButton("保存する", _enum(QMessageBox, "ButtonRole", "AcceptRole"))
            box.addButton("保存しない", _enum(QMessageBox, "ButtonRole", "RejectRole"))
            box.exec()
            if box.clickedButton() == save_btn and not self.points.commitChanges():
                QMessageBox.warning(self.iface.mainWindow(), TITLE,
                                    "保存できませんでした。\n\n" + "\n".join(self.points.commitErrors()))
        else:
            if self.started:  # 動かした点がない（戻したものだけ）なら、編集も始める前に戻す
                self.points.rollBack()
            QMessageBox.information(self.iface.mainWindow(), TITLE, "\n".join(lines))
        self.finished = True
        close_review()

    def _layers_removed(self, ids):
        if self.watch_ids & set(ids):
            # 施設レイヤが残っていて、点を1つも動かしていなければ、始めた編集を取り消す
            if (self.points.id() not in ids and self.started
                    and not any(it.state == MOVED for it in self.items)):
                self.points.rollBack()
            self.finished = True
            close_review()

    def closeEvent(self, event):
        self.pick_btn.setChecked(False)
        for item in (self.marker, self.band, self.line):
            self.canvas.scene().removeItem(item)
        try:
            QgsProject.instance().layersWillBeRemoved.disconnect(self._layers_removed)
        except TypeError:
            pass
        if not self.finished:
            if any(it.state == MOVED for it in self.items):
                QMessageBox.information(self.iface.mainWindow(), TITLE,
                                        f"動かした点は、施設レイヤ「{self.points.name()}」に編集中のまま残っています。\n"
                                        "残すときはQGISの「編集内容を保存」、やめるときは「編集を取り消す」を使ってください。")
            elif self.started:
                self.points.rollBack()
        self.finished = True
        global _dock
        if _dock is self:
            _dock = None
        self.iface.removeDockWidget(self)
        super().closeEvent(event)
        self.deleteLater()


def _esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
