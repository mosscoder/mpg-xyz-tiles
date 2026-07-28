"""PMTiles v3 archive reader for remote (http/https) and local archives.

Spec: https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md
Reads only what it needs via range requests: 127-byte header, then gzip'd
directories, then tile payloads.

Performance model: tiles are fetched through a shared thread pool over
per-thread persistent HTTPS connections (keep-alive), and a viewport's tile
set is coalesced into a handful of large range requests — Hilbert tile
ordering keeps neighbouring tiles nearly contiguous in the file. Falls back
to QgsBlockingNetworkRequest (which honours QGIS proxy settings) if direct
HTTPS fails.
"""

import gzip
import http.client
import ssl
import struct
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

HEADER_SIZE = 127
TILE_TYPES = {0: None, 1: "application/x-protobuf", 2: "image/png", 3: "image/jpeg", 4: "image/webp", 5: "image/avif"}
COMPRESSIONS = {0: "unknown", 1: "none", 2: "gzip", 3: "brotli", 4: "zstd"}

FETCH_TIMEOUT = 30
COALESCE_GAP = 512 * 1024      # merge ranges separated by less than this
COALESCE_MAX_SPAN = 8 * 1024 * 1024
POOL_WORKERS = 8

_pool = None
_pool_lock = threading.Lock()
_thread_state = threading.local()


class PMTilesError(Exception):
    pass


def _fetch_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=POOL_WORKERS,
                                       thread_name_prefix="pmtiles-fetch")
        return _pool


def _http_range(url, offset, length):
    """Range request over a per-thread persistent HTTPS/HTTP connection."""
    parts = urlsplit(url)
    key = f"conn_{parts.scheme}_{parts.netloc}"
    conn = getattr(_thread_state, key, None)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    last_exc = None
    for attempt in range(2):
        try:
            if conn is None:
                if parts.scheme == "https":
                    conn = http.client.HTTPSConnection(
                        parts.netloc, timeout=FETCH_TIMEOUT,
                        context=ssl.create_default_context())
                else:
                    conn = http.client.HTTPConnection(parts.netloc, timeout=FETCH_TIMEOUT)
            conn.request("GET", path, headers={
                "Range": f"bytes={offset}-{offset + length - 1}",
                "Connection": "keep-alive",
            })
            resp = conn.getresponse()
            data = resp.read()
            if resp.status not in (200, 206):
                raise PMTilesError(f"HTTP {resp.status} fetching {url}")
            setattr(_thread_state, key, conn)
            return data
        except (http.client.HTTPException, OSError) as exc:
            last_exc = exc
            try:
                conn.close()
            except Exception:
                pass
            conn = None
            setattr(_thread_state, key, None)
    raise PMTilesError(f"fetch failed ({last_exc}): {url}")


def _qgis_range(url, offset, length):
    """Fallback fetch through the QGIS network stack (proxy-aware)."""
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtNetwork import QNetworkRequest

    req = QNetworkRequest(QUrl(url))
    req.setRawHeader(b"Range", b"bytes=%d-%d" % (offset, offset + length - 1))
    blocking = QgsBlockingNetworkRequest()
    err = blocking.get(req)
    if err != QgsBlockingNetworkRequest.ErrorCode.NoError:
        raise PMTilesError(f"fetch failed ({blocking.errorMessage()}): {url}")
    data = bytes(blocking.reply().content())
    if not data:
        raise PMTilesError(f"empty range response: {url}")
    return data


def zxy_to_tileid(z, x, y):
    """Cumulative tile count below zoom z + Hilbert curve index at zoom z."""
    acc = ((1 << (2 * z)) - 1) // 3
    d = 0
    s = (1 << z) >> 1
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x = s - 1 - x
                y = s - 1 - y
            x, y = y, x
        s >>= 1
    return acc + d


