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

# 地図の表示に使う予約済みの列名（H：高さ、Hfill：高さを補った印、C：色）
RESERVED = ("H", "Hfill", "C")

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
        self.hidden = 0         # QGIS の色分けで描かれないため除外した件数
        self.legend = []        # 凡例 [{"label", "color", "count"}]
        self.keys = {}          # 列名 → GeoJSON での名前（予約済みの名前と重なったときだけ変わる）

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


def _json_value(value):
    """属性の値を JSON に入れられる形（文字・数値・真偽・None）にする。"""
    if value is None or value == NULL:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            return None
        return value
    if hasattr(value, "toString"):  # 日付・時刻（QDate など）
        if hasattr(value, "isValid") and not value.isValid():
            return None
        return value.toString(_iso())
    return str(value)


def _iso():
    from qgis.PyQt.QtCore import Qt
    return getattr(getattr(Qt, "DateFormat", Qt), "ISODate")


def _property_key(name):
    """予約済みの名前（H など）と重なる列は、後ろに「_」を付けて区別する。"""
    key = name
    while key in RESERVED:
        key += "_"
    return key


def _clean_geometry(geom):
    """曲線を直線化し、Z・M を落とした 2D の面にする。面でなければ None。"""
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    if geom.type() != POLYGON:
        return None
    g = QgsGeometry(geom)
    if QgsWkbTypes.isCurvedType(g.wkbType()):
        g = QgsGeometry(g.constGet().segmentize())
    solid = QgsWkbTypes.hasZ(g.wkbType()) and g.isMultipart() and g.constGet().numGeometries() > 1
    abstract = g.get()
    abstract.dropZValue()
    abstract.dropMValue()
    if solid:
        g = _footprint(g)
    return g


def _footprint(g):
    """立体（PLATEAU の LOD1 などで、屋根・床・壁の面がまとめて入った形）を、真上から見た1つの形にする。
    壁は真上から見ると面積がほぼ0になるので除き、残り（屋根・床）を重ねて合わせる。"""
    parts = [QgsGeometry(p.clone()) for p in g.constGet()]
    areas = [p.area() for p in parts]
    biggest = max(areas) if areas else 0
    if biggest <= 0:
        return g
    keep = [p for p, a in zip(parts, areas) if a > biggest * 1e-6]
    merged = QgsGeometry.unaryUnion(keep)
    return merged if merged is not None and not merged.isEmpty() else g


def export_layer(layer, height_field, default_height, selected_only=False, fields=(),
                 style=None, color="#7f9fc4"):
    """面レイヤの地物を EPSG:4326 の GeoJSON にし、高さ H と色 C を持たせる。

    H      … 高さ（m）。空・0・負の値は default_height で補う
    Hfill  … 補った地物だけ 1
    C      … 塗り色（#rrggbb）。style（StyleReader）があれば QGIS の色分け、なければ color の単色
    id     … 連番（MapLibre の選択表示に使う）
    fields … 残す列（表示名の列・詳細に出す列）。列名のまま properties に入れる
    """
    result = ExportResult()
    layer_fields = layer.fields()
    keep = [(layer_fields.indexOf(n), _property_key(n)) for n in fields
            if layer_fields.indexOf(n) >= 0]
    result.keys = {layer_fields.at(i).name(): k for i, k in keep}
    xform = QgsCoordinateTransform(
        layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())

    request = QgsFeatureRequest()
    if selected_only:
        request.setFilterFids(layer.selectedFeatureIds())

    field_idx = layer.fields().indexOf(height_field)

    if style is not None:
        style.start()
    try:
        _collect(layer.getFeatures(request), result, xform, field_idx, default_height, keep,
                 style, color)
    finally:
        if style is not None:
            style.stop()

    if style is not None:
        result.hidden = style.hidden
        result.legend = style.legend()
    else:
        result.legend = [{"label": layer.name().split(" — ")[-1], "color": color,
                          "count": result.count}]
    return result


def _collect(features, result, xform, field_idx, default_height, keep, style, color):
    west = south = float("inf")
    east = north = float("-inf")

    for feat in features:
        c = style.color_for(feat) if style is not None else color
        if c is None:  # QGIS で描かれない地物は書き出さない
            continue
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
        props["C"] = c
        for i, key in keep:
            props[key] = _json_value(feat.attribute(i))

        fid = len(result.features)
        result.features.append(
            {"type": "Feature", "id": fid, "properties": props, "geometry": geometry})

        r = geom.boundingBox()
        west, south = min(west, r.xMinimum()), min(south, r.yMinimum())
        east, north = max(east, r.xMaximum()), max(north, r.yMaximum())

    if result.features:
        result.bbox = [round(v, PRECISION) for v in (west, south, east, north)]
