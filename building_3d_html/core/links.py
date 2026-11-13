"""場所を確かめるための地図の URL（Google マップ・ストリートビュー・地理院地図）。画面には依存しない。
(EN) Build web map URLs (Google Maps / Street View / GSI Maps) for a lon/lat. No network access here;
the URL is only opened in the browser when the user clicks.

どれも、経度・緯度（EPSG:4326）を渡すとブラウザで開く URL を返す。
Google マップは公式の「Maps URLs」の書き方（https://developers.google.com/maps/documentation/urls）。
"""

from urllib.parse import quote, urlencode

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

GOOGLE = "https://www.google.com/maps/search/?"
GOOGLE_AT = "https://www.google.com/maps/@?"
GSI = "https://maps.gsi.go.jp/#{z}/{lat:.6f}/{lon:.6f}/"

# 選べる地図：(キー, メニューに出す名前)
MAPS = [
    ("google", "Google マップ（ピンを立てる）"),
    ("photo", "Google マップの航空写真"),
    ("street", "Google ストリートビュー"),
    ("gsi", "地理院地図"),
]


def to_lonlat(point, crs):
    """point（QgsPointXY、crs の座標）を (経度, 緯度) にする。"""
    xform = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
    p = xform.transform(point)
    return p.x(), p.y()


def url_for(kind, lon, lat):
    at = f"{lat:.6f},{lon:.6f}"
    if kind == "google":
        return GOOGLE + urlencode({"api": 1, "query": at})
    if kind == "photo":
        return GOOGLE_AT + urlencode({"api": 1, "map_action": "map", "center": at, "zoom": 19,
                                      "basemap": "satellite"})
    if kind == "street":
        return GOOGLE_AT + urlencode({"api": 1, "map_action": "pano", "viewpoint": at})
    if kind == "gsi":
        return GSI.format(z=18, lat=lat, lon=lon)
    raise ValueError(kind)


def search_url(text):
    """名前（と住所）で Google マップを検索する URL。Google が思う場所と、点の場所を見比べるのに使う。"""
    return GOOGLE + "api=1&query=" + quote(text)
