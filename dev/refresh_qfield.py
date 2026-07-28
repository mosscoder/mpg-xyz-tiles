"""Regenerate qfield/mpg_ranch_imagery_qfield.qgs from captures.json.

Same XYZ layers as the desktop project; opens centered on the default-on
capture's footprint. After running: re-zip qfield/ and commit (see README).
"""

import json
import math
import os
import struct
import urllib.request

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsReferencedRectangle,
)

CAPTURES_JSON = '/Users/kdoherty/front_country_surveys/viewer_app/dist/captures.json'
OUT = '/Users/kdoherty/mpg_xyz_tiles/qfield/mpg_ranch_imagery_qfield.qgs'
TILE_BASE = 'https://tiles-251613763089.us-west1.run.app'
BUCKET_PREFIX = 'https://storage.googleapis.com/mpg-aerial-survey/'
DEFAULT_ON = '2025-summer'
MAX_ZOOM = {'Drone': 21, 'Fixed-Wing': 19}
MERC = 20037508.342789244

# opening view: centered on the ranch, spanning the whole property
CENTER_LON, CENTER_LAT = -113.9760, 46.7240
VIEW_SPAN_M = 18000


def layer_name(c):
    season = c.get('season') or ('fall' if int(c['date'][5:7]) >= 9 else 'summer')
    return f"{c['date']} {season.capitalize()} {c['source']}"


def xyz_uri(c):
    tileset = c['pmtiles'].removeprefix(BUCKET_PREFIX).removesuffix('.pmtiles')
    url = f'{TILE_BASE}/{tileset}/{{z}}/{{x}}/{{y}}.webp'.replace('{', '%7B').replace('}', '%7D')
    return f"type=xyz&url={url}&zmin=8&zmax={MAX_ZOOM.get(c['source'], 19)}&tilePixelRatio=2"


def header_bounds_3857(pmtiles_url):
    req = urllib.request.Request(pmtiles_url, headers={'Range': 'bytes=0-126'})
    raw = urllib.request.urlopen(req, timeout=30).read()
    lon0, lat0, lon1, lat1 = (v / 1e7 for v in struct.unpack('<iiii', raw[102:118]))
    to_x = lambda lon: lon / 180.0 * MERC
    to_y = lambda lat: math.log(math.tan((90 + lat) * math.pi / 360)) / math.pi * MERC
    return QgsRectangle(to_x(lon0), to_y(lat0), to_x(lon1), to_y(lat1))


QgsApplication.setPrefixPath('/Applications/QGIS-final-4_0_1.app/Contents/MacOS', True)
QgsApplication.setPluginPath('/Applications/QGIS-final-4_0_1.app/Contents/PlugIns/qgis')
QgsApplication.setPkgDataPath('/Applications/QGIS-final-4_0_1.app/Contents/Resources/qgis')
app = QgsApplication([], False)
app.initQgis()

captures = sorted(json.load(open(CAPTURES_JSON)), key=lambda c: c['date'], reverse=True)

project = QgsProject.instance()
crs = QgsCoordinateReferenceSystem('EPSG:3857')
project.setCrs(crs)
project.setTitle('MPG Ranch aerial imagery — QField')
project.writeEntry('Paths', '/Absolute', False)
root = project.layerTreeRoot()

for c in reversed(captures):
    layer = QgsRasterLayer(xyz_uri(c), layer_name(c), 'wms')
    assert layer.isValid(), layer_name(c)
    project.addMapLayer(layer)
    root.findLayer(layer.id()).setItemVisibilityChecked(c['id'] == DEFAULT_ON)

cx = CENTER_LON / 180.0 * MERC
cy = math.log(math.tan((90 + CENTER_LAT) * math.pi / 360)) / math.pi * MERC
half = VIEW_SPAN_M / 2
home = QgsRectangle(cx - half, cy - half, cx + half, cy + half)
project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(home, crs))
print(f'home view centered on {CENTER_LAT}, {CENTER_LON} ({VIEW_SPAN_M} m span)')
assert project.write(OUT)

# QField (and desktop QGIS) restore the saved map-canvas extent, which
# headless writes omit — inject it so the project opens on the home view
canvas = (
    '<mapcanvas annotationsVisible="1" name="theMapCanvas">\n'
    '    <units>meters</units>\n'
    f'    <extent><xmin>{home.xMinimum()}</xmin><ymin>{home.yMinimum()}</ymin>'
    f'<xmax>{home.xMaximum()}</xmax><ymax>{home.yMaximum()}</ymax></extent>\n'
    '    <rotation>0</rotation>\n'
    '    <destinationsrs><spatialrefsys><authid>EPSG:3857</authid></spatialrefsys></destinationsrs>\n'
    '    <rendermaptile>0</rendermaptile>\n'
    '  </mapcanvas>\n  '
)
xml = open(OUT).read()
assert '<mapcanvas' not in xml and '<projectCrs>' in xml
xml = xml.replace('<projectCrs>', canvas + '<projectCrs>', 1)
open(OUT, 'w').write(xml)
print('wrote', OUT, '(+mapcanvas extent)')

project.clear()
assert project.read(OUT)
root = project.layerTreeRoot()
for lyr in root.layerOrder():
    mark = 'ON ' if root.findLayer(lyr.id()).itemVisibilityChecked() else 'off'
    print(f'  [{mark}] {lyr.name()}  valid={lyr.isValid()}')
os._exit(0)
