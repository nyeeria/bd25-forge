from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


APP_VERSION = "1.0.1"
HANDBRAKE_VERSION = "1.11.2"
TSMUXER_VERSION = "2.7.0"
ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = ROOT / "packaging" / "vendor"
RELEASE_ROOT = ROOT / "release"


@dataclass(frozen=True)
class Target:
    key: str
    handbrake_url: str
    handbrake_sha256: str
    handbrake_kind: str
    tsmuxer_url: str
    tsmuxer_sha256: str
    handbrake_name: str
    tsmuxer_name: str


TARGETS = {
    "windows": Target(
        key="windows",
        handbrake_url=(
            "https://github.com/HandBrake/HandBrake/releases/download/1.11.2/"
            "HandBrakeCLI-1.11.2-win-x86_64.zip"
        ),
        handbrake_sha256="80bfe8d5f5d11cc3ef76b834add3ed4e82dee6523ffeb435c283f88b1a21f09d",
        handbrake_kind="zip",
        tsmuxer_url=(
            "https://github.com/justdan96/tsMuxer/releases/download/2.7.0/"
            "tsMuxer-2.7.0-win64.zip"
        ),
        tsmuxer_sha256="e3cf117d2c6f01332188123641c63fe38ab9731ae7aa23645f6f9a261a7a301c",
        handbrake_name="HandBrakeCLI.exe",
        tsmuxer_name="tsMuxeR.exe",
    ),
    "macos": Target(
        key="macos",
        handbrake_url=(
            "https://github.com/HandBrake/HandBrake/releases/download/1.11.2/"
            "HandBrakeCLI-1.11.2.dmg"
        ),
        handbrake_sha256="14463aa81038aaa3ce421dc6cee65fd6c82fdabda040931541ccca38939299fa",
        handbrake_kind="dmg",
        tsmuxer_url=(
            "https://github.com/justdan96/tsMuxer/releases/download/2.7.0/"
            "tsMuxer-2.7.0-mac.zip"
        ),
        tsmuxer_sha256="bf90b608321f491b955fd9432000a8370c048e9c306e3a5833dfd75e5fa530e1",
        handbrake_name="HandBrakeCLI",
        tsmuxer_name="tsMuxeR",
    ),
}

HANDBRAKE_SOURCE_URL = (
    "https://github.com/HandBrake/HandBrake/releases/download/1.11.2/"
    "HandBrake-1.11.2-source.tar.bz2"
)
HANDBRAKE_SOURCE_SHA256 = "12b046350f2422dc28783ff94229aff4ba5fe5e683431e057355d36163b2593a"
HANDBRAKE_LICENSE_URL = (
    "https://raw.githubusercontent.com/HandBrake/HandBrake/1.11.2/COPYING"
)
TSMUXER_LICENSE_URL = (
    "https://raw.githubusercontent.com/justdan96/tsMuxer/2.7.0/LICENSE"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a self-contained BD25 Forge release for the current operating system."
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="download and stage the bundled runtimes without running PyInstaller",
    )
    args = parser.parse_args()

    target = _current_target()
    tools_dir, legal_dir = prepare_runtime(target)
    print(f"Bundled runtime prepared in {tools_dir}")
    if args.prepare_only:
        return

    _require_pyinstaller()
    output = build_application(target, tools_dir, legal_dir)
    print(f"Release created: {output}")


def _current_target() -> Target:
    system = platform.system()
    key = "windows" if system == "Windows" else "macos" if system == "Darwin" else ""
    if not key:
        raise SystemExit("Release builds currently support Windows and macOS.")
    if key == "windows" and platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("The Windows release must be built with x64 Python on an x64 machine.")
    if key == "macos" and platform.machine().lower() != "arm64":
        raise SystemExit("The macOS release currently targets Apple Silicon (arm64).")
    return TARGETS[key]


def prepare_runtime(target: Target) -> tuple[Path, Path]:
    vendor_dir = VENDOR_ROOT / target.key
    tools_dir = vendor_dir / "tools"
    legal_dir = vendor_dir / "third_party"
    tools_dir.mkdir(parents=True, exist_ok=True)
    legal_dir.mkdir(parents=True, exist_ok=True)

    for old_item in tools_dir.iterdir():
        if old_item.is_dir():
            shutil.rmtree(old_item)
        else:
            old_item.unlink()

    with tempfile.TemporaryDirectory(prefix="bd25-build-") as temporary:
        temp_dir = Path(temporary)
        handbrake_archive = temp_dir / Path(target.handbrake_url).name
        tsmuxer_archive = temp_dir / Path(target.tsmuxer_url).name
        _download(target.handbrake_url, handbrake_archive, target.handbrake_sha256)
        _download(target.tsmuxer_url, tsmuxer_archive, target.tsmuxer_sha256)

        handbrake_destination = tools_dir / target.handbrake_name
        if target.handbrake_kind == "zip":
            _extract_named_file(handbrake_archive, target.handbrake_name, handbrake_destination)
        else:
            _extract_handbrake_dmg(handbrake_archive, handbrake_destination)

        tsmuxer_extract = temp_dir / "tsmuxer"
        _extract_zip_safely(tsmuxer_archive, tsmuxer_extract)
        tsmuxer_binary = _find_named_file(tsmuxer_extract, target.tsmuxer_name)
        _copy_tool_directory(tsmuxer_binary.parent, tools_dir)
        if not (tools_dir / target.tsmuxer_name).is_file():
            shutil.copy2(tsmuxer_binary, tools_dir / target.tsmuxer_name)

    for executable in (tools_dir / target.handbrake_name, tools_dir / target.tsmuxer_name):
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    _download(
        HANDBRAKE_SOURCE_URL,
        legal_dir / f"HandBrake-{HANDBRAKE_VERSION}-source.tar.bz2",
        HANDBRAKE_SOURCE_SHA256,
    )
    _download(HANDBRAKE_LICENSE_URL, legal_dir / "HandBrake-COPYING.txt")
    _download(TSMUXER_LICENSE_URL, legal_dir / "tsMuxer-LICENSE.txt")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", legal_dir / "THIRD_PARTY_NOTICES.md")
    shutil.copy2(ROOT / "LICENSE", legal_dir / "BD25-Forge-LICENSE.txt")
    return tools_dir, legal_dir


