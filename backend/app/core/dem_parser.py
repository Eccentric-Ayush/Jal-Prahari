# backend/app/core/dem_parser.py
#
# ─── Responsibility ────────────────────────────────────────────────────────────
# Read Digital Elevation Model (DEM) GeoTIFF files and provide:
#
#   • DEMParser         — stateful class that holds an open rasterio dataset,
#                         ready for repeated elevation lookups.
#   • load_dem()        — open and validate a GeoTIFF, returning a DEMParser.
#   • get_elevation()   — resolve a (lat, lon) pair to an elevation in metres.
#   • get_metadata()    — return a structured metadata dictionary.
#
# ─── Why Rasterio over plain GDAL? ────────────────────────────────────────────
#   • Pythonic context-manager API (`with rasterio.open(...) as ds:`) guarantees
#     file handle release even on exceptions.
#   • Integrates natively with NumPy for array-level operations.
#   • Automatic CRS handling via pyproj under the hood.
#   • Better error messages than raw GDAL bindings.
#   • Windows wheel available on PyPI — no system GDAL installation required.
#
# ─── Coordinate transform strategy ────────────────────────────────────────────
#   Sensor coordinates arrive as WGS84 (EPSG:4326) latitude/longitude.
#   DEM files are commonly distributed in WGS84 as well, but may also be in a
#   projected CRS (e.g. UTM Zone 43N / EPSG:32643 for Mumbai).
#
#   Strategy: always transform from EPSG:4326 → the raster's native CRS using
#   pyproj.Transformer.  If the raster IS already EPSG:4326, pyproj returns the
#   coordinates unchanged with near-zero overhead.
#
# ─── Memory efficiency ────────────────────────────────────────────────────────
#   Large DEMs (SRTM 1-arc-second ≈ 100 MB; ALOS 30m ≈ 4 GB per tile) must
#   NOT be loaded entirely into memory.  We use rasterio's windowed read:
#
#       dataset.read(1, window=rasterio.windows.Window(col, row, 1, 1))
#
#   This reads exactly 1 pixel at a time from disk — O(1) memory regardless
#   of raster size.  For bulk lookups the caller should collect all (row, col)
#   pairs and issue a single wider window read.
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import rasterio
import rasterio.crs
import rasterio.errors
import rasterio.transform
from pyproj import Transformer
from pyproj.exceptions import CRSError

from app.core.logger import get_logger

# Module-level logger — all messages are tagged "app.core.dem_parser"
log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class CoordinateOutOfBoundsError(ValueError):
    """
    Raised when a requested (latitude, longitude) coordinate falls outside
    the spatial extent of the loaded DEM raster.

    Inherits from ValueError so callers can catch it as either
    CoordinateOutOfBoundsError or ValueError — their choice.
    """
    pass


# ---------------------------------------------------------------------------
# Internal helper — path validation
# ---------------------------------------------------------------------------

def _resolve_and_validate_path(path: str | Path) -> Path:
    """
    Convert *path* to an absolute :class:`pathlib.Path` and run safety checks.

    Parameters
    ----------
    path : str or Path
        Relative or absolute path to a GeoTIFF file.

    Returns
    -------
    Path
        Absolute, validated path.

    Raises
    ------
    TypeError
        If *path* is not a str or Path instance.
    FileNotFoundError
        If the file does not exist at the resolved location.
    PermissionError
        If the current process lacks read permission on the file.
    ValueError
        If the file extension is not ``.tif`` or ``.tiff``.

    Design notes
    ------------
    Using ``Path.resolve()`` converts relative paths (e.g. ``"data/dem.tif"``)
    to absolute paths anchored at the current working directory, eliminating
    the ambiguity that causes "file not found" errors when the working directory
    changes (common in IDE / Jupyter environments).
    """
    if not isinstance(path, (str, Path)):
        raise TypeError(
            f"path must be a str or pathlib.Path, got {type(path).__name__!r}."
        )

    resolved: Path = Path(path).resolve()

    # ── Extension check ──────────────────────────────────────────────────────
    if resolved.suffix.lower() not in {".tif", ".tiff"}:
        raise ValueError(
            f"Expected a GeoTIFF file (.tif or .tiff), "
            f"got '{resolved.suffix}' for path: {resolved}"
        )

    # ── Existence check ──────────────────────────────────────────────────────
    if not resolved.exists():
        raise FileNotFoundError(
            f"DEM file not found at: {resolved}\n"
            f"  • Check that the path is correct.\n"
            f"  • On Windows, ensure the drive letter is included.\n"
            f"  • Verify the file has not been moved or deleted."
        )

    # ── Read-permission check ────────────────────────────────────────────────
    if not resolved.is_file():
        raise FileNotFoundError(
            f"Path exists but is not a regular file: {resolved}"
        )

    try:
        # Opening for read and immediately closing is the most reliable cross-
        # platform way to check permissions without TOCTOU race conditions.
        with open(resolved, "rb"):
            pass
    except PermissionError:
        raise PermissionError(
            f"Read permission denied for DEM file: {resolved}\n"
            f"  • On Linux/macOS run: chmod +r {resolved}\n"
            f"  • On Windows adjust the file's security properties."
        )

    log.debug("Path validation passed: %s", resolved)
    return resolved


