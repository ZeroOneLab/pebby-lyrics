# -*- coding: utf-8 -*-
"""标题清洗与文件名回退：标签里带曲目号、或标签写错时仍能找到歌。"""

import unittest
from pathlib import Path

import prepare_lrc as core


def make_track(title, artist="萧亚轩"):
    return core.Track(Path("x.mp3"), title, artist, "", 200.0, "tags", 0)


class CleanTitle(unittest.TestCase):
    def test_strips_track_number_prefix(self):
        self.assertEqual(core.clean_title("06.心雨"), "心雨")
        self.assertEqual(core.clean_title("01 - 晴天"), "晴天")
        self.assertEqual(core.clean_title("03、七里香"), "七里香")
        self.assertEqual(core.clean_title("12_稻香"), "稻香")

    def test_keeps_titles_that_start_with_digits(self):
        self.assertEqual(core.clean_title("7 Rings"), "7 Rings")
        self.assertEqual(core.clean_title("2002年的第一场雪"), "2002年的第一场雪")
        self.assertEqual(core.clean_title("1984"), "1984")

    def test_empty_and_plain(self):
        self.assertEqual(core.clean_title(""), "")
        self.assertEqual(core.clean_title("心雨"), "心雨")


class FilenameTitle(unittest.TestCase):
    def test_track_number(self):
        self.assertEqual(core.filename_title(Path("06.心雨.mp3")), "心雨")

    def test_artist_dash_title(self):
        self.assertEqual(core.filename_title(Path("萧亚轩 - 最熟悉的陌生人.mp3")),
                         "最熟悉的陌生人")

    def test_plain_name(self):
        self.assertEqual(core.filename_title(Path("最熟悉的陌生人.mp3")), "最熟悉的陌生人")


class FallbackTitle(unittest.TestCase):
    def test_returns_filename_when_tag_differs(self):
        track = make_track("Track 01")
        self.assertEqual(core.fallback_search_title(Path("06.心雨.mp3"), track), "心雨")

    def test_empty_when_same_after_cleaning(self):
        track = make_track("06.心雨")
        self.assertEqual(core.fallback_search_title(Path("心雨.mp3"), track), "")

    def test_empty_when_tag_matches_filename(self):
        track = make_track("心雨")
        self.assertEqual(core.fallback_search_title(Path("心雨.mp3"), track), "")


if __name__ == "__main__":
    unittest.main()
