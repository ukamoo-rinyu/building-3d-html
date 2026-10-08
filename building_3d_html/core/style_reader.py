"""QGIS の色分け（レンダラー）から、地物ごとの塗り色と凡例を読む。

どの地物にどのシンボルが当たるかは QGIS 自身に判定させ（legendKeysForFeature）、
JS 側で分類の判定を再現しない。
"""

from qgis.core import QgsExpressionContextUtils, QgsRenderContext

# 対応している色分け（レンダラーの種類）。段階・ルールベースは後回し
SUPPORTED = {
    "singleSymbol": "単一シンボル",
    "categorizedSymbol": "分類",
}


# 対応していない色分けの、画面に出す名前
OTHER_NAMES = {
    "graduatedSymbol": "段階",
    "RuleRenderer": "ルールベース",
    "nullSymbol": "シンボルなし",
    "invertedPolygonRenderer": "反転ポリゴン",
    "25dRenderer": "2.5D",
    "mergedFeatureRenderer": "地物の結合",
    "embeddedSymbol": "埋め込みシンボル",
}


def renderer_name(layer):
    """色分けの種類の名前（画面の説明用）。"""
    r = layer.renderer()
    t = r.type() if r is not None else ""
    return SUPPORTED.get(t) or OTHER_NAMES.get(t, t)


def is_supported(layer):
    r = layer.renderer()
    return r is not None and r.type() in SUPPORTED


def _hex(color):
    return color.name()  # #rrggbb（透明度は使わない）


class StyleReader:
    """使い方:
        reader = StyleReader(layer)   # 対応していない色分けなら ValueError
        reader.start()
        color = reader.color_for(feature)   # QGIS で描かれない地物は None
        reader.stop()
        reader.legend()   # [{"label", "color", "count"}]（描かれた件数が1以上のもの）
    """

    def __init__(self, layer):
        if not is_supported(layer):
            raise ValueError(f"この色分けにはまだ対応していません：{renderer_name(layer)}")
        self.layer = layer
        self.renderer = layer.renderer().clone()
        self.context = QgsRenderContext()
        self.context.expressionContext().appendScopes(
            QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        # 凡例の項目：キー → (表示名, 色)。レイヤパネルでチェックを外した分類は含めない
        self.items = {}
        self.order = []
        for item in self.renderer.legendSymbolItems():
            symbol = item.symbol()
            if symbol is None:
                continue
            label = item.label() or layer.name().split(" — ")[-1]
            self.items[item.ruleKey()] = (label, _hex(symbol.color()))
            self.order.append(item.ruleKey())
        self.counts = {k: 0 for k in self.order}
        self.hidden = 0  # QGIS で描かれない地物（チェックを外した分類・どの分類にも当たらない）

    def start(self):
        self.renderer.startRender(self.context, self.layer.fields())

    def stop(self):
        self.renderer.stopRender(self.context)

    def color_for(self, feature):
        self.context.expressionContext().setFeature(feature)
        if not self.renderer.willRenderFeature(feature, self.context):
            self.hidden += 1
            return None
        for key in self.renderer.legendKeysForFeature(feature, self.context):
            if key in self.items and self._visible(key):
                self.counts[key] += 1
                return self.items[key][1]
        self.hidden += 1
        return None

    def _visible(self, key):
        return self.renderer.legendSymbolItemChecked(key) if self.renderer.legendSymbolItemsCheckable() else True

    def legend(self):
        return [{"label": self.items[k][0], "color": self.items[k][1], "count": self.counts[k]}
                for k in self.order if self.counts[k] > 0]