# ---------------------------------------------------------------------------
# DEMParser — stateful class
# ---------------------------------------------------------------------------

@dataclass
class DEMParser:
    """
    Stateful wrapper around an open :class:`rasterio.DatasetReader`.

    Instances are created by :func:`load_dem` and should be used as context
    managers to ensure the underlying file handle is always released:

    .. code-block:: python

        with load_dem("path/to/dem.tif") as parser:
            elev = parser.get_elevation(19.0760, 72.8777)
            meta = parser.get_metadata()

    Alternatively, close manually:

    .. code-block:: python

        parser = load_dem("path/to/dem.tif")
        try:
            elev = parser.get_elevation(19.0760, 72.8777)
        finally:
            parser.close()

    Attributes
    ----------
    path : Path
        Absolute path to the GeoTIFF file.
    dataset : rasterio.DatasetReader
        Open rasterio dataset.  Do NOT close this object externally.
    _transformer : pyproj.Transformer
        Pre-built coordinate transformer from EPSG:4326 → raster CRS.
        Created once at construction time for efficient repeated lookups.
    """

    path: Path
    dataset: rasterio.DatasetReader
    _transformer: Transformer = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Build the pyproj Transformer immediately after dataclass construction."""
        self._build_transformer()
        log.info(
            "DEMParser ready │ file=%s │ CRS=%s │ size=%dx%d px │ bands=%d",
            self.path.name,
            self.dataset.crs.to_string() if self.dataset.crs else "UNKNOWN",
            self.dataset.width,
            self.dataset.height,
            self.dataset.count,
        )

    # ── Private helpers ──────────────────────────────────────────────────────

    def _build_transformer(self) -> None:
        """
        Construct a :class:`pyproj.Transformer` from WGS84 (EPSG:4326) to the
        raster's native CRS.

        Caching the Transformer avoids the overhead of re-initialising the
        PROJ pipeline on every elevation lookup — important when processing
        thousands of IoT sensor coordinates per second.

        If the raster has no CRS defined (unusual but possible for raw DEMs),
        we log a warning and assume EPSG:4326 so the pipeline still runs.
        """
        if self.dataset.crs is None:
            log.warning(
                "DEM file '%s' has no CRS defined.  "
                "Assuming EPSG:4326.  Elevation values may be incorrect "
                "if coordinates are in a different reference system.",
                self.path.name,
            )
            target_crs = "EPSG:4326"
        else:
            target_crs = self.dataset.crs.to_string()

        try:
            # always_xy=True ensures (longitude, latitude) input order,
            # which matches Mapbox GL JS and GeoJSON conventions.
            self._transformer = Transformer.from_crs(
                crs_from="EPSG:4326",
                crs_to=target_crs,
                always_xy=True,   # (x=lon, y=lat) → (x, y) in target CRS
            )
            log.debug(
                "Coordinate transformer built: EPSG:4326 → %s", target_crs
            )
        except CRSError as exc:
            raise ValueError(
                f"Cannot build coordinate transformer for CRS '{target_crs}': {exc}"
            ) from exc

    def _latlon_to_rowcol(
        self, latitude: float, longitude: float
    ) -> Tuple[int, int]:
        """
        Transform (latitude, longitude) → raster (row, col) pixel indices.

        Steps
        -----
        1. Transform lon/lat from EPSG:4326 to the raster's native CRS.
        2. Use :func:`rasterio.transform.rowcol` with the affine transform of
           the dataset to convert projected coordinates to pixel indices.
        3. Validate that the indices fall within the raster extent.

        Parameters
        ----------
        latitude : float
            Geographic latitude in decimal degrees (WGS84).
        longitude : float
            Geographic longitude in decimal degrees (WGS84).

        Returns
        -------
        Tuple[int, int]
            Zero-based (row, col) pixel indices into the raster array.

        Raises
        ------
        CoordinateOutOfBoundsError
            If the pixel indices fall outside [0, height) × [0, width).
        """
        # Step 1: project coordinates to the raster CRS
        x, y = self._transformer.transform(longitude, latitude)

        # Step 2: convert projected (x, y) to integer pixel (row, col)
        row, col = rasterio.transform.rowcol(
            self.dataset.transform, xs=x, ys=y
        )
        row, col = int(row), int(col)

        log.debug(
            "Coordinate transform │ lat=%.6f lon=%.6f → projected (%.4f, %.4f) "
            "→ pixel (row=%d, col=%d)",
            latitude, longitude, x, y, row, col,
        )

        # Step 3: bounds check
        if not (0 <= row < self.dataset.height and 0 <= col < self.dataset.width):
            bounds = self.dataset.bounds
            raise CoordinateOutOfBoundsError(
                f"Coordinate (lat={latitude}, lon={longitude}) maps to pixel "
                f"(row={row}, col={col}) which is outside the raster extent.\n"
                f"  Raster bounds (native CRS): "
                f"left={bounds.left:.4f}, bottom={bounds.bottom:.4f}, "
                f"right={bounds.right:.4f}, top={bounds.top:.4f}\n"
                f"  Raster size: {self.dataset.width}×{self.dataset.height} pixels\n"
                f"  Tip: verify the coordinate is within the DEM tile's coverage area."
            )

        return row, col

    # ── Public API ───────────────────────────────────────────────────────────

    def get_elevation(self, latitude: float, longitude: float) -> float:
        """
        Return the elevation in **metres** at the given geographic coordinate.

        Parameters
        ----------
        latitude : float
            Geographic latitude in decimal degrees.
            Valid range: −90.0 to +90.0.
        longitude : float
            Geographic longitude in decimal degrees.
            Valid range: −180.0 to +180.0.

        Returns
        -------
        float
            Elevation value extracted from band 1 of the DEM, in metres.

        Raises
        ------
        ValueError
            If latitude or longitude fall outside their valid geographic ranges.
        CoordinateOutOfBoundsError
            If the coordinate maps to a pixel outside the raster extent.
        rasterio.errors.RasterioIOError
            If the underlying file read fails (e.g., corrupted GeoTIFF).
        RuntimeError
            If the dataset has been closed before this call.

        Notes
        -----
        - Uses rasterio windowed reading (1×1 pixel) to avoid loading the
          entire raster band into memory.
        - NoData pixels (e.g., ocean/void areas in SRTM) are returned as
          ``float('nan')`` with a warning, not as an exception, because
          the coordinate is technically valid — it just has no elevation data.

        Examples
        --------
        >>> with load_dem("data/mumbai_dem.tif") as parser:
        ...     elev = parser.get_elevation(19.0760, 72.8777)
        ...     print(f"Elevation: {elev:.2f} m")
        Elevation: 11.43 m
        """
        # ── Guard: dataset must still be open ────────────────────────────────
        if self.dataset.closed:
            raise RuntimeError(
                "Cannot query elevation: the DEM dataset has been closed.  "
                "Use `load_dem()` inside a `with` block, or call `load_dem()` again."
            )

        # ── Validate coordinate ranges ────────────────────────────────────────
        if not (-90.0 <= latitude <= 90.0):
            raise ValueError(
                f"Latitude must be in [-90, 90], got {latitude:.6f}.  "
                f"Note: latitude and longitude arguments may be swapped."
            )
        if not (-180.0 <= longitude <= 180.0):
            raise ValueError(
                f"Longitude must be in [-180, 180], got {longitude:.6f}."
            )

        log.info(
            "Elevation lookup requested │ lat=%.6f, lon=%.6f", latitude, longitude
        )

        # ── Convert to pixel indices ──────────────────────────────────────────
        row, col = self._latlon_to_rowcol(latitude, longitude)

        # ── Windowed read — reads exactly 1 pixel ────────────────────────────
        try:
            window = rasterio.windows.Window(
                col_off=col,
                row_off=row,
                width=1,
                height=1,
            )
            # dataset.read() returns shape (bands, rows, cols)
            pixel_array: np.ndarray = self.dataset.read(1, window=window)
        except rasterio.errors.RasterioIOError as exc:
            log.error(
                "RasterioIOError during pixel read at (row=%d, col=%d): %s",
                row, col, exc,
            )
            raise

        elevation: float = float(pixel_array[0, 0])

        # ── NoData detection ─────────────────────────────────────────────────
        nodata = self.dataset.nodata
        if nodata is not None and math.isclose(elevation, nodata, rel_tol=1e-6):
            log.warning(
                "NoData value (%.2f) encountered at lat=%.6f, lon=%.6f.  "
                "Returning NaN — this pixel has no elevation data (ocean/void).",
                nodata, latitude, longitude,
            )
            return float("nan")

        log.info(
            "Elevation result │ lat=%.6f, lon=%.6f → %.4f m",
            latitude, longitude, elevation,
        )
        return elevation

    def get_metadata(self) -> Dict[str, Any]:
        """
        Return a structured dictionary describing the DEM raster.

        Returns
        -------
        dict
            A dictionary with the following keys:

            ==================  =================================================
            Key                 Description
            ==================  =================================================
            ``file_path``       Absolute path to the GeoTIFF as a string.
            ``file_name``       Basename of the file.
            ``file_size_mb``    File size on disk in megabytes.
            ``crs``             CRS as a human-readable string (EPSG code if known).
            ``crs_is_geographic`` True if the CRS uses angular (degree) units.
            ``width``           Raster width in pixels.
            ``height``          Raster height in pixels.
            ``bands``           Number of spectral bands (1 for DEM).
            ``dtype``           NumPy data type of band 1 (e.g. ``"float32"``).
            ``nodata``          NoData sentinel value, or ``None`` if unset.
            ``bounds``          Bounding box in the raster's native CRS.
            ``resolution_x``    Pixel width in CRS units per pixel.
            ``resolution_y``    Pixel height in CRS units per pixel (positive).
            ``transform``       Affine transform matrix as a string.
            ==================  =================================================

        Examples
        --------
        >>> with load_dem("data/mumbai_dem.tif") as parser:
        ...     import pprint
        ...     pprint.pprint(parser.get_metadata())
        {'file_name': 'mumbai_dem.tif',
         'crs': 'EPSG:4326',
         'width': 3601,
         'height': 3601,
         ...}
        """
        if self.dataset.closed:
            raise RuntimeError(
                "Cannot retrieve metadata: the DEM dataset has been closed."
            )

        ds = self.dataset
        bounds = ds.bounds

        # Resolution: affine transform diagonal gives pixel size.
        # ds.transform.a = pixel width  (positive for west-east)
        # ds.transform.e = pixel height (negative for north-south, so abs())
        res_x: float = abs(ds.transform.a)
        res_y: float = abs(ds.transform.e)

        file_size_mb: float = round(self.path.stat().st_size / (1024 ** 2), 3)

        metadata: Dict[str, Any] = {
            "file_path":        str(self.path),
            "file_name":        self.path.name,
            "file_size_mb":     file_size_mb,
            "crs":              ds.crs.to_string() if ds.crs else None,
            "crs_is_geographic": ds.crs.is_geographic if ds.crs else None,
            "width":            ds.width,
            "height":           ds.height,
            "bands":            ds.count,
            "dtype":            ds.dtypes[0],
            "nodata":           ds.nodata,
            "bounds": {
                "left":   bounds.left,
                "bottom": bounds.bottom,
                "right":  bounds.right,
                "top":    bounds.top,
            },
            "resolution_x":     res_x,
            "resolution_y":     res_y,
            "transform":        str(ds.transform),
        }

        log.info(
            "Metadata retrieved │ file=%s │ %dx%d px │ %.3f MB │ CRS=%s",
            self.path.name,
            ds.width,
            ds.height,
            file_size_mb,
            metadata["crs"],
        )
        return metadata

    # ── Context-manager protocol ─────────────────────────────────────────────

    def close(self) -> None:
        """Explicitly close the underlying rasterio dataset and release the file handle."""
        if not self.dataset.closed:
            self.dataset.close()
            log.debug("DEMParser closed: %s", self.path.name)

    def __enter__(self) -> "DEMParser":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __repr__(self) -> str:
        status = "open" if not self.dataset.closed else "closed"
        return (
            f"<DEMParser file={self.path.name!r} "
            f"size={self.dataset.width}x{self.dataset.height} "
            f"CRS={self.dataset.crs} status={status}>"
        )


# ---------------------------------------------------------------------------
# Public factory function
# ---------------------------------------------------------------------------

def load_dem(path: str | Path) -> DEMParser:
    """
    Open and validate a DEM GeoTIFF file, returning a ready-to-use
    :class:`DEMParser` instance.

    This is the **primary entry point** for the DEM parsing module.
    It validates the path, opens the dataset with rasterio, performs
    sanity checks on the CRS and band count, and returns a :class:`DEMParser`
    that is ready for elevation lookups.

    Parameters
    ----------
    path : str or pathlib.Path
        Relative or absolute path to a GeoTIFF DEM file.

    Returns
    -------
    DEMParser
        An open, validated parser instance.
        **The caller is responsible for closing it** — use as a context manager
        (``with load_dem(...) as parser:``) to guarantee cleanup.

    Raises
    ------
    TypeError
        If *path* is not a str or Path.
    FileNotFoundError
        If the file does not exist.
    PermissionError
        If the file cannot be read.
    ValueError
        If the extension is not ``.tif``/``.tiff``, or the file has zero bands.
    rasterio.errors.RasterioIOError
        If rasterio cannot parse the file (corrupted or unsupported format).

    Examples
    --------
    Context-manager usage (recommended):

    .. code-block:: python

        from app.core.dem_parser import load_dem

        with load_dem("data/mumbai_dem.tif") as parser:
            elevation = parser.get_elevation(19.0760, 72.8777)
            print(f"Elevation: {elevation:.2f} m")

    Manual usage:

    .. code-block:: python

        parser = load_dem("data/mumbai_dem.tif")
        elevation = parser.get_elevation(19.0760, 72.8777)
        parser.close()
    """
    log.info("Loading DEM file: %s", path)

    # ── Step 1: path validation ───────────────────────────────────────────────
    resolved_path = _resolve_and_validate_path(path)

    # ── Step 2: open with rasterio ────────────────────────────────────────────
    try:
        dataset = rasterio.open(resolved_path)
    except rasterio.errors.RasterioIOError as exc:
        log.error(
            "Rasterio could not open '%s'.  "
            "The file may be corrupted, truncated, or in an unsupported format.  "
            "Original error: %s",
            resolved_path, exc,
        )
        raise rasterio.errors.RasterioIOError(
            f"Failed to open GeoTIFF '{resolved_path}': {exc}"
        ) from exc
    except Exception as exc:
        log.error("Unexpected error opening '%s': %s", resolved_path, exc)
        raise

    # ── Step 3: band count sanity check ──────────────────────────────────────
    if dataset.count == 0:
        dataset.close()
        raise ValueError(
            f"DEM file '{resolved_path.name}' contains zero bands.  "
            f"A valid DEM must have at least one band containing elevation data."
        )

    # ── Step 4: CRS warning (not an error — some raw DEMs lack CRS metadata) ─
    if dataset.crs is None:
        log.warning(
            "DEM file '%s' has no CRS defined in its metadata.  "
            "Coordinate lookups will assume EPSG:4326.  "
            "Consider adding CRS metadata with: "
            "gdalwarp -t_srs EPSG:4326 input.tif output.tif",
            resolved_path.name,
        )
    else:
        log.debug(
            "CRS detected: %s (geographic=%s)",
            dataset.crs.to_string(),
            dataset.crs.is_geographic,
        )

    log.info(
        "DEM loaded successfully │ file=%s │ size=%dx%d px │ bands=%d │ dtype=%s",
        resolved_path.name,
        dataset.width,
        dataset.height,
        dataset.count,
        dataset.dtypes[0],
    )

    return DEMParser(path=resolved_path, dataset=dataset)
