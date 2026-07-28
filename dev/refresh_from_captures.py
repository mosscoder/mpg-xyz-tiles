"""Regenerate mpg_ranch_imagery.qgs from the viewer app's captures.json.

Pure-XYZ build: every layer is a stock QGIS raster-tile layer pointing at the
Cloud Run tile endpoint (see front_country_surveys/tile_server/), so the
project opens in any QGIS >= 3.x with no plugins. Run after a new season is
published (captures.json gains an entry):

  export PYTHONPATH="/Applications/QGIS-final-4_0_1.app/Contents/Resources/python3.11/site-packages"
  export PROJ_DATA="/Applications/QGIS-final-4_0_1.app/Contents/Resources/qgis/proj"
  /Applications/QGIS-final-4_0_1.app/Contents/MacOS/python dev/refresh_from_captures.py
"""

import json
import os

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsReferencedRectangle,
)

CAPTURES_JSON = '/Users/kdoherty/front_country_surveys/viewer_app/dist/captures.json'
OUT = '/Users/kdoherty/mpg_xyz_tiles/mpg_ranch_imagery.qgs'
TILE_BASE = 'https://tiles-251613763089.us-west1.run.app'
BUCKET_PREFIX = 'https://storage.googleapis.com/mpg-aerial-survey/'
DEFAULT_ON = '2025-summer'  # capture id checked by default
MAX_ZOOM = {'Drone': 21, 'Fixed-Wing': 19}

# whole-ranch home view (fixed-wing union, EPSG:3857)
HOME_3857 = QgsRectangle(-12697545, 5884411, -12673984, 5910161)


def layer_name(c):
    season = c.get('season') or ('fall' if int(c['date'][5:7]) >= 9 else 'summer')
    return f"{c['date']} {season.capitalize()} {c['source']}"


def xyz_uri(c):
    tileset = c['pmtiles'].removeprefix(BUCKET_PREFIX).removesuffix('.pmtiles')
    url = f'{TILE_BASE}/{tileset}/{{z}}/{{x}}/{{y}}.webp'.replace('{', '%7B').replace('}', '%7D')
    zmax = MAX_ZOOM.get(c['source'], 19)
    return f'type=xyz&url={url}&zmin=8&zmax={zmax}&tilePixelRatio=2'


QgsApplication.setPrefixPath('/Applications/QGIS-final-4_0_1.app/Contents/MacOS', True)
QgsApplication.setPluginPath('/Applications/QGIS-final-4_0_1.app/Contents/PlugIns/qgis')
QgsApplication.setPkgDataPath('/Applications/QGIS-final-4_0_1.app/Contents/Resources/qgis')
app = QgsApplication([], False)
app.initQgis()

captures = sorted(json.load(open(CAPTURES_JSON)), key=lambda c: c['date'], reverse=True)

project = QgsProject.instance()
crs = QgsCoordinateReferenceSystem('EPSG:3857')
project.setCrs(crs)
project.setTitle('MPG Ranch aerial imagery')
root = project.layerTreeRoot()

for c in reversed(captures):  # addMapLayer stacks on top -> oldest first
    layer = QgsRasterLayer(xyz_uri(c), layer_name(c), 'wms')
    assert layer.isValid(), layer_name(c)
    project.addMapLayer(layer)
    root.findLayer(layer.id()).setItemVisibilityChecked(c['id'] == DEFAULT_ON)

project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(HOME_3857, crs))
assert project.write(OUT)
print('wrote', OUT)

project.clear()
assert project.read(OUT)
root = project.layerTreeRoot()
for lyr in root.layerOrder():
    mark = 'ON ' if root.findLayer(lyr.id()).itemVisibilityChecked() else 'off'
    print(f'  [{mark}] {lyr.name()}  valid={lyr.isValid()}')
os._exit(0)
