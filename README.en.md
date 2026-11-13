# Building Connect & 3D Publish for PLATEAU (QGIS plugin)

[日本語の README はこちら](README.md)

A QGIS plugin that **connects facility records to buildings** (for example
[PLATEAU](https://www.mlit.go.jp/plateau/) 3D city model buildings in Japan)
and **publishes the result as a single-file 3D map HTML**.

It was made by a local government officer to make it easy to show
"which building is which facility" (public housing, schools, offices…) in 3D,
without a web server or programming.

- QGIS 3.28 or later (Qt5 and Qt6)
- License: GPL-2.0-or-later ([LICENSE](LICENSE)).
  Bundled MapLibre GL JS 4.7.1 is BSD-3-Clause (`building_3d_html/vendor/LICENSE-maplibre.txt`).
- The user interface is in Japanese (English is shown next to the main menu items and titles).

## Features

### 1. Connect facilities to buildings

- **Points from addresses** (optional): turn a table of Japanese addresses into points
  using MLIT *Location Reference Information* (位置参照情報, block level).
  This works **offline**; no online geocoding service is used.
- **Match facilities to buildings**: the facility layer can be points (one building each)
  or site polygons (all buildings whose centre is inside the site, e.g. schools or housing estates).
  A point outside any building is matched to the nearest building within a set distance and flagged
  "needs checking".
- **Review panel** (dock): step through the results, move a point into the right building,
  pick one or more buildings by clicking, and re-run the matching. You can also open the place
  in Google Maps / Street View / GSI Maps in your web browser.

### 2. 3D Publish

- Export a polygon layer as **one HTML file** with buildings extruded by a height field
  (`measuredHeight` for PLATEAU is chosen automatically).
- Carries over the layer colours (single symbol or categorized), labels and attributes
  (click a building to see them).
- Optional layers: surrounding buildings (grey), planned buildings, and boundary lines (e.g. ward borders).
- Background map: GSI pale map or aerial photo.
- MapLibre GL JS is embedded, so the file can be shared and opened in any browser.

## How to use

All commands are in the **Web** menu → **Building Connect & 3D Publish for PLATEAU**.

1. Load buildings. For PLATEAU, use the MLIT "PLATEAU QGIS Plugin" to load CityGML
   (`bldg_Building`); LOD0 or LOD1 both work. Any polygon layer with a height field also works.
2. (If your facilities only have addresses) **Points from addresses…**: choose the table,
   the address field, and the folder where you put the Location Reference Information zip files.
3. **Match facilities to buildings…**: choose the facility layer and the building layer.
   Result layers are added to the project, and the review panel opens.
4. **Export 3D map HTML…**: choose the building layer (for example the matched result),
   the height field, and the output file. Open the HTML in your browser.

## Data you need

| Data | Where to get it | Notes |
| --- | --- | --- |
| Building polygons | [PLATEAU (G空間情報センター)](https://www.geospatial.jp/ckan/dataset/plateau) or your own data | Needs a height field, or a default height is used |
| Facility list | Your own data | Points, site polygons, or a table with addresses |
| Location Reference Information (only for addresses) | [MLIT 位置参照情報ダウンロードサービス](https://nlftp.mlit.go.jp/isj/) | Download the **block level (街区レベル)** zip files yourself and keep them zipped in one folder. Not bundled with the plugin. |

## Network access

- The plugin itself does not send any data over the network.
- The exported HTML loads **background tiles from GSI Japan** (`cyberjapandata.gsi.go.jp`)
  when opened in a browser. The buildings are inside the file and are shown without a network.
- The "check location" buttons and the "Check clicked place on web map" tool open
  Google Maps or GSI Maps in your web browser **only when you click them**
  (the coordinates, or the facility name for search, are put in the URL).

## Feedback

Please use [GitHub Issues](https://github.com/ukamoo-rinyu/building-3d-html/issues).
