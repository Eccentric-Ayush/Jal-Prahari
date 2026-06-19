# backend/app/tests/test_dem_parser.py
#
# ─── Purpose ──────────────────────────────────────────────────────────────────
# Demonstrate and validate all public behaviours of the DEM parsing module.
#
# ─── Running ──────────────────────────────────────────────────────────────────
# From the project root (backend/):
#
#   python -m pytest app/tests/test_dem_parser.py -v
#
# Or run this file directly as a standalone script:
#
#   python app/tests/test_dem_parser.py
#
# ─── Fixture strategy ─────────────────────────────────────────────────────────
# The tests create a small synthetic GeoTIFF in a temporary directory using
# rasterio's MemoryFile + write API.  This means:
#   • No real DEM download is required.
#   • Tests are fully deterministic — elevation values are known in advance.
#   • Tests run offline and in CI environments without external data.
#
# The synthetic DEM covers a 1-degree tile centred over Mumbai (EPSG:4326):
#   Left=72.0, Bottom=18.5, Right=73.0, Top=19.5
#   Size: 10 × 10 pixels  (resolution: 0.1° per pixel)
#   Band 1: elevation in metres, filled with deterministic values.
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
import rasterio
import rasterio.crs
import rasterio.transform

# ── Ensure the backend/ directory is on sys.path when run as __main__ ─────────
_BACKEND_DIR = Path(__file__).resolve().parents[2]  # .../backend/
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.dem_parser import (
    CoordinateOutOfBoundsError,
    DEMParser,
    load_dem,
)
from app.core.logger import get_logger

log = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Fixture helpers
# ══════════════════════════════════════════════════════════════════════════════

# Geographic extent of the synthetic DEM (WGS84)
TILE_LEFT   = 72.0
TILE_BOTTOM = 18.5
TILE_RIGHT  = 73.0
TILE_TOP    = 19.5
TILE_WIDTH  = 10    # pixels
TILE_HEIGHT = 10    # pixels

# Build affine transform: top-left corner, positive-x-east, negative-y-north
TILE_TRANSFORM = rasterio.transform.from_bounds(
    west=TILE_LEFT,
    south=TILE_BOTTOM,
    east=TILE_RIGHT,
    north=TILE_TOP,
    width=TILE_WIDTH,
    height=TILE_HEIGHT,
)

# Elevation grid: row 0 = northernmost strip, row 9 = southernmost strip.
# Values increase from north to south and west to east for easy manual verification.
ELEVATION_GRID = np.array(
    [
        [10, 11, 12, 13, 14, 15, 16, 17, 18, 19],  # row 0  (top / northernmost)
        [20, 21, 22, 23, 24, 25, 26, 27, 28, 29],  # row 1
        [30, 31, 32, 33, 34, 35, 36, 37, 38, 39],  # row 2
        [40, 41, 42, 43, 44, 45, 46, 47, 48, 49],  # row 3
        [50, 51, 52, 53, 54, 55, 56, 57, 58, 59],  # row 4
        [60, 61, 62, 63, 64, 65, 66, 67, 68, 69],  # row 5
        [70, 71, 72, 73, 74, 75, 76, 77, 78, 79],  # row 6
        [80, 81, 82, 83, 84, 85, 86, 87, 88, 89],  # row 7
        [90, 91, 92, 93, 94, 95, 96, 97, 98, 99],  # row 8
        [100,101,102,103,104,105,106,107,108,109],  # row 9  (bottom / southernmost)
    ],
    dtype=np.float32,
)

NODATA_VALUE = -9999.0


