"""Raster data provider that renders PMTiles archives (WebP/PNG/JPEG tiles).

QGIS ships no raster-PMTiles reader (GDAL's driver is vector-only until GDAL
3.14), so this provider fills the gap: block() picks a zoom level, fetches the
covering tiles straight from the archive, and mosaics them into an ARGB32
block. Layer URI is simply the archive URL or file path.
"""

import math
import threading
from collections import OrderedDict

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsDataProvider,
    QgsProviderMetadata,
    QgsProviderRegistry,
    QgsRasterBlock,
    QgsRasterDataProvider,
    QgsRectangle,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QImage, QPainter

from .pmtiles_reader import PMTilesError, open_archive

PROVIDER_KEY = "pmtilesraster"
PROVIDER_DESCRIPTION = "PMTiles raster data provider"

WEB_MERCATOR_MAX = 20037508.342789244
TILE_PIXELS = 512  # DroneDeploy/rio-pmtiles publish 512px tiles; decoded size is re-checked per tile
MAX_TILES_PER_BLOCK = 96
OVERZOOM_LEVELS = 4  # preview passes may substitute a parent tile up to this many zooms coarser

# decoded QImage cache shared by all providers/clones: preview redraws re-decode
# the same tiles many times per render without this
_decoded = OrderedDict()
_decoded_lock = threading.Lock()
_DECODED_CAP = 384


def _decode_tile(uri, key, data):
    cache_key = (uri, key)
    with _decoded_lock:
        img = _decoded.get(cache_key)
        if img is not None:
            _decoded.move_to_end(cache_key)
            return img
    img = QImage.fromData(data)
    if img.isNull():
        return None
    with _decoded_lock:
        _decoded[cache_key] = img
        while len(_decoded) > _DECODED_CAP:
            _decoded.popitem(last=False)
    return img

# SIP gotcha (QGIS 4.0, PyQt6): when C++ calls the clone() virtual, the
# returned wrapper has no Python references. If it is garbage collected, SIP
# silently loses dispatch to the Python overrides — dataType() degrades to
# "unknown", the raster pipe refuses the provider, and layers render blank.
# Every clone must therefore be kept referenced until its C++ side is deleted.
_live_clones = []
_live_clones_lock = threading.Lock()


def _retain_clone(provider):
    with _live_clones_lock:
        _live_clones[:] = [p for p in _live_clones if not sip.isdeleted(p)]
        _live_clones.append(provider)


def _lonlat_to_mercator(lon, lat):
    x = lon / 180.0 * WEB_MERCATOR_MAX
    lat = max(-89.9, min(89.9, lat))
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / math.pi * WEB_MERCATOR_MAX
    return x, y


