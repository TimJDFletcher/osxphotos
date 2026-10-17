"""utility functions used by PhotosDB"""

from __future__ import annotations

import logging
import os
import pathlib
import plistlib
import sys

from .._constants import (
    _DB_TABLE_NAMES,
    _PHOTOS_2_VERSION,
    _PHOTOS_3_VERSION,
    _PHOTOS_4_VERSION,
    _PHOTOS_5_MODEL_VERSION,
    _PHOTOS_5_VERSION,
    _PHOTOS_6_MODEL_VERSION,
    _PHOTOS_7_MODEL_VERSION,
    _PHOTOS_8_MODEL_VERSION,
    _PHOTOS_9_14_6_MODEL_VERSION,
    _PHOTOS_9_MODEL_VERSION,
    _PHOTOS_10_MODEL_VERSION,
    _PHOTOS_10B1_MODEL_VERSION,
    _PHOTOS_11_1_MODEL_VERSION,
    _PHOTOS_11_MODEL_VERSION,
    _PHOTOS_12_MODEL_VERSION,
    _TESTED_DB_VERSIONS,
)
from ..sqlite_utils import sqlite_open_ro

logger = logging.getLogger("osxphotos")

__all__ = [
    "get_db_version",
    "get_model_version",
    "get_photos_library_version",
    "get_photos_version_from_model",
]

LATEST_PHOTOS_VERSION = max(_DB_TABLE_NAMES.keys())


def get_db_version(db_file: os.PathLike) -> str:
    """Gets the Photos DB version from LiGlobals table

    Args:
        db_file: path to photos.db database file containing LiGlobals table or Photos.sqlite database file

    Returns: version as str
    """

    version = None

    (conn, c) = sqlite_open_ro(db_file)

    # get database version
    result = [
        row[0]
        for row in c.execute(
            "SELECT name FROM sqlite_master WHERE type ='table' AND name NOT LIKE 'sqlite_%';"
        ).fetchall()
    ]
    if "LiGlobals" in result:
        # it's a photos.db file
        c.execute(
            "SELECT value FROM LiGlobals WHERE LiGlobals.keyPath IS 'libraryVersion'"
        )
        version = c.fetchone()[0]
    elif "Z_METADATA" in result:
        # assume it's a Photos 5+ Photos.sqlite file
        # get_model_version will find the exact version
        version = "5001"
    else:
        raise ValueError(f"Unknown database format: {db_file}")
    conn.close()

    if version not in _TESTED_DB_VERSIONS:
        logger.warning(
            f"WARNING: OSXPhotos has only been tested on Photos database versions [{', '.join(_TESTED_DB_VERSIONS)}]"
            + f" You have database version={version} which has not been tested",
            file=sys.stderr,
        )

    return version


def get_model_version(db_file: str) -> str | None:
    """Returns the database model version from Z_METADATA

    Args:
        db_file: path to Photos.sqlite database file containing Z_METADATA table

    Returns: model version as str or None if model version not found
    """

    (conn, c) = sqlite_open_ro(db_file)

    # get database version
    try:
        c.execute("SELECT MAX(Z_VERSION), Z_PLIST FROM Z_METADATA")
        results = c.fetchone()
    except Exception as e:
        logger.warning(f"Error getting model version: {e}")
        return None

    conn.close()

    if not results or results[0] is None:
        logger.warning(f"Error getting model version; no results from query")
        return None

    try:
        plist = plistlib.loads(results[1])
        return plist["PLModelVersion"]
    except KeyError:
        logger.warning(f"Error getting model version: {results}")
        return None


def get_photos_version_from_model(db_file: str) -> int:
    """Returns Photos version based on model version found in db_file

    Args:
        db_file: path to Photos.sqlite file

    Returns: int of major Photos version number (e.g. 5 or 6).
    If unknown model version found, logs warning and returns most current Photos version.
    """

    model_ver = get_model_version(db_file)
    if model_ver is None:
        logger.warning(
            f"Could not determine model version for {db_file}; assuming latest version"
        )
        return LATEST_PHOTOS_VERSION
    model_ver = int(model_ver)
    if _PHOTOS_5_MODEL_VERSION[0] <= model_ver <= _PHOTOS_5_MODEL_VERSION[1]:
        return 5
    if _PHOTOS_6_MODEL_VERSION[0] <= model_ver <= _PHOTOS_6_MODEL_VERSION[1]:
        return 6
    if _PHOTOS_7_MODEL_VERSION[0] <= model_ver <= _PHOTOS_7_MODEL_VERSION[1]:
        return 7
    if _PHOTOS_8_MODEL_VERSION[0] <= model_ver <= _PHOTOS_8_MODEL_VERSION[1]:
        return 8
    if _PHOTOS_9_MODEL_VERSION[0] <= model_ver <= _PHOTOS_9_MODEL_VERSION[1]:
        return 9
    if _PHOTOS_9_14_6_MODEL_VERSION[0] <= model_ver <= _PHOTOS_9_14_6_MODEL_VERSION[1]:
        return 9.6
    if _PHOTOS_10B1_MODEL_VERSION[0] <= model_ver <= _PHOTOS_10B1_MODEL_VERSION[1]:
        return 9.9
    if _PHOTOS_10_MODEL_VERSION[0] <= model_ver <= _PHOTOS_10_MODEL_VERSION[1]:
        return 10
    if _PHOTOS_11_MODEL_VERSION[0] <= model_ver <= _PHOTOS_11_MODEL_VERSION[1]:
        return 11
    if _PHOTOS_11_1_MODEL_VERSION[0] <= model_ver <= _PHOTOS_11_1_MODEL_VERSION[1]:
        return 11.1
    if _PHOTOS_12_MODEL_VERSION[0] <= model_ver <= _PHOTOS_12_MODEL_VERSION[1]:
        return 12
    logger.warning(
        f"Unknown db / model version for {db_file}: model_ver={model_ver}; assuming latest version"
    )
    return LATEST_PHOTOS_VERSION


