"""レイヤ → GeoJSON（EPSG:4326）への変換。画面には依存しない。"""

import json

from qgis.core import (
    NULL,
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProject,
    QgsWkbTypes,
)

PRECISION = 6  # 小数6桁 ≒ 約10cm

# QGIS 3.30 から列挙型の置き場所が変わったため、両方に対応する
try:
    POLYGON = Qgis.GeometryType.Polygon
except AttributeError:
    POLYGON = QgsWkbTypes.PolygonGeometry


class ExportResult:
    def __init__(self):
        self.features = []      # GeoJSON の Feature（dict）
        self.bbox = None        # [西, 南, 東, 北]
        self.filled = 0         # 高さを既定値で補った件数
        self.skipped = 0        # 形が読めず除外した件数

    @property
    def count(self):
        return len(self.features)

    def geojson(self):
        return {"type": "FeatureCollection", "features": self.features}


def _height(value):
    """高さの値を数値にする。空・0・負・数値でないものは None。"""
    if value is None or value == NULL:
        return None
    try:
        h = float(value)
    except (TypeError, ValueError):
        return None
    if h != h or h <= 0:  # NaN または 0以下
        return None
    return h


def _clean_geometry(geom):
    """曲線を直線化し、Z・M を落とした 2D の面にする。面でなければ None。"""
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    if geom.type() != POLYGON:
        return None
    g = QgsGeometry(geom)
    if QgsWkbTypes.isCurvedType(g.wkbType()):
        g = QgsGeometry(g.constGet().segmentize())
    abstract = g.get()
    abstract.dropZValue()
    abstract.dropMValue()
    return g


def export_layer(layer, height_field, default_height, selected_only=False):
    """面レイヤの地物を EPSG:4326 の GeoJSON にし、高さ H を持たせる。

    H      … 高さ（m）。空・0・負の値は default_height で補う
    Hfill  … 補った地物だけ 1
    id     … 連番（MapLibre の選択表示に使う）
    """
    result = ExportResult()
    xform = QgsCoordinateTransform(
        layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())

    request = QgsFeatureRequest()
    if selected_only:
        request.setFilterFids(layer.selectedFeatureIds())

    field_idx = layer.fields().indexOf(height_field)
    west = south = float("inf")
    east = north = float("-inf")

    for feat in layer.getFeatures(request):
        geom = _clean_geometry(feat.geometry())
        if geom is None:
            result.skipped += 1
            continue
        try:
            geom.transform(xform)
        except Exception:
            result.skipped += 1
            continue
        try:
            geometry = json.loads(geom.asJson(PRECISION))
        except ValueError:
            result.skipped += 1
            continue

        h = _height(feat.attribute(field_idx)) if field_idx >= 0 else None
        props = {}
        if h is None:
            h = float(default_height)
            props["Hfill"] = 1
            result.filled += 1
        props["H"] = round(h, 2)

        fid = len(result.features)
        result.features.append(
            {"type": "Feature", "id": fid, "properties": props, "geometry": geometry})

        r = geom.boundingBox()
        west, south = min(west, r.xMinimum()), min(south, r.yMinimum())
        east, north = max(east, r.xMaximum()), max(north, r.yMaximum())

    if result.features:
        result.bbox = [round(v, PRECISION) for v in (west, south, east, north)]
    return result
