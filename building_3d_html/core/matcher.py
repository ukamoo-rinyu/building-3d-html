"""施設（点または敷地の面）と建物（面）の照合（簡易版）。画面には依存しない。
(EN) Match facilities (points or site polygons) to building polygons: containing building,
nearest building within a distance, or buildings whose centroid is inside the site.

当てはめ方（施設が点のとき：1つの施設に1棟）
  1. 点が入っている建物
  2. どれにも入っていなければ、max_distance（m）以内でいちばん近い建物
当てはめ方（施設が面＝敷地のとき：1つの施設に何棟でも）
  - 中心（建物の内側にとった点）が敷地に入っている建物を、すべて当てはめる
手で指定した施設（施設レイヤの「指定した建物」の列に建物のIDが入っているもの）
  - 位置にかかわらず、その建物（何棟でも）を当てはめる。「照合の確認・調整」で建物を選ぶと入る
結果
  - 建物レイヤ：当たった建物の形 ＋ 建物の属性 ＋ 施設の属性 ＋ 施設数・判定・距離m
    1つの建物に複数の施設が当たったら1行にまとめ、施設の属性は「 / 」でつなぐ
  - 建物なしレイヤ：どの建物にも当たらなかった施設 ＋ 施設の属性 ＋ 判定
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
    POINT, POLYGON = Qgis.GeometryType.Point, Qgis.GeometryType.Polygon
except AttributeError:
    POINT, POLYGON = QgsWkbTypes.PointGeometry, QgsWkbTypes.PolygonGeometry
_flag_group = getattr(QgsSpatialIndex, "Flag", QgsSpatialIndex)
STORE_GEOMETRIES = getattr(_flag_group, "FlagStoreFeatureGeometries")
if Qgis.versionInt() >= 33800:
    from qgis.PyQt.QtCore import QMetaType
    T_STRING, T_INT, T_DOUBLE = QMetaType.Type.QString, QMetaType.Type.Int, QMetaType.Type.Double
else:
    T_STRING, T_INT, T_DOUBLE = QVariant.String, QVariant.Int, QVariant.Double

INSIDE = "建物の中"
NEAR = "近くの建物で代用"
SITE = "敷地の中"
MANUAL = "手で指定"
NONE = "建物なし"
MANUAL_FIELD = "指定した建物"   # 施設レイヤの列。建物のIDを KEY_SEP でつないで入れる
KEY_SEP = ";"
# 建物のIDの列（この順に探す）。なければ建物レイヤの fid を「fid:12」の形で使う
KEY_FIELDS = ("建物ID", "buildingID", "gml_id", "bldg_id", "id", "ID")
JOIN = " / "
FACILITY_SUFFIX = "_施設"


class Check:
    """照合した施設1件。要確認（近くの建物で代用・建物なし）と、照合できた施設（建物の中・敷地の中）の両方に使う。"""

    def __init__(self, point_fid, status, building_fid=None, distance=None, building_fids=None):
        self.point_fid = point_fid        # 施設レイヤでの fid
        self.status = status              # NEAR・NONE（要確認）か INSIDE・SITE・MANUAL（照合できた）
        self.building_fid = building_fid  # 当てはめた建物（建物レイヤでの fid）。建物なしなら None
        self.distance = distance          # 建物までの距離（m）
        # 当てはめた建物すべて（敷地のときは何棟もある）
        self.building_fids = building_fids or ([building_fid] if building_fid is not None else [])


class MatchResult:
    def __init__(self):
        self.buildings = None   # 結果の建物レイヤ（メモリ）
        self.unmatched = None   # 建物なしの点レイヤ（メモリ）
        self.inside = 0         # 建物の中に入っていた点
        self.site = 0           # 敷地の中に建物があった施設（面）
        self.site_buildings = 0 # 敷地の中にあった建物の数（重なりは1棟と数える）
        self.manual = 0         # 手で指定した建物を当てた施設
        self.near = 0           # 近くの建物で代用した点
        self.none = 0           # 建物なしの点
        self.skipped = 0        # 位置が空で照合できなかった点
        self.shared = 0         # 複数の施設が当たった建物の数
        self.checks = []        # 要確認の施設：Check のリスト（施設の順）
        self.matched = []       # 照合できた施設（建物の中・敷地の中・手で指定）：Check のリスト（施設の順）

    @property
    def points(self):
        return self.inside + self.site + self.manual + self.near + self.none + self.skipped


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


def building_key_field(layer):
    """建物のIDの列の名前。なければ None（fid を使う）。"""
    for n in KEY_FIELDS:
        if layer.fields().indexOf(n) >= 0:
            return n
    return None


def building_key(feature, key_field):
    """手で指定するときに施設レイヤへ書く、建物1棟のID。"""
    if key_field:
        key = _text(feature.attribute(key_field))
        if key:
            return key
    return f"fid:{feature.id()}"


def parse_keys(value):
    """「指定した建物」の列の値を、建物のIDのリストにする。"""
    return [k.strip() for k in _text(value).split(KEY_SEP) if k.strip()]


def _key_lookup(buildings_layer):
    """建物のID → 建物の fid。"""
    key_field = building_key_field(buildings_layer)
    req = QgsFeatureRequest().setFlags(_no_geometry())
    if key_field:
        req.setSubsetOfAttributes([key_field], buildings_layer.fields())
    else:
        req.setNoAttributes()
    lookup = {}
    for f in buildings_layer.getFeatures(req):
        lookup.setdefault(building_key(f, key_field), f.id())
        lookup.setdefault(f"fid:{f.id()}", f.id())
    return lookup


def _no_geometry():
    flags = getattr(QgsFeatureRequest, "Flag", QgsFeatureRequest)
    return getattr(flags, "NoGeometry")


def is_site_layer(layer):
    """施設のレイヤが面（敷地）なら True。"""
    return layer.geometryType() == POLYGON


def match(points_layer, buildings_layer, max_distance=30.0, selected_points_only=False):
    """照合して MatchResult を返す。結果のレイヤはプロジェクトにはまだ追加しない。

    points_layer は施設のレイヤ。面のレイヤなら敷地として扱い、max_distance は使わない。"""
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

    sites = is_site_layer(points_layer)
    centers = {}  # 建物の fid → 建物の内側にとった点（敷地のとき使う）
    manual_idx = points_layer.fields().indexOf(MANUAL_FIELD)
    lookup = None
    for pf in points_layer.getFeatures(request):
        # 手で指定した建物があれば、位置にかかわらずそれを当てる（見つからないIDは無視して、ふつうに照合する）
        keys = parse_keys(pf.attribute(manual_idx)) if manual_idx >= 0 else []
        if keys:
            if lookup is None:
                lookup = _key_lookup(buildings_layer)
            fids = list(dict.fromkeys(lookup[k] for k in keys if k in lookup))
            if fids:
                result.manual += 1
                result.matched.append(Check(pf.id(), MANUAL, fids[0], 0.0, fids))
                for fid in fids:
                    hits.setdefault(fid, []).append((pf, MANUAL, 0.0))
                continue
        if sites:
            fids = _find_buildings_in_site(index, centers, pf.geometry(), to_b)
            if fids is None:
                result.skipped += 1
            elif not fids:
                result.none += 1
                unmatched.append(pf)
            else:
                result.site += 1
                result.matched.append(Check(pf.id(), SITE, fids[0], 0.0, fids))
                for fid in fids:
                    hits.setdefault(fid, []).append((pf, SITE, 0.0))
            continue

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
            result.checks.append(Check(pf.id(), NONE))
            continue
        if status == INSIDE:
            result.inside += 1
            result.matched.append(Check(pf.id(), INSIDE, fid, 0.0))
        else:
            result.near += 1
            result.checks.append(Check(pf.id(), NEAR, fid, dist))
        hits.setdefault(fid, []).append((pf, status, dist))

    result.buildings = _building_layer(buildings_layer, points_layer, hits, index)
    result.unmatched = _unmatched_layer(points_layer, unmatched)
    result.shared = sum(1 for v in hits.values() if len(v) > 1)
    if sites:
        result.site_buildings = len(hits)
    return result


def _find_buildings_in_site(index, centers, geom, to_b):
    """敷地の面に中心が入っている建物の fid のリスト。敷地の形が空なら None。"""
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    site = QgsGeometry(geom)
    site.transform(to_b)
    engine = QgsGeometry.createGeometryEngine(site.constGet())
    engine.prepareGeometry()
    found = []
    for fid in index.intersects(site.boundingBox()):
        if fid not in centers:
            centers[fid] = index.geometry(fid).pointOnSurface()
        c = centers[fid]
        if not c.isEmpty() and engine.contains(c.constGet()):
            found.append(fid)
    return found


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
            if near:  # 1つでも近くで代用なら要確認
                attrs.append(NEAR)
            elif any(s == MANUAL for _, s, _ in items):
                attrs.append(MANUAL)
            else:
                attrs.append(items[0][1])  # 建物の中 または 敷地の中
            attrs.append(round(max(near), 1) if near else 0.0)
            # 列の数が合わないのはプログラムの誤り。黙って列をずらさずに止める
            # (EN) A column count mismatch is a programming error: stop instead of shifting columns.
            if len(attrs) != n_b + len(point_names) + 3:
                raise RuntimeError("照合結果の列の数が合いません / result column count mismatch")
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