class PMTilesRasterProvider(QgsRasterDataProvider):

    def __init__(self, uri, providerOptions=QgsDataProvider.ProviderOptions(),
                 flags=Qgis.DataProviderReadFlags()):
        super().__init__(uri, providerOptions, flags)
        self._uri = uri
        self._archive = None
        self._error = ""
        self._extent = QgsRectangle()
        self._crs = QgsCoordinateReferenceSystem("EPSG:3857")
        try:
            self._archive = open_archive(uri)
            x0, y0 = _lonlat_to_mercator(self._archive.min_lon, self._archive.min_lat)
            x1, y1 = _lonlat_to_mercator(self._archive.max_lon, self._archive.max_lat)
            self._extent = QgsRectangle(x0, y0, x1, y1)
        except (PMTilesError, OSError) as exc:
            self._error = str(exc)

    # --- identification ------------------------------------------------
    @classmethod
    def providerKey(cls):
        return PROVIDER_KEY

    def name(self):
        return PROVIDER_KEY

    def description(self):
        return PROVIDER_DESCRIPTION

    def isValid(self):
        return self._archive is not None

    def lastErrorTitle(self):
        return "PMTiles"

    def lastError(self):
        return self._error

    def htmlMetadata(self):
        if not self._archive:
            return f"<p>Failed to open: {self._error}</p>"
        a = self._archive
        return (
            f"<p>PMTiles raster archive<br>URI: {self._uri}<br>"
            f"Tile type: {a.content_type}, zoom {a.min_zoom}–{a.max_zoom}<br>"
            f"Bounds (lon/lat): {a.min_lon:.5f}, {a.min_lat:.5f} → "
            f"{a.max_lon:.5f}, {a.max_lat:.5f}</p>"
        )

    # --- raster shape ---------------------------------------------------
    def crs(self):
        return QgsCoordinateReferenceSystem(self._crs)

    def extent(self):
        return QgsRectangle(self._extent)

    def bandCount(self):
        return 1

    def dataType(self, bandNo):
        return Qgis.DataType.ARGB32

    def sourceDataType(self, bandNo):
        return Qgis.DataType.ARGB32

    def capabilities(self):
        return Qgis.RasterInterfaceCapabilities(
            Qgis.RasterInterfaceCapability.Prefetch)

    def maximumTileSize(self):
        # one block per render instead of sequential 2000px chunks: the whole
        # viewport's tiles go out as a single parallel fetch wave
        return QSize(4096, 4096)

    # --- rendering ------------------------------------------------------
    def _pick_zoom(self, map_units_per_pixel):
        a = self._archive
        world = 2 * WEB_MERCATOR_MAX
        if map_units_per_pixel <= 0:
            return a.max_zoom
        # smallest zoom whose tile resolution is at least as fine as requested
        z = math.ceil(math.log2(world / (map_units_per_pixel * TILE_PIXELS)))
        return max(a.min_zoom, min(a.max_zoom, z))

    def block(self, bandNo, boundingBox, width, height, feedback=None):
        blk = QgsRasterBlock(Qgis.DataType.ARGB32, width, height)
        img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 0))
        if self._archive is None or width <= 0 or height <= 0 or boundingBox.isEmpty():
            blk.setImage(img.convertToFormat(QImage.Format.Format_ARGB32))
            return blk

        z = self._pick_zoom(boundingBox.width() / width)
        world = 2 * WEB_MERCATOR_MAX
        tile_span = world / (1 << z)

        tx0 = int(math.floor((boundingBox.xMinimum() + WEB_MERCATOR_MAX) / tile_span))
        tx1 = int(math.floor((boundingBox.xMaximum() + WEB_MERCATOR_MAX) / tile_span))
        ty0 = int(math.floor((WEB_MERCATOR_MAX - boundingBox.yMaximum()) / tile_span))
        ty1 = int(math.floor((WEB_MERCATOR_MAX - boundingBox.yMinimum()) / tile_span))

        # honour the tile budget by coarsening zoom instead of dropping tiles
        while z > self._archive.min_zoom and \
                (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > MAX_TILES_PER_BLOCK:
            z -= 1
            tile_span = world / (1 << z)
            tx0 = int(math.floor((boundingBox.xMinimum() + WEB_MERCATOR_MAX) / tile_span))
            tx1 = int(math.floor((boundingBox.xMaximum() + WEB_MERCATOR_MAX) / tile_span))
            ty0 = int(math.floor((WEB_MERCATOR_MAX - boundingBox.yMaximum()) / tile_span))
            ty1 = int(math.floor((WEB_MERCATOR_MAX - boundingBox.yMinimum()) / tile_span))

        coords = [(z, tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
        x_scale = width / boundingBox.width()
        y_scale = height / boundingBox.height()

        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        def target_rect(tx, ty):
            merc_x = tx * tile_span - WEB_MERCATOR_MAX
            merc_y = WEB_MERCATOR_MAX - ty * tile_span
            px = (merc_x - boundingBox.xMinimum()) * x_scale
            py = (boundingBox.yMaximum() - merc_y) * y_scale
            return QRectF(px, py, tile_span * x_scale, tile_span * y_scale)

        def paint_exact(key, data):
            tile_img = _decode_tile(self._uri, key, data)
            if tile_img is None:
                return
            painter.drawImage(target_rect(key[1], key[2]), tile_img,
                              QRectF(0, 0, tile_img.width(), tile_img.height()))

        def paint_overzoom(key):
            # preview fallback: substitute a cached coarser tile, scaled up —
            # same trick the web viewer uses while real tiles load
            tz, tx, ty = key
            for k in range(1, OVERZOOM_LEVELS + 1):
                pz = tz - k
                if pz < self._archive.min_zoom:
                    return
                found, data = self._archive.cached_tile(pz, tx >> k, ty >> k)
                if not found or not data:
                    continue
                parent_img = _decode_tile(self._uri, (pz, tx >> k, ty >> k), data)
                if parent_img is None:
                    return
                frac = 1 << k
                sw = parent_img.width() / frac
                sh = parent_img.height() / frac
                src = QRectF((tx % frac) * sw, (ty % frac) * sh, sw, sh)
                painter.drawImage(target_rect(tx, ty), parent_img, src)
                return

        preview = feedback is not None and feedback.isPreviewOnly()
        try:
            if preview:
                # cache-only, instant: exact tiles where cached, overzoomed
                # parents elsewhere — never touches the network
                for key in coords:
                    found, data = self._archive.cached_tile(*key)
                    if found and data:
                        paint_exact(key, data)
                    elif not found:
                        paint_overzoom(key)
            else:
                cancelled = (lambda: feedback.isCanceled()) if feedback is not None else None

                def on_batch(batch):
                    for key, data in batch.items():
                        if data:
                            paint_exact(key, data)
                    if feedback is not None:
                        # progressive canvas updates as each range lands
                        feedback.onNewData()

                self._archive.tiles_bulk(coords, cancelled=cancelled, on_batch=on_batch)
        except (PMTilesError, OSError):
            pass  # partial imagery is still worth returning
        finally:
            painter.end()

        blk.setImage(img.convertToFormat(QImage.Format.Format_ARGB32))
        return blk

    def clone(self):
        p = PMTilesRasterProvider(self._uri)
        sip.transferto(p, None)  # C++ owns the clone
        _retain_clone(p)         # ...but Python must keep the wrapper alive
        return p


class PMTilesRasterProviderMetadata(QgsProviderMetadata):

    def __init__(self):
        super().__init__(PROVIDER_KEY, PROVIDER_DESCRIPTION)

    def createProvider(self, uri, providerOptions, flags=Qgis.DataProviderReadFlags()):
        return PMTilesRasterProvider(uri, providerOptions, flags)

    def supportedLayerTypes(self):
        return [Qgis.LayerType.Raster]

    def icon(self):
        from qgis.core import QgsApplication
        return QgsApplication.getThemeIcon("mIconRaster.svg")


_metadata_instance = None  # module-level ref: registry dispatch dies if GC'd


def register_provider():
    global _metadata_instance
    registry = QgsProviderRegistry.instance()
    if PROVIDER_KEY in registry.providerList():
        return
    _metadata_instance = PMTilesRasterProviderMetadata()
    registry.registerProvider(_metadata_instance)
