"""Tests for PhotoExporter._needs_download_for_update, the pre-download check
used by export --update --download-missing (#1086)

These run at the PhotoExporter level. _stage_missing_photos_for_export_helper,
the one place a download through AppleScript or PhotoKit would start, is
monkeypatched to record its calls, so nothing is downloaded and Photos is
never touched.
"""

from __future__ import annotations

import pathlib
import shutil
import sqlite3

import pytest

import osxphotos
from osxphotos.exportoptions import ExportOptions
from osxphotos.photoexporter import PhotoExporter, StagedFiles
from osxphotos.photoinfo import PhotoInfo
from osxphotos.photosdb.photosdb_utils import (
    COMPACT_UTI_CODES,
    get_library_compact_uti_codes,
    uti_for_compact_uti,
)

# Edited Live Photos whose files, edited video included, are in the library
EDITED_LIVE_PHOTOS = [
    (
        "./tests/Test-Media-Types-15.7.2.photoslibrary",
        "B60C31AC-BE86-42C5-A383-A0A5710B18AE",  # IMG_4394.HEIC
    ),
    (
        "./tests/Test-Cloud-13.1.photoslibrary",
        "0FFBE909-7142-4299-BA6F-9B6E7E8FB338",  # IMG_0728.JPG
    ),
]


@pytest.fixture
def download_calls(monkeypatch):
    """Record calls to the download helper instead of downloading"""
    calls = []

    def fake_helper(self, options):
        calls.append(options)
        return StagedFiles()

    monkeypatch.setattr(
        PhotoExporter, "_stage_missing_photos_for_export_helper", fake_helper
    )
    return calls


def _export_edited_live_without_edited_mov(
    monkeypatch, tmp_path, library, uuid, use_photokit
):
    """Export an edited Live Photo whose edited video was never on the
    destination, then export it again with the edited files evicted

    Returns the second export's results and the destination of the edited image.
    """
    photo = osxphotos.PhotosDB(dbfile=library).get_photo(uuid)
    assert photo.live_photo and photo.hasadjustments
    assert photo.path_edited and photo.path_edited_live_photo

    dest_dir = tmp_path / "export"
    dest_dir.mkdir()

    def export(**kwargs):
        # each export is a separate run, with the export DB opened afresh
        export_db = osxphotos.ExportDB(tmp_path / "export.db", dest_dir)
        options = ExportOptions(
            edited=True,
            live_photo=True,
            update=True,
            export_db=export_db,
            use_photokit=use_photokit,
            **kwargs,
        )
        try:
            return PhotoExporter(photo).export(dest_dir, options=options)
        finally:
            export_db.close()

    # First run: the edited image is exported, the edited video is not
    # (as with an AppleScript download, which cannot produce one)
    monkeypatch.setattr(PhotoInfo, "path_edited_live_photo", None)
    first = export()
    assert len(first.exported) == 1
    edited_dest = pathlib.Path(first.exported[0])
    edited_mov = edited_dest.parent / f"{edited_dest.stem}.mov"
    assert edited_dest.exists()
    assert not edited_mov.exists()

    # Second run: the edited image is evicted from the library too
    monkeypatch.setattr(PhotoInfo, "path_edited", None)
    second = export(download_missing=True)
    return second, edited_dest


@pytest.mark.parametrize("library,uuid", EDITED_LIVE_PHOTOS)
def test_edited_live_photo_up_to_date_applescript_no_download(
    monkeypatch, tmp_path, download_calls, library, uuid
):
    """AppleScript can't export an edited Live Photo's video, so a missing
    <name>_edited.mov must not make the check ask for a download (#12)"""
    results, edited_dest = _export_edited_live_without_edited_mov(
        monkeypatch, tmp_path, library, uuid, use_photokit=False
    )
    assert download_calls == []
    assert results.exported == []
    assert str(edited_dest) in results.skipped


@pytest.mark.parametrize("library,uuid", EDITED_LIVE_PHOTOS)
def test_edited_live_photo_missing_edited_mov_photokit_downloads(
    monkeypatch, tmp_path, download_calls, library, uuid
):
    """PhotoKit can export an edited Live Photo's video, so a missing
    <name>_edited.mov still asks for a download"""
    _export_edited_live_without_edited_mov(
        monkeypatch, tmp_path, library, uuid, use_photokit=True
    )
    assert len(download_calls) == 1
    assert download_calls[0].use_photokit
    assert download_calls[0].live_photo


