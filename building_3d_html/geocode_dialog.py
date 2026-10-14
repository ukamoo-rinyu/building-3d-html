"""住所から点を作る画面。処理そのものは core/geocoder.py に任せる。"""

import os

from qgis.core import QgsMapLayerProxyModel, QgsMarkerSymbol, QgsProject, QgsSingleSymbolRenderer
from qgis.gui import QgsFieldComboBox, QgsFileWidget, QgsMapLayerComboBox
from qgis.PyQt.QtCore import QSettings, Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from .core.geocoder import AddressBook, geocode
from .dialog import _enum

TITLE = "住所から点を作る"
SETTING_FOLDER = "building_3d_html/isj_folder"
ADDRESS_FIELDS = ("住所", "所在地", "address", "ADDRESS", "Address")

_books = {}  # 読み込んだ位置参照情報（フォルダ → AddressBook）。同じフォルダは読み直さない


class GeocodeDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(480)

        self.layer = QgsMapLayerComboBox()
        self.layer.setFilters(_enum(QgsMapLayerProxyModel, "Filter", "VectorLayer"))
        active = iface.activeLayer()
        if active is not None and self.layer.findText(active.name()) >= 0:
            self.layer.setLayer(active)
        self.selected_only = QCheckBox("選択中の施設だけ")
        self.field = QgsFieldComboBox()

        self.folder = QgsFileWidget()
        self.folder.setStorageMode(_enum(QgsFileWidget, "StorageMode", "GetDirectory"))
        self.folder.setFilePath(QSettings().value(SETTING_FOLDER, "", type=str))

        note = QLabel(
            "国土交通省「位置参照情報」（街区レベル）を使って、住所から点を作ります。ネットにはつなぎません。"
            "ダウンロードした zip（展開しなくてよい）を1つのフォルダに入れて、そのフォルダを選んでください。"
            "「〇丁目〇番」まで見つかれば街区の位置、「〇丁目」までなら丁目のおおよその位置に点を置きます。")
        note.setWordWrap(True)

        form = QFormLayout()
        form.addRow("施設のレイヤ（表）", self.layer)
        form.addRow("", self.selected_only)
        form.addRow("住所の列", self.field)
        form.addRow("位置参照情報のフォルダ", self.folder)

        buttons = QDialogButtonBox(
            _enum(QDialogButtonBox, "StandardButton", "Ok")
            | _enum(QDialogButtonBox, "StandardButton", "Cancel"))
        buttons.button(_enum(QDialogButtonBox, "StandardButton", "Ok")).setText("点を作る")
        buttons.accepted.connect(self.run)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

        self.layer.layerChanged.connect(self.update_layer)
        self.update_layer(self.layer.currentLayer())

    def update_layer(self, layer):
        self.field.setLayer(layer)
        if layer is not None:
            for name in ADDRESS_FIELDS:
                if layer.fields().indexOf(name) >= 0:
                    self.field.setField(name)
                    break
        n = layer.selectedFeatureCount() if layer is not None else 0
        self.selected_only.setText(f"選択中の施設だけ（{n:,} 件）")
        self.selected_only.setEnabled(n > 0)
        self.selected_only.setChecked(n > 0)

    def run(self):
        layer, field, folder = self.layer.currentLayer(), self.field.currentField(), self.folder.filePath()
        if layer is None or not field:
            QMessageBox.warning(self, TITLE, "施設のレイヤと住所の列を選んでください。")
            return
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, TITLE, "位置参照情報のフォルダを選んでください。")
            return
        QSettings().setValue(SETTING_FOLDER, folder)

        QApplication.setOverrideCursor(_enum(Qt, "CursorShape", "WaitCursor"))
        try:
            book = _books.get(folder)
            if book is None:
                book = AddressBook().load(folder)
                if book.files:
                    _books[folder] = book
            if not book.files:
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(
                    self, TITLE,
                    "フォルダの中に位置参照情報（街区レベル）の CSV や zip が見つかりませんでした。\n"
                    "「大字・町丁目レベル」ではなく「街区レベル」をダウンロードしてください。")
                return
            r = geocode(layer, field, book, self.selected_only.isChecked())
        except Exception as e:  # 失敗の理由をそのまま見せる
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, TITLE, "点を作れませんでした。\n\n" + str(e))
            return
        QApplication.restoreOverrideCursor()

        symbol = QgsMarkerSymbol.createSimple({"name": "circle", "color": "#2e7d32", "size": "2.6",
                                               "outline_color": "#ffffff"})
        r.layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        QgsProject.instance().addMapLayer(r.layer)
        self.iface.setActiveLayer(r.layer)

        lines = [
            f"点を作った施設：{r.block + r.town:,} 件",
            f"　「〇丁目〇番」まで見つかった：{r.block:,} 件",
            f"　「〇丁目」まで見つかった：{r.town:,} 件（丁目のおおよその位置）",
            f"住所が見つからなかった施設：{r.none:,} 件（位置は空）",
        ]
        if r.examples:
            lines += ["　例：" + "、".join(r.examples)]
        lines += [
            "",
            f"「{r.layer.name()}」をレイヤに追加しました（一時レイヤです。残すときは右クリック →「エクスポート」）。",
            "どの細かさで見つかったかは「住所の一致」の列に入っています。",
            "",
            "点は街区の代表の位置なので、建物の中に入るとは限りません。",
            "続けて「点と建物を照合する…」で建物と結び付け、要確認のものを地図で直してください。",
        ]
        QMessageBox.information(self, TITLE, "\n".join(lines))
        self.accept()
