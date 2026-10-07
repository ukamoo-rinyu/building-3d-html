"""テンプレート（templates/map3d.html）にデータと MapLibre を埋め込み、1つのHTMLにする。"""

import html
import json
import os
import re

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(PLUGIN_DIR, "templates", "map3d.html")
VENDOR = os.path.join(PLUGIN_DIR, "vendor")

# テンプレート内の置き換え箇所。{{名前}} の形で書く
_SLOT = re.compile(r"\{\{(\w+)\}\}")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _json_for_script(obj):
    """<script> の中に置く JSON。「</」を「<\\/」にしてタグが途中で閉じるのを防ぐ。"""
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/")


def _code_for_script(text):
    """埋め込む JS・CSS がタグを閉じてしまわないようにする（MapLibre 本体には無いが念のため）。"""
    return re.sub(r"</(script|style)", r"<\\/\1", text, flags=re.IGNORECASE)


def build_html(geojson, config):
    """config: title, bbox, attribution（出典の追加表記のリスト）など。"""
    slots = {
        "TITLE": html.escape(config.get("title", "")),
        "MAPLIBRE_CSS": _code_for_script(_read(os.path.join(VENDOR, "maplibre-gl.css"))),
        "MAPLIBRE_JS": _code_for_script(_read(os.path.join(VENDOR, "maplibre-gl.js"))),
        "MAPLIBRE_LICENSE": _read(os.path.join(VENDOR, "LICENSE-maplibre.txt")).replace("--", "- -"),
        "CONFIG_JSON": _json_for_script(config),
        "DATA_JSON": _json_for_script(geojson),
    }
    # 1回の走査で置き換える（埋め込んだ中身に {{…}} があっても再置換しない）
    return _SLOT.sub(lambda m: slots[m.group(1)], _read(TEMPLATE))


def write_html(path, geojson, config):
    text = build_html(geojson, config)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return os.path.getsize(path)
