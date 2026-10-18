"""施設（点・敷地）と建物の照合（簡易版）の画面。処理そのものは core/matcher.py に任せる。"""

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFillSymbol,
    QgsMapLayerProxyModel,
    QgsMarkerSymbol,
    QgsProject,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
)
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from .core.matcher import INSIDE, NEAR, SITE, is_site_layer, match
from .dialog import _enum

TITLE = "点と建物の照合"
COLOR_INSIDE = "#7f9fc4"
COLOR_NEAR = "#ee9a4f"
COLOR_SITE = "#79b37f"
COLOR_NONE = "#cf3f4f"


class MatchDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(460)

        self.points = QgsMapLayerComboBox()
        self.points.setFilters(_enum(QgsMapLayerProxyModel, "Filter", "PointLayer")
                               | _enum(QgsMapLayerProxyModel, "Filter", "PolygonLayer"))
        self.buildings = QgsMapLayerComboBox()
        self.buildings.setFilters(_enum(QgsMapLayerProxyModel, "Filter", "PolygonLayer"))
        active = iface.activeLayer()
        if active is not None and self.points.findText(active.name()) >= 0:
            self.points.setLayer(active)

        self.selected_only = QCheckBox("選択中の施設だけ照合する")
        self.distance = QDoubleSpinBox()
        self.distance.setRange(0, 1000)
        self.distance.setDecimals(0)
        self.distance.setValue(30)
        self.distance.setSuffix(" m")
        self.distance.setToolTip("点がどの建物にも入っていないとき、この距離以内でいちばん近い建物を当てはめる（0なら当てはめない）")

        self.note = QLabel()
        self.note.setWordWrap(True)

        form = QFormLayout()
        form.addRow("施設（点・敷地の面）レイヤ", self.points)
        form.addRow("", self.selected_only)
        form.addRow("建物（面）レイヤ", self.buildings)
        form.addRow("近くの建物を探す距離", self.distance)

        buttons = QDialogButtonBox(
            _enum(QDialogButtonBox, "StandardButton", "Ok")
            | _enum(QDialogButtonBox, "StandardButton", "Cancel"))
        buttons.button(_enum(QDialogButtonBox, "StandardButton", "Ok")).setText("照合する")
        buttons.accepted.connect(self.run)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.note)
        layout.addWidget(buttons)

        self.points.layerChanged.connect(self.update_facility)
        self.update_facility(self.points.currentLayer())

    def update_facility(self, layer):
        sites = layer is not None and is_site_layer(layer)
        self.distance.setEnabled(not sites)
        if sites:
            self.note.setText(
                "施設が面（敷地）のレイヤなので、中心が敷地に入っている建物をすべて、"
                "その施設に当てはめます（1つの施設に何棟でも）。中に建物が1つもない敷地は「建物なし」になります。"
                "敷地が重なっていて1つの建物に複数の施設が当たったときは、1棟にまとめて施設の属性を「 / 」でつなぎます。")
        else:
            self.note.setText(
                "点が入っている建物を当てはめます。どの建物にも入っていない点は、"
                "上の距離以内でいちばん近い建物を当てはめ、「近くの建物で代用」の印を付けます。"
                "1つの建物に複数の施設が当たったときは、1棟にまとめて施設の属性を「 / 」でつなぎます。"
                "学校や団地など、1つの施設に何棟もあるときは、敷地を面で描いたレイヤを選んでください。")
        n = layer.selectedFeatureCount() if layer is not None else 0
        self.selected_only.setText(f"選択中の施設だけ照合する（{n:,} 件）")
        self.selected_only.setEnabled(n > 0)
        self.selected_only.setChecked(n > 0)

    def run(self):
        points, buildings = self.points.currentLayer(), self.buildings.currentLayer()
        if points is None or buildings is None:
            QMessageBox.warning(self, TITLE, "施設（点・敷地の面）レイヤと建物（面）レイヤを選んでください。")
            return
        if points.id() == buildings.id():
            QMessageBox.warning(self, TITLE, "施設のレイヤと建物のレイヤには、別のレイヤを選んでください。")
            return

        QApplication.setOverrideCursor(_enum(Qt, "CursorShape", "WaitCursor"))
        try:
            r = match(points, buildings, self.distance.value(), self.selected_only.isChecked())
        except Exception as e:  # 失敗の理由をそのまま見せる
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, TITLE, "照合に失敗しました。\n\n" + str(e))
            return
        QApplication.restoreOverrideCursor()

        sites = is_site_layer(points)
        _style_buildings(r.buildings)
        QgsProject.instance().addMapLayer(r.buildings)
        if r.unmatched.featureCount():
            _style_unmatched(r.unmatched, sites)
            QgsProject.instance().addMapLayer(r.unmatched)

        if sites:
            lines = [
                f"照合した施設（敷地）：{r.points:,} 件",
                f"　敷地の中に建物あり：{r.site:,} 件（建物 {r.site_buildings:,} 棟・緑色）",
                f"　建物なし：{r.none:,} 件（赤い面・要確認）",
            ]
        else:
            lines = [
                f"照合した施設：{r.points:,} 件",
                f"　建物の中：{r.inside:,} 件",
                f"　近くの建物で代用：{r.near:,} 件（オレンジ色・要確認）",
                f"　建物なし：{r.none:,} 件（赤い点・要確認）",
            ]
        if r.skipped:
            lines.append(f"　位置が空で照合できなかった施設：{r.skipped:,} 件")
        lines += [
            "",
            f"結果の建物：{r.buildings.featureCount():,} 棟（うち複数の施設が当たった建物 {r.shared:,} 棟）",
            "",
            "「照合結果_建物」「照合結果_建物なし」をレイヤに追加しました。",
            "どちらも一時レイヤです。残すときは右クリック →「エクスポート」で保存してください。",
            "",
            ("直すときは、敷地の形を直してから、もう一度照合してください。" if sites else
             "直すときは、施設の点を正しい建物の中に動かしてから、もう一度照合してください。"),
        ]
        QMessageBox.information(self, TITLE, "\n".join(lines))
        self.accept()


def _fill(color):
    return QgsFillSymbol.createSimple({"color": color, "outline_color": "#333333", "outline_width": "0.2"})


def _style_buildings(layer):
    """判定で色分けする：建物の中＝青、敷地の中＝緑、近くの建物で代用＝オレンジ。"""
    cats = [QgsRendererCategory(INSIDE, _fill(COLOR_INSIDE), INSIDE),
            QgsRendererCategory(SITE, _fill(COLOR_SITE), SITE),
            QgsRendererCategory(NEAR, _fill(COLOR_NEAR), NEAR + "（要確認）")]
    layer.setRenderer(QgsCategorizedSymbolRenderer("判定", cats))


def _style_unmatched(layer, sites=False):
    if sites:  # 敷地は塗りを薄くして、下の地図が見えるようにする
        symbol = QgsFillSymbol.createSimple({"color": "207,63,79,85", "outline_color": COLOR_NONE,
                                             "outline_width": "0.6"})
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        return
    symbol = QgsMarkerSymbol.createSimple({"name": "circle", "color": COLOR_NONE, "size": "3",
                                           "outline_color": "#ffffff"})
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
