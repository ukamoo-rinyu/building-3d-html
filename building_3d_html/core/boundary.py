"""境界：区境界などの面のレイヤを、地面に引く線として書き出す。画面には依存しない。
(EN) Export boundary polygons (e.g. ward borders) as ground lines for the 3D HTML.

- 面の外周（と穴）を線にして描く。属性は入れない（クリックもしない）
- 書き出す建物のまわり（margin 度）にかかる面だけにする。市全体の区境界を選んでも、
  遠くの区まで入れてファイルが大きくならないようにする
"""

import json

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsProject,
    QgsRectangle,
)

from .layer_export import PRECISION, _clean_geometry


class BoundaryResult:
    def __init__(self):
        self.features = []

    @property
    def count(self):
        return len(self.features)

    def geojson(self):
        return {"type": "FeatureCollection", "features": self.features}


def export_boundary(layer, bbox=None, margin=0.02):
    """bbox：書き出す建物の範囲 [西, 南, 東, 北]（EPSG:4326）。なければレイヤ全体。
    margin：bbox のまわりに足す幅（度。0.02度 ≒ 2km）。"""
    result = BoundaryResult()
    project = QgsProject.instance()
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    to_wgs = QgsCoordinateTransform(layer.crs(), wgs, project)
    request = QgsFeatureRequest()
    if bbox:
        area = QgsRectangle(bbox[0] - margin, bbox[1] - margin, bbox[2] + margin, bbox[3] + margin)
        to_layer = QgsCoordinateTransform(wgs, layer.crs(), project)
        request.setFilterRect(to_layer.transformBoundingBox(area))

    for feat in layer.getFeatures(request):
        geom = _clean_geometry(feat.geometry())
        if geom is None:
            continue
        try:
            geom.transform(to_wgs)
            geometry = json.loads(geom.asJson(PRECISION))
        except Exception:
            continue
        result.features.append({"type": "Feature", "id": len(result.features),
                                "properties": {}, "geometry": geometry})
    return result