def _read_varint(buf, pos):
    shift = 0
    result = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _deserialize_directory(data):
    """Gzip'd directory bytes -> list of [tile_id, offset, length, run_length]."""
    buf = gzip.decompress(data)
    pos = 0
    n, pos = _read_varint(buf, pos)
    entries = [[0, 0, 0, 0] for _ in range(n)]
    tid = 0
    for e in entries:
        delta, pos = _read_varint(buf, pos)
        tid += delta
        e[0] = tid
    for e in entries:
        e[3], pos = _read_varint(buf, pos)
    for e in entries:
        e[2], pos = _read_varint(buf, pos)
    for i, e in enumerate(entries):
        v, pos = _read_varint(buf, pos)
        e[1] = entries[i - 1][1] + entries[i - 1][2] if v == 0 and i > 0 else v - 1
    return entries


def _find_entry(entries, tile_id):
    lo, hi = 0, len(entries) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if entries[mid][0] < tile_id:
            lo = mid + 1
        elif entries[mid][0] > tile_id:
            hi = mid - 1
        else:
            return entries[mid]
    if hi >= 0:
        e = entries[hi]
        if e[3] == 0 or tile_id - e[0] < e[3]:
            return e
    return None


class PMTilesArchive:
    """One PMTiles archive; directories and tiles cached, concurrency-safe."""

    def __init__(self, uri):
        self.uri = uri
        self._local_path = None
        if uri.startswith("file://"):
            self._local_path = uri[7:]
        elif not uri.lower().startswith(("http://", "https://")):
            self._local_path = uri
        self._use_qgis_net = False
        self._lock = threading.Lock()
        self._leaf_cache = {}
        self._tile_cache = {}
        self._tile_cache_order = []

        raw = self._fetch(0, 16384)
        if len(raw) < HEADER_SIZE or raw[:7] != b"PMTiles":
            raise PMTilesError(f"not a PMTiles archive: {uri}")
        if raw[7] != 3:
            raise PMTilesError(f"unsupported PMTiles version {raw[7]}: {uri}")
        root_off, root_len = struct.unpack("<QQ", raw[8:24])
        self.leaf_off, self.leaf_len = struct.unpack("<QQ", raw[40:56])
        self.data_off, self.data_len = struct.unpack("<QQ", raw[56:72])
        self.internal_compression = COMPRESSIONS.get(raw[97], "unknown")
        self.tile_compression = COMPRESSIONS.get(raw[98], "unknown")
        self.tile_type = raw[99]
        self.content_type = TILE_TYPES.get(raw[99])
        self.min_zoom = raw[100]
        self.max_zoom = raw[101]
        self.min_lon, self.min_lat, self.max_lon, self.max_lat = (
            v / 1e7 for v in struct.unpack("<iiii", raw[102:118])
        )
        if self.tile_type == 1:
            raise PMTilesError("archive contains vector (MVT) tiles, not raster tiles")
        if self.internal_compression not in ("gzip", "none"):
            raise PMTilesError(f"unsupported internal compression: {self.internal_compression}")

        root_raw = raw[root_off:root_off + root_len] if root_off + root_len <= len(raw) \
            else self._fetch(root_off, root_len)
        self.root = _deserialize_directory(root_raw)

    # --- I/O -------------------------------------------------------------
    def _fetch(self, offset, length):
        if self._local_path is not None:
            with open(self._local_path, "rb") as f:
                f.seek(offset)
                return f.read(length)
        if self._use_qgis_net:
            return _qgis_range(self.uri, offset, length)
        try:
            return _http_range(self.uri, offset, length)
        except PMTilesError:
            # direct HTTPS blocked (proxy environment?) — retry via QGIS stack
            data = _qgis_range(self.uri, offset, length)
            self._use_qgis_net = True
            return data

    # --- directory lookups ----------------------------------------------
    def _leaf(self, offset, length):
        with self._lock:
            cached = self._leaf_cache.get(offset)
        if cached is not None:
            return cached
        entries = _deserialize_directory(self._fetch(self.leaf_off + offset, length))
        with self._lock:
            if len(self._leaf_cache) > 64:
                self._leaf_cache.clear()
            self._leaf_cache[offset] = entries
        return entries

    def _resolve(self, z, x, y):
        """(z, x, y) -> (offset, length) into tile data, or None."""
        if not self.min_zoom <= z <= self.max_zoom:
            return None
        if x < 0 or y < 0 or x >= (1 << z) or y >= (1 << z):
            return None
        tile_id = zxy_to_tileid(z, x, y)
        entries = self.root
        for _ in range(4):  # directory depth is bounded per spec
            e = _find_entry(entries, tile_id)
            if e is None:
                return None
            if e[3] > 0:
                return (e[1], e[2])
            entries = self._leaf(e[1], e[2])
        return None

    # --- tile access -----------------------------------------------------
    def _cache_put(self, key, data):
        with self._lock:
            if key not in self._tile_cache:
                self._tile_cache_order.append(key)
            self._tile_cache[key] = data
            while len(self._tile_cache_order) > 1024:
                old = self._tile_cache_order.pop(0)
                self._tile_cache.pop(old, None)

    def _decompress(self, data):
        if data is not None and self.tile_compression == "gzip":
            return gzip.decompress(data)
        return data

    def tile(self, z, x, y):
        """Raw tile bytes for one tile, or None. Prefer tiles_bulk for many."""
        return self.tiles_bulk([(z, x, y)]).get((z, x, y))

    def cached_tile(self, z, x, y):
        """(found, data) — cache-only lookup, never touches the network."""
        with self._lock:
            key = (z, x, y)
            if key in self._tile_cache:
                return True, self._tile_cache[key]
        return False, None

    def tiles_bulk(self, coords, cancelled=None, on_batch=None):
        """Fetch many tiles at once: coalesced ranges, parallel connections.

        Returns {(z, x, y): bytes | None}. `cancelled` is an optional zero-arg
        callable checked between requests. `on_batch`, if given, is called in
        the calling thread with a {key: data} dict as each coalesced range
        completes (cache hits arrive as the first batch) — this is what makes
        progressive rendering possible.
        """
        out = {}
        first_batch = {}
        wanted = {}  # (offset, length) -> [key, ...]
        for key in coords:
            with self._lock:
                if key in self._tile_cache:
                    out[key] = first_batch[key] = self._tile_cache[key]
                    continue
            loc = self._resolve(*key)
            if loc is None:
                out[key] = first_batch[key] = None
                self._cache_put(key, None)
            else:
                wanted.setdefault(loc, []).append(key)
        if on_batch is not None and first_batch:
            on_batch(first_batch)
        if not wanted:
            return out

        if self._local_path is not None:
            batch = {}
            for (off, ln), keys in wanted.items():
                data = self._decompress(self._fetch(self.data_off + off, ln))
                for k in keys:
                    out[k] = batch[k] = data
                    self._cache_put(k, data)
            if on_batch is not None:
                on_batch(batch)
            return out

        # coalesce contiguous/near-contiguous ranges (Hilbert locality)
        locs = sorted(wanted)
        spans = []  # [span_off, span_end, [locs...]]
        for off, ln in locs:
            if spans and off - spans[-1][1] <= COALESCE_GAP \
                    and off + ln - spans[-1][0] <= COALESCE_MAX_SPAN:
                spans[-1][1] = max(spans[-1][1], off + ln)
                spans[-1][2].append((off, ln))
            else:
                spans.append([off, off + ln, [(off, ln)]])

        def fetch_span(span):
            span_off, span_end, span_locs = span
            if cancelled is not None and cancelled():
                return None
            blob = self._fetch(self.data_off + span_off, span_end - span_off)
            return (span_off, blob, span_locs)

        futures = [_fetch_pool().submit(fetch_span, s) for s in spans]
        for fut in as_completed(futures):
            res = fut.result()
            if res is None:
                continue
            span_off, blob, span_locs = res
            batch = {}
            for off, ln in span_locs:
                data = self._decompress(blob[off - span_off:off - span_off + ln])
                for k in wanted[(off, ln)]:
                    out[k] = batch[k] = data
                    self._cache_put(k, data)
            if on_batch is not None:
                on_batch(batch)
        return out


_archives = {}
_archives_lock = threading.Lock()


def open_archive(uri):
    """Shared archive instances so provider clones reuse directories and caches."""
    with _archives_lock:
        archive = _archives.get(uri)
    if archive is None:
        archive = PMTilesArchive(uri)  # may raise PMTilesError
        with _archives_lock:
            _archives[uri] = archive
    return archive
