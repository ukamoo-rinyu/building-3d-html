"""書き出しの画面。処理そのものは core/ に任せる。"""

import os
import re

from qgis.core import QgsFieldProxyModel, QgsMapLayerProxyModel, QgsSettings
from qgis.gui import QgsColorButton, QgsFieldComboBox, QgsFileWidget, QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QColor, QDesktopServices
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .core.html_builder import write_html
from .core.layer_export import export_layer
from .core.style_reader import StyleReader, is_supported, renderer_name
from .core.surroundings import export_surroundings

SETTINGS_KEY = "building_3d_html/last_dir"
DEFAULT_COLOR = "#7f9fc4"
PLATEAU_ATTR = "PLATEAU（国土交通省）"
HEIGHT_HINTS = ("measuredheight", "height", "高さ")
NAME_HINTS = ("名称", "name", "名")
PLAN_COLOR = "#e4572e"
MANY_CATEGORIES = 20  # これを超える分類は「凡例が長くなる」と知らせる（書き出しは止めない）


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
        self.setMinimumWidth(600)

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
        self.detail.setMinimumHeight(150)
        check_all = QPushButton("すべて選ぶ")
        check_none = QPushButton("すべて外す")
        check_all.clicked.connect(lambda: self.set_all_checked(True))
        check_none.clicked.connect(lambda: self.set_all_checked(False))
        detail_buttons = QHBoxLayout()
        detail_buttons.addWidget(QLabel("詳細に出す列"))
        detail_buttons.addStretch()
        detail_buttons.addWidget(check_all)
        detail_buttons.addWidget(check_none)

        # 色：QGIS のスタイルに合わせる／単色
        self.color_style = QRadioButton("QGISのスタイルに合わせる")
        self.color_single = QRadioButton("単色")
        self.color_group = QButtonGroup(self)
        self.color_group.addButton(self.color_style)
        self.color_group.addButton(self.color_single)
        self.color_button = QgsColorButton()
        self.color_button.setColor(QColor(DEFAULT_COLOR))
        self.color_button.setShowNoColor(False)
        self.color_button.setAllowOpacity(False)
        self.color_button.setMaximumWidth(80)
        self.color_single.toggled.connect(self.color_button.setEnabled)
        color_box = QHBoxLayout()
        color_box.addWidget(self.color_style)
        color_box.addWidget(self.color_single)
        color_box.addWidget(self.color_button)
        color_box.addStretch()
        self.color_note = QLabel()
        self.color_note.setWordWrap(True)

        # 計画建物・周辺の建物（どちらも、なくてもよい）
        self.plan_layer, self.plan_height = self._optional_layer("計画建物は使わない")
        self.sur_layer, self.sur_height = self._optional_layer("周辺の建物は使わない")
        self.sur_distance = QDoubleSpinBox()
        self.sur_distance.setRange(10, 5000)
        self.sur_distance.setDecimals(0)
        self.sur_distance.setValue(200)
        self.sur_distance.setSuffix(" m 以内")
        self.sur_distance.setToolTip("書き出す建物からこの距離以内の周辺の建物を、灰色で立ち上げる")
        plan_box = QHBoxLayout()
        plan_box.addWidget(self.plan_layer, 2)
        plan_box.addWidget(QLabel("高さの列"))
        plan_box.addWidget(self.plan_height, 1)
        sur_box = QHBoxLayout()
        sur_box.addWidget(self.sur_layer, 2)
        sur_box.addWidget(QLabel("高さの列"))
        sur_box.addWidget(self.sur_height, 1)
        for combo in (self.plan_layer, self.sur_layer):
            combo.setMinimumWidth(200)
        dist_box = QHBoxLayout()
        dist_box.addWidget(self.sur_distance)
        dist_box.addStretch()

        # 「一緒に表示するレイヤ」の枠。それぞれ何に使うかを書いておく
        extra = QGroupBox("一緒に表示するレイヤ（使わないときは空欄のまま）")
        extra_form = QFormLayout(extra)
        extra_form.addRow(_note("計画建物：これから建てる建物。半透明で立ち上げ、HTMLでオン・オフして今と見比べられます"
                                "（上の建物レイヤとは別のレイヤ）。"))
        extra_form.addRow("計画建物", plan_box)
        extra_form.addRow(_note("周辺の建物：まわりの建物（PLATEAUなど）。指定の範囲だけを灰色の背景として立ち上げます。"))
        extra_form.addRow("周辺の建物", sur_box)
        extra_form.addRow("周辺の範囲", dist_box)

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
        form2.addRow("色", color_box)
        form2.addRow("", self.color_note)
        form2.addRow("タイトル", self.title)
        form2.addRow("", self.plateau)
        form2.addRow("出力先", self.output)

        buttons = QDialogButtonBox(
            _enum(QDialogButtonBox, "StandardButton", "Ok")
            | _enum(QDialogButtonBox, "StandardButton", "Cancel"))
        buttons.button(_enum(QDialogButtonBox, "StandardButton", "Ok")).setText("書き出す")
        buttons.accepted.connect(self.export)
        buttons.rejected.connect(self.reject)

        # 中身はスクロールできるようにし、「書き出す」ボタンは常に下に見えるようにする
        content = QWidget()
        inner = QVBoxLayout(content)
        inner.setContentsMargins(0, 0, 6, 0)
        inner.addLayout(form)
        inner.addLayout(detail_buttons)
        inner.addWidget(self.detail)
        inner.addWidget(extra)
        inner.addLayout(form2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_enum(QFrame, "Shape", "NoFrame"))
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(buttons)
        self._fit_to_screen(content)

        self.layer.layerChanged.connect(self.on_layer_changed)
        self.on_layer_changed(self.layer.currentLayer())

    def on_layer_changed(self, layer):
        self.height_field.setLayer(layer)
        self.name_field.setLayer(layer)
        self.fill_detail(layer)
        self.update_target(layer)
        self.update_color(layer)
        self.update_plan(layer)
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

    def _fit_to_screen(self, content):
        """開いたときの大きさを、画面に収まる範囲にする（はみ出す分はスクロール）。"""
        screen = QApplication.primaryScreen()
        if self.parent() is not None and hasattr(self.parent(), "screen") and self.parent().screen():
            screen = self.parent().screen()
        avail = screen.availableGeometry()
        want = content.sizeHint().height() + 70
        self.resize(min(680, int(avail.width() * 0.9)), min(want, int(avail.height() * 0.88)))

    def update_plan(self, main):
        """計画建物の欄には、建物レイヤと同じレイヤを出さない（同じ建物が二重に描かれるため）。
        まだ選んでいなければ、レイヤ名に「計画」を含む別の面レイヤを選んでおく。"""
        self.plan_layer.setExceptedLayerList([main] if main is not None else [])
        current = self.plan_layer.currentLayer()
        if current is not None and current is main:
            self.plan_layer.setLayer(None)
        if self.plan_layer.currentLayer() is None:
            for i in range(self.plan_layer.count()):
                lyr = self.plan_layer.layer(i)
                if lyr is not None and lyr is not main and "計画" in lyr.name():
                    self.plan_layer.setLayer(lyr)
                    break

    def _optional_layer(self, empty_text):
        """「使わない」を選べる面レイヤのプルダウンと、その高さの列。"""
        combo = QgsMapLayerComboBox()
        combo.setFilters(_enum(QgsMapLayerProxyModel, "Filter", "PolygonLayer"))
        combo.setAllowEmptyLayer(True, empty_text)
        combo.setLayer(None)
        field = QgsFieldComboBox()
        field.setFilters(_enum(QgsFieldProxyModel, "Filter", "Numeric"))
        field.setEnabled(False)

        def changed(layer):
            field.setLayer(layer)
            field.setEnabled(layer is not None)
            if layer is not None:
                match = _guess_field(layer, HEIGHT_HINTS, numeric=True)
                if match:
                    field.setField(match)
        combo.layerChanged.connect(changed)
        return combo, field

    def update_color(self, layer):
        """対応している色分けなら「QGISのスタイル」を既定にする。対応していなければ単色だけにする。"""
        ok = layer is not None and is_supported(layer)
        self.color_style.setEnabled(ok)
        (self.color_style if ok else self.color_single).setChecked(True)
        self.color_button.setEnabled(not ok)
        if layer is None:
            self.color_note.setText("")
        elif ok:
            self.color_note.setText(f"このレイヤの色分け：{renderer_name(layer)}")
        else:
            self.color_note.setText(f"このレイヤの色分け（{renderer_name(layer)}）にはまだ対応していないため、単色で書き出します。"
                                    "対応しているのは「単一シンボル」と「分類」です。")

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
            style = StyleReader(layer) if self.color_style.isChecked() else None
            if style is not None and len(style.items) > MANY_CATEGORIES:
                QApplication.restoreOverrideCursor()
                QMessageBox.information(
                    self, "Building 3D HTML",
                    f"分類が {len(style.items)} 個あるため、凡例が長くなります。\n"
                    "HTMLでは凡例をスクロールして全件を見られます。このまま書き出します。")
                QApplication.setOverrideCursor(_enum(Qt, "CursorShape", "WaitCursor"))
            color = self.color_button.color().name()
            result = export_layer(layer, field, self.default_height.value(),
                                  selected_only=self.target_sel.isChecked(), fields=keep,
                                  style=style, color=color)
            if result.count == 0:
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(self, "Building 3D HTML", "書き出せる建物がありませんでした。")
                return
            attribution = [PLATEAU_ATTR] if self.plateau.isChecked() else []
            config = {
                "title": self.title.text().strip() or layer.name(),
                "bbox": result.bbox,
                "legend": result.legend,
                "defaultHeight": self.default_height.value(),
                "attribution": attribution,
                "nameKey": result.keys.get(name_field),
                "columns": [[result.keys[n], label] for n, label in cols if n in result.keys],
            }
            plan, sur = self.export_plan(), None
            if plan is not None:
                config["plan"] = plan["config"]
                config["bbox"] = _union_bbox(config["bbox"], plan["result"].bbox)
            sur_layer = self.sur_layer.currentLayer()
            if sur_layer is not None:
                sur = export_surroundings(sur_layer, self.sur_height.currentField(),
                                          self.default_height.value(), result.geojson(),
                                          self.sur_distance.value())
            self.extra_lines = []
            if plan is not None:
                self.extra_lines.append(f"計画建物：{plan['result'].count:,} 棟（半透明で表示）")
            if sur is not None:
                self.extra_lines.append(
                    f"周辺の建物：{sur.count:,} 棟（{self.sur_distance.value():g} m 以内・灰色）"
                    + (f"　同じ建物のため除いた {sur.same:,} 棟" if sur.same else ""))
            size = write_html(path, result.geojson(), config,
                              surroundings=sur.geojson() if sur is not None else None,
                              plan=plan["result"].geojson() if plan is not None else None)
        except Exception as e:  # 失敗の理由をそのまま見せる
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Building 3D HTML", "書き出しに失敗しました。\n\n" + str(e))
            return
        QApplication.restoreOverrideCursor()

        QgsSettings().setValue(SETTINGS_KEY, os.path.dirname(path))
        self.show_done(path, result, size)
        self.accept()

    def export_plan(self):
        """計画建物を書き出す。名称の列は見出しに、ほかの列（fid を除く）は詳細に出す。"""
        layer = self.plan_layer.currentLayer()
        if layer is None:
            return None
        cols = [f.name() for i, f in enumerate(layer.fields())
                if i not in set(layer.primaryKeyAttributes()) and f.name().lower() != "fid"]
        name = _guess_field(layer, NAME_HINTS, numeric=False)
        r = export_layer(layer, self.plan_height.currentField(), self.default_height.value(),
                         fields=cols, color=PLAN_COLOR)
        if r.count == 0:
            return None
        return {"result": r, "config": {
            "color": PLAN_COLOR,
            "nameKey": r.keys.get(name),
            "columns": [[r.keys[n], n] for n in cols if n in r.keys and n != name],
        }}

    def show_done(self, path, result, size):
        lines = [
            f"書き出した建物：{result.count:,} 棟",
            f"高さを補った建物：{result.filled:,} 棟（{self.default_height.value():g} m で表示・半透明）",
        ]
        if result.hidden:
            lines.append(f"QGISで表示されない分類のため除外した建物：{result.hidden:,} 棟")
        if result.skipped:
            lines.append(f"形が読めず除外した建物：{result.skipped:,} 棟")
        lines += getattr(self, "extra_lines", [])
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


def _note(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: gray;")
    return label


def _guess_field(layer, hints, numeric):
    """列名にヒントの語を含む列を探す（ヒントの順に優先）。"""
    for hint in hints:
        for f in layer.fields():
            if f.isNumeric() == numeric and hint in f.name().lower():
                return f.name()
    return None


def _union_bbox(a, b):
    if not b:
        return a
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
