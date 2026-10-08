"""testdata/ の建物データを、QGISの画面を開かずに3D HTMLへ書き出す（動作確認用）。

使い方（PowerShell）:
  & "C:\\Program Files\\QGIS 3.44.11\\bin\\python-qgis-ltr.bat" tools\\export_testdata.py
書き出し先: testdata/out/
  1_全件              … 単一シンボル
  2_高さ欠け50件      … 高さが空の建物を既定値で補う
  3_選択中のみ_中央区 … 選択中の地物だけ
  4_分類_耐震性       … 分類（カテゴリ）の色分け。「D」の分類はチェックを外して非表示
  5_分類_区           … 分類が20を超える（凡例のスクロール）
"""

import os
import sys

from qgis.core import (
    NULL,
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsFeatureRequest,
    QgsFillSymbol,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GPKG = os.path.join(ROOT, "testdata", "shiei_jutaku_3d_trial.gpkg")
OUT = os.path.join(ROOT, "testdata", "out")
LAYER = "市営住宅_住棟"
HEIGHT = "高さm"
NAME = "住宅名"
ALIASES = {"高さm": "高さ（m）", "PLATEAU建物ID": "PLATEAUの建物ID"}
SEIS_COLORS = {"A": "#7f9fc4", "B": "#e2bb55", "D": "#cf3f4f", "": "#a3a9b1"}
WARD_COLORS = ["#e07a5f", "#3d405b", "#81b29a", "#f2cc8f", "#6d597a", "#b56576",
               "#355070", "#eaac8b", "#2a9d8f", "#e9c46a"]


def export(layer, name, title, selected_only=False, use_style=True):
    from building_3d_html.core.html_builder import write_html
    from building_3d_html.core.layer_export import export_layer
    from building_3d_html.core.style_reader import StyleReader

    # 表示名＝住宅名、詳細＝全列（一部に別名を付けて試す）
    cols = [(f.name(), ALIASES.get(f.name(), f.name())) for f in layer.fields() if f.name() != "fid"]
    style = StyleReader(layer) if use_style else None
    result = export_layer(layer, HEIGHT, 10, selected_only,
                          fields=[NAME] + [c[0] for c in cols], style=style)
    config = {
        "title": title,
        "bbox": result.bbox,
        "legend": result.legend,
        "defaultHeight": 10,
        "attribution": ["PLATEAU（国土交通省）"],
        "nameKey": result.keys.get(NAME),
        "columns": [[result.keys[n], label] for n, label in cols if n in result.keys],
    }
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name + ".html")
    size = write_html(path, result.geojson(), config)
    print(f"[{name}] {result.count} 棟 / 高さを補った {result.filled} 棟 / 非表示で除外 {result.hidden} 棟"
          f" / 形で除外 {result.skipped} 棟 / 凡例 {len(result.legend)} 項目 / {size / 1024 / 1024:.2f} MB")
    return result


def fill(color):
    return QgsFillSymbol.createSimple({"color": color, "outline_color": "#333333"})


def run():
    layer = QgsVectorLayer(f"{GPKG}|layername={LAYER}", LAYER, "ogr")
    if not layer.isValid():
        sys.exit(f"レイヤを開けません: {GPKG} / {LAYER}")
    print(f"読み込み: {LAYER}  {layer.featureCount()} 件  CRS={layer.crs().authid()}")

    layer.setRenderer(QgsSingleSymbolRenderer(fill("#7f9fc4")))
    r = export(layer, "1_全件", "市営住宅 住棟（テスト）")
    print(f"  範囲: {r.bbox}")

    # 高さが空の建物：先頭50件の高さを空にした複製で試す
    blank = layer.materialize(QgsFeatureRequest())
    blank.setRenderer(layer.renderer().clone())
    idx = blank.fields().indexOf(HEIGHT)
    blank.startEditing()
    for f in list(blank.getFeatures())[:50]:
        blank.changeAttributeValue(f.id(), idx, NULL)
    blank.commitChanges()
    export(blank, "2_高さ欠け50件", "高さが空の建物（50件）")

    # 選択中の地物のみ：中央区だけ選ぶ
    layer.selectByExpression("\"区\" = '中央区'")
    export(layer, "3_選択中のみ_中央区", "中央区だけ", selected_only=True)
    layer.removeSelection()

    # 分類：耐震性。「D」はチェックを外す（QGIS で描かれない → 書き出さない）
    cats = [QgsRendererCategory(v, fill(c), v or "記載なし", v != "D") for v, c in SEIS_COLORS.items()]
    layer.setRenderer(QgsCategorizedSymbolRenderer("耐震性", cats))
    r = export(layer, "4_分類_耐震性", "耐震性で色分け（Dは非表示）")
    check_colors(layer, r, "耐震性", SEIS_COLORS, hidden={"D"})

    # 分類が20を超える：区（24区）
    wards = sorted({f["区"] for f in layer.getFeatures()})
    ward_colors = {w: WARD_COLORS[i % len(WARD_COLORS)] for i, w in enumerate(wards)}
    layer.setRenderer(QgsCategorizedSymbolRenderer(
        "区", [QgsRendererCategory(w, fill(c), w) for w, c in ward_colors.items()]))
    r = export(layer, "5_分類_区", "区で色分け（24分類）")
    check_colors(layer, r, "区", ward_colors)


def check_colors(layer, result, field, colors, hidden=()):
    """書き出した色が、分類の値から期待される色と一致するか確かめる。"""
    expected = []
    for f in layer.getFeatures():
        v = f[field]
        v = "" if v is None or v == NULL else v
        if v in hidden or v not in colors:
            continue
        expected.append(colors[v])
    got = [x["properties"]["C"] for x in result.features]
    ok = got == expected
    print(f"  色の確認（{field}）: {'OK' if ok else 'NG'}  {len(got)} 棟")
    for item in result.legend:
        print(f"    {item['color']}  {item['label']}  {item['count']}")
    if not ok:
        sys.exit("色が一致しません")


def main():
    app = QgsApplication([], False)
    app.initQgis()
    try:
        run()  # レイヤは run() の中で片付けてから QGIS を終了する（逆順だと落ちる）
    finally:
        app.exitQgis()


if __name__ == "__main__":
    main()
