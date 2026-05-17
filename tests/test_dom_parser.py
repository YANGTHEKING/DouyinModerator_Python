import unittest

from douyin_mod_manager.sources.dom_parser import normalize_dom_record, parse_gift_line, split_chat_line


class DomParserTest(unittest.TestCase):
    def test_splits_colon_chat_line(self) -> None:
        self.assertEqual(split_chat_line("大叔：你好"), ("大叔", "你好"))
        self.assertEqual(split_chat_line("Alice: hello"), ("Alice", "hello"))
        self.assertEqual(split_chat_line("九宝\n胡福🧧：就我个人觉得大家向前走"), ("胡福🧧", "就我个人觉得大家向前走"))

    def test_normalizes_plain_text_chat_record(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "chat",
                "username": "",
                "content": "大叔：你好",
                "raw": {"text": "大叔：你好"},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.username, "大叔")
        self.assertEqual(parsed.content, "你好")

    def test_keeps_structured_username_and_content(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "chat",
                "username": "大叔",
                "content": "你好",
                "raw": {"text": "大叔 你好"},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.username, "大叔")
        self.assertEqual(parsed.content, "你好")

    def test_keeps_structured_username_with_colon(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "chat",
                "username": "阿:澈",
                "content": "晚安",
                "raw": {"text": "阿:澈：晚安", "parseMethod": "interactive"},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.username, "阿:澈")
        self.assertEqual(parsed.content, "晚安")

    def test_parses_plain_text_gift_line(self) -> None:
        self.assertEqual(parse_gift_line("大叔 送出【啤酒】x3"), ("大叔", "啤酒", 3))
        self.assertEqual(parse_gift_line("大叔送了[小心心]"), ("大叔", "小心心", 1))

    def test_normalizes_gift_record_without_structured_username(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "gift",
                "username": "",
                "content": "大叔 送出【啤酒】x3",
                "raw": {"text": "大叔 送出【啤酒】x3"},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.username, "大叔")
        self.assertEqual(parsed.content, "送出 啤酒 x3")
        self.assertEqual(parsed.raw["gift_name"], "啤酒")
        self.assertEqual(parsed.raw["gift_count"], 3)

    def test_uses_media_label_as_gift_name(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "gift",
                "username": "大叔",
                "content": "送出 x1",
                "raw": {"text": "大叔 送出 x1", "mediaLabels": ["【啤酒】"]},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.username, "大叔")
        self.assertEqual(parsed.content, "送出 啤酒 x1")
        self.assertEqual(parsed.raw["gift_name"], "啤酒")

    def test_parses_icon_only_gift_with_badge_line(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "gift",
                "username": "",
                "content": "九宝\n张晓ᴸᴶ ₛ：",
                "raw": {"text": "九宝\n张晓ᴸᴶ ₛ：", "mediaLabels": ["[抱抱你]"]},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.username, "张晓ᴸᴶ ₛ")
        self.assertEqual(parsed.content, "送出 抱抱你 x1")
        self.assertEqual(parsed.raw["gift_name"], "抱抱你")

    def test_keeps_icon_only_chat_as_emote_text(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "chat",
                "username": "",
                "content": "九宝\n张晓ᴸᴶ ₛ：",
                "raw": {"text": "九宝\n张晓ᴸᴶ ₛ：", "mediaLabels": ["[抱抱你]"]},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.username, "张晓ᴸᴶ ₛ")
        self.assertEqual(parsed.content, "[抱抱你]")

    def test_parses_user_enter_name(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "user_enter",
                "username": "",
                "content": "扶苏ᴶ🍓ₛ 来了",
                "raw": {"text": "扶苏ᴶ🍓ₛ 来了"},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.username, "扶苏ᴶ🍓ₛ")
        self.assertEqual(parsed.content, "进入直播间")

    def test_parses_system_action_name(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "system",
                "username": "",
                "content": "㿝氼223ᴶ ₛ 推荐直播给Ta的朋友",
                "raw": {"text": "㿝氼223ᴶ ₛ 推荐直播给Ta的朋友"},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.username, "㿝氼223ᴶ ₛ")
        self.assertEqual(parsed.content, "推荐直播给Ta的朋友")

    def test_parses_share_system_action_name(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "system",
                "username": "",
                "content": "袁 推荐了直播",
                "raw": {"text": "袁 推荐了直播"},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.username, "袁")
        self.assertEqual(parsed.content, "推荐了直播")

    def test_recovers_share_action_misclassified_as_chat(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "chat",
                "username": "",
                "content": "凤儿 推荐了直播",
                "raw": {"text": "凤儿 推荐了直播"},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.type, "system")
        self.assertEqual(parsed.username, "凤儿")
        self.assertEqual(parsed.content, "推荐了直播")

    def test_recovers_chat_misclassified_as_follow(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "follow",
                "username": "",
                "content": "九宝\n桃了个兔🐰 ᴶ ₛ：哥姐们，喜欢九叔的话点点关注",
                "raw": {"text": "九宝\n桃了个兔🐰 ᴶ ₛ：哥姐们，喜欢九叔的话点点关注"},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.type, "chat")
        self.assertEqual(parsed.username, "桃了个兔🐰 ᴶ ₛ")
        self.assertEqual(parsed.content, "哥姐们，喜欢九叔的话点点关注")

    def test_parses_follow_action_name(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "follow",
                "username": "",
                "content": "桃了个兔🐰 ᴶ ₛ 关注了主播",
                "raw": {"text": "桃了个兔🐰 ᴶ ₛ 关注了主播"},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.type, "follow")
        self.assertEqual(parsed.username, "桃了个兔🐰 ᴶ ₛ")
        self.assertEqual(parsed.content, "关注了主播")

    def test_recovers_chat_misclassified_as_user_enter(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "user_enter",
                "username": "",
                "content": "人 鱼：来了来了呀~",
                "raw": {"text": "人 鱼：来了来了呀~"},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.type, "chat")
        self.assertEqual(parsed.username, "人 鱼")
        self.assertEqual(parsed.content, "来了来了呀~")

    def test_extracts_username_from_empty_chat_line(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "chat",
                "username": "",
                "content": "畅月宝🍓：",
                "raw": {"text": "畅月宝🍓："},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.username, "畅月宝🍓")
        self.assertEqual(parsed.content, "畅月宝🍓：")

    def test_recovers_chat_misclassified_as_gift_when_text_mentions_gifts(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "gift",
                "username": "",
                "content": "九宝\n桃了个兔🐰 ᴶ ₛ：感谢姐宝们的礼物和陪伴",
                "raw": {"text": "九宝\n桃了个兔🐰 ᴶ ₛ：感谢姐宝们的礼物和陪伴", "mediaLabels": ["[比心]"]},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.type, "chat")
        self.assertEqual(parsed.username, "桃了个兔🐰 ᴶ ₛ")
        self.assertEqual(parsed.content, "感谢姐宝们的礼物和陪伴")
        self.assertNotIn("gift_name", parsed.raw)

    def test_structured_gift_action_without_visible_gift_name(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "gift",
                "username": "羽*****：",
                "content": "送出了 × 5",
                "raw": {"text": "羽*****：送出了 × 5", "parseMethod": "interactive", "mediaLabels": []},
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.type, "gift")
        self.assertEqual(parsed.username, "羽*****")
        self.assertEqual(parsed.raw["gift_count"], 5)
        self.assertNotIn("gift_name", parsed.raw)

    def test_uses_known_gift_image_url_as_gift_name(self) -> None:
        parsed = normalize_dom_record(
            {
                "type": "gift",
                "username": "杨*****",
                "content": "送出了 × 1",
                "raw": {
                    "text": "杨*****：送出了 × 1",
                    "mediaLabels": [],
                    "childSummaries": [
                        {"tag": "IMG", "src": "https://p3-webcast.douyinpic.com/img/webcast/7ef47758a435313180e6b78b056dda4e.png~tplv-obj.png"}
                    ],
                },
            }
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.raw["gift_name"], "小心心")
        self.assertEqual(parsed.content, "送出 小心心 x1")


if __name__ == "__main__":
    unittest.main()
