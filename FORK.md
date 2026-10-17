# Fork notes: fixes-0.77.2

This is Tim's fork of [RhetTbull/osxphotos](https://github.com/RhetTbull/osxphotos). The branch `fixes-0.77.2` starts at upstream's `v0.77.2` tag, the version [icloud-photos-backup](https://oxygen.vpn.night-shade.org.uk/tim/icloud-photos-backup) pins. It carries fixes for two re-download bugs until upstream has them. `fork/` and this file are fork-only: they stay out of any upstream PR. The work is tracked in tim/icloud-photos-backup [#10](https://oxygen.vpn.night-shade.org.uk/tim/icloud-photos-backup/issues/10) to #14.

In `export --update --download-missing`, the pre-download check (`PhotoExporter._needs_download_for_update`) downloads originals that are already up to date on the destination:

- **#11, RAW+JPEG pairs while evicted:** on Photos 7 and later `PhotoInfo.uti_raw` comes from `path_raw`, which is empty while the RAW is evicted. The check then guesses `.raw`, never finds `DSCF5860.raw` next to the real `DSCF5860.raf`, and downloads the pair every run. With `--cleanup` it also deleted the real `.raf`, which was recorded missing as `.raw`.
- **#12, edited Live Photos:** the check needs `<name>_edited.mov` on the destination, which AppleScript export can never produce, so every edited Live Photo is downloaded every run.

Commit straight to `fixes-0.77.2` in small commits and push each one. Remotes: `origin` is the fork, `upstream` is RhetTbull/osxphotos.

## Dev environment

uv and Python 3.13, the same as the backup, which runs this fork from a `-tim.N` tag (`uvx --python 3.13 --from git+https://github.com/TimJDFletcher/osxphotos@<tag> osxphotos`):

```bash
uv venv --python 3.13
uv pip install -e ".[dev]"
```

`[dev]` in `pyproject.toml` holds the test requirements; there is no separate requirements file (see `README_DEV.md`). exiftool comes from Homebrew (`/opt/homebrew/bin/exiftool`), so the exiftool tests run too.

## Running the export-path tests

```bash
.venv/bin/python -m pytest -p fork.no_photos -rsfE -q -p no:cacheprovider \
  tests/test_photoexporter_needs_download.py tests/test_photoexporter_needs_download_raw.py \
  tests/test_cli.py tests/test_cli_export_*.py tests/test_cli_exportdb.py \
  tests/test_export_*.py tests/test_exportresults.py \
  tests/test_concurrent_export.py tests/test_live_catalina_10_15_1.py
```

On 2026-10-17, on macOS 27.0.1 (arm64) with Python 3.13.5 and the branch unchanged at v0.77.2, the run (without the two `needs_download` modules) took about 95 s: **439 passed, 17 skipped, 0 failed**. With the fixes and both modules: **567 passed, 17 skipped, 0 failed** (128 of them in the two new modules).

These modules cover `photoexporter.py`, `--update`, `--download-missing` and the export CLI. On macOS 13 and later, `tests/conftest.py` uses `tests/Test-13.0.0.photoslibrary` as the main test library; `test_cli.py` also reads several others.

### `fork/no_photos.py` keeps the run away from the real library

`-p fork.no_photos` is a pytest plugin that lives in this fork. It changes no upstream test. Before conftest loads, it makes all of these raise `PhotosAccessBlocked`:

- running AppleScript (`applescript.AppleScript.run`/`call`, which is what photoscript uses)
- `osascript` subprocesses, and `open`/`killall`/`pkill` subprocesses that name Photos
- PhotoKit authorization
- looking up the system or last-opened Photos library

A test that would reach Photos therefore fails instead of touching `~/Pictures/Photos Library.photoslibrary`. Nothing tripped it in the run above.

Leave these out. They need the Photos app or the system library:

- `tests/test_photoexporter_kill_photos.py`: launches and kills Photos.app.
- Tests gated on conftest flags: `--addalbum`, `--timewarp`, `--photodates`, `--test-import`, `--test-import-takeout`, `--test-sync`, `--test-add-locations` and `--test-batch-edit`. They copy a library into `~/Pictures` and drive Photos. Never pass these flags.
- `test_photokit.py`, `test_platform_photos_framework.py`, `test_local_library.py` and `test_search_info_personal_library.py`. These use PhotoKit or the system library.
- Never set `OSXPHOTOS_TEST_LOCAL` or `OSXPHOTOS_TEST_LOCAL_V2`. Those tests export from the author's own library with `--use-photos-export` and `--download-missing`, that is through AppleScript and Photos.

### The 17 skips (all upstream's own skip markers; none are caused by this Mac)

| Count | Where | Reason |
|---|---|---|
| 7 | `test_cli.py:5873, 9474, 9513, 9561, 9608, 9640`, `test_export_convert_to_jpeg.py:67` | `OSXPHOTOS_TEST_LOCAL`/`_V2` not set: they need the author's personal library and the Photos app. These include upstream's only `--download-missing --update` tests (#456, #564). |
| 7 | `test_cli.py:3125, 3153, 3183, 9408, 9439`, `test_export_convert_to_jpeg.py:37, 52` | `OSXPHOTOS_TEST_CONVERT` not set (GPU image conversion). They are safe to run, but upstream skips them by default. |
| 1 | `test_cli.py:4791` | `OSXPHOTOS_TEST_LOCALE` not set. |
| 1 | `test_cli.py:1366` | Unconditional `@pytest.mark.skip`: "fails ... on recent macOS". |
| 1 | `test_cli.py:6680` | Unconditional skip: "fails on some machines but not all". |

## What the fixes change beyond the bugs

- **`uti_raw` of an evicted RAW.** On Photos 7+ `PhotoInfo.uti_raw` now reports the RAW's type from the Photos database when the RAW isn't on disk, where it used to be `None`. That shows wherever `uti_raw` is read: `asdict()` and JSON output, `print_photo_info`, queries, and the dry-run filename prediction (`_predict_export_filenames`).
- **`tests/test-Test-26.1.json`.** Because of that, IMG_2000's `uti_raw` there changed from `null` to `com.canon.cr2-raw-image`: its RAW is missing, and the type now comes from the database.
- **Unknown compact UTI codes.** `COMPACT_UTI_CODES` maps the codes seen in Photos 8 to 11 (1 jpeg, 3 heic, 6 png, 9 dng, 11 cr2, 21 raf were each consistent across the libraries seen). A code not in it is learned from the library: first from original resources with that code (keyed by the asset's UTI), then from RAWs with that code whose file is on disk (the UTI for the file's extension). So one local RAW of a type, say a Sony ARW, resolves the evicted RAWs of that type. With neither, `uti_raw` stays `None` and the check guesses `.raw`, as before. The table comes first because the library-derived guesses are only as good as the library's records.

## A possible upstream bug: reusing one ExportDB

Found while writing #12's tests: if one `ExportDB` is used for two exports in the same process, the second export reports the files as `NOT_IN_DATABASE`, because the directory record cache from the first export's prefetch isn't refreshed. The CLI opens a fresh `ExportDB` per run, so it doesn't hit this, and the tests open a fresh one per export. Not reported upstream yet.

## Upstream PRs

The fixes go upstream as two PRs, one per fix: they touch different code and neither needs the other. Branches with just the upstream-bound changes, each one commit off `v0.77.2`, are on the fork for checking that each applies and passes on its own (built from the files on `fixes-0.77.2`, not cherry-picked, because the trunk history mixes the two fixes' tests in one module before the split):

| PR | Branch | Files |
|---|---|---|
| Edited Live Photos re-downloaded (#12) | `upstream/edited-live-photo` | `osxphotos/photoexporter.py`, `tests/test_photoexporter_needs_download.py`, its `CHANGELOG.md` line |
| RAW type from the Photos database (#11, incl. `--cleanup`) | `upstream/raw-type-from-db` | `osxphotos/photoinfo.py`, `osxphotos/photosdb/photosdb.py`, `osxphotos/photosdb/photosdb_utils.py`, `tests/test-Test-26.1.json`, `tests/test_photoexporter_needs_download_raw.py`, its two `CHANGELOG.md` lines |

Leave out `fork/` and this file. The tests don't need `fork/no_photos.py`; it only guards runs here. The two PRs each add lines under `[Unreleased]` → `Fixed`, so whichever merges second needs a trivial CHANGELOG rebase.

To rebuild a branch after trunk changes, check those files out from `fixes-0.77.2` onto `v0.77.2` and add the CHANGELOG lines by hand.

## How the tests for #11 and #12 were planned

Upstream has no test for `_needs_download_for_update` (#1086). Its only `--download-missing --update` tests need the author's own library. The new tests are in their own modules, `tests/test_photoexporter_needs_download.py` (#12) and `tests/test_photoexporter_needs_download_raw.py` (#11), rather than in `test_cli.py`.

**Test at the `PhotoExporter` level, not through the CLI.** The check is pure logic over `PhotoInfo` attributes, the destination files and the export DB.

1. Export the photo once with `PhotoExporter(photo).export(dest, options=ExportOptions(export_db=ExportDB(...), update=True, ...))`.
2. Monkeypatch `PhotoExporter._stage_missing_photos_for_export_helper` to record each call and return an empty `StagedFiles()`. That is the one place a download (AppleScript or PhotoKit) would start, and the patch keeps the test away from Photos.
3. Export again with `download_missing=True`. Assert that the helper was not called, and that the results list the files as `skipped` (`staged.update_skipped` feeds `results.skipped`, `photoexporter.py` around lines 358-440).

Calling `_needs_download_for_update(staged, dest, options)` directly is also possible for small cases. `options.use_photokit` is just a flag, so #12's AppleScript and PhotoKit cases are two `ExportOptions` values. Only the CLI wiring would need `CliRunner` (see `test_cli.py`).

Fixtures:

- **#11, a RAW+JPEG pair with the RAW missing:** `8E1D7BC9-9321-44F9-8CFB-4083F6B9232A` (IMG_2000.JPG). It is in `Test-13.0.0`, `Test-15.4.1` and `Test-26.1` (Photos 8, 10 and 11.1): `has_raw=True`, `ismissing=True`, `path_raw=None`, `uti_raw=None`. That is the bug as found, with no mocking. `test_cli.py` already names it `CLI_EXPORT_UUID_MISSING`. Pairs whose RAW is present (CR2) are `A92D9C26-...` (IMG_1994, JPEG original) and `4D521201-...` (IMG_1997, `raw_original=True`) in the same libraries. Make them evicted with a monkeypatch of `path`/`path_raw` (see `tests/photoinfo_mock.py`, or `monkeypatch.setattr` on the `PhotoInfo` class).
  - For a "destination already up to date" state on IMG_2000, write the files the export DB expects by hand, under the RAW's real extension.
  - `Test-RAW-10.15.1` is Photos 5, where `uti_raw` is read from the database, so it does not show the bug.
  - In `Test-13.0.0`, `Test-15.4.1` and `Test-26.1`, the RAW resources (`ZDATASTORESUBTYPE` 17) have `ZINTERNALRESOURCE.ZCOMPACTUTI` `11`, the code for CR2; the fix reads it.
- **#12, edited Live Photos:**
  - `B60C31AC-BE86-42C5-A383-A0A5710B18AE` (IMG_4394.HEIC, `Test-Media-Types-15.7.2`)
  - `0FFBE909-7142-4299-BA6F-9B6E7E8FB338` (IMG_0728.JPG, `Test-Cloud-13.1`)

  Both are edited Live Photos whose files are on disk, edited `.mov` included. Monkeypatch `path_edited` and `path_edited_live_photo` to `None` to make them look evicted. Then export with `edited=True, live_photo=True` and no `_edited.mov` on the destination.
