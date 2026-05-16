from __future__ import annotations

import re
from dataclasses import dataclass, field


CHAT_LINE_RE = re.compile(
    r"^\s*(?P<username>[^:：\n\r]{1,80})\s*[:：]\s*(?P<content>.*?)\s*$"
)
GIFT_LINE_RE = re.compile(r"^\s*(?P<username>.+?)\s*(?:送出|赠送|送了|送)\s*(?P<tail>.+?)\s*$")
BRACKET_GIFT_RE = re.compile(r"[【\[](?P<gift>[^】\]]+)[】\]]")
COUNT_RE = re.compile(r"[xX×*＊]\s*(?P<count>\d+)|(?P<count_cn>\d+)\s*(?:个|份|枚)")


@dataclass(slots=True)
class ParsedDomRecord:
    type: str
    username: str | None
    content: str | None
    raw: dict = field(default_factory=dict)


def normalize_dom_record(record: dict) -> ParsedDomRecord | None:
    event_type = str(record.get("type") or "chat")
    raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}

    username = _clean(record.get("username"))
    content = _clean(record.get("content"))
    visible_text = _clean(raw.get("text"))

    if event_type == "gift":
        username, content, raw = normalize_gift_fields(username, content, visible_text, raw)
    elif event_type == "follow":
        follow_username, follow_content = normalize_follow_fields(username, content or visible_text)
        if follow_username and follow_content:
            username, content = follow_username, follow_content
        else:
            split = split_chat_line(content or visible_text)
            if split is not None:
                event_type = "chat"
                username, content = split
    elif event_type == "user_enter":
        enter_username, enter_content = normalize_user_enter_fields(username, content or visible_text)
        if enter_username and enter_content:
            username, content = enter_username, enter_content
        else:
            split = split_chat_line(content or visible_text)
            if split is not None:
                event_type = "chat"
                username, content = split
    elif event_type == "system":
        username, content = normalize_system_fields(username, content or visible_text)

    if not username and event_type == "chat":
        split_source = content or visible_text
        split = split_chat_line(split_source)
        if split is not None:
            username, content = split
        else:
            username = split_username_prefix(split_source)
            labels = [str(label).strip() for label in raw.get("mediaLabels", []) if str(label).strip()]
            if labels:
                content = " ".join(labels)
            elif username:
                content = chat_content_after_prefix(split_source)

    if username and content and content.startswith(username):
        stripped = content.removeprefix(username).strip()
        stripped = stripped.removeprefix(":").removeprefix("：").strip()
        content = stripped or content

    if not username and not content and not visible_text:
        return None

    return ParsedDomRecord(
        type=event_type,
        username=username,
        content=content or visible_text,
        raw=raw,
    )


def split_chat_line(text: str | None) -> tuple[str, str] | None:
    if not text:
        return None
    match = CHAT_LINE_RE.match(_last_non_empty_line(text))
    if not match:
        return None
    username = match.group("username").strip()
    content = match.group("content").strip()
    if not username or not content:
        return None
    return username, content


def split_username_prefix(text: str | None) -> str | None:
    if not text:
        return None
    match = CHAT_LINE_RE.match(_last_non_empty_line(text))
    if not match:
        return None
    username = match.group("username").strip()
    return username or None


def chat_content_after_prefix(text: str | None) -> str | None:
    if not text:
        return None
    match = CHAT_LINE_RE.match(_last_non_empty_line(text))
    if not match:
        return None
    return match.group("content").strip() or None


def normalize_gift_fields(
    username: str | None,
    content: str | None,
    visible_text: str | None,
    raw: dict,
) -> tuple[str | None, str | None, dict]:
    text = visible_text or content
    parsed = parse_gift_line(text)
    if parsed:
        parsed_user, gift_name, gift_count = parsed
        username = username or parsed_user
        raw["gift_name"] = raw.get("gift_name") or gift_name
        raw["gift_count"] = raw.get("gift_count") or gift_count
    elif not username:
        username = split_username_prefix(text)

    gift_name = gift_name_from_media_labels(raw.get("mediaLabels")) or _clean(raw.get("gift_name"))
    if gift_name:
        raw["gift_name"] = gift_name

    gift_count = _gift_count(raw.get("gift_count")) or gift_count_from_text(text)
    raw["gift_count"] = gift_count
    raw["gift_value"] = raw.get("gift_value") or gift_count

    if gift_name:
        content = f"送出 {gift_name} x{gift_count}"
    elif content and username and content.startswith(username):
        content = content.removeprefix(username).strip()
    return username, content, raw


def normalize_user_enter_fields(username: str | None, content: str | None) -> tuple[str | None, str | None]:
    if username or not content:
        return username, content
    match = re.match(r"^\s*(?P<username>.+?)\s+(?:来了|进入直播间|进入了直播间)\s*$", _last_non_empty_line(content))
    if not match:
        return username, content
    return match.group("username").strip(), "进入直播间"


def normalize_follow_fields(username: str | None, content: str | None) -> tuple[str | None, str | None]:
    if username:
        return username, content
    if not content:
        return username, content
    match = re.match(
        r"^\s*(?P<username>.+?)\s+(?P<action>关注了主播|关注主播|加入了粉丝团|成为了粉丝)\s*$",
        _last_non_empty_line(content),
    )
    if not match:
        return None, None
    return match.group("username").strip(), match.group("action").strip()


def normalize_system_fields(username: str | None, content: str | None) -> tuple[str | None, str | None]:
    if not content:
        return username, content
    split = split_chat_line(content)
    if split is not None:
        parsed_user, parsed_content = split
        return username or parsed_user, parsed_content
    match = re.match(r"^\s*(?P<username>.+?)\s+(?P<action>推荐直播给Ta的朋友|为主播点赞了)\s*$", _last_non_empty_line(content))
    if match:
        return username or match.group("username").strip(), match.group("action").strip()
    return username, content


def parse_gift_line(text: str | None) -> tuple[str, str, int] | None:
    if not text:
        return None
    compact = " ".join(text.split())
    match = GIFT_LINE_RE.match(compact)
    if not match:
        return None
    username = match.group("username").strip()
    tail = match.group("tail").strip()
    gift = None
    bracket = BRACKET_GIFT_RE.search(tail)
    if bracket:
        gift = clean_gift_name(bracket.group("gift"))
    if not gift:
        gift = clean_gift_name(COUNT_RE.sub("", tail))
    if not username or not gift:
        return None
    count_match = COUNT_RE.search(tail)
    count = _gift_count(count_match.group("count") or count_match.group("count_cn")) if count_match else 1
    return username, gift, count


def gift_name_from_media_labels(labels: object) -> str | None:
    if not isinstance(labels, list):
        return None
    for label in labels:
        cleaned = clean_gift_name(_clean(label))
        if cleaned:
            return cleaned
    return None


def gift_count_from_text(text: str | None) -> int:
    if not text:
        return 1
    match = COUNT_RE.search(text)
    if match:
        return _gift_count(match.group("count") or match.group("count_cn"))
    return 1


def clean_gift_name(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    text = text.removeprefix("【").removesuffix("】")
    text = text.removeprefix("[").removesuffix("]")
    text = re.sub(r"^(礼物|gift)\s*[:：]\s*", "", text, flags=re.IGNORECASE).strip()
    if not text or re.search(r"登录|关注|进入|弹幕", text):
        return None
    return text


def _gift_count(value: object) -> int:
    try:
        count = int(str(value).strip())
    except (TypeError, ValueError):
        return 1
    return max(1, count)


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _last_non_empty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else text.strip()
