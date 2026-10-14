"""住所から点を作る（国土交通省「位置参照情報」を使う。ネットにはつながない）。画面には依存しない。

位置参照情報（街区レベル）の CSV（zip のままでもよい）を読み、
  1. 「〇丁目〇番」まで一致 → 街区の代表点（一致した細かさ＝街区）
  2. 「〇丁目」まで一致     → その丁目の街区の代表点の平均（一致した細かさ＝町丁目）
  3. どちらも見つからない   → 位置は空（一致した細かさ＝見つからない）
住所の書き方の違い（全角・半角、「三丁目」「3丁目」「3-」、「大阪府」「大阪市」のあるなし）は、
比べる前にそろえる。
"""

import csv
import io
import os
import re
import unicodedata
import zipfile

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMemoryProviderUtils,
    QgsPointXY,
    QgsWkbTypes,
)

from .matcher import T_STRING, _text, _unique

BLOCK = "街区"
TOWN = "町丁目"
NOT_FOUND = "見つからない"

_KANJI = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_KANJI_NUM = "〇一二三四五六七八九十百"
_DASH = "-‐‑–—―−ｰー－"


def _kanji_to_int(s):
    """「十二」「二十」「百五」などの漢数字を数にする。"""
    total, cur = 0, 0
    for ch in s:
        if ch == "百":
            total += (cur or 1) * 100
            cur = 0
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        else:
            cur = cur * 10 + _KANJI[ch]
    return total + cur


def normalize(s):
    """住所をそろえる：全角→半角、空白を除く、「三丁目」→「3丁目」、数字の間の「ー」「－」→「-」、「ヶ」→「ケ」。"""
    s = unicodedata.normalize("NFKC", _text(s)).replace(" ", "").replace("　", "")
    s = re.sub(f"([{_KANJI_NUM}]+)丁目", lambda m: f"{_kanji_to_int(m.group(1))}丁目", s)
    s = re.sub(f"(?<=\\d)[{_DASH}](?=\\d)", "-", s)
    return s.replace("ヶ", "ケ").replace("ヵ", "カ")


class AddressBook:
    """位置参照情報を読み込んだもの。市区町村 → 町丁目 → 街区 → (経度, 緯度)。"""

    def __init__(self):
        self.blocks = {}   # (市区町村, 町丁目) → {街区符号: (経度, 緯度)}
        self.towns = {}    # (市区町村, 町丁目) → (経度, 緯度)（街区の平均）
        self.cities = {}   # 市区町村 → [町丁目, …]（長い順。前から一致を探すため）
        self.files = 0

    def load(self, folder):
        """folder の中の CSV と zip（中の CSV）をすべて読む。"""
        for root, _, names in os.walk(folder):
            for name in sorted(names):
                path = os.path.join(root, name)
                low = name.lower()
                if low.endswith(".csv"):
                    with open(path, "rb") as f:
                        self._read(f.read())
                elif low.endswith(".zip"):
                    with zipfile.ZipFile(path) as z:
                        for n in z.namelist():
                            if n.lower().endswith(".csv"):
                                self._read(z.read(n))
        self._finish()
        return self

    def _read(self, data):
        try:
            text = data.decode("cp932")
        except UnicodeDecodeError:
            text = data.decode("utf-8-sig")
        rows = csv.reader(io.StringIO(text))
        header = next(rows, None)
        if not header or "街区符号・地番" not in header:
            return  # 位置参照情報の街区レベルではない CSV は読まない
        col = {h: i for i, h in enumerate(header)}
        i_city, i_town, i_koaza = col["市区町村名"], col["大字・丁目名"], col.get("小字・通称名")
        i_block, i_lat, i_lon = col["街区符号・地番"], col["緯度"], col["経度"]
        i_rep, i_old = col.get("代表フラグ"), col.get("更新前履歴フラグ")
        for r in rows:
            if len(r) < len(header):
                continue
            if i_old is not None and r[i_old] == "1":
                continue  # 住所が変わる前の古い記録
            city = normalize(r[i_city])
            town = normalize(r[i_town] + (r[i_koaza] if i_koaza is not None else ""))
            block = normalize(r[i_block])
            try:
                xy = (float(r[i_lon]), float(r[i_lat]))
            except ValueError:
                continue
            blocks = self.blocks.setdefault((city, town), {})
            # 1つの街区に複数の点があるときは、代表の点を使う
            if block not in blocks or (i_rep is not None and r[i_rep] == "1"):
                blocks[block] = xy
        self.files += 1

    def _finish(self):
        self.towns, cities = {}, {}
        for (city, town), blocks in self.blocks.items():
            xs = [p[0] for p in blocks.values()]
            ys = [p[1] for p in blocks.values()]
            self.towns[(city, town)] = (sum(xs) / len(xs), sum(ys) / len(ys))
            cities.setdefault(city, []).append(town)
        self.cities = {c: sorted(ts, key=len, reverse=True) for c, ts in cities.items()}

    @property
    def block_count(self):
        return sum(len(b) for b in self.blocks.values())

    # ---- 探す ------------------------------------------------------------------

    def find(self, address):
        """(経度, 緯度) または None、一致した細かさ、一致した住所 を返す。"""
        s = normalize(address)
        city, rest = self._city(s)
        if city is None:
            return None, NOT_FOUND, ""
        town, rest = self._town(city, rest)
        if town is None:
            return None, NOT_FOUND, city
        m = re.match(r"-?(\d+)", rest)
        if m:
            xy = self.blocks[(city, town)].get(m.group(1))
            if xy is not None:
                return xy, BLOCK, f"{city}{town}{m.group(1)}"
        return self.towns[(city, town)], TOWN, city + town

    def _city(self, s):
        """市区町村を前から探す。「大阪府」「大阪市」が省かれていてもよい（区の名前だけでも探す）。"""
        best = None
        for city in self.cities:
            for name in _city_variants(city):
                i = s.find(name)
                # 住所の頭（都道府県名のあと）で一致したものだけ。長く一致したほうを使う
                if i >= 0 and i <= 4 and (best is None or len(name) > best[1]):
                    best = (city, len(name), i + len(name))
        if best is None:
            return None, s
        return best[0], s[best[2]:]

    def _town(self, city, rest):
        # 「上町1-15-1」のように丁目を「-」で書いたもの（「上町」という丁目のない町より先に見る）
        m = re.match(r"(.+?)(\d+)-", rest)
        if m:
            town = f"{m.group(1)}{m.group(2)}丁目"
            if (city, town) in self.towns:
                return town, rest[m.end() - 1:]
        for town in self.cities[city]:  # 長い順なので、「上町1丁目」は「上町」より先に当たる
            if rest.startswith(town):
                return town, rest[len(town):]
        return None, rest


