"""Debug why createProvider doesn't dispatch: call the registry directly."""
import os

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsDataProvider,
    QgsProviderMetadata,
    QgsProviderRegistry,
)

QgsApplication.setPrefixPath('/Applications/QGIS-final-4_0_1.app/Contents/MacOS', True)
QgsApplication.setPluginPath('/Applications/QGIS-final-4_0_1.app/Contents/PlugIns/qgis')
QgsApplication.setPkgDataPath('/Applications/QGIS-final-4_0_1.app/Contents/Resources/qgis')
app = QgsApplication([], False)
app.initQgis()

KEY = 'pmtilesraster'


class Meta(QgsProviderMetadata):
    def __init__(self):
        super().__init__(KEY, 'debug meta')

    def createProvider(self, *args, **kwargs):
        print('  createProvider dispatched, args:', [type(a).__name__ for a in args], flush=True)
        return None

    def supportedLayerTypes(self):
        return [Qgis.LayerType.Raster]

    def capabilities(self):
        print('  metadata.capabilities() dispatched', flush=True)
        return QgsProviderMetadata.ProviderMetadataCapabilities()


META = Meta()
print('register:', QgsProviderRegistry.instance().registerProvider(META), flush=True)

reg = QgsProviderRegistry.instance()
print('--- direct registry.createProvider call ---', flush=True)
p = reg.createProvider(KEY, 'proto://dummy', QgsDataProvider.ProviderOptions(), Qgis.DataProviderReadFlags())
print('returned:', p, flush=True)

print('--- providerMetadata lookup ---', flush=True)
m = reg.providerMetadata(KEY)
print('metadata obj:', m, 'same python obj:', m is META, flush=True)
print('--- calling m.createProvider directly on looked-up obj ---', flush=True)
p2 = m.createProvider('proto://dummy', QgsDataProvider.ProviderOptions(), Qgis.DataProviderReadFlags())
print('returned:', p2, flush=True)
os._exit(0)
