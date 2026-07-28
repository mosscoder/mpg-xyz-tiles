"""QGIS plugin shell: registers the provider and adds a layer-adding action."""

from qgis.core import QgsRasterLayer
from qgis.PyQt.QtWidgets import QAction, QInputDialog, QLineEdit, QMessageBox

from .provider import PROVIDER_KEY, register_provider

# register at import time so layers in a project being opened resolve even
# before initGui runs
register_provider()


class PMTilesRasterPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        register_provider()
        self.action = QAction("Add PMTiles Raster Layer…", self.iface.mainWindow())
        self.action.triggered.connect(self.add_layer_dialog)
        self.iface.addLayerMenu().addAction(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.addLayerMenu().removeAction(self.action)
            self.action = None
        # provider stays registered: unregistering would break loaded layers

    def add_layer_dialog(self):
        uri, ok = QInputDialog.getText(
            self.iface.mainWindow(),
            "Add PMTiles Raster Layer",
            "PMTiles URL or file path:",
            QLineEdit.EchoMode.Normal,
            "https://",
        )
        if not ok or not uri.strip():
            return
        uri = uri.strip()
        name = uri.rstrip("/").rsplit("/", 1)[-1].removesuffix(".pmtiles") or "pmtiles"
        layer = QgsRasterLayer(uri, name, PROVIDER_KEY)
        if not layer.isValid():
            QMessageBox.warning(
                self.iface.mainWindow(),
                "PMTiles",
                f"Could not open PMTiles archive:\n{uri}\n\n{layer.error().summary()}",
            )
            return
        from qgis.core import QgsProject
        QgsProject.instance().addMapLayer(layer)
