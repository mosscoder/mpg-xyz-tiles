"""v3: trace every virtual on the provider to find where rendering bails."""
import faulthandler
import os

faulthandler.enable()

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsDataProvider,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsProviderMetadata,
    QgsProviderRegistry,
    QgsRasterBlock,
    QgsRasterDataProvider,
    QgsRasterLayer,
    QgsRectangle,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QImage

KEY = 'pmtilesraster'
EXTENT = QgsRectangle(-12696489, 5886976, -12686938, 5898912)


def trace(name, *args):
    print(f'  [{name}]', *args, flush=True)


class ProtoProvider(QgsRasterDataProvider):
    def __init__(self, uri, providerOptions=QgsDataProvider.ProviderOptions(), flags=Qgis.DataProviderReadFlags()):
        super().__init__(uri, providerOptions, flags)
        self._uri = uri

    def name(self):
        return KEY

    def description(self):
        return 'prototype'

    def isValid(self):
        trace('isValid')
        return True

    def crs(self):
        trace('crs')
        return QgsCoordinateReferenceSystem('EPSG:3857')

    def extent(self):
        trace('extent')
        return QgsRectangle(EXTENT)

    def bandCount(self):
        trace('bandCount')
        return 1

    def dataType(self, bandNo):
        trace('dataType', bandNo)
        return Qgis.DataType.ARGB32

    def sourceDataType(self, bandNo):
        trace('sourceDataType', bandNo)
        return Qgis.DataType.ARGB32

    def capabilities(self):
        trace('capabilities')
        return Qgis.RasterInterfaceCapabilities(Qgis.RasterInterfaceCapability.Prefetch)

    def block(self, bandNo, boundingBox, width, height, feedback=None):
        trace('block', bandNo, width, height)
        img = QImage(width, height, QImage.Format.Format_ARGB32)
        img.fill(QColor(30, 144, 255))
        blk = QgsRasterBlock(Qgis.DataType.ARGB32, width, height)
        blk.setImage(img)
        return blk

    def readBlock(self, bandNo, viewExtent, width, height, feedback=None):
        trace('readBlock', bandNo, width, height)
        return True

    def xSize(self):
        trace('xSize')
        return 9551

    def ySize(self):
        trace('ySize')
        return 11936

    def clone(self):
        trace('clone')
        p = ProtoProvider(self._uri)
        sip.transferto(p, None)
        return p


class ProtoMetadata(QgsProviderMetadata):
    def __init__(self):
        super().__init__(KEY, 'PMTiles raster (prototype)')

    def createProvider(self, uri, providerOptions, flags=Qgis.DataProviderReadFlags()):
        return ProtoProvider(uri, providerOptions, flags)

    def supportedLayerTypes(self):
        return [Qgis.LayerType.Raster]


QgsApplication.setPrefixPath('/Applications/QGIS-final-4_0_1.app/Contents/MacOS', True)
QgsApplication.setPluginPath('/Applications/QGIS-final-4_0_1.app/Contents/PlugIns/qgis')
QgsApplication.setPkgDataPath('/Applications/QGIS-final-4_0_1.app/Contents/Resources/qgis')
app = QgsApplication([], False)
app.initQgis()

META = ProtoMetadata()
QgsProviderRegistry.instance().registerProvider(META)

layer = QgsRasterLayer('proto://dummy', 'proto', KEY)
print('layer valid:', layer.isValid(), flush=True)

print('--- direct block() call on layer provider ---', flush=True)
blk = layer.dataProvider().block(1, QgsRectangle(EXTENT), 20, 15)
print('direct block result:', blk, 'w', blk.width() if blk else '-', 'valid', blk.isValid() if blk else '-', flush=True)
if blk and blk.isValid():
    print('block pixel(0,0) color value:', hex(blk.value(0, 0)) if blk.dataType() != Qgis.DataType.ARGB32 else 'argb-block', flush=True)

print('--- parallel render job ---', flush=True)
settings = QgsMapSettings()
settings.setLayers([layer])
settings.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
settings.setOutputSize(QSize(200, 150))
settings.setExtent(QgsRectangle(EXTENT))
job = QgsMapRendererParallelJob(settings)
job.start()
job.waitForFinished()
img = job.renderedImage()
c = img.pixelColor(100, 75)
print('rendered center pixel:', (c.red(), c.green(), c.blue()), '-> expect (30, 144, 255)', flush=True)
os._exit(0)
