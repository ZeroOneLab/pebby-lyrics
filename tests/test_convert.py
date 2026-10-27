# -*- coding: utf-8 -*-
"""强制简繁转换：两个方向都要能走通。"""

import unittest

import prepare_lrc as core


class ConvertText(unittest.TestCase):
    def test_to_simplified(self):
        self.assertEqual(core.convert_text("漢字測試", "simplified"), "汉字测试")

    def test_to_traditional(self):
        self.assertEqual(core.convert_text("汉字测试", "traditional"), "漢字測試")

    def test_default_is_simplified(self):
        self.assertEqual(core.convert_text("漢字"), "汉字")

    def test_unknown_form_falls_back_to_simplified(self):
        self.assertEqual(core.convert_text("漢字", "bogus"), "汉字")

    def test_already_target_form_is_unchanged(self):
        self.assertEqual(core.convert_text("汉字测试", "simplified"), "汉字测试")
        self.assertEqual(core.convert_text("漢字測試", "traditional"), "漢字測試")


if __name__ == "__main__":
    unittest.main()
