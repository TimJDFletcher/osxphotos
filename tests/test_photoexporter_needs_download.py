"""Tests for PhotoExporter._needs_download_for_update, the pre-download check
used by export --update --download-missing (#1086), with edited Live Photos

An edited Live Photo's edited video can be exported through PhotoKit but not
through AppleScript, so the check only requires <name>_edited.mov on the
destination when PhotoKit would download it.

_stage_missing_photos_for_export_helper, the one place a download through
AppleScript or PhotoKit would start, is monkeypatched to record its calls, so
nothing is downloaded and Photos is never touched.
"""

from __future__ import annotations

import pathlib

import pytest

import osxphotos
from osxphotos.exportoptions import ExportOptions
from osxphotos.photoexporter import PhotoExporter, StagedFiles
from osxphotos.photoinfo import PhotoInfo

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


def _export(photo, dest_dir, **options):
    """Export photo with --update and options as a separate run, with the
    export DB opened afresh as the CLI does"""
    export_db = osxphotos.ExportDB(dest_dir.parent / "export.db", dest_dir)
    try:
        return PhotoExporter(photo).export(
            dest_dir,
            options=ExportOptions(update=True, export_db=export_db, **options),
        )
    finally:
        export_db.close()


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
        return _export(
            photo,
            dest_dir,
            edited=True,
            live_photo=True,
            use_photokit=use_photokit,
            **kwargs,
        )

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
