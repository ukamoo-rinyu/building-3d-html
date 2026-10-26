"""PLATEAU の建物（CityGML、bldg_*.gml）から、阿倍野区の建物だけを取り出して GeoPackage にする。

建物ごとの「市区町村コード」（uro:city）が 27119（阿倍野区）のものだけを使う。
形は LOD0 の足元の形（lod0FootPrint）を、平面直角座標 VI 系（EPSG:6674）の面にする。
高さが測れていない建物（-9999）は「計測高さ」を空にし、「表示用の高さ」は
地上階数 × 3m（階数も不明なら 3m）で埋める。

使い方（PowerShell）:
  & "C:\\Program Files\\QGIS 3.44.11\\bin\\python-qgis-ltr.bat" tools\\make_abeno_plateau.py 出力.gpkg "bld abeno"
"""

import glob
import os
import sys
import xml.etree.ElementTree as ET

from osgeo import ogr, osr

CITY = "27119"
LAYER = "阿倍野区_建物"
FLOOR_H = 3.0

NS = {
    "bldg": "http://www.opengis.net/citygml/building/2.0",
    "gml": "http://www.opengis.net/gml",
    "uro": "https://www.geospatial.jp/iur/uro/3.1",
    "gen": "http://www.opengis.net/citygml/generics/2.0",
}
BUILDING = "{%s}Building" % NS["bldg"]

USAGE = {
    "401": "業務施設", "402": "商業施設", "403": "宿泊施設", "404": "商業系複合施設",
    "411": "住宅", "412": "共同住宅", "413": "店舗等併用住宅", "414": "店舗等併用共同住宅",
    "415": "作業所併用住宅", "421": "官公庁施設", "422": "文教厚生施設", "431": "運輸倉庫施設",
    "441": "工場", "451": "農林漁業用施設", "452": "供給処理施設", "453": "防衛施設",
    "454": "その他", "461": "不明",
}
STRUCTURE = {
    "601": "木造・土蔵造", "602": "鉄骨鉄筋コンクリート造", "603": "鉄筋コンクリート造",
    "604": "鉄骨造", "605": "軽量鉄骨造", "606": "レンガ造・コンクリートブロック造・石造",
    "610": "非木造", "611": "不明",
}
FIELDS = [
    ("建物ID", ogr.OFTString), ("用途", ogr.OFTString), ("構造", ogr.OFTString),
    ("地上階数", ogr.OFTInteger), ("計測高さ", ogr.OFTReal), ("表示用の高さ", ogr.OFTReal),
    ("町丁目", ogr.OFTString), ("元ファイル", ogr.OFTString),
]


def text(b, path):
    e = b.find(path, NS)
    return e.text.strip() if e is not None and e.text else ""


def number(s, cls=float):
    try:
        v = cls(float(s))
    except ValueError:
        return None
    return None if v in (-9999, 9999) else v


def footprint(b, to_6674):
    geom = ogr.Geometry(ogr.wkbMultiPolygon)
    for poly in b.findall("bldg:lod0FootPrint//gml:Polygon", NS):
        p = ogr.Geometry(ogr.wkbPolygon)
        for pos in poly.findall(".//gml:LinearRing/gml:posList", NS):
            v = [float(x) for x in pos.text.split()]
            ring = ogr.Geometry(ogr.wkbLinearRing)
            for i in range(0, len(v), 3):  # 緯度 経度 高さ の順
                ring.AddPoint_2D(v[i + 1], v[i])
            p.AddGeometry(ring)
        geom.AddGeometry(p)
    if geom.GetGeometryCount() == 0:
        return None
    geom.Transform(to_6674)
    return geom


def run(out, folder):
    src, dst = osr.SpatialReference(), osr.SpatialReference()
    src.ImportFromEPSG(6668)
    dst.ImportFromEPSG(6674)
    for s in (src, dst):
        s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    to_6674 = osr.CoordinateTransformation(src, dst)

    if os.path.exists(out):
        os.remove(out)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(out)
    lyr = ds.CreateLayer(LAYER, dst, ogr.wkbMultiPolygon, ["GEOMETRY_NAME=geom", "FID=fid"])
    for name, kind in FIELDS:
        lyr.CreateField(ogr.FieldDefn(name, kind))

    total = no_height = no_shape = 0
    lyr.StartTransaction()
    for path in sorted(glob.glob(os.path.join(folder, "*_bldg_*.gml"))):
        n = 0
        for _, b in ET.iterparse(path):
            if b.tag != BUILDING:
                continue
            if text(b, ".//uro:city") == CITY:
                g = footprint(b, to_6674)
                if g is None:
                    no_shape += 1
                else:
                    h = number(text(b, "bldg:measuredHeight"))
                    floors = number(text(b, "bldg:storeysAboveGround"), int)
                    f = ogr.Feature(lyr.GetLayerDefn())
                    f.SetGeometry(g)
                    f.SetField("建物ID", text(b, ".//uro:buildingID"))
                    f.SetField("用途", USAGE.get(text(b, "bldg:usage"), text(b, "bldg:usage")))
                    s = text(b, ".//uro:buildingStructureType")
                    f.SetField("構造", STRUCTURE.get(s, s))
                    if floors is not None:
                        f.SetField("地上階数", floors)
                    if h is not None and h > 0:
                        f.SetField("計測高さ", h)
                    else:
                        no_height += 1
                    f.SetField("表示用の高さ", h if h is not None and h > 0 else (floors or 1) * FLOOR_H)
                    f.SetField("町丁目", text(b, "gen:stringAttribute[@name='町丁目名称']/gen:value"))
                    f.SetField("元ファイル", os.path.basename(path))
                    lyr.CreateFeature(f)
                    n += 1
            b.clear()
        print(f"{os.path.basename(path)}：阿倍野区 {n:,} 棟")
        total += n
    lyr.CommitTransaction()
    print(f"{LAYER}：{total:,} 棟（高さ不明 {no_height:,}、形なしで除外 {no_shape}）→ {out}")


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
