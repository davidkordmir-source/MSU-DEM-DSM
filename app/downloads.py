"""Stream a ZIP64 download without duplicating large rasters on the work disk."""

import io
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

RESULT_FILES = ("height.tif", "extracted.tif", "mask.tif", "report.json")


class _Buffer(io.RawIOBase):
    def __init__(self):
        self.parts = []
        self.position = 0

    def writable(self):
        return True

    def tell(self):
        return self.position

    def write(self, data):
        self.parts.append(bytes(data))
        self.position += len(data)
        return len(data)

    def drain(self):
        data = b"".join(self.parts)
        self.parts.clear()
        return data


def stream_results(directory: Path):
    buffer = _Buffer()
    with ZipFile(buffer, "w", compression=ZIP_STORED, allowZip64=True) as archive:
        for name in RESULT_FILES:
            path = directory / name
            info = ZipInfo.from_file(path, arcname=name)
            with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                yield buffer.drain()
                while data := source.read(1024**2):
                    target.write(data)
                    yield buffer.drain()
            yield buffer.drain()
    yield buffer.drain()
