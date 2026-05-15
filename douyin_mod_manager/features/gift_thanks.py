from __future__ import annotations

from douyin_mod_manager.core.events import ActionProposal, EventType, LiveEvent


class GiftThanksStrategy:
    def build(self, event: LiveEvent) -> ActionProposal | None:
        if event.type != EventType.GIFT:
            return None
        value = int(event.raw.get("gift_value", 0))
        gift = event.raw.get("gift_name", "礼物")
        count = event.raw.get("gift_count", 1)
        if value <= 2:
            text = f"谢谢 {event.display_user} 的 {gift} x{count}，小礼物合并感谢中。"
            auto = True
        elif value <= 20:
            text = f"谢谢 {event.display_user} 的 {gift} x{count}，要不要人工补一句专属感谢？"
            auto = False
        else:
            text = f"高价值礼物提醒：{event.display_user} 送出 {gift} x{count}，建议主播/房管人工感谢。"
            auto = False
        return ActionProposal(event_id=event.id, rule_id="gift-thanks", rule_name="礼物感谢策略", text=text, auto_send=auto)