def _city_variants(city):
    """「大阪市中央区」なら「大阪市中央区」「中央区」。政令市の区は区の名前だけでも探す。"""
    out = [city]
    m = re.match(r".+?市(.+区)$", city)
    if m:
        out.append(m.group(1))
    return out


class GeocodeResult:
    def __init__(self):
        self.layer = None   # 結果の点レイヤ（メモリ、緯度経度）
        self.block = 0      # 街区まで一致
        self.town = 0       # 町丁目まで一致
        self.none = 0       # 見つからない（空の住所も含む）
        self.examples = []  # 見つからなかった住所の例（最初の数件）


def geocode(layer, address_field, book, selected_only=False):
    """layer の各行の住所から点を作り、GeocodeResult を返す。結果のレイヤはまだプロジェクトに足さない。"""
    result = GeocodeResult()
    used = set()
    fields = QgsFields()
    for f in layer.fields():
        f2 = QgsField(f)
        f2.setName(_unique(f.name(), used))
        fields.append(f2)
    fields.append(QgsField(_unique("住所の一致", used), T_STRING))
    fields.append(QgsField(_unique("一致した住所", used), T_STRING))

    out = QgsMemoryProviderUtils.createMemoryLayer(
        f"{layer.name()}_住所から点", fields, QgsWkbTypes.Point, QgsCoordinateReferenceSystem("EPSG:4326"))
    request = QgsFeatureRequest().setFlags(_no_geometry())
    if selected_only:
        request.setFilterFids(layer.selectedFeatureIds())
    idx = layer.fields().indexOf(address_field)
    feats = []
    for src in layer.getFeatures(request):
        address = _text(src.attribute(idx))
        xy, level, hit = book.find(address) if address else (None, NOT_FOUND, "")
        feat = QgsFeature(fields)
        if xy is not None:
            feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*xy)))
        feat.setAttributes(list(src.attributes()) + [level, hit])
        feats.append(feat)
        if level == BLOCK:
            result.block += 1
        elif level == TOWN:
            result.town += 1
        else:
            result.none += 1
            if len(result.examples) < 5:
                result.examples.append(address or "（空）")
    out.dataProvider().addFeatures(feats)
    out.updateExtents()
    result.layer = out
    return result


def _no_geometry():
    group = getattr(QgsFeatureRequest, "Flag", QgsFeatureRequest)
    return getattr(group, "NoGeometry")
