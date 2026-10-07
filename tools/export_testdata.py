"""testdata/ の建物データを、QGISの画面を開かずに3D HTMLへ書き出す（動作確認用）。

使い方（PowerShell）:
  & "C:\\Program Files\\QGIS 3.44.11\\bin\\python-qgis-ltr.bat" tools\\export_testdata.py
書き出し先: testdata/out/ （全件・高さ欠け・選択中のみ の3ファイル）
"""

import os
import sys

from qgis.core import NULL, QgsApplication, QgsFeatureRequest, QgsVectorLayer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GPKG = os.path.join(ROOT, "testdata", "shiei_jutaku_3d_trial.gpkg")
OUT = os.path.join(ROOT, "testdata", "out")
LAYER = "市営住宅_住棟"
HEIGHT = "高さm"


def export(layer, name, title, selected_only=False):
    from building_3d_html.core.html_builder import write_html
    from building_3d_html.core.layer_export import export_layer

    result = export_layer(layer, HEIGHT, 10, selected_only)
    config = {
        "title": title,
        "bbox": result.bbox,
        "color": "#7f9fc4",
        "defaultHeight": 10,
        "attribution": ["PLATEAU（国土交通省）"],
    }
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name + ".html")
    size = write_html(path, result.geojson(), config)
    print(f"[{name}] {result.count} 棟 / 高さを補った {result.filled} 棟 / 除外 {result.skipped} 棟"
          f" / {size / 1024 / 1024:.2f} MB")
    return result


def run():
    layer = QgsVectorLayer(f"{GPKG}|layername={LAYER}", LAYER, "ogr")
    if not layer.isValid():
        sys.exit(f"レイヤを開けません: {GPKG} / {LAYER}")
    print(f"読み込み: {LAYER}  {layer.featureCount()} 件  CRS={layer.crs().authid()}")

    r = export(layer, "1_全件", "市営住宅 住棟（テスト）")
    print(f"  範囲: {r.bbox}")

    # 高さが空の建物：先頭50件の高さを空にした複製で試す
    blank = layer.materialize(QgsFeatureRequest())
    idx = blank.fields().indexOf(HEIGHT)
    blank.startEditing()
    for f in list(blank.getFeatures())[:50]:
        blank.changeAttributeValue(f.id(), idx, NULL)
    blank.commitChanges()
    export(blank, "2_高さ欠け50件", "高さが空の建物（50件）")

    # 選択中の地物のみ：中央区だけ選ぶ
    layer.selectByExpression("\"区\" = '中央区'")
    export(layer, "3_選択中のみ_中央区", "中央区だけ", selected_only=True)

def main():
    app = QgsApplication([], False)
    app.initQgis()
    try:
        run()  # レイヤは run() の中で片付けてから QGIS を終了する（逆順だと落ちる）
    finally:
        app.exitQgis()


if __name__ == "__main__":
    main()
