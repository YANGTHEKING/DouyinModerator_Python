import tempfile
import unittest
from pathlib import Path

from douyin_mod_manager.sources.gift_images import GiftImageRegistry, gift_image_key, gift_image_urls_from_raw


class GiftImageRegistryTest(unittest.TestCase):
    def test_extracts_gift_image_key_from_douyin_url(self) -> None:
        url = "https://p3-webcast.douyinpic.com/img/webcast/7ef47758a435313180e6b78b056dda4e.png~tplv-obj.png"

        self.assertEqual(gift_image_key(url), "7ef47758a435313180e6b78b056dda4e")

    def test_extracts_gift_urls_from_child_summaries(self) -> None:
        raw = {
            "childSummaries": [
                {"src": "https://p3-webcast.douyinpic.com/img/webcast/abc123abc123abc123abc123abc123.png"},
                {"backgroundImage": 'url("https://p3-webcast.douyinpic.com/img/webcast/def456def456def456def456def456.webp")'},
                {"src": "https://p3-webcast.douyinpic.com/img/webcast/new_user_grade_v1.png"},
            ]
        }

        urls = gift_image_urls_from_raw(raw)

        self.assertEqual(len(urls), 2)
        self.assertIn("abc123abc123abc123abc123abc123.png", urls[0])
        self.assertIn("def456def456def456def456def456.webp", urls[1])

    def test_records_unknown_and_persists_manual_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gift_image_mappings.json"
            registry = GiftImageRegistry(path)
            raw = {
                "childSummaries": [
                    {"src": "https://p3-webcast.douyinpic.com/img/webcast/abc123abc123abc123abc123abc123.png"}
                ]
            }

            self.assertTrue(registry.record_raw(raw))
            entry = registry.entries["abc123abc123abc123abc123abc123"]
            self.assertEqual(entry.status, "未知")
            self.assertEqual(entry.seen_count, 1)
            entry.diamond_count = 9

            registry.set_name(entry.image_key, "测试礼物")
            reloaded = GiftImageRegistry(path)

            self.assertEqual(reloaded.get_name(entry.image_key), "测试礼物")
            self.assertEqual(reloaded.entries[entry.image_key].diamond_count, 9)


if __name__ == "__main__":
    unittest.main()
