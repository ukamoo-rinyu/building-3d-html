"""点と建物の照合（簡易版）を、QGISの画面を開かずに試す（動作確認用）。

testdata の住棟を「建物」とし、そこから試し用の施設（点、緯度経度＝EPSG:4326）を作って照合する。
  - ほとんどの点：建物の中                 → 建物の中
  - 20件に1件：建物の東の端から15m外      → 近くの建物で代用
  - 50件に1件：大阪湾の海の上             → 建物なし
  - 先頭5棟：同じ建物にもう1つ点を足す     → 1棟に2施設（複合施設）

試し用の施設は testdata/試し_施設.gpkg にも保存する（QGIS で照合を試すとき用）。

あわせて、施設を敷地（面）にしたときも試す。団地ごとに住棟を囲んだ面を敷地とする。
  - 団地ごとの敷地：中の住棟すべて         → 敷地の中（1つの施設に何棟も）
  - 大阪湾の海の上の敷地 1つ               → 建物なし
試し用の敷地は testdata/試し_敷地.gpkg に保存する。

使い方（PowerShell）:
  & "C:\\Program Files\\QGIS 3.44.11\\bin\\python-qgis-ltr.bat" tools\\match_testdata.py
"""

import os
import sys

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMemoryProviderUtils,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from building_3d_html.core.matcher import T_INT, T_STRING  # noqa: E402

GPKG = os.path.join(ROOT, "testdata", "shiei_jutaku_3d_trial.gpkg")
POINTS_GPKG = os.path.join(ROOT, "testdata", "試し_施設.gpkg")
SITES_GPKG = os.path.join(ROOT, "testdata", "試し_敷地.gpkg")


def make_points(buildings):
    fields = QgsFields()
    fields.append(QgsField("施設名", T_STRING))
    fields.append(QgsField("種別", T_STRING))
    fields.append(QgsField("番号", T_INT))
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    layer = QgsMemoryProviderUtils.createMemoryLayer("試し_施設", fields, QgsWkbTypes.Point, wgs)
    xform = QgsCoordinateTransform(buildings.crs(), wgs, QgsProject.instance())
    feats, expect = [], {"inside": 0, "near": 0, "none": 0, "shared": 0}

    def add(geom, name, kind, no):
        f = QgsFeature(fields)
        g = QgsGeometry(geom)
        g.transform(xform)
        f.setGeometry(g)
        f.setAttributes([name, kind, no])
        feats.append(f)

    for i, b in enumerate(buildings.getFeatures()):
        name = b["住宅名"]
        if i % 50 == 49:  # 海の上（建物なし）
            add(QgsGeometry.fromPointXY(QgsPointXY(-60000 + i * 10, -160000)), name, "海の上", i)
            expect["none"] += 1
        elif i % 20 == 19:  # 東の端から15m外（近くの建物で代用。もっと近い別の建物に当たることもある）
            bb = b.geometry().boundingBox()
            add(QgsGeometry.fromPointXY(QgsPointXY(bb.xMaximum() + 15, bb.center().y())), name, "15m外", i)
            expect["near"] += 1
        else:
            add(b.geometry().pointOnSurface(), name, "中", i)
            expect["inside"] += 1
        if i < 5:  # 同じ建物にもう1つ（複合施設）
            add(b.geometry().pointOnSurface(), name + "（別館）", "中", 9000 + i)
            expect["inside"] += 1
            expect["shared"] += 1
    layer.dataProvider().addFeatures(feats)
    return layer, expect


def make_sites(buildings):
    """団地ごとに、住棟をまとめて囲んだ面（5m 広げる）を敷地にする。最後に海の上の敷地を1つ足す。"""
    fields = QgsFields()
    fields.append(QgsField("施設名", T_STRING))
    layer = QgsMemoryProviderUtils.createMemoryLayer(
        "試し_敷地", fields, QgsWkbTypes.MultiPolygon, buildings.crs())
    groups = {}
    for b in buildings.getFeatures():
        groups.setdefault(b["団地"], []).append(b.geometry())
    feats = []
    for name, geoms in groups.items():
        f = QgsFeature(fields)
        f.setGeometry(QgsGeometry.collectGeometry(geoms).convexHull().buffer(5, 4))
        f.setAttributes([name])
        feats.append(f)
    sea = QgsFeature(fields)
    sea.setGeometry(QgsGeometry.fromRect(QgsRectangle(-60000, -160100, -59900, -160000)))
    sea.setAttributes(["海の上の敷地"])
    feats.append(sea)
    layer.dataProvider().addFeatures(feats)
    return layer, len(groups)


