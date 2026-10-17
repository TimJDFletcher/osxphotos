"""pytest plugin: stop the test run reaching the Photos app or the system library.

Load it with `-p fork.no_photos`. It changes no upstream test. A test that
would run AppleScript (photoscript, py-applescript, osascript), ask PhotoKit
for access, open or kill Photos, or look up the system or last-opened Photos
library raises PhotosAccessBlocked instead, so it fails rather than touching
the real library. See FORK.md.

With `--linux-stat-cache` it also gives DirectoryStatCache its default off
macOS, matching names exactly, so a test that only passes with macOS's
case-insensitive matching fails here as it would on upstream's Ubuntu CI.
"""

import os
import subprocess

import pytest


class PhotosAccessBlocked(RuntimeError):
    """A test tried to reach Photos, AppleScript, PhotoKit or the system library."""


def _blocked(what):
    def blocked(*args, **kwargs):
        raise PhotosAccessBlocked(f"fork/no_photos.py blocked {what}")

    return blocked


def _is_blocked_command(args) -> bool:
    if isinstance(args, (str, bytes)):
        argv = [os.fsdecode(args)]
    else:
        argv = [os.fsdecode(a) for a in args]
    if not argv:
        return False
    cmd = os.path.basename(argv[0].split()[0]) if argv[0].strip() else ""
    joined = " ".join(argv)
    if cmd == "osascript":
        return True
    if cmd in ("killall", "pkill") and "Photos" in joined:
        return True
    return cmd == "open" and "Photos" in joined


_real_popen_init = subprocess.Popen.__init__


def _guarded_popen_init(self, args, *rest, **kwargs):
    if _is_blocked_command(args):
        raise PhotosAccessBlocked(f"fork/no_photos.py blocked command {args!r}")
    return _real_popen_init(self, args, *rest, **kwargs)


def pytest_addoption(parser):
    parser.addoption(
        "--linux-stat-cache",
        action="store_true",
        help="fork/no_photos.py: match names exactly in DirectoryStatCache, as off macOS",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config, parser, args):
    # Runs before tests/conftest.py and the test modules import osxphotos, so
    # names they import with `from ... import` are already the blocked ones.
    import applescript

    applescript.AppleScript.run = _blocked("AppleScript.run")
    applescript.AppleScript.call = _blocked("AppleScript.call")

    subprocess.Popen.__init__ = _guarded_popen_init

    import osxphotos.utils

    osxphotos.utils.get_system_library_path = _blocked("get_system_library_path")
    osxphotos.utils.get_last_library_path = _blocked("get_last_library_path")

    import osxphotos.photokit

    osxphotos.photokit.check_photokit_authorization = _blocked(
        "check_photokit_authorization"
    )
    osxphotos.photokit.request_photokit_authorization = _blocked(
        "request_photokit_authorization"
    )

    if early_config.known_args_namespace.linux_stat_cache:
        import osxphotos.stat_cache

        # DirectoryStatCache reads this when it's created, to pick its default
        osxphotos.stat_cache.is_macos = False
