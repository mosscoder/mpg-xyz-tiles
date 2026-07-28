"""Fail-fast prototype (v2: faulthandler): can a pure-Python QgsRasterDataProvider register, clone,
and render in QGIS 4.0.1? Returns solid-color ARGB32 blocks — no network."""
import os
import faulthandler
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


class ProtoProvider(QgsRasterDataProvider):
    def __init__(self, uri, providerOptions=QgsDataProvider.ProviderOptions(), flags=Qgis.DataProviderReadFlags()):
        super().__init__(uri, providerOptions, flags)
        self._uri = uri

    # --- identification ---
    def name(self):
        return KEY

    def description(self):
        return 'prototype'

    def isValid(self):
        return True

    def crs(self):
        return QgsCoordinateReferenceSystem('EPSG:3857')

    def extent(self):
        return QgsRectangle(EXTENT)

    # --- raster shape ---
    def bandCount(self):
        return 1

    def dataType(self, bandNo):
        return Qgis.DataType.ARGB32

    def sourceDataType(self, bandNo):
        return Qgis.DataType.ARGB32

    def capabilities(self):
        return Qgis.RasterInterfaceCapabilities(Qgis.RasterInterfaceCapability.Prefetch)

    def block(self, bandNo, boundingBox, width, height, feedback=None):
        print('  block() called', bandNo, width, height, flush=True)
        img = QImage(width, height, QImage.Format.Format_ARGB32)
        img.fill(QColor(30, 144, 255))
        blk = QgsRasterBlock(Qgis.DataType.ARGB32, width, height)
        blk.setImage(img)
        return blk

    def clone(self):
        print('  clone() called', flush=True)
        p = ProtoProvider(self._uri)
        sip.transferto(p, None)  # C++ owns the clone; without this SIP deletes it -> use-after-free
        return p


class ProtoMetadata(QgsProviderMetadata):
    def __init__(self):
        super().__init__(KEY, 'PMTiles raster (prototype)')

    def createProvider(self, uri, providerOptions, flags=Qgis.DataProviderReadFlags()):
        print('  createProvider called with uri:', uri, flush=True)
        return ProtoProvider(uri, providerOptions, flags)

    def supportedLayerTypes(self):
        return [Qgis.LayerType.Raster]


QgsApplication.setPrefixPath('/Applications/QGIS-final-4_0_1.app/Contents/MacOS', True)
QgsApplication.setPluginPath('/Applications/QGIS-final-4_0_1.app/Contents/PlugIns/qgis')
QgsApplication.setPkgDataPath('/Applications/QGIS-final-4_0_1.app/Contents/Resources/qgis')
app = QgsApplication([], False)
app.initQgis()

META = ProtoMetadata()  # module-level ref: registry dispatch dies if Python GCs this
ok = QgsProviderRegistry.instance().registerProvider(META)
print('registerProvider:', ok, flush=True)
print('in registry:', KEY in QgsProviderRegistry.instance().providerList(), flush=True)

layer = QgsRasterLayer('proto://dummy', 'proto', KEY)
print('layer valid:', layer.isValid(), flush=True)
print('layer extent:', layer.extent().toString(0), flush=True)
print('renderer:', type(layer.renderer()).__name__ if layer.renderer() else None, flush=True)

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
