"""住所から点を作る処理を、QGISの画面を開かずに試す（動作確認用）。

市営住宅の住棟の「所在地」（例：中央区上町１丁目１５番１号）から点を作り、
  - 住所の書き方の違い（全角・半角、「三丁目」、「1-15-1」、「大阪府大阪市」付き）でも同じ場所になるか
  - 作った点を住棟と照合すると、どれくらい建物に当たるか
を確かめる。位置参照情報は「街区データ」フォルダ（大阪市の各区の zip）を使う。

使い方（PowerShell）:
  & "C:\\Program Files\\QGIS 3.44.11\\bin\\python-qgis-ltr.bat" tools\\geocode_testdata.py
"""

import os
import sys
import time

from qgis.core import QgsApplication, QgsVectorLayer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GPKG = os.path.join(ROOT, "testdata", "shiei_jutaku_3d_trial.gpkg")
ISJ = os.path.join(ROOT, "街区データ")


def run():
    from building_3d_html.core.geocoder import BLOCK, TOWN, AddressBook, geocode, normalize
    from building_3d_html.core.matcher import match

    t = time.time()
    book = AddressBook().load(ISJ)
    print(f"位置参照情報：{book.files} ファイル、{len(book.cities)} 市区町村、"
          f"{len(book.towns):,} 町丁目、{book.block_count:,} 街区（{time.time() - t:.1f} 秒）")

    # 書き方の違い
    same = ["中央区上町１丁目１５番１号", "中央区上町1丁目15番1号", "中央区上町一丁目15-1",
            "大阪府大阪市中央区上町1-15-1", "大阪市 中央区 上町１－１５－１", "中央区上町1丁目15"]
    found = [book.find(a) for a in same]
    for a, (xy, level, hit) in zip(same, found):
        print(f"  {a} → {level}（{hit}）")
    ok = all(f[0] == found[0][0] and f[1] == BLOCK for f in found)
    xy, level, _ = book.find("中央区上町1丁目999番")
    ok &= level == TOWN
    xy, level, _ = book.find("東京都千代田区霞が関2丁目1")
    ok &= xy is None
    print("  丁目まで：中央区上町1丁目999番 → 町丁目 / 大阪市外 → 見つからない")
    ok &= normalize("十三東一丁目") == "十三東1丁目" and normalize("二十丁目") == "20丁目"

    # 市営住宅の住所から点を作り、住棟と照合する
    buildings = QgsVectorLayer(f"{GPKG}|layername=市営住宅_住棟", "建物", "ogr")
    t = time.time()
    r = geocode(buildings, "所在地", book)
    print(f"住所から点：街区まで {r.block:,} / 町丁目まで {r.town:,} / 見つからない {r.none:,}"
          f"（{time.time() - t:.1f} 秒）")
    for a in r.examples:
        print("  見つからない例：", a)
    ok &= r.layer.featureCount() == buildings.featureCount()
    ok &= r.block > buildings.featureCount() * 0.8

    m = match(r.layer, buildings, 30)
    print(f"住棟と照合：建物の中 {m.inside:,} / 近くの建物で代用 {m.near:,} / 建物なし {m.none:,} / 位置が空 {m.skipped:,}")
    # 照合で当たった建物が、その住所の住棟そのものかどうか（住宅名で比べる）
    right = sum(1 for f in m.buildings.getFeatures() if f["住宅名"] in str(f["住宅名_施設"]).split(" / "))
    print(f"  当たった建物が、その住所の住棟だった：{right:,} 棟 / {m.buildings.featureCount():,} 棟")

    print("住所から点の確認:", "OK" if ok else "NG")
    return ok


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
