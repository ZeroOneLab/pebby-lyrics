# -*- coding: utf-8 -*-
"""文件名格式与清洗规则，不读写真实音频。"""

import unittest
from pathlib import Path

import prepare_lrc as core


def make_track(title, artist="歌手", number=0):
    return core.Track(Path("x.mp3"), title, artist, "", 180.0, "tags", number)


class SafeComponent(unittest.TestCase):
    def test_replaces_invalid_chars(self):
        self.assertEqual(core.safe_component('a<b>c:d"e/f\\g|h?i*j'),
                         "a_b_c_d_e_f_g_h_i_j")

    def test_trims_spaces_and_dots(self):
        self.assertEqual(core.safe_component("  歌名 . "), "歌名")

    def test_empty_input(self):
        self.assertEqual(core.safe_component(""), "")
        self.assertEqual(core.safe_component(None), "")

    def test_length_limit(self):
        self.assertEqual(len(core.safe_component("字" * 200)), 80)


class OutputPath(unittest.TestCase):
    def setUp(self):
        self.audio = Path("/music") / "01.mp3"

    def test_same_ignores_tags(self):
        expected = Path("/music") / "01.lrc"
        self.assertEqual(core.output_path(self.audio, make_track("晴天"), "same"), expected)

    def test_unknown_format_falls_back_to_same(self):
        expected = Path("/music") / "01.lrc"
        self.assertEqual(core.output_path(self.audio, make_track("晴天"), "bogus"), expected)

    def test_title(self):
        self.assertEqual(core.output_path(self.audio, make_track("晴天"), "title"),
                         Path("/music") / "晴天.lrc")

    def test_title_artist(self):
        self.assertEqual(core.output_path(self.audio, make_track("晴天", "周杰伦"), "title-artist"),
                         Path("/music") / "晴天 - 周杰伦.lrc")

    def test_artist_title(self):
        self.assertEqual(core.output_path(self.audio, make_track("晴天", "周杰伦"), "artist-title"),
                         Path("/music") / "周杰伦 - 晴天.lrc")

    def test_track_title(self):
        self.assertEqual(core.output_path(self.audio, make_track("晴天", number=3), "track-title"),
                         Path("/music") / "03 晴天.lrc")

    def test_track_title_without_number(self):
        self.assertEqual(core.output_path(self.audio, make_track("晴天"), "track-title"),
                         Path("/music") / "晴天.lrc")

    def test_empty_title_falls_back_to_audio_stem(self):
        self.assertEqual(core.output_path(self.audio, make_track(""), "title"),
                         Path("/music") / "01.lrc")

    def test_invalid_tag_chars_are_sanitized(self):
        self.assertEqual(core.output_path(self.audio, make_track("A/B:C"), "title"),
                         Path("/music") / "A_B_C.lrc")


class TrackNumber(unittest.TestCase):
    def test_parse_variants(self):
        self.assertEqual(core.track_number("3/12"), 3)
        self.assertEqual(core.track_number("07"), 7)
        self.assertEqual(core.track_number(""), 0)
        self.assertEqual(core.track_number(None), 0)
        self.assertEqual(core.track_number("A1"), 0)


if __name__ == "__main__":
    unittest.main()
