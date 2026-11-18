"""Tile-local polygonization, disk-backed connectivity, and attributed shapefiles."""

import sqlite3
from itertools import pairwise
from pathlib import Path

import numpy as np
import shapefile
from rasterio.features import rasterize, shapes
from shapely import from_wkb, make_valid, orient_polygons, to_wkb, union_all
from shapely.affinity import affine_transform, translate
from shapely.geometry import mapping, shape

from app.errors import ProcessingError

TILE_SIZE = 256
SHAPEFILE_BYTES = 1_800_000_000  # Stay below the interoperable 2 GB limit per component.
FIELDS = [
    ("OBJ_ID", "N", 18, 0),
    ("PIXELS", "N", 20, 0),
    ("H_MIN_M", "F", 24, 6),
    ("H_MAX_M", "F", 24, 6),
    ("H_MEAN_M", "F", 24, 6),
    ("AREA_M2", "F", 24, 3),
    ("THRESH_M", "F", 24, 6),
    ("RULE", "C", 2, 0),
    ("GND_SRC", "C", 12, 0),
    ("WEAK_PX", "N", 20, 0),
]


class VectorBuilder:
    def __init__(self, directory: Path, transform, crs, threshold, inclusive, ground_source="DEM"):
        self.directory, self.transform, self.crs = directory, transform, crs
        self.ground_source = ground_source
        self.threshold, self.rule = threshold, ">=" if inclusive else ">"
        self.path = directory / ".vector-work.sqlite"
        self.db = sqlite3.connect(self.path)
        self.db.executescript("""
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            PRAGMA temp_store=FILE;
            PRAGMA cache_size=-32768;
            CREATE TABLE fragments(
                id INTEGER PRIMARY KEY, parent INTEGER, root INTEGER,
                pixels INTEGER, lo REAL, hi REAL, total REAL, weak INTEGER, geom BLOB);
            CREATE TABLE edges(axis INTEGER, line INTEGER, side INTEGER,
                start INTEGER, end INTEGER, fragment INTEGER);
            CREATE INDEX edge_line ON edges(axis,line,side,start);
        """)
        self.next_id = 1

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()
        self.path.unlink(missing_ok=True)

    def _root(self, identity):
        trail = []
        while True:
            parent = self.db.execute("SELECT parent FROM fragments WHERE id=?", (identity,)).fetchone()[0]
            if parent == identity:
                break
            trail.append(identity)
            identity = parent
        self.db.executemany("UPDATE fragments SET parent=? WHERE id=?", ((identity, x) for x in trail))
        return identity

    def _join(self, first, second):
        a, b = self._root(first), self._root(second)
        if a != b:
            self.db.execute("UPDATE fragments SET parent=? WHERE id=?", (min(a, b), max(a, b)))

    def _edge(self, values, ids, axis, line, side, offset):
        # Run-length encode selected border cells; match only positive shared edge lengths.
        changes = np.flatnonzero(np.diff(np.pad(values.astype("int64"), (1, 1), constant_values=-1)))
        for start, end in pairwise(changes):
            label = int(values[start])
            if not label:
                continue
            identity = ids[label - 1]
            lo, hi = int(start + offset), int(end + offset)
            matches = self.db.execute(
                "SELECT fragment FROM edges WHERE axis=? AND line=? AND side!=? AND start<? AND end>?",
                (axis, line, side, hi, lo),
            ).fetchall()
            for (other,) in matches:
                self._join(identity, other)
            self.db.execute("INSERT INTO edges VALUES(?,?,?,?,?,?)", (axis, line, side, lo, hi, identity))

    def add(self, window, selected, heights, weak=None):
        for row in range(0, selected.shape[0], TILE_SIZE):
            for col in range(0, selected.shape[1], TILE_SIZE):
                mask = selected[row : row + TILE_SIZE, col : col + TILE_SIZE]
                if not mask.any():
                    continue
                values = heights[row : row + TILE_SIZE, col : col + TILE_SIZE]
                geometries = [g for g, _ in shapes(mask.astype("uint8"), mask=mask, connectivity=4)]
                labels = rasterize(
                    ((geom, i + 1) for i, geom in enumerate(geometries)),
                    out_shape=mask.shape,
                    dtype="int32",
                )
                indices, samples = labels[mask], values[mask]
                count = np.bincount(indices, minlength=len(geometries) + 1)
                totals = np.bincount(indices, weights=samples, minlength=len(geometries) + 1)
                weak_counts = (
                    np.bincount(
                        indices,
                        weights=weak[row : row + TILE_SIZE, col : col + TILE_SIZE][mask],
                        minlength=len(geometries) + 1,
                    )
                    if weak is not None
                    else np.zeros(len(geometries) + 1)
                )
                low, high = np.full(len(count), np.inf), np.full(len(count), -np.inf)
                np.minimum.at(low, indices, samples)
                np.maximum.at(high, indices, samples)
                x, y = int(window.col_off) + col, int(window.row_off) + row
                ids = []
                for label, geometry in enumerate(geometries, 1):
                    polygon = shape(geometry)
                    if not polygon.is_valid:
                        polygon = make_valid(polygon)
                    identity = self.next_id
                    self.next_id += 1
                    ids.append(identity)
                    self.db.execute(
                        "INSERT INTO fragments VALUES(?,?,NULL,?,?,?,?,?,?)",
                        (
                            identity,
                            identity,
                            int(count[label]),
                            float(low[label]),
                            float(high[label]),
                            float(totals[label]),
                            int(weak_counts[label]),
                            to_wkb(translate(polygon, xoff=x, yoff=y)),
                        ),
                    )
                self._edge(labels[0], ids, 0, y, 0, x)
                self._edge(labels[-1], ids, 0, y + mask.shape[0], 1, x)
                self._edge(labels[:, 0], ids, 1, x, 0, y)
                self._edge(labels[:, -1], ids, 1, x + mask.shape[1], 1, y)
        self.db.commit()

    def finish(self, progress):
        total = self.next_id - 1
        progress(84, "اتصال مرز عوارض و محاسبهٔ جدول ویژگی‌ها")
        for index in range(1, self.next_id):
            self.db.execute("UPDATE fragments SET root=? WHERE id=?", (self._root(index), index))
            if index % 1000 == 0:
                progress(84 + int(3 * index / max(total, 1)), "اتصال عوارض دو سوی مرز بلوک‌ها")
        self.db.execute("CREATE INDEX fragment_root ON fragments(root)")
        self.db.commit()
        pixel_area = (
            abs(self.transform.determinant) * self.crs.linear_units_factor[1] ** 2
            if self.crs.is_projected
            else None
        )
        count = 0
        sample = []
        parts = []
        writer = None

        def open_part():
            basename = f"features_{len(parts) + 1:03d}"
            parts.append(basename)
            current = shapefile.Writer(str(self.directory / basename), shapeType=shapefile.POLYGON)
            for field in FIELDS:
                current.field(*field)
            (self.directory / f"{basename}.prj").write_text(
                self.crs.to_wkt(version="WKT1_ESRI"), encoding="utf-8"
            )
            (self.directory / f"{basename}.cpg").write_text("UTF-8")
            return current

        try:
            writer = open_part()  # Also deliver a valid empty layer when nothing qualifies.
            grouped = self.db.execute(
                "SELECT root,SUM(pixels),MIN(lo),MAX(hi),SUM(total),SUM(weak) FROM fragments GROUP BY root ORDER BY root"
            )
            for root, pixels, low, high, total_height, weak_pixels in grouped:
                merged = None
                cursor = self.db.execute("SELECT geom FROM fragments WHERE root=? ORDER BY id", (root,))
                while batch := cursor.fetchmany(128):
                    geometries = [from_wkb(record[0]) for record in batch]
                    if merged is not None:
                        geometries.append(merged)
                    merged = union_all(geometries)
                if merged is None or abs(merged.area - pixels) > 0.01:
                    raise ProcessingError("تبدیل مرز عارضه به شیپ‌فایل با خطا روبه‌رو شد.")
                a, b, c, d, e, f = self.transform[:6]
                polygon = orient_polygons(affine_transform(merged, [a, b, d, e, c, f]), exterior_cw=True)
                feature = shapefile.Shape._from_geojson(mapping(polygon))
                record_bytes = 52 + 4 * len(feature.parts) + 16 * len(feature.points)
                if record_bytes + 100 > SHAPEFILE_BYTES:
                    raise ProcessingError(
                        "مرز یک عارضه از ظرفیت فرمت Shapefile بزرگ‌تر است؛ محدوده را کوچک‌تر کنید."
                    )
                if (
                    writer.shp.tell() + record_bytes > SHAPEFILE_BYTES
                    or max(writer.dbf.tell(), 33 + 32 * len(FIELDS)) + 1 + sum(f[2] for f in FIELDS)
                    > SHAPEFILE_BYTES
                ):
                    writer.close()
                    writer = open_part()
                count += 1
                writer.shape(feature)
                writer.record(
                    count,
                    pixels,
                    low,
                    high,
                    total_height / pixels,
                    pixels * pixel_area if pixel_area is not None else None,
                    self.threshold,
                    self.rule,
                    self.ground_source,
                    weak_pixels,
                )
                if len(sample) < 20:
                    sample.append(
                        dict(
                            zip(
                                (f[0] for f in FIELDS),
                                (
                                    count,
                                    pixels,
                                    low,
                                    high,
                                    total_height / pixels,
                                    pixels * pixel_area if pixel_area is not None else None,
                                    self.threshold,
                                    self.rule,
                                    self.ground_source,
                                    weak_pixels,
                                ),
                                strict=True,
                            )
                        )
                    )
                if count % 100 == 0:
                    progress(89, f"نوشتن عوارض و ویژگی‌ها: {count}")
        finally:
            if writer:
                writer.close()
        files = [f"{part}.{ext}" for part in parts for ext in ("shp", "shx", "dbf", "prj", "cpg")]
        return {
            "feature_count": count,
            "layers": parts,
            "files": files,
            "connectivity": 4,
            "sample": sample,
            "fields": {
                f[0]: meaning
                for f, meaning in zip(
                    FIELDS,
                    [
                        "Unique connected feature ID",
                        "Selected DSM pixels",
                        "Minimum height above local ground (m)",
                        "Maximum height above local ground (m)",
                        "Mean height above local ground (m)",
                        "Grid area (m2), null for geographic CRS",
                        "Height threshold (m)",
                        "Comparison operator",
                        "Ground source: DSM_EST=estimated from DSM, DEM=external terrain",
                        "Selected pixels with limited ground-model support; zero does not certify accuracy",
                    ],
                    strict=True,
                )
            },
            "notes": [
                "Adjacent selected pixels sharing an edge form one feature; diagonal contacts stay separate.",
                "Pixel-edge boundaries retain holes; features crossing processing tiles are merged.",
                "Layers are split before the Shapefile component-size limit. OBJ_ID stays unique.",
            ],
        }
