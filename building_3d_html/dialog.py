"""書き出しの画面。処理そのものは core/ に任せる。"""

import os
import re

from qgis.core import QgsFieldProxyModel, QgsMapLayerProxyModel, QgsSettings
from qgis.gui import QgsFieldComboBox, QgsFileWidget, QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from .core.html_builder import write_html
from .core.layer_export import export_layer

SETTINGS_KEY = "building_3d_html/last_dir"
DEFAULT_COLOR = "#7f9fc4"
GSI_ATTR = "地理院タイル"
PLATEAU_ATTR = "PLATEAU（国土交通省）"
HEIGHT_HINTS = ("measuredheight", "height", "高さ")


def _enum(owner, scoped, name):
    """Qt6・新しいQGIS（owner.scoped.name）と、古い書き方（owner.name）の両方に対応する。"""
    group = getattr(owner, scoped, None)
    if group is not None and hasattr(group, name):
        return getattr(group, name)
    return getattr(owner, name)


class Building3DHtmlDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("Building 3D HTML")
        self.setMinimumWidth(460)

        self.layer = QgsMapLayerComboBox()
        self.layer.setFilters(_enum(QgsMapLayerProxyModel, "Filter", "PolygonLayer"))
        active = iface.activeLayer()
        if active is not None and self.layer.findText(active.name()) >= 0:
            self.layer.setLayer(active)

        self.height_field = QgsFieldComboBox()
        self.height_field.setFilters(_enum(QgsFieldProxyModel, "Filter", "Numeric"))

        self.default_height = QDoubleSpinBox()
        self.default_height.setRange(0.1, 1000)
        self.default_height.setDecimals(1)
        self.default_height.setValue(10)
        self.default_height.setSuffix(" m")
        self.default_height.setToolTip("高さが空・0・マイナスの建物に使う高さ")

        self.title = QLineEdit()

        self.plateau = QCheckBox("出典に「PLATEAU（国土交通省）」を表示する")
        self.plateau.setChecked(True)

        self.output = QgsFileWidget()
        self.output.setStorageMode(_enum(QgsFileWidget, "StorageMode", "SaveFile"))
        self.output.setFilter("HTML (*.html)")
        self.output.setDialogTitle("書き出すHTMLファイル")

        form = QFormLayout()
        form.addRow("建物レイヤ", self.layer)
        form.addRow("高さの列", self.height_field)
        form.addRow("高さが空のとき", self.default_height)
        form.addRow("タイトル", self.title)
        form.addRow("", self.plateau)
        form.addRow("出力先", self.output)

        buttons = QDialogButtonBox(
            _enum(QDialogButtonBox, "StandardButton", "Ok")
            | _enum(QDialogButtonBox, "StandardButton", "Cancel"))
        buttons.button(_enum(QDialogButtonBox, "StandardButton", "Ok")).setText("書き出す")
        buttons.accepted.connect(self.export)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self.layer.layerChanged.connect(self.on_layer_changed)
        self.on_layer_changed(self.layer.currentLayer())

    def on_layer_changed(self, layer):
        self.height_field.setLayer(layer)
        if layer is None:
            return
        # 列名に height / 高さ / measuredHeight を含む数値列があれば自動で選ぶ
        for hint in HEIGHT_HINTS:
            match = next((f.name() for f in layer.fields()
                          if f.isNumeric() and hint in f.name().lower()), None)
            if match:
                self.height_field.setField(match)
                break
        self.title.setText(layer.name())
        last_dir = QgsSettings().value(SETTINGS_KEY, os.path.expanduser("~"))
        safe = re.sub(r'[\\/:*?"<>|]', "_", layer.name()) or "building3d"
        self.output.setFilePath(os.path.join(last_dir, safe + "_3d.html"))

    def export(self):
        layer = self.layer.currentLayer()
        field = self.height_field.currentField()
        path = self.output.filePath().strip()
        if layer is None:
            QMessageBox.warning(self, "Building 3D HTML", "建物レイヤ（面のレイヤ）を選んでください。")
            return
        if not field:
            QMessageBox.warning(self, "Building 3D HTML", "高さの列を選んでください。")
            return
        if not path:
            QMessageBox.warning(self, "Building 3D HTML", "出力先のファイルを指定してください。")
            return
        if not path.lower().endswith((".html", ".htm")):
            path += ".html"

        QApplication.setOverrideCursor(_enum(Qt, "CursorShape", "WaitCursor"))
        try:
            result = export_layer(layer, field, self.default_height.value())
            if result.count == 0:
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(self, "Building 3D HTML", "書き出せる建物がありませんでした。")
                return
            attribution = [PLATEAU_ATTR] if self.plateau.isChecked() else []
            config = {
                "title": self.title.text().strip() or layer.name(),
                "bbox": result.bbox,
                "color": DEFAULT_COLOR,
                "defaultHeight": self.default_height.value(),
                "attribution": attribution,
            }
            size = write_html(path, result.geojson(), config)
        except Exception as e:  # 失敗の理由をそのまま見せる
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Building 3D HTML", "書き出しに失敗しました。\n\n" + str(e))
            return
        QApplication.restoreOverrideCursor()

        QgsSettings().setValue(SETTINGS_KEY, os.path.dirname(path))
        self.show_done(path, result, size)
        self.accept()

    def show_done(self, path, result, size):
        lines = [
            f"書き出した建物：{result.count:,} 棟",
            f"高さを補った建物：{result.filled:,} 棟（{self.default_height.value():g} m で表示・半透明）",
        ]
        if result.skipped:
            lines.append(f"形が読めず除外した建物：{result.skipped:,} 棟")
        lines.append(f"ファイルサイズ：{size / 1024 / 1024:.1f} MB")
        lines.append("")
        lines.append(path)

        box = QMessageBox(self)
        box.setWindowTitle("書き出しました")
        box.setText("\n".join(lines))
        open_btn = box.addButton("ブラウザで開く", _enum(QMessageBox, "ButtonRole", "AcceptRole"))
        box.addButton("閉じる", _enum(QMessageBox, "ButtonRole", "RejectRole"))
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
