from __future__ import annotations

import unittest

from whisper_workflow.youtube import (
    is_online_video_url,
    is_youtube_url,
    platform_for_url,
    safe_output_name,
    safe_submission_date,
    safe_title_component,
)


class YouTubeTests(unittest.TestCase):
    def test_accepts_supported_youtube_links(self) -> None:
        self.assertTrue(is_youtube_url("https://www.youtube.com/watch?v=abc123"))
        self.assertTrue(is_youtube_url("https://youtu.be/abc123"))
        self.assertTrue(is_youtube_url("https://music.youtube.com/watch?v=abc123"))

    def test_rejects_non_youtube_and_spoofed_links(self) -> None:
        self.assertFalse(is_youtube_url("https://example.com/video"))
        self.assertFalse(is_youtube_url("https://youtube.com.example.com/watch?v=abc123"))
        self.assertFalse(is_youtube_url("not a URL"))

    def test_accepts_bilibili_links_without_mislabeling_them_as_youtube(self) -> None:
        for value in (
            "https://www.bilibili.com/video/BV1234567890",
            "https://m.bilibili.com/video/BV1234567890",
            "https://b23.tv/abc123",
        ):
            self.assertTrue(is_online_video_url(value))
            self.assertFalse(is_youtube_url(value))
            self.assertEqual(platform_for_url(value), "Bilibili")

    def test_rejects_spoofed_bilibili_links(self) -> None:
        self.assertFalse(is_online_video_url("https://bilibili.com.example.com/video/BV1"))

    def test_builds_a_dated_unicode_safe_output_name(self) -> None:
        self.assertEqual(
            safe_output_name("中文 / English: Demo 😀", "ab-c_123", "20260909"),
            "20260909_中文_English_Demo",
        )

    def test_short_or_unsupported_titles_use_a_supported_fallback(self) -> None:
        self.assertEqual(safe_title_component("😀 / :"), "video")
        self.assertEqual(safe_title_component("W"), "video")

    def test_long_multibyte_titles_are_truncated_on_character_boundaries(self) -> None:
        value = safe_title_component("中" * 100, maximum_bytes=20)
        self.assertLessEqual(len(value.encode("utf-8")), 20)
        self.assertEqual(value, "中" * 6)

    def test_submission_date_validation(self) -> None:
        self.assertEqual(safe_submission_date("20260228"), "20260228")
        with self.assertRaises(ValueError):
            safe_submission_date("20260230")


if __name__ == "__main__":
    unittest.main()
