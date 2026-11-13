# 画面（dialog.py）から切り離した処理をまとめる。
# (EN) Processing logic independent of the UI: layer -> GeoJSON, style reading, HTML building,
#      facility-building matching, offline address geocoding, map URLs.
# v0.1: layer_export（レイヤ→GeoJSON）、html_builder（HTML組み立て）
# 予定: style_reader（色分け）、matcher（点と建物の照合）
