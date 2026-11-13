# Building Connect & 3D Publish for PLATEAU - QGIS plugin entry point.
# Licensed under GPL-2.0-or-later (see LICENSE).


def classFactory(iface):
    from .plugin import Building3DHtml
    return Building3DHtml(iface)
