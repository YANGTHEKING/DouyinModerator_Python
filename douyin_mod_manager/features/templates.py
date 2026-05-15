from __future__ import annotations

from dataclasses import dataclass

from douyin_mod_manager.core.events import LiveMode


@dataclass(frozen=True, slots=True)
class ReplyTemplate:
    group: str
    label: str
    text: str
    modes: set[LiveMode]


DEFAULT_TEMPLATES = [
    ReplyTemplate("通用", "欢迎", "欢迎 {username} 来直播间。", set(LiveMode)),
    ReplyTemplate("歌回", "点歌格式", "点歌请发送：点歌 歌名", {LiveMode.SINGING}),
    ReplyTemplate("歌回", "当前歌", "当前歌曲：{current_song}", {LiveMode.SINGING}),
    ReplyTemplate("歌回", "暂停点歌", "当前暂不接点歌，感谢理解。", {LiveMode.SINGING}),
    ReplyTemplate("杂谈", "温和控场", "大家慢慢聊，不要刷屏喔。", {LiveMode.TALK}),
    ReplyTemplate("PK", "应援", "PK 中理性应援，文明观看。", {LiveMode.PK}),
    ReplyTemplate("风险处理", "文明提醒", "请大家文明交流，专注直播内容。", set(LiveMode)),
    ReplyTemplate("风险处理", "不讨论现实身份", "请不要讨论主播现实身份相关内容。", set(LiveMode)),
]
