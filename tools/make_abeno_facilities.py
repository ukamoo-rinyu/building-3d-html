"""大阪市オープンデータの施設一覧（CSV）を阿倍野区に絞って、施設の点の GeoPackage を作る。

「所在地」に「阿倍野区」が入っている行だけを取り出し、経度・緯度から点を作って、
全部を1つのレイヤ「阿倍野区_施設」にまとめる。種類は「カテゴリ」の列で分かる。
CSVの列はそのまま残す（2つ目の「分類」は「分類_1」）。どのCSVから来たかは「元データ」の列。

使い方（PowerShell）:
  & "C:\\Program Files\\QGIS 3.44.11\\bin\\python-qgis-ltr.bat" tools\\make_abeno_facilities.py 出力.gpkg 入力1.csv 入力2.csv ...
"""

import csv
import os
import sys

from osgeo import ogr, osr

WARD = "阿倍野区"
ALL_LAYER = "阿倍野区_施設"
REAL = ("経度", "緯度")


def read_csv(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    rows = list(csv.reader(text.splitlines()))
    header, seen = [], set()
    for h in rows[0]:
        name = h
        while name in seen:
            name += "_1"
        seen.add(name)
        header.append(name)
    return header, [dict(zip(header, r)) for r in rows[1:] if r]


def to_float(s):
    try:
        return float(s)
    except ValueError:
        return None


def write_layer(ds, name, header, rows, source=True):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    lyr = ds.CreateLayer(name, srs, ogr.wkbPoint, ["GEOMETRY_NAME=geom", "FID=fid"])
    cols = header + (["元データ"] if source else [])
    for h in cols:
        lyr.CreateField(ogr.FieldDefn(h, ogr.OFTReal if h in REAL else ogr.OFTString))
    lyr.StartTransaction()
    for r in rows:
        f = ogr.Feature(lyr.GetLayerDefn())
        for h in cols:
            v = r.get(h, "")
            if h in REAL:
                v = to_float(v)
            if v not in (None, ""):
                f.SetField(h, v)
        x, y = to_float(r.get("経度", "")), to_float(r.get("緯度", ""))
        if x is not None and y is not None:
            p = ogr.Geometry(ogr.wkbPoint)
            p.AddPoint_2D(x, y)
            f.SetGeometry(p)
        lyr.CreateFeature(f)
    lyr.CommitTransaction()


def run(out, paths):
    if os.path.exists(out):
        os.remove(out)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(out)
    header_all, rows_all = [], []
    for path in paths:
        header, rows = read_csv(path)
        picked = [dict(r, 元データ=os.path.basename(path)) for r in rows if WARD in r.get("所在地", "")]
        cat = picked[0]["カテゴリ"] if picked else os.path.splitext(os.path.basename(path))[0]
        no_xy = sum(1 for r in picked if to_float(r.get("経度", "")) is None or to_float(r.get("緯度", "")) is None)
        print(f"{os.path.basename(path)}（{cat}）：{len(rows):,} 件 → 阿倍野区 {len(picked)} 件"
              + (f"（位置なし {no_xy}）" if no_xy else ""))
        header_all += [h for h in header if h not in header_all]
        rows_all += picked
    write_layer(ds, ALL_LAYER, header_all, rows_all)
    print(f"{ALL_LAYER}：{len(rows_all)} 件 → {out}")


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2:])