def create_synthetic_dem(tmp_path: Path, include_nodata: bool = False) -> Path:
    """
    Write a small synthetic GeoTIFF DEM to *tmp_path* and return its path.

    Parameters
    ----------
    tmp_path : Path
        Directory where the file is created.
    include_nodata : bool
        If True, set pixel (9, 9) to the NODATA value to test NoData handling.
    """
    dem_path = tmp_path / "synthetic_dem.tif"
    grid = ELEVATION_GRID.copy()

    if include_nodata:
        grid[9, 9] = NODATA_VALUE

    with rasterio.open(
        dem_path,
        mode="w",
        driver="GTiff",
        height=TILE_HEIGHT,
        width=TILE_WIDTH,
        count=1,
        dtype="float32",
        crs=rasterio.crs.CRS.from_epsg(4326),
        transform=TILE_TRANSFORM,
        nodata=NODATA_VALUE if include_nodata else None,
    ) as dst:
        dst.write(grid, 1)

    log.debug("Synthetic DEM created: %s", dem_path)
    return dem_path


# ══════════════════════════════════════════════════════════════════════════════
# pytest fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def dem_path(tmp_path):
    """Standard synthetic DEM without NoData."""
    return create_synthetic_dem(tmp_path)


@pytest.fixture
def dem_path_with_nodata(tmp_path):
    """Synthetic DEM with one NoData pixel at (row=9, col=9)."""
    return create_synthetic_dem(tmp_path, include_nodata=True)


# ══════════════════════════════════════════════════════════════════════════════
# Test Section 1 — File Loading
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadDEM:
    """Tests for the load_dem() factory function."""

    def test_load_valid_dem_returns_dem_parser(self, dem_path):
        """load_dem() should return a DEMParser instance for a valid GeoTIFF."""
        print(f"\n[TEST] Loading valid DEM: {dem_path}")
        with load_dem(dem_path) as parser:
            assert isinstance(parser, DEMParser)
            assert not parser.dataset.closed
        print("  ✔ DEMParser created and dataset is open")

    def test_load_accepts_string_path(self, dem_path):
        """load_dem() must accept a plain string path, not just pathlib.Path."""
        with load_dem(str(dem_path)) as parser:
            assert isinstance(parser, DEMParser)
        print("  ✔ String path accepted")

    def test_load_missing_file_raises_file_not_found(self, tmp_path):
        """load_dem() must raise FileNotFoundError for a non-existent path."""
        missing = tmp_path / "ghost.tif"
        print(f"\n[TEST] Loading non-existent file: {missing}")
        with pytest.raises(FileNotFoundError) as exc_info:
            load_dem(missing)
        print(f"  ✔ FileNotFoundError raised: {exc_info.value}")

    def test_load_wrong_extension_raises_value_error(self, tmp_path):
        """load_dem() must reject files that are not .tif or .tiff."""
        bad_ext = tmp_path / "dem.nc"
        bad_ext.touch()
        print(f"\n[TEST] Loading file with wrong extension: {bad_ext.suffix}")
        with pytest.raises(ValueError, match="GeoTIFF"):
            load_dem(bad_ext)
        print("  ✔ ValueError raised for unsupported extension")

    def test_load_corrupted_file_raises_rasterio_error(self, tmp_path):
        """load_dem() must raise RasterioIOError for a corrupted GeoTIFF."""
        corrupted = tmp_path / "corrupted.tif"
        corrupted.write_bytes(b"this is not a valid GeoTIFF file at all")
        print(f"\n[TEST] Loading corrupted GeoTIFF")
        with pytest.raises(rasterio.errors.RasterioIOError):
            load_dem(corrupted)
        print("  ✔ RasterioIOError raised for corrupted file")

    def test_load_wrong_type_raises_type_error(self):
        """load_dem() must raise TypeError for non-path input types."""
        with pytest.raises(TypeError):
            load_dem(12345)
        print("  ✔ TypeError raised for integer input")


# ══════════════════════════════════════════════════════════════════════════════
# Test Section 2 — Elevation Lookup
# ══════════════════════════════════════════════════════════════════════════════