def get_photos_library_version(library_path: str | pathlib.Path) -> int:
    """Return int indicating which Photos version a library was created with

    Args:
        library_path: path to Photos library; may be path to the root of the library or the photos.db file

    Returns: int of major Photos version number (e.g. 5, 6, ...)
    """
    library_path = pathlib.Path(library_path)
    if not library_path.is_dir():
        # assume we got passed library path/database/Photos.sqlite or library path/database/photos.db
        library_path = library_path.parent.parent

    db_path = library_path / "database" / "photos.db"
    if db_path.exists():
        try:
            db_ver = int(get_db_version(str(db_path)))
            if db_ver == int(_PHOTOS_2_VERSION):
                return 2
            if db_ver == int(_PHOTOS_3_VERSION):
                return 3
            if db_ver == int(_PHOTOS_4_VERSION):
                return 4
        except Exception:
            pass

    # assume it's a Photos 5+ library, get the model version to determine which version
    db_path = library_path / "database" / "Photos.sqlite"
    if db_path.exists():
        return get_photos_version_from_model(str(db_path))
    else:
        raise ValueError(f"Could not find Photos database at: {library_path}")


def get_db_path_for_library(photos_library: str | pathlib.Path) -> pathlib.Path:
    """Returns path to Photos database file for Photos library

    Args:
        photos_library: path to Photos library; may be path to the root of the library or the photos.db file

    Returns: pathlib.Path to Photos database file
    """
    photos_library = pathlib.Path(photos_library)
    if photos_library.is_file():
        return photos_library
    photos_version = get_photos_library_version(photos_library)
    if photos_version < 5:
        if photos_library.is_dir():
            photos_library = photos_library / "database" / "photos.db"
    elif photos_library.is_dir():
        photos_library = photos_library / "database" / "Photos.sqlite"
    return photos_library


# ZINTERNALRESOURCE.ZCOMPACTUTI codes in Photos 7+ libraries, seen in Photos 8 to 11.
# A type with no code is stored as "_" followed by its UTI, e.g. "_org.webmproject.webp".
COMPACT_UTI_CODES = {
    "1": "public.jpeg",
    "3": "public.heic",
    "6": "public.png",
    "8": "public.tiff",
    "9": "com.adobe.raw-image",
    "11": "com.canon.cr2-raw-image",
    "21": "com.fuji.raw-image",
    "23": "com.apple.quicktime-movie",
    "24": "public.mpeg-4",
}


def uti_for_compact_uti(
    compact_uti: str | int | None, library_codes: dict[str, str] | None = None
) -> str | None:
    """Return the UTI for a ZINTERNALRESOURCE.ZCOMPACTUTI value (Photos 7+)

    Args:
        compact_uti: the ZCOMPACTUTI value
        library_codes: codes not in COMPACT_UTI_CODES, mapped to UTIs from the
            library itself (see get_library_compact_uti_codes)

    Returns: the UTI, or None if it can't be determined
    """
    if compact_uti is None:
        return None
    compact_uti = str(compact_uti)
    if compact_uti.startswith("_"):
        return compact_uti[1:] or None
    return COMPACT_UTI_CODES.get(compact_uti) or (library_codes or {}).get(compact_uti)


def get_library_compact_uti_codes(cursor, asset_table: str) -> dict[str, str]:
    """Map each ZCOMPACTUTI code in a Photos 7+ library to a UTI, from the
    library's own original resources: the asset UTI most often seen with each code

    Args:
        cursor: sqlite3 cursor on the library's Photos.sqlite
        asset_table: name of the asset table, e.g. ZASSET
    """
    cursor.execute(
        f""" SELECT
        ZINTERNALRESOURCE.ZCOMPACTUTI,
        {asset_table}.ZUNIFORMTYPEIDENTIFIER,
        COUNT(*)
        FROM ZINTERNALRESOURCE
        JOIN {asset_table} ON {asset_table}.Z_PK = ZINTERNALRESOURCE.ZASSET
        WHERE ZINTERNALRESOURCE.ZRESOURCETYPE = 0
        AND ZINTERNALRESOURCE.ZDATASTORESUBTYPE = 1
        GROUP BY 1, 2
        ORDER BY 3
        """
    )
    # rows are in ascending count, so the most common UTI for each code wins
    return {
        str(code): uti for code, uti, _ in cursor.fetchall() if code is not None and uti
    }
