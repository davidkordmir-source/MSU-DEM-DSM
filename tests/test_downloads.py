from io import BytesIO
from zipfile import ZipFile

from app.downloads import RESULT_FILES, stream_results


def test_streamed_zip_is_bounded_and_preserves_files(tmp_path):
    contents = {}
    for index, name in enumerate(RESULT_FILES):
        contents[name] = bytes([index]) * (3 * 1024**2 + 17)
        (tmp_path / name).write_bytes(contents[name])
    chunks = list(stream_results(tmp_path))
    assert max(map(len, chunks)) <= 1024**2 + 1024
    with ZipFile(BytesIO(b"".join(chunks))) as archive:
        assert archive.testzip() is None
        for name, content in contents.items():
            assert archive.read(name) == content
            assert archive.getinfo(name).extract_version >= 45  # ZIP64 headers.
    assert not (tmp_path / "results.zip").exists()
