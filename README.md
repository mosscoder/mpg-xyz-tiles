# mpg-xyz-tiles

MPG Ranch aerial imagery for QGIS and QField — all sixteen published captures
(drone 2024–2025, fixed-wing 2011–2024) as plain XYZ tile layers. No plugins,
no installs: download a project file and open it.

## Downloads

| Platform | File |
|---|---|
| **QGIS (desktop)** | [mpg_ranch_imagery.qgs](https://raw.githubusercontent.com/mosscoder/mpg-xyz-tiles/main/mpg_ranch_imagery.qgs) — right-click → save, open in QGIS |
| **QField (phone)** | [mpg_ranch_imagery_qfield.zip](https://raw.githubusercontent.com/mosscoder/mpg-xyz-tiles/main/qfield/mpg_ranch_imagery_qfield.zip) — import instructions below |

## QField setup — iPhone

1. Download [mpg_ranch_imagery_qfield.zip](https://raw.githubusercontent.com/mosscoder/mpg-xyz-tiles/main/qfield/mpg_ranch_imagery_qfield.zip) in Safari.
2. Open **QField** → **Open local file** → tap the **⋮ / +** button → **Import project from ZIP** → pick the zip from Downloads (QField unzips it itself).
3. Open `mpg_ranch_imagery_qfield.qgs` under **Imported projects**.

If the import menu is missing: extract the zip in the **Files** app, move the folder to **On My iPhone → QField** (launch QField once first if that folder doesn't exist), then QField → Open local file.

## QField setup — Android

1. Download [mpg_ranch_imagery_qfield.zip](https://raw.githubusercontent.com/mosscoder/mpg-xyz-tiles/main/qfield/mpg_ranch_imagery_qfield.zip) in the browser.
2. Open **QField** → **Open local file** → **⋮ menu** (top right) → **Import project from ZIP** → pick the zip from Downloads.
3. Open `mpg_ranch_imagery_qfield.qgs` under **Imported projects**.

## Using the projects

- The map opens centered on the ranch. Only **2025-07-01 Summer Drone** is on
  by default (QGIS) — toggle other captures in the layers panel.
- **Zoom to layer** only works on the **Coverage footprints** layer (QField) —
  XYZ imagery layers claim the whole world as their extent.
- Streaming only: imagery needs internet. First view of a cold area takes a
  few seconds; pans and revisits are fast.

## How it works

Layers stream from a serverless tile endpoint that translates the published
PMTiles archives on the fly:

    https://tiles-251613763089.us-west1.run.app/{tileset}/{z}/{x}/{y}.webp

`{tileset}` is the archive's bucket key without `.pmtiles`, e.g.
`surveys/2025_front_country/summer/processing/drone_deploy/visible`.
Endpoint infrastructure (deploy/teardown/billing notes) lives in the
`front_country_surveys` repo under `tile_server/`.

Hand-adding a layer? Min zoom 8, max zoom **21** (drone) / **19** (fixed-wing),
tile resolution **512 px** (`tilePixelRatio=2`).

## Maintenance

- New season published → `dev/refresh_from_captures.py` regenerates the QGIS
  project from the viewer app's `captures.json`; re-zip `qfield/` and commit.
- `archive_plugin/` holds the retired `pmtiles_raster` QGIS plugin (a native
  raster-PMTiles reader; replaced by this XYZ approach). Its README documents
  the QGIS 4 SIP pitfalls for posterity.
- Everything here retires once QGIS/QField ship GDAL ≥ 3.14, which reads
  raster PMTiles natively.
