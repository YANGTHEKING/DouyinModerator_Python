from __future__ import annotations

import re
from dataclasses import dataclass, field

from douyin_mod_manager.sources.gift_images import gift_image_key, lookup_gift_name_from_url


CHAT_LINE_RE = re.compile(
    r"^\s*(?P<username>[^:：\n\r]{1,80})\s*[:：]\s*(?P<content>.*?)\s*$"
)
GIFT_LINE_RE = re.compile(r"^\s*(?P<username>.+?)\s*(?:送出了|送出|赠送|送了|送给|送上|送)\s*(?P<tail>.*?)\s*$")
GIFT_ACTION_RE = re.compile(r"^\s*(?:送出了|送出|赠送|送了|送给|送上|送)\s*(?P<tail>.*?)\s*$")
BRACKET_GIFT_RE = re.compile(r"[【\[](?P<gift>[^】\]]+)[】\]]")
COUNT_RE = re.compile(r"[xX×*＊]\s*(?P<count>\d+)|(?P<count_cn>\d+)\s*(?:个|份|枚)")
GIFT_IMAGE_NAME_BY_HASH = {
    "0fc1fff5bcc835209390b87f609688f1": "欢乐号角",
    "7ef47758a435313180e6b78b056dda4e": "小心心",
    "4960c39f645d524beda5d50dc372510e": "真好看",
}


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
    if username:
        username = username.rstrip(":：").strip() or username

    if event_type == "gift":
        split = split_chat_line(content or visible_text)
        if split is not None and not is_explicit_gift_text(content or visible_text) and not has_gift_color(raw):
            event_type = "chat"
            username, content = split
        else:
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
        sys_text = " ".join((content or visible_text or "").split())
        if re.search(r"成为No\.\d+.*贡献用户", sys_text):
            return None
        if "成功冠名了" in sys_text:
            return None
        special_gift = re.search(r"恭喜\s*(.+?)\s*成为(星守护|月度会员)", sys_text)
        if special_gift:
            event_type = "gift"
            username = special_gift.group(1).strip()
            content = special_gift.group(2)
            raw["gift_name"] = content
            raw["gift_count"] = 1
            raw["gift_value"] = 0
            username, content, raw = normalize_gift_fields(username, content, visible_text, raw)
        else:
            username, content = normalize_system_fields(username, content or visible_text)

    if event_type == "chat" and username and (is_gift_action_content(content) or has_gift_color(raw)):
        event_type = "gift"
        username, content, raw = normalize_gift_fields(username, content, visible_text, raw)

    if not username and event_type == "chat":
        split_source = content or visible_text
        split = split_chat_line(split_source)
        if split is not None:
            username, content = split
        else:
            system_username, system_content = normalize_system_fields(username, split_source)
            if system_username or system_content != split_source:
                event_type = "system"
                username, content = system_username, system_content
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

    if event_type == "chat" and username and (_content_is_only_username(content, username) or _content_is_only_username(visible_text, username)):
        labels = [str(label).strip() for label in raw.get("mediaLabels", []) if str(label).strip()]
        if labels:
            content = " ".join(labels)
        elif _has_image_only_content(raw):
            content = "[表情]"

    if not username and not content and not visible_text:
        return None

    final_content = content
    if not final_content and visible_text:
        if not username or not _content_is_only_username(visible_text, username):
            final_content = visible_text

    return ParsedDomRecord(
        type=event_type,
        username=username,
        content=final_content,
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
    text = content if username and is_gift_action_content(content) else visible_text or content
    parsed = parse_gift_line(text)
    if parsed:
        parsed_user, gift_name, gift_count = parsed
        username = username or parsed_user
        raw["gift_name"] = raw.get("gift_name") or gift_name
        raw["gift_count"] = raw.get("gift_count") or gift_count
    elif username and content:
        action = parse_gift_action_content(content)
        if action:
            gift_name, gift_count = action
            if gift_name:
                raw["gift_name"] = raw.get("gift_name") or gift_name
            raw["gift_count"] = raw.get("gift_count") or gift_count
    elif not username:
        username = split_username_prefix(text)

    gift_name = (
        gift_name_from_media_labels(raw.get("mediaLabels"))
        or gift_name_from_hints(raw.get("giftHints"), raw, username)
        or gift_name_from_child_summaries(raw.get("childSummaries"), raw)
        or _clean(raw.get("gift_name"))
    )
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
    match = re.match(r"^\s*(?P<username>.+?)\s+(?P<action>推荐直播给Ta的朋友|推荐了直播|为主播点赞了)\s*$", _last_non_empty_line(content))
    if match:
        return username or match.group("username").strip(), match.group("action").strip()
    if re.match(r"^\s*恭喜.+刚刚升级至Lv\.\d+\s*$", _last_non_empty_line(content)):
        return username, content
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


def parse_gift_action_content(text: str | None) -> tuple[str | None, int] | None:
    if not text:
        return None
    compact = " ".join(text.split())
    match = GIFT_ACTION_RE.match(compact)
    if not match:
        return None
    tail = match.group("tail").strip()
    gift = None
    bracket = BRACKET_GIFT_RE.search(tail)
    if bracket:
        gift = clean_gift_name(bracket.group("gift"))
    if not gift:
        gift = clean_gift_name(COUNT_RE.sub("", tail))
    count_match = COUNT_RE.search(tail)
    count = _gift_count(count_match.group("count") or count_match.group("count_cn")) if count_match else 1
    return gift, count


def is_explicit_gift_text(text: str | None) -> bool:
    if not text:
        return False
    compact = " ".join(text.split())
    if CHAT_LINE_RE.search(_last_non_empty_line(compact)):
        return False
    return bool(re.search(r"(送出|送出了|赠送|送了|送给|送上)", compact))


def is_gift_action_content(text: str | None) -> bool:
    return bool(text and GIFT_ACTION_RE.match(" ".join(text.split())))


_GIFT_COLORS = {"rgb(235, 168, 37)", "rgb(255, 196, 0)", "rgb(255, 200, 0)"}


def has_gift_color(raw: dict) -> bool:
    children = raw.get("childSummaries")
    if not isinstance(children, list):
        return False
    has_golden_image = False
    for child in children:
        if not isinstance(child, dict):
            continue
        color = str(child.get("color") or "").strip().lower()
        if color not in _GIFT_COLORS:
            continue
        text = str(child.get("text") or "").strip()
        if text and re.search(r"(送出了|送出|赠送|送了|送给|送上|送)", text):
            return True
        if not text:
            src = str(child.get("src") or child.get("backgroundImage") or "").strip()
            if src and "webcast" in src:
                has_golden_image = True
    return has_golden_image


def gift_name_from_media_labels(labels: object) -> str | None:
    if not isinstance(labels, list):
        return None
    for label in labels:
        cleaned = clean_gift_name(_clean(label))
        if cleaned:
            return cleaned
    return None


def gift_name_from_hints(hints: object, raw: dict, username: str | None) -> str | None:
    if not isinstance(hints, list):
        return None
    normalized_username = _normalize_match_text(username)
    for hint in reversed(hints):
        if not isinstance(hint, dict):
            continue
        hint_username = _normalize_match_text(hint.get("username"))
        if normalized_username and hint_username:
            if normalized_username not in hint_username and hint_username not in normalized_username:
                continue
        elif normalized_username:
            continue
        gift_name = clean_gift_name(_clean(hint.get("giftName")))
        if not gift_name:
            continue
        raw["gift_hint_text"] = hint.get("text")
        raw["gift_hint_username"] = hint.get("username")
        if hint.get("giftCount") and not raw.get("gift_count"):
            raw["gift_count"] = _gift_count(hint.get("giftCount"))
        return gift_name
    return None


def gift_name_from_child_summaries(children: object, raw: dict) -> str | None:
    if not isinstance(children, list):
        return None
    golden_texts: list[str] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        color = str(child.get("color") or "").strip().lower()
        if color in _GIFT_COLORS:
            text = _clean(child.get("text"))
            if text:
                golden_texts.append(text)
        src = _clean(child.get("src")) or _clean(child.get("backgroundImage"))
        if not src or "new_user_grade" in src:
            continue
        image_key = gift_image_key(src)
        if image_key:
            raw["gift_image_key"] = image_key
            mapped_name = lookup_gift_name_from_url(src)
            if mapped_name:
                raw["gift_image_url"] = src
                return mapped_name
        for image_hash, gift_name in GIFT_IMAGE_NAME_BY_HASH.items():
            if image_hash in src:
                raw["gift_image_url"] = src
                raw["gift_image_key"] = image_hash
                return gift_name
        if "webcast" in src and re.search(r"\.(png|webp|jpg|jpeg)", src, re.IGNORECASE):
            raw["gift_image_url"] = src
    for text in golden_texts:
        cleaned = clean_gift_name(COUNT_RE.sub("", text))
        if cleaned and not re.match(r"^[×xX*＊\d\s]+$", cleaned):
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


def _has_image_only_content(raw: dict) -> bool:
    children = raw.get("childSummaries")
    if not isinstance(children, list):
        return False
    for child in children:
        if not isinstance(child, dict):
            continue
        cls = str(child.get("className") or "")
        text = str(child.get("text") or "").strip()
        if "hts-live-text-img" in cls and not text:
            return True
    return False


def _content_is_only_username(content: str | None, username: str) -> bool:
    if not content:
        return True
    normalized_content = content.strip().rstrip(":：").strip()
    normalized_username = username.strip().rstrip(":：").strip()
    return normalized_content == normalized_username


def _normalize_match_text(value: object) -> str:
    return re.sub(r"[\s:：*＊]+", "", str(value or "").strip())


def _last_non_empty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else text.strip()