def _download(url: str, destination: Path, expected_sha256: str | None = None) -> None:
    if destination.is_file() and (
        expected_sha256 is None or _sha256(destination) == expected_sha256
    ):
        return
    print(f"Downloading {Path(url).name}...")
    request = urllib.request.Request(url, headers={"User-Agent": "BD25-Forge-Builder/1.0"})
    partial = destination.with_suffix(destination.suffix + ".part")
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
        shutil.copyfileobj(response, output)
    if expected_sha256 and _sha256(partial) != expected_sha256:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Checksum verification failed for {destination.name}.")
    partial.replace(destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_named_file(archive: Path, filename: str, destination: Path) -> None:
    with zipfile.ZipFile(archive) as source:
        member = next(
            (item for item in source.infolist() if Path(item.filename).name.lower() == filename.lower()),
            None,
        )
        if member is None:
            raise RuntimeError(f"{filename} was not found in {archive.name}.")
        with source.open(member) as input_file, destination.open("wb") as output_file:
            shutil.copyfileobj(input_file, output_file)


def _extract_zip_safely(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        root = destination.resolve()
        for member in source.infolist():
            output = (destination / member.filename).resolve()
            if root != output and root not in output.parents:
                raise RuntimeError(f"Unsafe path in {archive.name}: {member.filename}")
        source.extractall(destination)


def _extract_handbrake_dmg(archive: Path, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="bd25-dmg-") as temporary:
        mountpoint = Path(temporary) / "mount"
        mountpoint.mkdir()
        subprocess.run(
            ["hdiutil", "attach", str(archive), "-nobrowse", "-readonly", "-mountpoint", str(mountpoint)],
            check=True,
        )
        try:
            source = _find_named_file(mountpoint, "HandBrakeCLI")
            shutil.copy2(source, destination)
        finally:
            subprocess.run(["hdiutil", "detach", str(mountpoint)], check=True)


def _find_named_file(root: Path, filename: str) -> Path:
    match = next(
        (path for path in root.rglob("*") if path.is_file() and path.name.lower() == filename.lower()),
        None,
    )
    if match is None:
        raise RuntimeError(f"{filename} was not found under {root}.")
    return match


def _copy_tool_directory(source: Path, destination: Path) -> None:
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def _require_pyinstaller() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            "PyInstaller is required only to build a release. Run: "
            "python -m pip install -r packaging/requirements-build.txt"
        )


def build_application(target: Target, tools_dir: Path, legal_dir: Path) -> Path:
    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        "BD25 Forge",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build"),
        "--specpath",
        str(ROOT / "build"),
    ]
    if target.key == "macos":
        command.extend(("--osx-bundle-identifier", "com.bd25forge.app"))
    command.extend(_pyinstaller_files("--add-binary", tools_dir, "tools"))
    command.extend(_pyinstaller_files("--add-data", legal_dir, "third_party"))
    command.append(str(ROOT / "run_bd25.py"))
    subprocess.run(command, cwd=ROOT, check=True)

    architecture = platform.machine().lower().replace("amd64", "x86_64")
    if target.key == "windows":
        app_dir = ROOT / "dist" / "BD25 Forge"
        archive_base = RELEASE_ROOT / f"BD25-Forge-{APP_VERSION}-windows-{architecture}"
        archive = Path(shutil.make_archive(str(archive_base), "zip", app_dir.parent, app_dir.name))
        return archive

    app_bundle = ROOT / "dist" / "BD25 Forge.app"
    dmg = RELEASE_ROOT / f"BD25-Forge-{APP_VERSION}-macos-{architecture}.dmg"
    dmg.unlink(missing_ok=True)
    subprocess.run(
        [
            "hdiutil",
            "create",
            "-volname",
            "BD25 Forge",
            "-srcfolder",
            str(app_bundle),
            "-ov",
            "-format",
            "UDZO",
            str(dmg),
        ],
        check=True,
    )
    return dmg


def _pyinstaller_files(flag: str, source_root: Path, destination_root: str) -> list[str]:
    arguments: list[str] = []
    for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
        relative_parent = source.relative_to(source_root).parent
        destination = Path(destination_root) / relative_parent
        arguments.extend((flag, f"{source}{os.pathsep}{destination.as_posix()}"))
    return arguments


if __name__ == "__main__":
    main()