class TestGetElevation:
    """Tests for DEMParser.get_elevation()."""

    def test_elevation_top_left_pixel(self, dem_path):
        """
        Pixel (row=0, col=0) covers lat∈[19.4, 19.5), lon∈[72.0, 72.1).
        Centre of pixel: lat=19.45, lon=72.05 → expected elevation = 10.
        """
        lat, lon = 19.45, 72.05
        print(f"\n[TEST] Elevation at top-left pixel (lat={lat}, lon={lon})")
        with load_dem(dem_path) as parser:
            elev = parser.get_elevation(lat, lon)
        print(f"  Result: {elev:.2f} m  (expected: 10.00 m)")
        assert elev == pytest.approx(10.0, abs=0.1)
        print("  ✔ Elevation matches expected value")

    def test_elevation_bottom_right_pixel(self, dem_path):
        """
        Pixel (row=9, col=9) covers lat∈[18.5, 18.6), lon∈[72.9, 73.0).
        Centre of pixel: lat=18.55, lon=72.95 → expected elevation = 109.
        """
        lat, lon = 18.55, 72.95
        print(f"\n[TEST] Elevation at bottom-right pixel (lat={lat}, lon={lon})")
        with load_dem(dem_path) as parser:
            elev = parser.get_elevation(lat, lon)
        print(f"  Result: {elev:.2f} m  (expected: 109.00 m)")
        assert elev == pytest.approx(109.0, abs=0.1)
        print("  ✔ Elevation matches expected value")

    def test_elevation_centre_pixel(self, dem_path):
        """
        Centre of the tile: lat≈19.0, lon≈72.5 → falls in pixel (row≈4, col≈5).
        Expected elevation = ELEVATION_GRID[4][5] = 55.
        """
        lat, lon = 19.0, 72.5
        print(f"\n[TEST] Elevation at tile centre (lat={lat}, lon={lon})")
        with load_dem(dem_path) as parser:
            elev = parser.get_elevation(lat, lon)
        print(f"  Result: {elev:.2f} m  (expected: ~55.00 m)")
        # Row 4 of the grid is [50..59], col 5 → 55
        assert elev == pytest.approx(55.0, abs=1.0)
        print("  ✔ Elevation matches expected value")

    def test_multiple_sequential_lookups(self, dem_path):
        """Parser should handle multiple sequential lookups without errors."""
        coords = [
            (19.45, 72.05),   # top-left  → 10
            (19.45, 72.95),   # top-right → 19
            (18.55, 72.05),   # bot-left  → 100
        ]
        expected = [10.0, 19.0, 100.0]
        print("\n[TEST] Multiple sequential elevation lookups")
        with load_dem(dem_path) as parser:
            for (lat, lon), exp in zip(coords, expected):
                elev = parser.get_elevation(lat, lon)
                print(f"  lat={lat}, lon={lon} → {elev:.2f} m  (expected {exp:.2f} m)")
                assert elev == pytest.approx(exp, abs=0.1)
        print("  ✔ All lookups returned correct elevations")

    def test_elevation_invalid_latitude_raises_value_error(self, dem_path):
        """Latitude outside [-90, 90] must raise ValueError."""
        with load_dem(dem_path) as parser:
            with pytest.raises(ValueError, match="Latitude"):
                parser.get_elevation(100.0, 72.0)
        print("  ✔ ValueError raised for invalid latitude (100.0)")

    def test_elevation_invalid_longitude_raises_value_error(self, dem_path):
        """Longitude outside [-180, 180] must raise ValueError."""
        with load_dem(dem_path) as parser:
            with pytest.raises(ValueError, match="Longitude"):
                parser.get_elevation(19.0, 200.0)
        print("  ✔ ValueError raised for invalid longitude (200.0)")

    def test_elevation_out_of_bounds_raises_error(self, dem_path):
        """Coordinates outside the raster tile must raise CoordinateOutOfBoundsError."""
        # Tokyo is far outside the Mumbai tile
        lat, lon = 35.6762, 139.6503
        print(f"\n[TEST] Out-of-bounds coordinate (Tokyo: lat={lat}, lon={lon})")
        with load_dem(dem_path) as parser:
            with pytest.raises(CoordinateOutOfBoundsError) as exc_info:
                parser.get_elevation(lat, lon)
        print(f"  ✔ CoordinateOutOfBoundsError raised: {exc_info.value}")

    def test_elevation_nodata_pixel_returns_nan(self, dem_path_with_nodata):
        """A pixel containing the NoData value must return float('nan')."""
        # Bottom-right pixel (row=9, col=9) was set to NODATA
        lat, lon = 18.55, 72.95
        print(f"\n[TEST] NoData pixel at (lat={lat}, lon={lon})")
        with load_dem(dem_path_with_nodata) as parser:
            elev = parser.get_elevation(lat, lon)
        print(f"  Result: {elev}  (expected: nan)")
        assert math.isnan(elev)
        print("  ✔ NaN returned for NoData pixel")

    def test_elevation_closed_dataset_raises_runtime_error(self, dem_path):
        """Calling get_elevation() after close() must raise RuntimeError."""
        parser = load_dem(dem_path)
        parser.close()
        with pytest.raises(RuntimeError, match="closed"):
            parser.get_elevation(19.0, 72.5)
        print("  ✔ RuntimeError raised when dataset is closed")


