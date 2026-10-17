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
RAF_UTI = "com.fuji.raw-image"
# IMG_2000.JPG: a pair not copied into the library, missing: path_raw is None
UUID_MISSING_PAIR = "8E1D7BC9-9321-44F9-8CFB-4083F6B9232A"
# IMG_1994.JPG: a pair stored in the library with its CR2 on disk
UUID_RAW_PAIR = "A92D9C26-3A50-4197-9388-CB5F7DB9FA91"
RAW_PAIRS = [(UUID_MISSING_PAIR, "IMG_2000"), (UUID_RAW_PAIR, "IMG_1994")]


class RawType:
    """A RAW format under test: its UTI, and the extension a download stages
    it under (as Photos writes it: .CR2 in upstream's tests, .raf on the
    owner's share, e.g. DSCF5860.raf)"""

    def __init__(self, name: str, uti: str, staged_ext: str):
        self.name = name
        self.uti = uti
        self.staged_ext = staged_ext

    def __repr__(self):
        return self.name


CR2 = RawType("cr2", CR2_UTI, "CR2")
RAF = RawType("raf", RAF_UTI, "raf")
# ZINTERNALRESOURCE.ZCOMPACTUTI for a Fuji RAF, as in the owner's library
# (9,311 RAW resources, whose RAW originals have UTI com.fuji.raw-image)
RAF_COMPACT_UTI = 21
# Pumkins2.jpg, made a RAF-only original in the RAF copies (no JPEG pair): the
# owner's library has one named DSCF5273.RAF, on the share as DSCF5273.RAF
UUID_RAF_ONLY = "D79B8D77-BFFC-460B-9312-034F2877D35B"
RAF_ONLY_FILENAME = "DSCF5273.RAF"


def _make_raf_library(library: str, dest: pathlib.Path) -> pathlib.Path:
    """Copy a test library and turn its CR2+JPEG pairs into RAF+JPEG pairs

    The test libraries have no Fuji photos. In the copy, the RAW resources of
    IMG_2000 and IMG_1994 get the compact UTI the owner's library has for RAF,
    IMG_2000's referenced RAW is renamed IMG_2000.RAF, and IMG_1994's RAW in
    originals/ is renamed from _4.cr2 to _4.raf. The checked-in library is
    not modified.
    """
    copy = dest / pathlib.Path(library).name
    shutil.copytree(library, copy)
    conn = sqlite3.connect(copy / "database" / "Photos.sqlite")
    try:
        uuids = (UUID_MISSING_PAIR, UUID_RAW_PAIR)
        raw_resources = """ SELECT ZINTERNALRESOURCE.Z_PK FROM ZINTERNALRESOURCE
            JOIN ZASSET ON ZASSET.Z_PK = ZINTERNALRESOURCE.ZASSET
            WHERE ZINTERNALRESOURCE.ZDATASTORESUBTYPE = 17 AND ZASSET.ZUUID IN (?, ?)
        """
        cur = conn.execute(
            f"UPDATE ZINTERNALRESOURCE SET ZCOMPACTUTI = ? WHERE Z_PK IN ({raw_resources})",
            (RAF_COMPACT_UTI, *uuids),
        )
        assert cur.rowcount == 2
        cur = conn.execute(
            f""" UPDATE ZFILESYSTEMBOOKMARK
            SET ZPATHRELATIVETOVOLUME = replace(ZPATHRELATIVETOVOLUME, '.CR2', '.RAF')
            WHERE ZRESOURCE IN ({raw_resources})""",
            uuids,
        )
        assert cur.rowcount == 1  # IMG_2000, a referenced file
        _make_raf_only_original(conn)
        conn.commit()
    finally:
        conn.close()
    (raw_cr2,) = (copy / "originals").glob(f"*/{UUID_RAW_PAIR}_4.cr2")
    raw_cr2.rename(raw_cr2.with_suffix(".raf"))
    # the RAF-only original is evicted: its file isn't in originals/
    (copy / "originals" / "D" / f"{UUID_RAF_ONLY}.jpeg").unlink()
    return copy


def _make_raf_only_original(conn):
    """Turn Pumkins2.jpg into an evicted RAF-only original, DSCF5273.RAF

    Shaped on the owner's 3FFD68E9-...: asset UTI com.fuji.raw-image, compact
    UTI 21 on the original resource, not available locally.
    """
    (asset_pk,) = conn.execute(
        "SELECT Z_PK FROM ZASSET WHERE ZUUID = ?", (UUID_RAF_ONLY,)
    ).fetchone()
    conn.execute(
        "UPDATE ZASSET SET ZUNIFORMTYPEIDENTIFIER = ?, ZFILENAME = ? WHERE Z_PK = ?",
        (RAF_UTI, f"{UUID_RAF_ONLY}.raf", asset_pk),
    )
    conn.execute(
        "UPDATE ZADDITIONALASSETATTRIBUTES SET ZORIGINALFILENAME = ? WHERE ZASSET = ?",
        (RAF_ONLY_FILENAME, asset_pk),
    )
    cur = conn.execute(
        """ UPDATE ZINTERNALRESOURCE SET ZCOMPACTUTI = ?, ZDATALENGTH = ?
        WHERE ZASSET = ? AND ZRESOURCETYPE = 0 AND ZDATASTORESUBTYPE = 1""",
        (RAF_COMPACT_UTI, 33790976, asset_pk),
    )
    assert cur.rowcount == 1
    conn.execute(
        "UPDATE ZINTERNALRESOURCE SET ZLOCALAVAILABILITY = -1 WHERE ZASSET = ?",
        (asset_pk,),
    )