def save(layer, path, name):
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName, opts.layerName, opts.fileEncoding = "GPKG", name, "UTF-8"
    try:
        if os.path.exists(path):
            os.remove(path)
        QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, QgsProject.instance().transformContext(), opts)
    except PermissionError:  # QGIS で開いているときは作り直さない（中身は毎回同じ）
        print(f"（{os.path.basename(path)} は使用中のため作り直しませんでした）")


def run_sites(buildings):
    from building_3d_html.core.matcher import SITE, match

    sites, n_groups = make_sites(buildings)
    save(sites, SITES_GPKG, "試し_敷地")
    n_b = buildings.featureCount()
    print(f"敷地 {sites.featureCount()} 件 / 期待：建物あり {n_groups} 件（建物 {n_b} 棟） / 建物なし 1 件")

    r = match(sites, buildings)
    print(f"結果：建物あり {r.site} 件（建物 {r.site_buildings} 棟） / 建物なし {r.none} / 位置が空 {r.skipped}"
          f" / 複数の敷地に入った建物 {r.shared} 棟")
    print(f"結果の建物レイヤ：{r.buildings.featureCount()} 棟 / 建物なしレイヤ：{r.unmatched.featureCount()} 件")

    # 住棟はどれも自分の団地の敷地に入る（隣の敷地と重なれば、そちらの名前も「 / 」でつながる）
    own = all(f["団地"] in f["施設名"].split(" / ") for f in r.buildings.getFeatures())
    ok = (r.site == n_groups and r.site_buildings == n_b and r.none == 1
          and r.buildings.featureCount() == n_b and r.unmatched.featureCount() == 1
          and all(f["判定"] == SITE for f in r.buildings.getFeatures()) and own)
    for f in r.buildings.getFeatures():
        print("  敷地の例：", f["住宅名"], "←", f["施設名"], "/", f["判定"])
        break
    print("敷地の照合の確認:", "OK" if ok else "NG")
    return ok


def run():
    from building_3d_html.core.matcher import NEAR, match

    buildings = QgsVectorLayer(f"{GPKG}|layername=市営住宅_住棟", "建物", "ogr")
    points, expect = make_points(buildings)
    save(points, POINTS_GPKG, "試し_施設")
    print(f"建物 {buildings.featureCount()} 棟（{buildings.crs().authid()}） / 施設 {points.featureCount()} 件（{points.crs().authid()}）")
    print(f"期待：中 {expect['inside']} / 近く {expect['near']} / 建物なし {expect['none']} / 複合 {expect['shared']} 棟")

    r = match(points, buildings, max_distance=30)
    print(f"結果：中 {r.inside} / 近く {r.near} / 建物なし {r.none} / 位置が空 {r.skipped} / 複合 {r.shared} 棟")
    print(f"結果の建物レイヤ：{r.buildings.featureCount()} 棟 / 建物なしレイヤ：{r.unmatched.featureCount()} 件")
    print("列：", [f.name() for f in r.buildings.fields()][-8:])

    # 「15m外」の点は、隣の建物の中に入ることがある（そのときは「建物の中」が正しい）
    near_d = [f["距離m"] for f in r.buildings.getFeatures() if f["判定"] == NEAR]
    ok = (r.none == expect["none"] and r.unmatched.featureCount() == expect["none"]
          and r.inside + r.near == expect["inside"] + expect["near"]
          and r.inside >= expect["inside"] and r.shared >= expect["shared"]
          and all(0 < d <= 30 for d in near_d)
          and sum(f["施設数"] for f in r.buildings.getFeatures()) == r.inside + r.near)
    for f in r.buildings.getFeatures():
        if f["施設数"] > 1:
            print("  複合の例：", f["施設名"], "/ 施設数", f["施設数"], "/", f["判定"])
            break
    for f in r.buildings.getFeatures():
        if f["判定"] == NEAR:
            print("  近くの例：", f["住宅名"], "←", f["施設名"], f"/ 距離 {f['距離m']} m")
            break
    print("照合の確認:", "OK" if ok else "NG")
    return run_sites(buildings) and ok


def main():
    app = QgsApplication([], False)
    app.initQgis()
    try:
        ok = run()  # レイヤは run() の中で片付けてから QGIS を終了する（逆順だと落ちる）
    finally:
        app.exitQgis()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
