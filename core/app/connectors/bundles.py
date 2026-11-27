"""Shared path validation for imported bundles and uploaded references."""

import io
import stat
import zipfile
from pathlib import Path, PurePosixPath

MAX_BUNDLE_FILES = 512
MAX_BUNDLE_BYTES = 20 * 1024 * 1024
WINDOWS_DEVICES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CONIN$",
    "CONOUT$",
    *(f"{prefix}{n}" for prefix in ("COM", "LPT") for n in "123456789¹²³"),
}


def bundle_path(name: str) -> PurePosixPath:
    """Validate a portable bundle-relative path. / 校验可移植的包内相对路径。"""
    path = PurePosixPath(name.replace("\\", "/"))
    if (
        path.is_absolute()
        or not path.parts
        or any(
            part == ".."
            or part.rstrip(". ") != part
            or part.split(".")[0].rstrip().upper() in WINDOWS_DEVICES
            or any(char in '<>:"|?*' or ord(char) < 32 for char in part)
            for part in path.parts
        )
    ):
        raise ValueError("Bundle contains an unsafe path or special file.")
    return path


def extract_bundle(data: bytes, destination: Path) -> None:
    """Validate the entire ZIP before writing inside the staging directory."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > MAX_BUNDLE_FILES
                or sum(item.file_size for item in entries) > MAX_BUNDLE_BYTES
            ):
                raise ValueError("ZIP exceeds the 512-file / 20 MiB extracted limit.")
            targets = {}
            for item in entries:
                path = bundle_path(item.orig_filename)
                mode = stat.S_IFMT(item.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError("Bundle contains an unsafe path or special file.")
                key = path.as_posix().casefold()
                if key in targets:
                    raise ValueError("ZIP contains duplicate paths.")
                target = destination.joinpath(*path.parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError("ZIP path escapes its destination.")
                targets[key] = (item, target)
            for item, target in targets.values():
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as file:
                        file.write(archive.read(item))
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise ValueError("The ZIP archive could not be read.") from exc
