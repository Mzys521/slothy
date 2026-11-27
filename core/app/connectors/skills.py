"""Read and stage SKILL.md and its references for the existing skill loader."""

import base64
import binascii
import re
import shutil
import stat
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.connectors.bundles import MAX_BUNDLE_BYTES, MAX_BUNDLE_FILES, bundle_path

MAX_SKILL_TEXT = 32000
MAX_REFERENCE_UPLOAD = 10 * 1024 * 1024
_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|$)", re.M | re.S)


class ReferenceUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=180)
    data: str


class SkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    instructions: str = Field(min_length=1, max_length=MAX_SKILL_TEXT)
    enabled: StrictBool = False
    revision: str | None = Field(default=None, max_length=64)
    files: list[ReferenceUpload] = Field(default_factory=list, max_length=MAX_BUNDLE_FILES - 1)
    remove_files: list[str] = Field(default_factory=list, max_length=MAX_BUNDLE_FILES - 1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Provide a skill name on one line.")
        return value

    @field_validator("instructions")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Provide skill instructions.")
        return value


def split_skill(text: str) -> tuple[dict, str]:
    """Read front matter if recognized, preserving legacy instruction text otherwise."""
    header = _FRONT_MATTER.match(text)
    if header:
        try:
            metadata = yaml.safe_load(header[1])
            if isinstance(metadata, dict):
                return metadata, text[header.end() :]
        except (yaml.YAMLError, ValueError, RecursionError):
            pass
    return {}, text


def skill_markdown(body: SkillInput, metadata: dict) -> str:
    """Serialize user fields as YAML values, followed by the Markdown instructions."""
    header = yaml.safe_dump(
        {**metadata, "name": body.name, "description": body.description.strip()},
        sort_keys=False,
        allow_unicode=True,
    )
    text = f"---\n{header}---\n{body.instructions.rstrip()}\n"
    if len(text) > MAX_SKILL_TEXT:
        raise ValueError("SKILL.md including its metadata must fit in 32,000 characters.")
    return text


def bundle_files(root: Path) -> list[str]:
    """List plain bundle files with portable names, rejecting links and reparse points."""
    files = []
    for entry in root.rglob("*"):
        metadata = entry.lstat()
        if stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("Skill bundles cannot contain linked files or directories.")
        if stat.S_ISREG(metadata.st_mode):
            files.append(bundle_path(entry.relative_to(root).as_posix()).as_posix())
        elif not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("Skill bundles must contain regular files and directories.")
    return sorted(files)


def stage_skill(
    stage: Path,
    source: Path | None,
    entry: str,
    body: SkillInput,
    previous_text: str,
) -> tuple[str, int, int]:
    """Stage a replacement bundle without modifying any of the currently registered files."""
    entry_path = bundle_path(entry)
    previous = bundle_files(source) if source is not None else []
    references = references_from_paths(previous, entry)
    if not set(body.remove_files).issubset(references):
        raise ValueError("A reference selected for removal no longer exists.")
    removed = {(entry_path.parent / name).as_posix() for name in body.remove_files}
    kept = [name for name in previous if name != entry and name not in removed]
    occupied = {name.casefold() for name in kept}
    metadata, _ = split_skill(previous_text)
    text = skill_markdown(body, metadata)
    uploaded = []
    received = 0
    for file in body.files:
        name = bundle_path(file.name)
        if name.as_posix() != file.name or len(name.parts) != 1:
            raise ValueError("Reference files must have a plain filename.")
        if name.name.casefold() == "skill.md":
            raise ValueError("Use the instruction editor to update SKILL.md.")
        relative = (entry_path.parent / "references" / name).as_posix()
        if relative.casefold() in occupied:
            raise ValueError("Reference filenames must be unique.")
        occupied.add(relative.casefold())
        try:
            data = base64.b64decode(file.data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("A reference upload is not valid base64.") from exc
        received += len(data)
        if received > MAX_REFERENCE_UPLOAD:
            raise ValueError("New reference files must total at most 10 MiB.")
        uploaded.append((relative, data))
    paths = [entry, *kept, *(name for name, _ in uploaded)]
    existing_bytes = sum((source / name).stat().st_size for name in kept) if source else 0
    if len(paths) > MAX_BUNDLE_FILES or (
        existing_bytes + received + len(text.encode("utf-8")) > MAX_BUNDLE_BYTES
    ):
        raise ValueError("The skill bundle exceeds 512 files or 20 MiB.")
    for name in kept:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    for name, data in uploaded:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    target = stage / entry
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return text, len(paths), len(references_from_paths(paths, entry))


def references_from_paths(paths: list[str], entry: str) -> list[str]:
    """Return file paths relative to the loader's SKILL.md directory."""
    root_file = bundle_path(entry)
    parent = root_file.parent
    references = []
    for name in paths:
        path = bundle_path(name)
        if path != root_file and path.is_relative_to(parent):
            references.append(path.relative_to(parent).as_posix())
    return sorted(references)
