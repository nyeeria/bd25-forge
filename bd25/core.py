from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable


DECIMAL_GB = 1_000_000_000
BLURAY_VIDEO_BITRATE_LIMIT_KBPS = 35_000
DEFAULT_AUDIO_BITRATE_KBPS = 640


class ConversionError(RuntimeError):
    pass


class ConversionCancelled(ConversionError):
    pass


@dataclass(frozen=True)
class TitleInfo:
    index: int
    duration_seconds: float
    name: str = ""
    audio_track_count: int = 1
    audio_track_indices: tuple[int, ...] = ()
    audio_bitrate_kbps: int = 0
    audio_track_descriptions: tuple[str, ...] = ()
    audio_track_languages: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScanResult:
    titles: tuple[TitleInfo, ...]
    main_feature: int

    @property
    def selected_title(self) -> TitleInfo:
        longest = max(self.titles, key=lambda item: item.duration_seconds)
        for title in self.titles:
            if title.index == self.main_feature:
                if title.duration_seconds >= longest.duration_seconds * 0.8:
                    return title
                break
        return longest


@dataclass(frozen=True)
class EncoderChoice:
    label: str
    handbrake_name: str
    hardware_decoder: str | None = None


ENCODERS = (
    EncoderChoice("NVIDIA NVENC", "nvenc_h264", "nvdec"),
    EncoderChoice("Apple VideoToolbox", "vt_h264"),
    EncoderChoice("CPU x264", "x264"),
)


@dataclass(frozen=True)
class ConversionOptions:
    source: Path
    destination: Path
    handbrake: Path
    tsmuxer: Path
    encoder: EncoderChoice
    target_gb: float = 25.0
    title: int | None = None
    audio_track: int | None = None
    audio_language: str | None = None
    burn_forced_subtitles: bool = False


ProgressCallback = Callable[[str, float, str], None]
LogCallback = Callable[[str], None]


def find_handbrake() -> Path | None:
    return _find_tool(
        ("HandBrakeCLI", "HandBrakeCLI.exe"),
        (
            Path("C:/Program Files/HandBrake/HandBrakeCLI.exe"),
            Path("/Applications/HandBrake.app/Contents/MacOS/HandBrakeCLI"),
            Path("/opt/homebrew/bin/HandBrakeCLI"),
            Path("/usr/local/bin/HandBrakeCLI"),
        ),
    )


def find_tsmuxer() -> Path | None:
    return _find_tool(
        ("tsMuxeR", "tsMuxeR.exe", "tsmuxer"),
        (
            Path("C:/Program Files/tsMuxeR/tsMuxeR.exe"),
            Path("C:/Program Files/tsMuxer/tsMuxeR.exe"),
            Path("/Applications/tsMuxerGUI.app/Contents/MacOS/tsMuxeR"),
            Path("/opt/homebrew/bin/tsMuxeR"),
            Path("/usr/local/bin/tsMuxeR"),
        ),
    )


def _find_tool(names: Iterable[str], candidates: Iterable[Path]) -> Path | None:
    for root in _runtime_roots():
        for name in names:
            candidate = root / "tools" / name
            if candidate.is_file():
                return candidate.resolve()
    for name in names:
        match = shutil.which(name)
        if match:
            return Path(match).resolve()
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _runtime_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots.append(Path(bundle_root))

    executable_root = Path(sys.executable).resolve().parent
    roots.extend((executable_root, executable_root / "_internal"))

    project_root = Path(__file__).resolve().parents[1]
    system_name = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(
        platform.system(), platform.system().lower()
    )
    roots.extend((project_root, project_root / "packaging" / "vendor" / system_name))
    return tuple(dict.fromkeys(roots))


