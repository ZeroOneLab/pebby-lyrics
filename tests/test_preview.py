# -*- coding: utf-8 -*-
"""预览文件命名与落盘，不访问网络。"""

import tempfile
import unittest
from pathlib import Path

import prepare_lrc as core


class PreviewTarget(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_name_is_plain(self):
        self.assertEqual(core.preview_target(self.dir, "晴天.lrc"), self.dir / "晴天.lrc")

    def test_collision_gets_counter(self):
        (self.dir / "晴天.lrc").write_text("x", encoding="utf-8")
        self.assertEqual(core.preview_target(self.dir, "晴天.lrc"), self.dir / "晴天 (2).lrc")
        (self.dir / "晴天 (2).lrc").write_text("x", encoding="utf-8")
        self.assertEqual(core.preview_target(self.dir, "晴天.lrc"), self.dir / "晴天 (3).lrc")

    def test_accepts_string_directory(self):
        self.assertEqual(core.preview_target(str(self.dir), "a.lrc"), self.dir / "a.lrc")


if __name__ == "__main__":
    unittest.main()
