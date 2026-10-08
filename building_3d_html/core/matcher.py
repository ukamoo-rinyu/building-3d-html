"""施設（点）と建物（面）の照合（簡易版）。画面には依存しない。

当てはめ方
  1. 点が入っている建物
  2. どれにも入っていなければ、max_distance（m）以内でいちばん近い建物
結果
  - 建物レイヤ：当たった建物の形 ＋ 建物の属性 ＋ 施設の属性 ＋ 施設数・判定・距離m
    1つの建物に複数の施設が当たったら1行にまとめ、施設の属性は「 / 」でつなぐ
  - 建物なしレイヤ：どの建物にも当たらなかった点 ＋ 施設の属性 ＋ 判定
"""

import math

from qgis.core import (
    NULL,
    Qgis,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMemoryProviderUtils,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSpatialIndex,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

# QGIS 3.30 / 3.38 で列挙型・列の型の書き方が変わったため、両方に対応する
try:
    POINT = Qgis.GeometryType.Point
except AttributeError:
    POINT = QgsWkbTypes.PointGeometry
_flag_group = getattr(QgsSpatialIndex, "Flag", QgsSpatialIndex)
STORE_GEOMETRIES = getattr(_flag_group, "FlagStoreFeatureGeometries")
if Qgis.versionInt() >= 33800:
    from qgis.PyQt.QtCore import QMetaType
    T_STRING, T_INT, T_DOUBLE = QMetaType.Type.QString, QMetaType.Type.Int, QMetaType.Type.Double
else:
    T_STRING, T_INT, T_DOUBLE = QVariant.String, QVariant.Int, QVariant.Double

INSIDE = "建物の中"
NEAR = "近くの建物で代用"
NONE = "建物なし"
JOIN = " / "
FACILITY_SUFFIX = "_施設"


class MatchResult:
    def __init__(self):
        self.buildings = None   # 結果の建物レイヤ（メモリ）
        self.unmatched = None   # 建物なしの点レイヤ（メモリ）
        self.inside = 0         # 建物の中に入っていた点
        self.near = 0           # 近くの建物で代用した点
        self.none = 0           # 建物なしの点
        self.skipped = 0        # 位置が空で照合できなかった点
        self.shared = 0         # 複数の施設が当たった建物の数

    @property
    def points(self):
        return self.inside + self.near + self.none + self.skipped


def _text(value):
    if value is None or value == NULL:
        return ""
    if hasattr(value, "toString") and not isinstance(value, str):
        return value.toString()
    return str(value)


def _join(values):
    """空でない値を、重なりを除いて「 / 」でつなぐ（順番は施設の順）。"""
    out = []
    for v in values:
        t = _text(v)
        if t and t not in out:
            out.append(t)
    return JOIN.join(out)


def _unique(name, used):
    n = name
    while n in used:
        n += "_"
    used.add(n)
    return n


def _string_field(name):
    return QgsField(name, T_STRING)


def _to_point(geom):
    """点（マルチポイントなら中心）を QgsPointXY にする。"""
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    if QgsWkbTypes.isMultiType(geom.wkbType()) or geom.type() != POINT:
        return geom.centroid().asPoint()
    return geom.asPoint()


def match(points_layer, buildings_layer, max_distance=30.0, selected_points_only=False):
    """照合して MatchResult を返す。結果のレイヤはプロジェクトにはまだ追加しない。"""
    result = MatchResult()
    project = QgsProject.instance()
    b_crs = buildings_layer.crs()
    to_b = QgsCoordinateTransform(points_layer.crs(), b_crs, project)

    # 距離は m で測る（地理座標系の建物レイヤでも楕円体の上で測る）
    da = QgsDistanceArea()
    da.setSourceCrs(b_crs, project.transformContext())
    da.setEllipsoid(project.ellipsoid() if project.ellipsoid() not in ("", "NONE") else "EPSG:7030")

    index = QgsSpatialIndex(buildings_layer.getFeatures(),
                            flags=STORE_GEOMETRIES)

    # 建物ごとに当たった施設：fid → [(施設の地物, 判定, 距離)]
    hits = {}
    unmatched = []
    request = QgsFeatureRequest()
    if selected_points_only:
        request.setFilterFids(points_layer.selectedFeatureIds())

    for pf in points_layer.getFeatures(request):
        pt = _to_point(pf.geometry())
        if pt is None:
            result.skipped += 1
            continue
        g = QgsGeometry.fromPointXY(pt)
        g.transform(to_b)
        p = g.asPoint()

        fid, status, dist = _find_building(index, da, p, max_distance, b_crs.isGeographic())
        if fid is None:
            result.none += 1
            unmatched.append(pf)
            continue
        if status == INSIDE:
            result.inside += 1
        else:
            result.near += 1
        hits.setdefault(fid, []).append((pf, status, dist))

    result.buildings = _building_layer(buildings_layer, points_layer, hits, index)
    result.unmatched = _unmatched_layer(points_layer, unmatched)
    result.shared = sum(1 for v in hits.values() if len(v) > 1)
    return result


def _find_building(index, da, p, max_distance, geographic):
    # 1. 点が入っている建物（重なっていたら面積の小さいほう）
    inside = [fid for fid in index.intersects(QgsRectangle(p.x(), p.y(), p.x(), p.y()))
              if index.geometry(fid).contains(QgsGeometry.fromPointXY(p))]
    if inside:
        best = min(inside, key=lambda fid: index.geometry(fid).area())
        return best, INSIDE, 0.0

    # 2. max_distance 以内でいちばん近い建物
    if max_distance <= 0:
        return None, NONE, None
    r = max_distance
    if geographic:  # 度に直す（経度方向は緯度で伸びるので広めにとる）
        r = max_distance / 110000.0 / max(0.2, math.cos(math.radians(p.y())))
    box = QgsRectangle(p.x() - r, p.y() - r, p.x() + r, p.y() + r)
    best, best_d = None, None
    for fid in index.intersects(box):
        geom = index.geometry(fid)
        _, closest, _, _ = geom.closestSegmentWithContext(p)
        d = da.measureLine(p, QgsPointXY(closest))
        if d <= max_distance and (best_d is None or d < best_d):
            best, best_d = fid, d
    if best is None:
        return None, NONE, None
    return best, NEAR, best_d


def _facility_fields(points_layer):
    """結果に入れる施設の列の番号。fid などの自動の番号は見る人に意味がないので入れない。"""
    auto_ids = set(points_layer.primaryKeyAttributes())
    return [i for i, f in enumerate(points_layer.fields())
            if i not in auto_ids and f.name().lower() != "fid"]


def _building_layer(buildings_layer, points_layer, hits, index):
    used = set()
    fields = QgsFields()
    for f in buildings_layer.fields():
        f2 = QgsField(f)
        f2.setName(_unique(f.name(), used))
        fields.append(f2)
    point_names = []
    facility_idx = _facility_fields(points_layer)
    for i in facility_idx:
        f = points_layer.fields().at(i)
        name = f.name() if f.name() not in used else f.name() + FACILITY_SUFFIX
        name = _unique(name, used)
        point_names.append(name)
        fields.append(_string_field(name))
    n_name = _unique("施設数", used)
    s_name = _unique("判定", used)
    d_name = _unique("距離m", used)
    fields.append(QgsField(n_name, T_INT))
    fields.append(_string_field(s_name))
    fields.append(QgsField(d_name, T_DOUBLE, len=10, prec=1))

    layer = QgsMemoryProviderUtils.createMemoryLayer(
        "照合結果_建物", fields, buildings_layer.wkbType(), buildings_layer.crs())
    n_b = buildings_layer.fields().count()
    out = []
    if hits:
        req = QgsFeatureRequest().setFilterFids(list(hits.keys()))
        for bf in buildings_layer.getFeatures(req):
            items = hits[bf.id()]
            feat = QgsFeature(fields)
            feat.setGeometry(bf.geometry())
            attrs = list(bf.attributes())
            for i in facility_idx:
                attrs.append(_join(pf.attribute(i) for pf, _, _ in items))
            near = [d for _, s, d in items if s == NEAR]
            attrs.append(len(items))
            attrs.append(NEAR if near else INSIDE)  # 1つでも近くで代用なら要確認
            attrs.append(round(max(near), 1) if near else 0.0)
            assert len(attrs) == n_b + len(point_names) + 3
            feat.setAttributes(attrs)
            out.append(feat)
    layer.dataProvider().addFeatures(out)
    layer.updateExtents()
    return layer


def _unmatched_layer(points_layer, unmatched):
    used = set()
    fields = QgsFields()
    for f in points_layer.fields():
        f2 = QgsField(f)
        f2.setName(_unique(f.name(), used))
        fields.append(f2)
    fields.append(_string_field(_unique("判定", used)))
    layer = QgsMemoryProviderUtils.createMemoryLayer(
        "照合結果_建物なし", fields, points_layer.wkbType(), points_layer.crs())
    out = []
    for pf in unmatched:
        feat = QgsFeature(fields)
        feat.setGeometry(pf.geometry())
        feat.setAttributes(list(pf.attributes()) + [NONE])
        out.append(feat)
    layer.dataProvider().addFeatures(out)
    layer.updateExtents()
    return layer
