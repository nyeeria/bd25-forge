import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bd25.core as core
from bd25.core import (
    BLURAY_VIDEO_BITRATE_LIMIT_KBPS,
    ConversionOptions,
    ENCODERS,
    TitleInfo,
    build_handbrake_command,
    build_tsmuxer_meta,
    calculate_video_bitrate_kbps,
    parse_handbrake_scan,
    parse_tsmuxer_tracks,
)


class ScanParsingTests(unittest.TestCase):
    def test_uses_reported_main_feature(self) -> None:
        payload = {
            "MainFeature": 2,
            "TitleList": [
                {"Index": 1, "Duration": {"Hours": 0, "Minutes": 12, "Seconds": 5}},
                {"Index": 2, "Name": "Feature", "Duration": {"Hours": 2, "Minutes": 3, "Seconds": 4}},
            ],
        }
        result = parse_handbrake_scan("log line\nJSON Title Set:\n" + json.dumps(payload) + "\nmore log")

        self.assertEqual(result.main_feature, 2)
        self.assertEqual(result.selected_title.duration_seconds, 7384)

    def test_falls_back_to_longest_title(self) -> None:
        payload = {
            "MainFeature": 99,
            "TitleList": [
                {"Index": 3, "Duration": {"Minutes": 40}},
                {"Index": 4, "Duration": {"Hours": 1, "Minutes": 20}},
            ],
        }
        result = parse_handbrake_scan("JSON Title Set: " + json.dumps(payload))

        self.assertEqual(result.main_feature, 4)

    def test_rejects_implausibly_short_reported_main_feature(self) -> None:
        payload = {
            "MainFeature": 1,
            "TitleList": [
                {"Index": 1, "Duration": {"Minutes": 2}},
                {"Index": 2, "Duration": {"Hours": 2}},
            ],
        }
        result = parse_handbrake_scan("JSON Title Set: " + json.dumps(payload))

        self.assertEqual(result.main_feature, 1)
        self.assertEqual(result.selected_title.index, 2)


class BitrateTests(unittest.TestCase):
    def test_two_hour_movie_uses_safe_target_rate(self) -> None:
        bitrate = calculate_video_bitrate_kbps(7200, 25.0)

        self.assertGreater(bitrate, 20_000)
        self.assertLess(bitrate, 27_000)

    def test_short_movie_obeys_bluray_limit(self) -> None:
        self.assertEqual(
            calculate_video_bitrate_kbps(600, 25.0),
            BLURAY_VIDEO_BITRATE_LIMIT_KBPS,
        )

    def test_rejects_oversized_target(self) -> None:
        with self.assertRaises(ValueError):
            calculate_video_bitrate_kbps(7200, 25.1)


class CommandTests(unittest.TestCase):
    def test_nvenc_command_enables_nvdec(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            options = ConversionOptions(
                source=base / "source.iso",
                destination=base / "output.iso",
                handbrake=base / "HandBrakeCLI",
                tsmuxer=base / "tsMuxeR",
                encoder=ENCODERS[0],
            )
            command = build_handbrake_command(
                options,
                base / "encoded.mkv",
                TitleInfo(index=7, duration_seconds=7200),
            )

        self.assertIn("nvenc_h264", command)
        self.assertEqual(command[command.index("--enable-hw-decoding") + 1], "nvdec")
        self.assertEqual(command[command.index("-t") + 1], "7")

    def test_cpu_command_uses_multi_pass(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            options = ConversionOptions(
                source=base / "source.iso",
                destination=base / "output.iso",
                handbrake=base / "HandBrakeCLI",
                tsmuxer=base / "tsMuxeR",
                encoder=ENCODERS[-1],
            )
            command = build_handbrake_command(
                options,
                base / "encoded.mkv",
                TitleInfo(index=1, duration_seconds=7200),
            )

        self.assertIn("--multi-pass", command)
        self.assertNotIn("--enable-hw-decoding", command)


class BundledRuntimeTests(unittest.TestCase):
    def test_bundled_tools_are_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tools = root / "tools"
            tools.mkdir()
            handbrake = tools / "HandBrakeCLI.exe"
            tsmuxer = tools / "tsMuxeR.exe"
            handbrake.touch()
            tsmuxer.touch()

            with patch.object(core, "_runtime_roots", return_value=(root,)):
                self.assertEqual(core.find_handbrake(), handbrake.resolve())
                self.assertEqual(core.find_tsmuxer(), tsmuxer.resolve())


class MuxMetadataTests(unittest.TestCase):
    def test_detects_track_ids(self) -> None:
        output = """Track ID: 4
Stream type: H.264
Stream ID: V_MPEG4/ISO/AVC
Track ID: 9
Stream type: AC3
Stream ID: A_AC3
"""
        self.assertEqual(parse_tsmuxer_tracks(output), (4, 9))

    def test_builds_bluray_iso_metadata(self) -> None:
        meta = build_tsmuxer_meta(Path("movie.mkv"), 4, 9, "deu")

        self.assertIn("MUXOPT --blu-ray", meta)
        self.assertIn("track=4", meta)
        self.assertIn("track=9, lang=deu", meta)


if __name__ == "__main__":
    unittest.main()