# ══════════════════════════════════════════════════════════════════════════════
# Test Section 3 — Metadata
# ══════════════════════════════════════════════════════════════════════════════

class TestGetMetadata:
    """Tests for DEMParser.get_metadata()."""

    def test_metadata_returns_dict(self, dem_path):
        """get_metadata() must return a dictionary."""
        with load_dem(dem_path) as parser:
            meta = parser.get_metadata()
        assert isinstance(meta, dict)
        print("  ✔ Metadata returned as dict")

    def test_metadata_contains_expected_keys(self, dem_path):
        """All expected metadata keys must be present."""
        expected_keys = {
            "file_path", "file_name", "file_size_mb",
            "crs", "crs_is_geographic",
            "width", "height", "bands",
            "dtype", "nodata", "bounds",
            "resolution_x", "resolution_y", "transform",
        }
        with load_dem(dem_path) as parser:
            meta = parser.get_metadata()
        missing = expected_keys - set(meta.keys())
        assert not missing, f"Missing keys in metadata: {missing}"
        print(f"  ✔ All {len(expected_keys)} expected keys present")

    def test_metadata_dimensions_correct(self, dem_path):
        """Width and height must match the synthetic raster dimensions."""
        with load_dem(dem_path) as parser:
            meta = parser.get_metadata()
        print(f"\n[TEST] Metadata dimensions: {meta['width']}×{meta['height']} px")
        assert meta["width"]  == TILE_WIDTH
        assert meta["height"] == TILE_HEIGHT
        print("  ✔ Dimensions match")

    def test_metadata_crs_is_wgs84(self, dem_path):
        """CRS of the synthetic DEM must be EPSG:4326."""
        with load_dem(dem_path) as parser:
            meta = parser.get_metadata()
        print(f"\n[TEST] CRS reported: {meta['crs']}")
        assert "4326" in meta["crs"]
        assert meta["crs_is_geographic"] is True
        print("  ✔ CRS is WGS84 (EPSG:4326)")

    def test_metadata_print_pretty(self, dem_path):
        """Pretty-print the full metadata dictionary (visual inspection)."""
        import pprint
        with load_dem(dem_path) as parser:
            meta = parser.get_metadata()
        print("\n─── Full Metadata Output ───────────────────────────────────")
        pprint.pprint(meta, width=70)
        print("────────────────────────────────────────────────────────────")

    def test_metadata_resolution(self, dem_path):
        """Resolution must be 0.1° per pixel (1° tile / 10 pixels)."""
        with load_dem(dem_path) as parser:
            meta = parser.get_metadata()
        print(f"\n[TEST] Resolution: x={meta['resolution_x']:.4f}°, y={meta['resolution_y']:.4f}°")
        assert meta["resolution_x"] == pytest.approx(0.1, rel=1e-4)
        assert meta["resolution_y"] == pytest.approx(0.1, rel=1e-4)
        print("  ✔ Resolution is 0.1° per pixel")