@pytest.fixture(scope="module")
def raf_libraries(tmp_path_factory):
    """RAF copies of the test libraries, made when first asked for"""
    copies = {}

    def get(library):
        if library not in copies:
            copies[library] = _make_raf_library(
                library, tmp_path_factory.mktemp("raf_library")
            )
        return copies[library]

    return get


RAW_LIBRARIES = [
    pytest.param((library, raw_type), id=f"{pathlib.Path(library).stem}-{raw_type}")
    for raw_type in (CR2, RAF)
    for library in RAW_PAIR_LIBRARIES
]


@pytest.fixture(scope="module", params=RAW_LIBRARIES)
def raw_library(request, raf_libraries):
    """(PhotosDB, RawType): a test library as it is (CR2 pairs), or its RAF copy"""
    library, raw_type = request.param
    if raw_type is RAF:
        library = raf_libraries(library)
    return osxphotos.PhotosDB(dbfile=str(library)), raw_type


@pytest.fixture
def raw_photosdb(raw_library):
    return raw_library[0]


@pytest.fixture
def raw_type(raw_library):
    return raw_library[1]


@pytest.fixture
def evicted_pair(raw_photosdb, monkeypatch):
    """Make IMG_1994 look evicted to iCloud: neither its JPEG nor its RAW is on disk

    Returns the paths of IMG_1994's JPEG and RAW in the library, read beforehand.
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
def exported_pair(raw_photosdb, raw_type, evicted_pair, monkeypatch, tmp_path, request):
    """Export an evicted RAW+JPEG pair once, downloading <stem>.JPG and the RAW
    (<stem>.CR2 or <stem>.raf)

    The downloaded content is IMG_1994's. Returns the photo, the export
    directory, the stem and the name of the RAW on the destination.
    """
    uuid, stem = request.param
    photo = raw_photosdb.get_photo(uuid)
    assert photo.has_raw and not photo.path and not photo.path_raw

    dest_dir = tmp_path / "export"
    dest_dir.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir()
    jpeg, raw = evicted_pair
    raw_name = f"{stem}.{raw_type.staged_ext}"
    download = FakeDownload(
        staging, {"original": (jpeg, f"{stem}.JPG"), "raw": (raw, raw_name)}
    )
    first = _export_raw_pair(photo, dest_dir, monkeypatch, download)
    assert len(download.calls) == 1
    assert _lower(first.new) == _lower([dest_dir / f"{stem}.JPG", dest_dir / raw_name])
    # the RAW is written under the staged name, case included
    assert raw_name in [path.name for path in dest_dir.iterdir()]
    return photo, dest_dir, stem, raw_name


@pytest.mark.parametrize("exported_pair", RAW_PAIRS, indirect=True)
def test_raw_pair_up_to_date_no_download(exported_pair, monkeypatch):
    """An evicted RAW+JPEG pair already exported and unchanged is skipped
    without a download (#11)"""
    photo, dest_dir, stem, raw_name = exported_pair
    download = FakeDownload()
    results = _export_raw_pair(photo, dest_dir, monkeypatch, download)
    assert download.calls == []
    assert _lower(results.skipped) == _lower(
        [dest_dir / f"{stem}.JPG", dest_dir / raw_name]
    )
    assert results.new == results.updated == results.missing == []


@pytest.mark.parametrize("changed", ["JPG", "RAW"])
@pytest.mark.parametrize("exported_pair", RAW_PAIRS, indirect=True)
def test_raw_pair_changed_downloads(exported_pair, monkeypatch, changed):
    """An evicted pair whose JPEG or RAW changed on the destination is
    downloaded again; one not yet exported is downloaded by exported_pair"""
    photo, dest_dir, stem, raw_name = exported_pair
    changed_name = f"{stem}.JPG" if changed == "JPG" else raw_name
    (dest_dir / changed_name).write_bytes(b"changed")
    download = FakeDownload()
    _export_raw_pair(photo, dest_dir, monkeypatch, download)
    assert len(download.calls) == 1
    assert download.calls[0].raw_photo


@pytest.mark.parametrize("exported_pair", RAW_PAIRS, indirect=True)
def test_missing_raw_recorded_with_real_extension(exported_pair, raw_type, monkeypatch):
    """A RAW that needs updating but doesn't download is reported missing
    under its real extension (.cr2 or .raf), not .raw"""
    photo, dest_dir, stem, raw_name = exported_pair
    (dest_dir / raw_name).write_bytes(b"changed")
    download = FakeDownload()
    results = _export_raw_pair(photo, dest_dir, monkeypatch, download)
    assert len(download.calls) == 1
    assert _lower(results.missing) == _lower(
        [dest_dir / f"{stem}.JPG", dest_dir / f"{stem}.{raw_type.name}"]
    )


@pytest.mark.parametrize("uuid", [UUID_MISSING_PAIR, UUID_RAW_PAIR])
def test_uti_raw_without_raw_on_disk(raw_photosdb, raw_type, evicted_pair, uuid):
    """uti_raw comes from the Photos database when the RAW isn't on disk"""
    photo = raw_photosdb.get_photo(uuid)
    assert photo.path_raw is None
    assert photo.uti_raw == raw_type.uti


@pytest.mark.parametrize("uuid", [UUID_MISSING_PAIR, UUID_RAW_PAIR])
def test_uti_raw_with_raw_on_disk_unchanged(raw_photosdb, raw_type, uuid):
    """uti_raw still comes from the RAW file when it is on disk"""
    photo = raw_photosdb.get_photo(uuid)
    if photo.path_raw:
        assert pathlib.Path(photo.path_raw).suffix == f".{raw_type.name}"
        assert photo.uti_raw == raw_type.uti


def test_raf_pair_exported_and_checked_under_lower_case_raf(
    raf_libraries, monkeypatch, tmp_path
):
    """A RAF+JPEG pair with its RAF on disk is exported as <stem>.raf, lower
    case as on the owner's share (DSCF5860.raf), and once it is evicted the
    check looks for that exact name, so it is skipped without a download even
    on a case-sensitive destination"""
    library = raf_libraries("./tests/Test-26.1.photoslibrary")
    photo = osxphotos.PhotosDB(dbfile=str(library)).get_photo(UUID_RAW_PAIR)
    assert photo.path and photo.path_raw.endswith("_4.raf")
    dest_dir = tmp_path / "export"
    dest_dir.mkdir()

    download = FakeDownload()
    first = _export_raw_pair(photo, dest_dir, monkeypatch, download)
    assert download.calls == []
    names = sorted(path.name for path in dest_dir.iterdir())
    assert names == ["IMG_1994.JPG", "IMG_1994.raf"]
    assert sorted(first.new) == [str(dest_dir / name) for name in names]

    for attr in ("path", "path_raw"):
        monkeypatch.setattr(PhotoInfo, attr, None)
    download = FakeDownload()
    results = _export_raw_pair(photo, dest_dir, monkeypatch, download)
    assert download.calls == []
    assert sorted(results.skipped) == [str(dest_dir / name) for name in names]


@pytest.mark.parametrize("library", RAW_PAIR_LIBRARIES)
def test_raf_only_original_checked_under_its_upper_case_name(
    raf_libraries, monkeypatch, tmp_path, library
):
    """An evicted RAF-only original is exported under its original name,
    DSCF5273.RAF (upper case, unlike a pair's .raf), and once up to date the
    check finds that exact name and skips it without a download"""
    library = raf_libraries(library)
    photo = osxphotos.PhotosDB(dbfile=str(library)).get_photo(UUID_RAF_ONLY)
    assert photo.ismissing and photo.path is None
    assert not photo.has_raw
    assert photo.original_filename == RAF_ONLY_FILENAME
    assert photo.uti == photo.uti_original == RAF_UTI

    dest_dir = tmp_path / "export"
    dest_dir.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir()
    raf_bytes = tmp_path / "raf_bytes"
    raf_bytes.write_bytes(b"FUJIFILMCCD-RAW ")
    download = FakeDownload(staging, {"original": (raf_bytes, RAF_ONLY_FILENAME)})
    first = _export_raw_pair(photo, dest_dir, monkeypatch, download)
    assert len(download.calls) == 1
    assert [path.name for path in dest_dir.iterdir()] == [RAF_ONLY_FILENAME]
    assert first.new == [str(dest_dir / RAF_ONLY_FILENAME)]

    download = FakeDownload()
    results = _export_raw_pair(photo, dest_dir, monkeypatch, download)
    assert download.calls == []
    assert results.skipped == [str(dest_dir / RAF_ONLY_FILENAME)]
    assert results.new == results.updated == results.missing == []


@pytest.mark.parametrize(
    "compact_uti,library_codes,uti",
    [
        ("11", None, CR2_UTI),
        (11, None, CR2_UTI),
        ("21", None, RAF_UTI),
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
