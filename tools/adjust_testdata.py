"""照合の確認・調整パネルを、QGISの画面を開かずに試す（動作確認用）。

testdata/試し_施設.gpkg（tools/match_testdata.py で作る）を作業用フォルダへコピーして照合し、
  1. 「近くの建物で代用」の1件目で「この建物でよい」を押す
  2. 「建物なし」の1件目で、地図の建物の中をクリックする
  3. 「照合し直して結果を更新」を押す
と、要確認が2件減ることを確かめる。元の testdata は変えない。

使い方（PowerShell）:
  & "C:\\Program Files\\QGIS 3.44.11\\bin\\python-qgis-ltr.bat" tools\\adjust_testdata.py
"""

import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import QgsApplication, QgsCoordinateTransform, QgsProject, QgsVectorLayer  # noqa: E402
from qgis.gui import QgsMapCanvas  # noqa: E402
from qgis.PyQt.QtCore import Qt  # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow, QMessageBox  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GPKG = os.path.join(ROOT, "testdata", "shiei_jutaku_3d_trial.gpkg")
POINTS_GPKG = os.path.join(ROOT, "testdata", "試し_施設.gpkg")


class FakeIface:
    """パネルが使う分だけの iface。"""

    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.window.setCentralWidget(self.canvas)
        self.canvas.resize(800, 600)

    def mainWindow(self):
        return self.window

    def mapCanvas(self):
        return self.canvas

    def addDockWidget(self, area, dock):
        self.window.addDockWidget(area, dock)


def run(work):
    from building_3d_html import adjust_dock
    from building_3d_html.core.matcher import NEAR, NONE, match
    from building_3d_html.match_dialog import add_result_layers

    QMessageBox.information = staticmethod(lambda *a, **k: None)  # 完了の知らせは出さない
    QMessageBox.warning = staticmethod(lambda *a, **k: print("  警告：", a[2]))

    copy = os.path.join(work, "施設.gpkg")
    shutil.copy(POINTS_GPKG, copy)
    project = QgsProject.instance()
    buildings = QgsVectorLayer(f"{GPKG}|layername=市営住宅_住棟", "建物", "ogr")
    points = QgsVectorLayer(f"{copy}|layername=試し_施設", "施設", "ogr")
    project.addMapLayers([buildings, points])

    iface = FakeIface()
    iface.canvas.setDestinationCrs(buildings.crs())
    iface.canvas.setLayers([points, buildings])

    params = {"points": points, "buildings": buildings, "distance": 30, "selected_only": False}
    r = match(points, buildings, 30)
    ids = add_result_layers(r)
    dock = adjust_dock.open_dock(iface, params, r, ids)
    before = len(dock.checks)
    print(f"要確認：{before} 件（近く {r.near} / 建物なし {r.none}）、一覧 {dock.list.count()} 行")
    print("  1行目：", dock.list.item(0).text())
    ok = before == r.near + r.none == dock.list.count()

    # 1. 近くの建物で代用 → この建物でよい
    row = next(i for i, c in enumerate(dock.checks) if c.status == NEAR)
    dock.list.setCurrentRow(row)
    ok &= dock.accept_btn.isEnabled() and dock.marker.isVisible()
    dock.accept_candidate()
    print("  1. この建物でよい：", dock.list.item(row).text(), "/", dock.detail.text()[:20])

    # 2. 建物なし → 地図の建物の中をクリック（クリックは地図の座標で届く）
    row = next(i for i, c in enumerate(dock.checks) if c.status == NONE)
    dock.list.setCurrentRow(row)
    ok &= not dock.accept_btn.isEnabled()
    dock.click_btn.setChecked(True)
    ok &= iface.canvas.mapTool() is dock.tool
    target = next(buildings.getFeatures()).geometry().pointOnSurface().asPoint()
    to_canvas = QgsCoordinateTransform(buildings.crs(), iface.canvas.mapSettings().destinationCrs(), project)
    dock.tool.canvasClicked.emit(to_canvas.transform(target), adjust_dock._enum(Qt, "MouseButton", "LeftButton"))
    print("  2. 地図をクリック：", dock.list.item(row).text(), "/", dock.detail.text()[:20])
    dock.click_btn.setChecked(False)
    ok &= len(dock.fixed) == 2 and points.isEditable() and points.isModified()

    # 3. 照合し直す
    dock.rematch()
    after = len(dock.checks)
    print(f"照合し直したあとの要確認：{after} 件（前は {before} 件）")
    ok &= after == before - 2
    ok &= all(project.mapLayer(i) is None for i in ids)  # 古い結果は外れている
    ok &= all(project.mapLayer(i) is not None for i in dock.layer_ids)

    points.rollBack()  # 動かした点は保存しない
    dock.close()
    project.removeAllMapLayers()
    print("調整の確認:", "OK" if ok else "NG")
    return ok


def main():
    app = QgsApplication([], True)
    app.initQgis()
    work = tempfile.mkdtemp()
    try:
        ok = run(work)
    finally:
        app.exitQgis()
        shutil.rmtree(work, ignore_errors=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
