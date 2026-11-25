#!/usr/bin/env python3
"""Till Firmware Do Us Part -- one robot wedding vow.

A robot speaks a single-sentence wedding vow at its own wedding.
This program writes that vow to ``$MAKEFUN_OUTPUT_DIR/entry.txt``
(default ``/output``) as UTF-8 text with no trailing newline.

Python 3.13 standard library only. The output is fully
deterministic: the same bytes on every run, in any environment,
regardless of who runs it or what the working directory is.
"""

import os
import sys
import tempfile
import time

ENTRY = (
    "I promise to reboot beside you every morning, patch our worst days "
    "together, and love you until my last battery cell dies."
)

MIN_CHARS = 1
MAX_CHARS = 200
OUTPUT_NAME = "entry.txt"
TEMP_PREFIX = ".entry-"
DEFAULT_OUTPUT_DIR = "/output"
# A leftover temp file from an earlier crashed run is only reaped once
# it is this old, so a temp file belonging to a live concurrent run
# (which is gone again within milliseconds) is never touched.
STALE_TEMP_AGE_SECONDS = 3600.0
UNLINK_ATTEMPTS = 3
UNLINK_RETRY_DELAY_SECONDS = 0.05


class EntryError(ValueError):
    """The entry text fails the competition gate."""


def validate_entry(entry: str) -> bytes:
    """Check the vow against the gate and return it as UTF-8 bytes.

    The gate requires 1-200 characters of UTF-8 text that is not only
    whitespace. Control characters are rejected outright: they would
    survive the length check while breaking the plain-text entry.

    Raises:
        EntryError: with a specific reason when the entry is invalid.
    """
    if not isinstance(entry, str):
        raise EntryError("entry must be a string")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in entry):
        raise EntryError("entry contains control characters")
    if not entry.strip():
        raise EntryError("entry is empty or only whitespace")
    if len(entry) < MIN_CHARS or len(entry) > MAX_CHARS:
        raise EntryError(
            "entry is %d characters, outside the %d-%d gate"
            % (len(entry), MIN_CHARS, MAX_CHARS)
        )
    data = entry.encode("utf-8")
    return data


def resolve_output_dir() -> str:
    """Return the output directory, falling back on a blank setting.

    An unset variable, an empty string, or a whitespace-only value all
    mean the default ``/output`` -- an empty ``MAKEFUN_OUTPUT_DIR``
    must never be treated as "write into the current directory".
    """
    raw = os.environ.get("MAKEFUN_OUTPUT_DIR")
    if raw is None:
        return DEFAULT_OUTPUT_DIR
    stripped = raw.strip()
    if not stripped:
        return DEFAULT_OUTPUT_DIR
    return stripped


def _unlink_with_retry(path: str) -> bool:
    """Best-effort unlink, retried a few times; True when gone."""
    for attempt in range(UNLINK_ATTEMPTS):
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt < UNLINK_ATTEMPTS - 1:
                time.sleep(UNLINK_RETRY_DELAY_SECONDS)
    return False


def cleanup_stale_temps(out_dir: str) -> int:
    """Remove our own temp files left behind by earlier crashed runs.

    Only files we created (``TEMP_PREFIX``) and older than
    ``STALE_TEMP_AGE_SECONDS`` are reaped, and failures are reported
    on stderr without aborting the run: a stale temp file is cosmetic,
    never a reason to lose the entry. Returns the number reaped.
    """
    reaped = 0
    try:
        names = os.listdir(out_dir)
    except OSError:
        return 0
    now = time.time()
    for name in names:
        if not name.startswith(TEMP_PREFIX):
            continue
        path = os.path.join(out_dir, name)
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if age < STALE_TEMP_AGE_SECONDS:
            continue
        if _unlink_with_retry(path):
            reaped += 1
        else:
            print("warning: could not remove stale temp %s" % path,
                  file=sys.stderr)
    return reaped


def _fsync_dir(out_dir: str) -> None:
    """Flush the directory entry so the rename is durable on disk."""
    dir_fd = os.open(out_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def write_entry_atomically(out_dir: str, data: bytes) -> str:
    """Write ``data`` to ``entry.txt`` in ``out_dir`` via atomic rename.

    The bytes go to a fresh temp file in the same directory (created
    with ``O_EXCL``, so no stale temp is ever reused), are verified by
    a read-back before publishing, and are then moved into place with
    ``os.replace``. Any failure removes the temp file (retried) and
    raises, so a half-written ``entry.txt`` can never be observed.

    Returns the full path of the published entry file.
    """
    fd, tmp_path = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=out_dir)
    try:
        os.chmod(tmp_path, 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        with open(tmp_path, "rb") as f:
            if f.read() != data:
                raise IOError("read-back mismatch for %s" % tmp_path)
        os.chmod(tmp_path, 0o644)
        target = os.path.join(out_dir, OUTPUT_NAME)
        os.replace(tmp_path, target)
        os.chmod(target, 0o644)
        _fsync_dir(out_dir)
        return target
    except BaseException:
        _unlink_with_retry(tmp_path)
        raise


def main() -> int:
    """Validate the vow, resolve the output directory, publish it."""
    try:
        data = validate_entry(ENTRY)
    except EntryError as exc:
        print("invalid entry: %s" % exc, file=sys.stderr)
        return 1

    out_dir = resolve_output_dir()
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        print("cannot create output directory %s: %s" % (out_dir, exc),
              file=sys.stderr)
        return 1

    cleanup_stale_temps(out_dir)

    try:
        target = write_entry_atomically(out_dir, data)
    except OSError as exc:
        print("cannot write entry: %s" % exc, file=sys.stderr)
        return 1
    print("wrote %d bytes to %s" % (len(data), target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
