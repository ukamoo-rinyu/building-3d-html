"""PLATEAU QGIS Plugin の LOD1 の形（屋根・床・壁の面をまとめた 3D のマルチポリゴン、EPSG:6668）を
まねたレイヤで、書き出しが「真上から見た1つの形」になるかを確かめる（動作確認用）。

使い方（PowerShell）:
  & "C:\\Program Files\\QGIS 3.44.11\\bin\\python-qgis-ltr.bat" tools\\plateau_lod1_test.py
"""

import os
import sys

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsProject,
    QgsVectorLayer,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def box_faces(x0, y0, w, d, h):
    """平面直角座標（m）で、幅 w × 奥行 d × 高さ h の箱の6面を WKT の MultiPolygonZ にする。"""
    c = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + d), (x0, y0 + d)]
    faces = []
    faces.append([(x, y, 0) for x, y in c])            # 床
    faces.append([(x, y, h) for x, y in c])            # 屋根
    for i in range(4):                                 # 壁
        (xa, ya), (xb, yb) = c[i], c[(i + 1) % 4]
        faces.append([(xa, ya, 0), (xb, yb, 0), (xb, yb, h), (xa, ya, h)])
    rings = ["((" + ",".join(f"{x} {y} {z}" for x, y, z in f + [f[0]]) + "))" for f in faces]
    return "MULTIPOLYGONZ(" + ",".join(rings) + ")"


def run():
    from building_3d_html.core.layer_export import export_layer
    from building_3d_html.dialog import HEIGHT_HINTS, _guess_field

    layer = QgsVectorLayer("MultiPolygonZ?crs=EPSG:6668&field=buildingID:string&field=measuredHeight:double",
                           "bldg_Building_lod1", "memory")
    to_6668 = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:6674"), layer.crs(), QgsProject.instance())
    feats = []
    for i, (w, d, h) in enumerate([(20, 10, 30), (40, 15, 12.5)]):
        g = QgsGeometry.fromWkt(box_faces(-30000 + i * 100, -150000, w, d, h))
        g.transform(to_6668)
        f = QgsFeature(layer.fields())
        f.setGeometry(g)
        f.setAttributes([f"bldg_{i}", h])
        feats.append(f)
    layer.dataProvider().addFeatures(feats)

    field = _guess_field(layer, HEIGHT_HINTS, numeric=True)
    print("高さの列（自動）:", field)
    r = export_layer(layer, field, 10)
    ok = field == "measuredHeight" and r.count == 2 and r.skipped == 0
    to_m = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                  QgsCoordinateReferenceSystem("EPSG:6674"), QgsProject.instance())
    for f, (w, d) in zip(r.features, [(20, 10), (40, 15)]):
        g = QgsGeometry.fromWkt(_wkt(f["geometry"]))
        g.transform(to_m)
        area = g.area()
        print(f"  {f['geometry']['type']} / 面積 {area:.1f} m²（期待 {w * d}）/ 高さ {f['properties']['H']} m")
        # 座標を約10cmに丸めるため、面積は少しずれる（1%以内なら良し）
        ok = ok and f["geometry"]["type"] == "Polygon" and abs(area - w * d) < w * d * 0.01
    print("LOD1 の確認:", "OK" if ok else "NG")
    return ok


def _wkt(geometry):
    ring = lambda r: "(" + ",".join(f"{x} {y}" for x, y in r) + ")"
    if geometry["type"] == "Polygon":
        return "POLYGON(" + ",".join(ring(r) for r in geometry["coordinates"]) + ")"
    return "MULTIPOLYGON(" + ",".join("(" + ",".join(ring(r) for r in p) + ")" for p in geometry["coordinates"]) + ")"


def main():
    app = QgsApplication([], False)
    app.initQgis()
    try:
        ok = run()
    finally:
        app.exitQgis()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
