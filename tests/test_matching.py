import unittest

from apple_music_migrator.matching import score


class MatchingTests(unittest.TestCase):
    def test_japanese_romanization(self) -> None:
        self.assertGreater(score("朝凪", "あたらよ", "Asanagi", "Atarayo"), 0.9)

    def test_chinese_pinyin(self) -> None:
        self.assertGreater(score("白日", "King Gnu", "Hakujitsu", "King Gnu"), 0.9)

    def test_rejects_unrelated_candidate(self) -> None:
        self.assertLess(score("Wanna Stay", "KKI", "Sweet Sorrow", "K!-Ki"), 0.58)


if __name__ == "__main__":
    unittest.main()
