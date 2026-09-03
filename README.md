# BD25 Forge

BD25 Forge is a self-contained desktop application that compresses the main feature from a 30 GB or 50 GB Blu-ray ISO and authors a Blu-ray ISO no larger than 25,000,000,000 bytes.

It supports:

- NVIDIA GPU encoding through NVENC and NVDEC on Windows
- Apple GPU encoding through VideoToolbox on macOS
- CPU fallback through two-pass x264
- Automatic main-feature selection or a manually entered title number
- AC-3 5.1 audio, chapters, and optional burned forced subtitles
- Cancellation, progress reporting, and a detailed activity log

## No Runtime Installation

Official release bundles include:

- The BD25 Forge GUI
- Python and Tkinter
- HandBrakeCLI
- tsMuxeR
- Required license notices and HandBrake's corresponding source code

Users do not install Python, HandBrake, tsMuxeR, FFmpeg, or command-line tools. Extract the Windows release and run `BD25 Forge.exe`, or install the macOS application from its DMG.

An NVIDIA display driver is still required to use an NVIDIA GPU. Apple VideoToolbox is supplied by macOS. These hardware interfaces cannot be embedded in an application.

## Important Limitations

- The output contains the selected movie title and all of its audio tracks. Original menus, extras, and Java content are not preserved; tsMuxeR creates the standard Blu-ray title structure rather than interactive BD-J menus.
- Use only unencrypted ISOs that you are legally allowed to process. The application does not remove Blu-ray encryption or copy protection.
- GPU encoding is much faster, but CPU x264 generally gives better image quality at the same file size.
- Hardware average-bitrate encoding is not byte-exact. The app reserves space for encoder variance, audio, M2TS overhead, and Blu-ray metadata, then verifies the completed ISO size.
- A conversion needs about twice the target size free because the encoded movie and final ISO temporarily coexist.

## Use

1. Choose the source Blu-ray ISO and a different output path.
2. Select NVIDIA NVENC, Apple VideoToolbox, or CPU x264.
3. Keep `25.0` for a nominal BD-25 target.
4. Keep **Title** set to `Auto`, or enter a title number if the wrong playlist is selected.
5. Select **Build BD25 ISO**.

The source scan lists each available audio track with its original language, codec, channel layout, and bitrate. Choose one track in the GUI; that track is copied without transcoding or metadata overrides. Its original metadata is retained where supported, and its selected language code is written to the output track without defaulting to English.

## Build Distributable Apps

Build Windows releases on Windows and macOS releases on macOS:

```text
python -m pip install -r packaging/requirements-build.txt
python packaging/build_release.py
```

The builder downloads pinned official media runtimes, verifies available SHA-256 checksums, stages legal materials, invokes PyInstaller, and creates a distributable archive in `release/`. Detailed instructions and automated GitHub Actions builds are in `BUILDING.md`.

For source development, prepare the integrated runtime first:

```text
python packaging/build_release.py --prepare-only
python run_bd25.py
```

## Test

```text
python -m unittest discover -v
```