# RAW+JPEG pairs (#11). On Photos 7+ these libraries have no RAW type in the
# column osxphotos reads for Photos 5 and 6, and uti_raw came from the RAW file
# on disk, so it was None while the RAW was evicted.
RAW_PAIR_LIBRARIES = [
    "./tests/Test-13.0.0.photoslibrary",
    "./tests/Test-15.4.1.photoslibrary",
    "./tests/Test-26.1.photoslibrary",
]
CR2_UTI = "com.canon.cr2-raw-image"
# IMG_2000.JPG: a pair not copied into the library, missing: path_raw is None
UUID_MISSING_PAIR = "8E1D7BC9-9321-44F9-8CFB-4083F6B9232A"
# IMG_1994.JPG: a pair stored in the library with its CR2 on disk
UUID_RAW_PAIR = "A92D9C26-3A50-4197-9388-CB5F7DB9FA91"
RAW_PAIRS = [(UUID_MISSING_PAIR, "IMG_2000"), (UUID_RAW_PAIR, "IMG_1994")]


@pytest.fixture(scope="module", params=RAW_PAIR_LIBRARIES)
def raw_photosdb(request):
    return osxphotos.PhotosDB(dbfile=request.param)


@pytest.fixture
def evicted_pair(raw_photosdb, monkeypatch):
    """Make IMG_1994 look evicted to iCloud: neither its JPEG nor its RAW is on disk

    Returns the paths of IMG_1994's JPEG and CR2 in the library, read beforehand.
    """
    present = raw_photosdb.get_photo(UUID_RAW_PAIR)
    files = (present.path, present.path_raw)
    assert all(files)
    for attr in ("path", "path_raw"):
        prop = getattr(PhotoInfo, attr)

        def evicted_path(self, prop=prop):
            return None if self.uuid == UUID_RAW_PAIR else prop.fget(self)

        monkeypatch.setattr(PhotoInfo, attr, property(evicted_path))
    return files


class FakeDownload:
    """Stands in for _stage_missing_photos_for_export_helper and records each call

    It stages copies of the given files under the given names, as an AppleScript
    download does; with no files it stages nothing, as a failed download does.
    """

    def __init__(self, staging: pathlib.Path | None = None, files=None):
        self.calls = []
        self.staging = staging
        self.files = files or {}

    def __call__(self, exporter, options):
        self.calls.append(options)
        staged = StagedFiles()
        for kind, (src, name) in self.files.items():
            staged_path = self.staging / name
            shutil.copy(src, staged_path)
            setattr(staged, kind, str(staged_path))
        return staged


def _export_raw_pair(photo, dest_dir, monkeypatch, download):
    """Export a RAW+JPEG pair with --update --download-missing"""
    monkeypatch.setattr(
        PhotoExporter,
        "_stage_missing_photos_for_export_helper",
        lambda self, options: download(self, options),
    )
    # each export is a separate run, with the export DB opened afresh
    export_db = osxphotos.ExportDB(dest_dir.parent / "export.db", dest_dir)
    options = ExportOptions(
        update=True,
        download_missing=True,
        raw_photo=True,
        export_db=export_db,
    )
    try:
        return PhotoExporter(photo).export(dest_dir, options=options)
    finally:
        export_db.close()


def _lower(paths):
    """Paths as lower-case strings: the RAW's extension case comes from Photos"""
    return sorted(str(path).lower() for path in paths)


@pytest.fixture
def exported_pair(raw_photosdb, evicted_pair, monkeypatch, tmp_path, request):
    """Export an evicted RAW+JPEG pair once, downloading <stem>.JPG and <stem>.CR2

    The downloaded content is IMG_1994's. Returns the photo, the export
    directory and the stem.
    """
    uuid, stem = request.param
    photo = raw_photosdb.get_photo(uuid)
    assert photo.has_raw and not photo.path and not photo.path_raw

    dest_dir = tmp_path / "export"
    dest_dir.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir()
    jpeg, raw = evicted_pair
    download = FakeDownload(
        staging, {"original": (jpeg, f"{stem}.JPG"), "raw": (raw, f"{stem}.CR2")}
    )
    first = _export_raw_pair(photo, dest_dir, monkeypatch, download)
    assert len(download.calls) == 1
    assert _lower(first.new) == _lower(
        [dest_dir / f"{stem}.JPG", dest_dir / f"{stem}.CR2"]
    )
    return photo, dest_dir, stem