# ══════════════════════════════════════════════════════════════════════════════
# Test Section 4 — Context Manager
# ══════════════════════════════════════════════════════════════════════════════

class TestContextManager:
    """Tests for the with-statement protocol."""

    def test_dataset_closed_after_context_manager_exit(self, dem_path):
        """The rasterio dataset must be closed after exiting the with block."""
        with load_dem(dem_path) as parser:
            assert not parser.dataset.closed
        assert parser.dataset.closed
        print("  ✔ Dataset closed after 'with' block exit")

    def test_dataset_closed_after_exception_in_with_block(self, dem_path):
        """File handle must be released even if an exception occurs inside the with block."""
        parser_ref = None
        try:
            with load_dem(dem_path) as parser:
                parser_ref = parser
                raise ValueError("Simulated application error")
        except ValueError:
            pass
        assert parser_ref is not None
        assert parser_ref.dataset.closed
        print("  ✔ Dataset closed despite exception in with block")


# ══════════════════════════════════════════════════════════════════════════════
# Standalone runner (python app/tests/test_dem_parser.py)
# ══════════════════════════════════════════════════════════════════════════════

def _run_standalone_demo() -> None:
    """
    Run a human-readable demonstration when the file is executed directly.
    This shows the expected command-line output described in the task brief.
    """
    import pprint, tempfile

    print("=" * 65)
    print("  Jal-Prahari — DEM Parser Standalone Demo")
    print("=" * 65)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dem = create_synthetic_dem(tmp_path)

        # ── Demo 1: load + metadata ──────────────────────────────────────────
        print("\n[DEMO 1] Loading DEM and printing metadata")
        print("-" * 50)
        with load_dem(dem) as parser:
            meta = parser.get_metadata()
            pprint.pprint(meta, width=60)

            # ── Demo 2: multiple elevation lookups ───────────────────────────
            print("\n[DEMO 2] Elevation lookups at multiple coordinates")
            print("-" * 50)
            coords = [
                (19.45, 72.05, "Top-left corner (Mumbai North)"),
                (19.0,  72.5,  "Tile centre"),
                (18.55, 72.05, "Bottom-left corner"),
                (19.45, 72.95, "Top-right corner"),
            ]
            for lat, lon, label in coords:
                elev = parser.get_elevation(lat, lon)
                print(f"  {label:35s} lat={lat:.2f}, lon={lon:.2f} → {elev:.2f} m")

            # ── Demo 3: invalid coordinates ──────────────────────────────────
            print("\n[DEMO 3] Handling invalid coordinates")
            print("-" * 50)
            try:
                parser.get_elevation(200.0, 72.5)
            except ValueError as e:
                print(f"  ValueError (bad lat): {e}")

            # ── Demo 4: out-of-bounds coordinates ────────────────────────────
            print("\n[DEMO 4] Handling out-of-bounds coordinates (Tokyo)")
            print("-" * 50)
            try:
                parser.get_elevation(35.6762, 139.6503)
            except CoordinateOutOfBoundsError as e:
                print(f"  CoordinateOutOfBoundsError: {e}")

        # ── Demo 5: missing file ─────────────────────────────────────────────
        print("\n[DEMO 5] Handling missing file")
        print("-" * 50)
        try:
            load_dem(tmp_path / "nonexistent.tif")
        except FileNotFoundError as e:
            print(f"  FileNotFoundError: {e}")

        # ── Demo 6: closed dataset guard ─────────────────────────────────────
        print("\n[DEMO 6] Handling closed dataset")
        print("-" * 50)
        p = load_dem(dem)
        p.close()
        try:
            p.get_elevation(19.0, 72.5)
        except RuntimeError as e:
            print(f"  RuntimeError: {e}")

    print("\n" + "=" * 65)
    print("  Demo complete. All scenarios handled gracefully.")
    print("=" * 65)


if __name__ == "__main__":
    _run_standalone_demo()
