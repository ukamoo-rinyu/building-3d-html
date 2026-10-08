"""書き出しの画面。処理そのものは core/ に任せる。"""

import os
import re

from qgis.core import QgsFieldProxyModel, QgsMapLayerProxyModel, QgsSettings
from qgis.gui import QgsFieldComboBox, QgsFileWidget, QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .core.html_builder import write_html
from .core.layer_export import export_layer

SETTINGS_KEY = "building_3d_html/last_dir"
DEFAULT_COLOR = "#7f9fc4"
GSI_ATTR = "地理院タイル"
PLATEAU_ATTR = "PLATEAU（国土交通省）"
HEIGHT_HINTS = ("measuredheight", "height", "高さ")
NAME_HINTS = ("名称", "name", "名")


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
        self.setMinimumWidth(520)

        self.layer = QgsMapLayerComboBox()
        self.layer.setFilters(_enum(QgsMapLayerProxyModel, "Filter", "PolygonLayer"))
        active = iface.activeLayer()
        if active is not None and self.layer.findText(active.name()) >= 0:
            self.layer.setLayer(active)

        self.target_all = QRadioButton("全件")
        self.target_sel = QRadioButton("選択中の地物のみ")
        self.target_group = QButtonGroup(self)
        self.target_group.addButton(self.target_all)
        self.target_group.addButton(self.target_sel)
        target_box = QHBoxLayout()
        target_box.addWidget(self.target_all)
        target_box.addWidget(self.target_sel)
        target_box.addStretch()

        self.height_field = QgsFieldComboBox()
        self.height_field.setFilters(_enum(QgsFieldProxyModel, "Filter", "Numeric"))

        self.default_height = QDoubleSpinBox()
        self.default_height.setRange(0.1, 1000)
        self.default_height.setDecimals(1)
        self.default_height.setValue(10)
        self.default_height.setSuffix(" m")
        self.default_height.setToolTip("高さが空・0・マイナスの建物に使う高さ")

        self.name_field = QgsFieldComboBox()
        self.name_field.setAllowEmptyFieldName(True)
        self.name_field.setToolTip("建物をクリックしたとき、パネルの見出しに出す列")

        # 詳細に出す列：チェック・列名・別名（パネルに出す項目名）
        self.detail = QTableWidget(0, 2)
        self.detail.setHorizontalHeaderLabels(["列（チェックした列を出す）", "別名（空欄なら列名のまま）"])
        self.detail.verticalHeader().setVisible(False)
        self.detail.horizontalHeader().setSectionResizeMode(_enum(QHeaderView, "ResizeMode", "Stretch"))
        self.detail.setSelectionMode(_enum(QAbstractItemView, "SelectionMode", "NoSelection"))
        self.detail.setMinimumHeight(180)
        check_all = QPushButton("すべて選ぶ")
        check_none = QPushButton("すべて外す")
        check_all.clicked.connect(lambda: self.set_all_checked(True))
        check_none.clicked.connect(lambda: self.set_all_checked(False))
        detail_buttons = QHBoxLayout()
        detail_buttons.addWidget(QLabel("詳細に出す列"))
        detail_buttons.addStretch()
        detail_buttons.addWidget(check_all)
        detail_buttons.addWidget(check_none)

        self.title = QLineEdit()

        self.plateau = QCheckBox("出典に「PLATEAU（国土交通省）」を表示する")
        self.plateau.setChecked(True)

        self.output = QgsFileWidget()
        self.output.setStorageMode(_enum(QgsFileWidget, "StorageMode", "SaveFile"))
        self.output.setFilter("HTML (*.html)")
        self.output.setDialogTitle("書き出すHTMLファイル")

        form = QFormLayout()
        form.addRow("建物レイヤ", self.layer)
        form.addRow("対象", target_box)
        form.addRow("高さの列", self.height_field)
        form.addRow("高さが空のとき", self.default_height)
        form.addRow("表示名の列", self.name_field)

        form2 = QFormLayout()
        form2.addRow("タイトル", self.title)
        form2.addRow("", self.plateau)
        form2.addRow("出力先", self.output)

        buttons = QDialogButtonBox(
            _enum(QDialogButtonBox, "StandardButton", "Ok")
            | _enum(QDialogButtonBox, "StandardButton", "Cancel"))
        buttons.button(_enum(QDialogButtonBox, "StandardButton", "Ok")).setText("書き出す")
        buttons.accepted.connect(self.export)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(detail_buttons)
        layout.addWidget(self.detail)
        layout.addLayout(form2)
        layout.addWidget(buttons)

        self.layer.layerChanged.connect(self.on_layer_changed)
        self.on_layer_changed(self.layer.currentLayer())

    def on_layer_changed(self, layer):
        self.height_field.setLayer(layer)
        self.name_field.setLayer(layer)
        self.fill_detail(layer)
        self.update_target(layer)
        if layer is None:
            return
        # 列名に height / 高さ / measuredHeight を含む数値列があれば自動で選ぶ
        for hint in HEIGHT_HINTS:
            match = next((f.name() for f in layer.fields()
                          if f.isNumeric() and hint in f.name().lower()), None)
            if match:
                self.height_field.setField(match)
                break
        # 表示名の列：列名に 名称 / name / 名 を含む文字の列があれば自動で選ぶ
        self.name_field.setField("")
        for hint in NAME_HINTS:
            match = next((f.name() for f in layer.fields()
                          if not f.isNumeric() and hint in f.name().lower()), None)
            if match:
                self.name_field.setField(match)
                break
        # gpkg から追加したレイヤ名「ファイル名 — レイヤ名」は、レイヤ名の部分をタイトルにする
        short_name = layer.name().split(" — ")[-1]
        self.title.setText(short_name)
        last_dir = QgsSettings().value(SETTINGS_KEY, os.path.expanduser("~"))
        safe = re.sub(r'[\\/:*?"<>|]', "_", short_name) or "building3d"
        self.output.setFilePath(os.path.join(last_dir, safe + "_3d.html"))

    def update_target(self, layer):
        """選択があれば「選択中の地物のみ」を既定にする。選択がなければ選べなくする。"""
        n = layer.selectedFeatureCount() if layer is not None else 0
        self.target_sel.setText(f"選択中の地物のみ（{n:,} 件）")
        self.target_sel.setEnabled(n > 0)
        (self.target_sel if n > 0 else self.target_all).setChecked(True)

    def fill_detail(self, layer):
        """レイヤの列を、レイヤの列順で並べる。最初は全部チェック。別名は QGIS の別名を入れておく。"""
        self.detail.setRowCount(0)
        if layer is None:
            return
        checkable = _enum(Qt, "ItemFlag", "ItemIsUserCheckable") | _enum(Qt, "ItemFlag", "ItemIsEnabled")
        # GeoPackage の fid などの自動の番号は、見る人に意味がないので最初は外しておく
        auto_ids = set(layer.primaryKeyAttributes())
        for i, field in enumerate(layer.fields()):
            self.detail.insertRow(i)
            item = QTableWidgetItem(field.name())
            item.setFlags(checkable)
            off = i in auto_ids or field.name().lower() == "fid"
            item.setCheckState(_enum(Qt, "CheckState", "Unchecked" if off else "Checked"))
            self.detail.setItem(i, 0, item)
            self.detail.setItem(i, 1, QTableWidgetItem(layer.attributeAlias(i)))
        self.detail.resizeRowsToContents()

    def set_all_checked(self, checked):
        state = _enum(Qt, "CheckState", "Checked" if checked else "Unchecked")
        for i in range(self.detail.rowCount()):
            self.detail.item(i, 0).setCheckState(state)

    def detail_columns(self):
        """チェックした列の [(列名, 別名), ...]。別名が空なら列名。"""
        checked = _enum(Qt, "CheckState", "Checked")
        cols = []
        for i in range(self.detail.rowCount()):
            item = self.detail.item(i, 0)
            if item.checkState() == checked:
                alias = self.detail.item(i, 1)
                label = alias.text().strip() if alias is not None else ""
                cols.append((item.text(), label or item.text()))
        return cols

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
            name_field = self.name_field.currentField()
            cols = self.detail_columns()
            keep = list(dict.fromkeys(([name_field] if name_field else []) + [c[0] for c in cols]))
            result = export_layer(layer, field, self.default_height.value(),
                                  selected_only=self.target_sel.isChecked(), fields=keep)
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
                "nameKey": result.keys.get(name_field),
                "columns": [[result.keys[n], label] for n, label in cols if n in result.keys],
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