@pytest.mark.parametrize("exported_pair", RAW_PAIRS, indirect=True)
def test_raw_pair_up_to_date_no_download(exported_pair, monkeypatch):
    """An evicted RAW+JPEG pair already exported and unchanged is skipped
    without a download (#11)"""
    photo, dest_dir, stem = exported_pair
    download = FakeDownload()
    results = _export_raw_pair(photo, dest_dir, monkeypatch, download)
    assert download.calls == []
    assert _lower(results.skipped) == _lower(
        [dest_dir / f"{stem}.JPG", dest_dir / f"{stem}.CR2"]
    )
    assert results.new == results.updated == results.missing == []


@pytest.mark.parametrize("changed", ["JPG", "CR2"])
@pytest.mark.parametrize("exported_pair", RAW_PAIRS, indirect=True)
def test_raw_pair_changed_downloads(exported_pair, monkeypatch, changed):
    """An evicted pair whose JPEG or RAW changed on the destination is
    downloaded again; one not yet exported is downloaded by exported_pair"""
    photo, dest_dir, stem = exported_pair
    (dest_dir / f"{stem}.{changed}").write_bytes(b"changed")
    download = FakeDownload()
    _export_raw_pair(photo, dest_dir, monkeypatch, download)
    assert len(download.calls) == 1
    assert download.calls[0].raw_photo


@pytest.mark.parametrize("exported_pair", RAW_PAIRS, indirect=True)
def test_missing_raw_recorded_with_real_extension(exported_pair, monkeypatch):
    """A RAW that needs updating but doesn't download is reported missing as
    .CR2, not .raw"""
    photo, dest_dir, stem = exported_pair
    (dest_dir / f"{stem}.CR2").write_bytes(b"changed")
    download = FakeDownload()
    results = _export_raw_pair(photo, dest_dir, monkeypatch, download)
    assert len(download.calls) == 1
    assert _lower(results.missing) == _lower(
        [dest_dir / f"{stem}.JPG", dest_dir / f"{stem}.CR2"]
    )


@pytest.mark.parametrize("uuid", [UUID_MISSING_PAIR, UUID_RAW_PAIR])
def test_uti_raw_without_raw_on_disk(raw_photosdb, evicted_pair, uuid):
    """uti_raw comes from the Photos database when the RAW isn't on disk"""
    photo = raw_photosdb.get_photo(uuid)
    assert photo.path_raw is None
    assert photo.uti_raw == CR2_UTI


@pytest.mark.parametrize("uuid", [UUID_MISSING_PAIR, UUID_RAW_PAIR])
def test_uti_raw_with_raw_on_disk_unchanged(raw_photosdb, uuid):
    """uti_raw still comes from the RAW file when it is on disk"""
    photo = raw_photosdb.get_photo(uuid)
    if photo.path_raw:
        assert photo.uti_raw == CR2_UTI


@pytest.mark.parametrize(
    "compact_uti,library_codes,uti",
    [
        ("11", None, CR2_UTI),
        (11, None, CR2_UTI),
        ("21", None, "com.fuji.raw-image"),
        ("_org.webmproject.webp", None, "org.webmproject.webp"),
        ("99", None, None),
        ("99", {"99": "com.sony.arw-raw-image"}, "com.sony.arw-raw-image"),
        ("11", {"11": "public.jpeg"}, CR2_UTI),
        (None, None, None),
    ],
)
def test_uti_for_compact_uti(compact_uti, library_codes, uti):
    """ZCOMPACTUTI is a code, or "_" and the UTI itself"""
    assert uti_for_compact_uti(compact_uti, library_codes) == uti


@pytest.mark.parametrize("library", RAW_PAIR_LIBRARIES)
def test_library_compact_uti_codes_match_table(library):
    """The codes the test libraries use for their originals agree with the table"""
    db = pathlib.Path(library) / "database" / "Photos.sqlite"
    conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    try:
        codes = get_library_compact_uti_codes(conn.cursor(), "ZASSET")
    finally:
        conn.close()
    assert codes
    for code, uti in codes.items():
        assert COMPACT_UTI_CODES.get(code, uti) == uti
