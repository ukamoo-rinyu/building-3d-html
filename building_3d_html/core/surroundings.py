"""周辺建物：主の建物から distance（m）以内の建物を、灰色の背景用に書き出す。画面には依存しない。

- 高さ H だけを持たせる（属性は入れない。クリックもしない）
- 主の建物と同じ建物（周辺建物の中の点が主の建物に入るもの）は、二重に描かないよう除く
- 距離は、主の建物の位置に合わせた UTM（m 単位の座標系）で測る
"""

import json

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
    QgsSpatialIndex,
)

from .layer_export import PRECISION, _clean_geometry, _height


class SurroundResult:
    def __init__(self):
        self.features = []
        self.filled = 0     # 高さを既定値で補った件数
        self.same = 0       # 主の建物と同じため除いた件数

    @property
    def count(self):
        return len(self.features)

    def geojson(self):
        return {"type": "FeatureCollection", "features": self.features}


def _utm(lon, lat):
    zone = int((lon + 180) // 6) + 1
    return QgsCoordinateReferenceSystem(f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}")


def export_surroundings(layer, height_field, default_height, main_geojson, distance=200.0):
    """main_geojson：書き出した主の建物（EPSG:4326 の GeoJSON）。"""
    result = SurroundResult()
    mains = main_geojson["features"]
    if not mains:
        return result
    project = QgsProject.instance()
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")

    # 主の建物を UTM に移して索引を作る
    first = mains[0]["geometry"]["coordinates"]
    while isinstance(first[0], list):
        first = first[0]
    utm = _utm(first[0], first[1])
    wgs_to_utm = QgsCoordinateTransform(wgs, utm, project)
    index = QgsSpatialIndex()
    main_geoms = {}
    for i, f in enumerate(mains):
        g = QgsGeometry.fromWkt(_geojson_to_wkt(f["geometry"]))
        g.transform(wgs_to_utm)
        main_geoms[i] = g
        index.addFeature(i, g.boundingBox())
    area = QgsRectangle(main_geoms[0].boundingBox())
    for g in main_geoms.values():
        area.combineExtentWith(g.boundingBox())
    area = area.buffered(distance)

    to_utm = QgsCoordinateTransform(layer.crs(), utm, project)
    to_wgs = QgsCoordinateTransform(layer.crs(), wgs, project)
    utm_to_layer = QgsCoordinateTransform(utm, layer.crs(), project)
    request = QgsFeatureRequest().setFilterRect(utm_to_layer.transformBoundingBox(area))
    field_idx = layer.fields().indexOf(height_field)

    for feat in layer.getFeatures(request):
        geom = _clean_geometry(feat.geometry())
        if geom is None:
            continue
        g_utm = QgsGeometry(geom)
        try:
            g_utm.transform(to_utm)
        except Exception:
            continue
        near = index.intersects(g_utm.boundingBox().buffered(distance))
        if not near:
            continue
        # 主の建物と同じ建物は除く
        inside_pt = g_utm.pointOnSurface()
        if any(main_geoms[i].contains(inside_pt) for i in near):
            result.same += 1
            continue
        if not any(main_geoms[i].distance(g_utm) <= distance for i in near):
            continue
        try:
            geom.transform(to_wgs)
            geometry = json.loads(geom.asJson(PRECISION))
        except Exception:
            continue
        h = _height(feat.attribute(field_idx)) if field_idx >= 0 else None
        if h is None:
            h = float(default_height)
            result.filled += 1
        result.features.append({"type": "Feature", "id": len(result.features),
                                "properties": {"H": round(h, 2)}, "geometry": geometry})
    return result


def _geojson_to_wkt(geometry):
    """GeoJSON の Polygon / MultiPolygon を WKT にする（主の建物を QGIS の形に戻すため）。"""
    def ring(r):
        return "(" + ",".join(f"{x} {y}" for x, y in r) + ")"

    def poly(p):
        return "(" + ",".join(ring(r) for r in p) + ")"

    if geometry["type"] == "Polygon":
        return "POLYGON" + poly(geometry["coordinates"])
    return "MULTIPOLYGON(" + ",".join(poly(p) for p in geometry["coordinates"]) + ")"
