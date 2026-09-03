# Building Self-Contained Releases

The release builder creates an application that includes Python, Tkinter, HandBrakeCLI, tsMuxeR, required licenses, and HandBrake's corresponding source code. End users do not install Python or either media tool.

Build on the same operating system as the release target. Windows and macOS binaries cannot be cross-compiled by this workflow.

## Windows x64

Use 64-bit Python 3.10 or newer:

```powershell
python -m pip install -r packaging/requirements-build.txt
python packaging/build_release.py
```

The result is `release/BD25-Forge-1.0.5-windows-x86_64.zip`. Users extract it and run `BD25 Forge.exe`; no separate runtime installation is needed.

## macOS Apple Silicon

Use Python 3.10 or newer on the target architecture:

```bash
python3 -m pip install -r packaging/requirements-build.txt
python3 packaging/build_release.py
```

The result is an arm64 DMG in `release/`. The pinned tsMuxeR release is an Apple Silicon binary, so this workflow intentionally rejects Intel builds rather than producing a package that requires Rosetta. Code-sign and notarize public macOS releases with your Apple Developer identity after building.

## Runtime Preparation Only

To download and verify the pinned media runtimes without invoking PyInstaller:

```text
python packaging/build_release.py --prepare-only
```

HandBrake archives are verified against their official SHA-256 digests. Release inputs are pinned in `packaging/build_release.py`; review and update the version, URL, digest, license, and corresponding source archive together.

## Automated Builds

The GitHub Actions workflow in `.github/workflows/build-release.yml` builds Windows x64 and macOS Apple Silicon artifacts. It runs manually or for a version tag. Signing credentials are intentionally not embedded in the repository.