def detect_encoders(handbrake: Path) -> tuple[EncoderChoice, ...]:
    try:
        result = subprocess.run(
            [str(handbrake), "--help"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
            **_subprocess_window_options(),
        )
    except (OSError, subprocess.SubprocessError):
        return ()

    help_text = result.stdout + result.stderr
    available = tuple(item for item in ENCODERS if item.handbrake_name in help_text)
    system = platform.system()
    preferred = "vt_h264" if system == "Darwin" else "nvenc_h264"
    return tuple(sorted(available, key=lambda item: item.handbrake_name != preferred))


def _parse_handbrake_title_set(output: str) -> dict:
    marker = "JSON Title Set:"
    marker_position = output.find(marker)
    if marker_position < 0:
        raise ConversionError("HandBrake did not return title information.")

    json_start = output.find("{", marker_position + len(marker))
    if json_start < 0:
        raise ConversionError("HandBrake returned an invalid title scan.")

    try:
        payload, _ = json.JSONDecoder().raw_decode(output[json_start:])
    except json.JSONDecodeError as exc:
        raise ConversionError("Could not parse HandBrake's title scan.") from exc

    if not isinstance(payload, dict):
        raise ConversionError("HandBrake returned an invalid title scan.")
    return payload


def parse_handbrake_scan(output: str) -> ScanResult:
    payload = _parse_handbrake_title_set(output)

    parsed_titles: list[TitleInfo] = []
    for title in payload.get("TitleList", []):
        duration = title.get("Duration", {})
        seconds = (
            float(duration.get("Hours", 0)) * 3600
            + float(duration.get("Minutes", 0)) * 60
            + float(duration.get("Seconds", 0))
        )
        index = int(title.get("Index", 0))
        if index > 0 and seconds > 0:
            audio_list = title.get("AudioList", []) or []
            audio_indices = tuple(
                int(audio.get("Track", audio.get("TrackNumber", position)))
                for position, audio in enumerate(audio_list, start=1)
            )
            audio_bitrate = sum(
                int(float(audio.get("Bitrate", 0) or 0))
                for audio in audio_list
                if float(audio.get("Bitrate", 0) or 0) > 0
            )
            audio_descriptions = tuple(_describe_audio_track(audio, position) for position, audio in enumerate(audio_list, start=1))
            audio_languages = tuple(
                str(audio.get("LanguageCode", "") or "").lower() for audio in audio_list
            )
            parsed_titles.append(
                TitleInfo(
                    index=index,
                    duration_seconds=seconds,
                    name=str(title.get("Name", "")),
                    audio_track_count=max(1, len(audio_list)),
                    audio_track_indices=audio_indices,
                    audio_bitrate_kbps=audio_bitrate,
                    audio_track_descriptions=audio_descriptions,
                    audio_track_languages=audio_languages,
                )
            )

    if not parsed_titles:
        raise ConversionError("No playable titles were found in the ISO.")

    main_feature = int(payload.get("MainFeature", 0) or 0)
    indexes = {title.index for title in parsed_titles}
    if main_feature not in indexes:
        main_feature = max(parsed_titles, key=lambda item: item.duration_seconds).index
    return ScanResult(tuple(parsed_titles), main_feature)


def _describe_audio_track(audio: dict, position: int) -> str:
    track = int(audio.get("Track", audio.get("TrackNumber", position)))
    language = str(audio.get("Language", "") or audio.get("LanguageCode", "") or "Unknown")
    codec = str(audio.get("CodecName", "Unknown"))
    channels = audio.get("ChannelLayout") or audio.get("ChannelCount")
    channel_text = f", {channels} channels" if channels else ""
    bitrate = audio.get("Bitrate")
    bitrate_text = f", {int(float(bitrate))} kb/s" if bitrate else ""
    return f"Track {track}: {language}, {codec}{channel_text}{bitrate_text}"


def parse_handbrake_audio_codecs(output: str) -> tuple[str, ...]:
    payload = _parse_handbrake_title_set(output)
    codecs: list[str] = []
    for title in payload.get("TitleList", []):
        for audio in title.get("AudioList", []) or []:
            codec = str(audio.get("CodecName", "")).strip().lower()
            if codec:
                codecs.append(codec)
    return tuple(codecs)


def calculate_video_bitrate_kbps(
    duration_seconds: float,
    target_gb: float,
    audio_bitrate_kbps: int = DEFAULT_AUDIO_BITRATE_KBPS,
) -> int:
    if duration_seconds <= 0:
        raise ValueError("Duration must be greater than zero.")
    if not 1 <= target_gb <= 25:
        raise ValueError("Target size must be between 1 and 25 GB.")

    # Keep room for AC-3 audio, M2TS packet overhead, Blu-ray metadata, and
    # hardware encoders that slightly overshoot their requested average rate.
    payload_bytes = target_gb * DECIMAL_GB * 0.94 - 100_000_000
    total_kbps = payload_bytes * 8 / duration_seconds / 1000
    video_kbps = int(total_kbps - audio_bitrate_kbps)
    return max(1_000, min(video_kbps, BLURAY_VIDEO_BITRATE_LIMIT_KBPS))


def build_handbrake_command(
    options: ConversionOptions,
    intermediate: Path,
    title: TitleInfo,
) -> list[str]:
    audio_tracks = title.audio_track_indices or tuple(range(1, max(1, title.audio_track_count) + 1))
    audio_bitrate = title.audio_bitrate_kbps or DEFAULT_AUDIO_BITRATE_KBPS * len(audio_tracks)
    bitrate = calculate_video_bitrate_kbps(
        title.duration_seconds,
        options.target_gb,
        audio_bitrate,
    )
    command = [
        str(options.handbrake),
        "--json",
        "-i",
        str(options.source),
        "-o",
        str(intermediate),
        "-t",
        str(title.index),
        "-f",
        "av_mkv",
        "-e",
        options.encoder.handbrake_name,
        "-b",
        str(bitrate),
        "--encoder-profile",
        "high",
        "--encoder-level",
        "4.1",
        "--cfr",
        "--crop-mode",
        "none",
        "--markers",
        "-a",
        ",".join(str(track) for track in audio_tracks),
        "-E",
        "copy",
    ]
    if options.encoder.hardware_decoder:
        command.extend(("--enable-hw-decoding", options.encoder.hardware_decoder))
    if options.encoder.handbrake_name == "x264":
        command.extend(("--multi-pass", "--turbo"))
    if options.burn_forced_subtitles:
        command.extend(("--subtitle", "scan", "--subtitle-forced", "--subtitle-burned"))
    else:
        command.extend(("--subtitle", "none"))
    return command


def parse_tsmuxer_track_set(output: str) -> tuple[int, tuple[tuple[int, str], ...]]:
    video_track = 0
    audio_tracks: list[tuple[int, str]] = []
    current_track = 0
    for line in output.splitlines():
        track_match = re.search(r"Track ID:\s*(\d+)", line, re.IGNORECASE)
        if track_match:
            current_track = int(track_match.group(1))
            continue
        stream_match = re.search(r"Stream ID:\s*([^\s]+)", line, re.IGNORECASE)
        stream_type = stream_match.group(1).upper() if stream_match else line.upper()
        if current_track and not video_track and (
            "V_MPEG4/ISO/AVC" in stream_type or "H.264" in stream_type
        ):
            video_track = current_track
        if current_track and stream_type.startswith("A_"):
            track = (current_track, stream_type)
            if track not in audio_tracks:
                audio_tracks.append(track)

    if not video_track:
        raise ConversionError("tsMuxeR did not detect the H.264 video track.")
    if not audio_tracks:
        raise ConversionError("tsMuxeR did not detect any audio tracks.")
    return video_track, tuple(audio_tracks)


def parse_tsmuxer_tracks(output: str) -> tuple[int, int]:
    """Return the first audio track for compatibility with existing callers."""
    video_track, audio_tracks = parse_tsmuxer_track_set(output)
    return video_track, audio_tracks[0][0]


def build_tsmuxer_meta(
    media: Path,
    video_track: int,
    audio_tracks: int | Iterable[int | tuple[int, str]],
    language: str | None = None,
) -> str:
    safe_path = str(media.resolve()).replace("\\", "/").replace('"', '\\"')
    if isinstance(audio_tracks, int):
        audio_tracks = (audio_tracks,)
    audio_lines = tuple(
        f'{codec}, "{safe_path}", track={track_id}'
        + (
            f", lang={language.lower()}"
            if language and re.fullmatch(r"[a-zA-Z]{3}", language)
            else ""
        )
        for track_id, codec in (
            track if isinstance(track, tuple) else (track, "A_AC3")
            for track in audio_tracks
        )
    )
    return "\n".join(
        (
            'MUXOPT --blu-ray --vbr --auto-chapters=5 --label="BD25"',
            f'V_MPEG4/ISO/AVC, "{safe_path}", track={video_track}, insertSEI, contSPS',
            *audio_lines,
            "",
        )
    )


class Converter:
    def __init__(
        self,
        progress: ProgressCallback | None = None,
        log: LogCallback | None = None,
    ) -> None:
        self._progress = progress or (lambda stage, fraction, message: None)
        self._log = log or (lambda line: None)
        self._cancel = threading.Event()
        self._process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        self._cancel.set()
        process = self._process
        if process and process.poll() is None:
            process.terminate()

    def scan(self, handbrake: Path, source: Path) -> ScanResult:
        self._check_cancelled()
        self._progress("Scanning", 0.01, "Reading Blu-ray titles")
        output = self._run_capture(
            [str(handbrake), "--json", "-i", str(source), "-t", "0", "--scan"],
            echo=False,
        )
        result = parse_handbrake_scan(output)
        titles = ", ".join(
            f"{title.index}={_format_duration(title.duration_seconds)}" for title in result.titles
        )
        self._log(f"Detected titles: {titles}")
        if result.selected_title.index != result.main_feature:
            self._log(
                f"HandBrake marked title {result.main_feature} as the main feature, but it was "
                f"much shorter than title {result.selected_title.index}; using the longer title."
            )
        self._progress("Scanning", 0.05, f"Selected title {result.selected_title.index}")
        return result

    def convert(self, options: ConversionOptions) -> Path:
        self._validate(options)
        work_dir: Path | None = None
        succeeded = False
        try:
            scan_result = self.scan(options.handbrake, options.source)
            title = self._select_title(scan_result, options.title)
            if options.audio_track is not None:
                available_audio = title.audio_track_indices or tuple(
                    range(1, max(1, title.audio_track_count) + 1)
                )
                if options.audio_track not in available_audio:
                    raise ConversionError(
                        f"Audio track {options.audio_track} was not found in title {title.index}."
                    )
                selected_position = available_audio.index(options.audio_track)
                selected_language = options.audio_language or (
                    title.audio_track_languages[selected_position]
                    if selected_position < len(title.audio_track_languages)
                    else ""
                )
                title = replace(
                    title,
                    audio_track_count=1,
                    audio_track_indices=(options.audio_track,),
                    audio_bitrate_kbps=(
                        title.audio_bitrate_kbps // max(1, title.audio_track_count)
                    ),
                    audio_track_languages=(selected_language,),
                )
            bitrate = calculate_video_bitrate_kbps(
                title.duration_seconds,
                options.target_gb,
                title.audio_bitrate_kbps
                or DEFAULT_AUDIO_BITRATE_KBPS * max(1, title.audio_track_count),
            )
            self._log(
                f"Title {title.index}: {_format_duration(title.duration_seconds)}; "
                f"target video bitrate {bitrate:,} kb/s"
            )

            work_dir = Path(
                tempfile.mkdtemp(
                    prefix=f".{options.destination.stem}-work-",
                    dir=options.destination.parent,
                )
            )
            intermediate = work_dir / "encoded.mkv"
            command = build_handbrake_command(options, intermediate, title)
            self._progress("Encoding", 0.05, f"Encoding with {options.encoder.label}")
            self._run_stream(command, self._parse_handbrake_progress)
            self._check_cancelled()
            if not intermediate.is_file() or intermediate.stat().st_size == 0:
                raise ConversionError("HandBrake finished without creating the encoded movie.")
            expected_bytes = (
                (
                    bitrate
                    + (
                        title.audio_bitrate_kbps
                        or DEFAULT_AUDIO_BITRATE_KBPS * max(1, title.audio_track_count)
                    )
                )
                * title.duration_seconds
                * 1000
                / 8
            )
            if intermediate.stat().st_size < expected_bytes * 0.5:
                raise ConversionError(
                    "The encoded movie is implausibly small for the selected title. "
                    "Intermediate files were retained; check the activity log and choose the title manually."
                )

            track_output = self._run_capture([str(options.tsmuxer), str(intermediate)], echo=True)
            video_track, audio_tracks = parse_tsmuxer_track_set(track_output)
            meta = work_dir / "disc.meta"
            meta.write_text(
                build_tsmuxer_meta(
                    intermediate,
                    video_track,
                    audio_tracks,
                    title.audio_track_languages[0]
                    if title.audio_track_languages
                    else None,
                ),
                encoding="utf-8",
            )

            self._progress("Authoring", 0.90, "Building the Blu-ray ISO")
            self._run_stream(
                [str(options.tsmuxer), str(meta), str(options.destination)],
                self._parse_tsmuxer_progress,
            )
            self._check_cancelled()
            if not options.destination.is_file() or options.destination.stat().st_size == 0:
                raise ConversionError("tsMuxeR finished without creating an ISO.")
            if options.destination.stat().st_size < intermediate.stat().st_size * 0.95:
                raise ConversionError(
                    "The authored ISO is much smaller than the encoded movie. "
                    "The incomplete ISO was removed."
                )
            if options.destination.stat().st_size > options.target_gb * DECIMAL_GB:
                raise ConversionError(
                    "The authored ISO exceeded the requested size. The partial ISO was removed."
                )

            self._progress("Verifying", 0.99, "Checking authored Blu-ray audio")
            verify_output = self._run_capture(
                [
                    str(options.handbrake),
                    "--json",
                    "-i",
                    str(options.destination),
                    "-t",
                    "0",
                    "--scan",
                ],
                echo=False,
            )
            audio_codecs = parse_handbrake_audio_codecs(verify_output)
            if len(audio_codecs) < max(1, title.audio_track_count):
                detected = ", ".join(audio_codecs) if audio_codecs else "none"
                raise ConversionError(
                    "The authored Blu-ray ISO does not expose all copied audio tracks "
                    f"(detected: {detected}). The ISO was removed."
                )
            self._log(f"Verified authored ISO audio: {', '.join(audio_codecs)}")

            succeeded = True
            size_gb = options.destination.stat().st_size / DECIMAL_GB
            self._progress("Complete", 1.0, f"Finished: {size_gb:.2f} GB")
            self._log(f"Created {options.destination} ({size_gb:.2f} GB)")
            return options.destination
        except BaseException as exc:
            if options.destination.exists():
                try:
                    options.destination.unlink()
                except OSError:
                    pass
            if work_dir and work_dir.exists() and not isinstance(exc, ConversionCancelled):
                self._log(f"Intermediate files retained in {work_dir}")
            raise
        finally:
            self._process = None
            if work_dir and succeeded:
                shutil.rmtree(work_dir, ignore_errors=True)
            elif work_dir and self._cancel.is_set():
                shutil.rmtree(work_dir, ignore_errors=True)

    def _validate(self, options: ConversionOptions) -> None:
        if not options.source.is_file():
            raise ConversionError("Choose an existing source ISO.")
        if options.source.suffix.lower() != ".iso":
            raise ConversionError("The source must be a .iso file.")
        if options.destination.suffix.lower() != ".iso":
            raise ConversionError("The destination must end in .iso.")
        if options.source.resolve() == options.destination.resolve():
            raise ConversionError("Source and destination must be different files.")
        if options.destination.exists():
            raise ConversionError("The destination already exists.")
        if not options.destination.parent.is_dir():
            raise ConversionError("The destination folder does not exist.")
        if not options.handbrake.is_file():
            raise ConversionError("The integrated HandBrake runtime is missing or damaged.")
        if not options.tsmuxer.is_file():
            raise ConversionError("The integrated Blu-ray authoring runtime is missing or damaged.")
        try:
            calculate_video_bitrate_kbps(1, options.target_gb)
        except ValueError as exc:
            raise ConversionError(str(exc)) from exc
        required_space = int(options.target_gb * DECIMAL_GB * 1.95)
        if shutil.disk_usage(options.destination.parent).free < required_space:
            required_gb = required_space / DECIMAL_GB
            raise ConversionError(
                f"The destination drive needs about {required_gb:.1f} GB free for the ISO and working file."
            )

    @staticmethod
    def _select_title(scan: ScanResult, requested: int | None) -> TitleInfo:
        if requested is None:
            return scan.selected_title
        for title in scan.titles:
            if title.index == requested:
                return title
        raise ConversionError(f"Title {requested} was not found in the source ISO.")

    def _run_capture(self, command: list[str], echo: bool) -> str:
        lines: list[str] = []

        def collect(line: str) -> None:
            lines.append(line)
            if echo:
                self._log(line.rstrip())

        self._run_stream(command, collect)
        return "".join(lines)

    def _run_stream(self, command: list[str], line_handler: Callable[[str], None]) -> None:
        self._check_cancelled()
        self._log(f"> {_display_command(command)}")
        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                bufsize=1,
                **_subprocess_window_options(),
            )
        except OSError as exc:
            raise ConversionError(f"Could not start {Path(command[0]).name}: {exc}") from exc

        assert self._process.stdout is not None
        for line in self._process.stdout:
            if self._cancel.is_set():
                self.cancel()
                break
            line_handler(line)
        return_code = self._process.wait()
        if self._cancel.is_set():
            raise ConversionCancelled("Conversion cancelled.")
        if return_code != 0:
            raise ConversionError(f"{Path(command[0]).name} exited with code {return_code}.")

    def _parse_handbrake_progress(self, line: str) -> None:
        self._log(line.rstrip())
        match = re.search(r'"PercentComplete"\s*:\s*([0-9.]+)', line)
        if not match:
            return
        percent = float(match.group(1))
        if percent <= 1:
            percent *= 100
        fraction = 0.05 + min(percent, 100) / 100 * 0.84
        self._progress("Encoding", fraction, f"Encoding {percent:.1f}%")

    def _parse_tsmuxer_progress(self, line: str) -> None:
        self._log(line.rstrip())
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)%", line)
        if match:
            percent = min(float(match.group(1)), 100)
            self._progress(
                "Authoring",
                0.90 + percent / 100 * 0.09,
                f"Authoring {percent:.1f}%",
            )

    def _check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise ConversionCancelled("Conversion cancelled.")


def _format_duration(seconds: float) -> str:
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _display_command(command: list[str]) -> str:
    return " ".join(f'"{item}"' if " " in item else item for item in command)


def _subprocess_window_options() -> dict[str, int]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
