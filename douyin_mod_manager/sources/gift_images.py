from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_GIFT_IMAGE_NAMES = {
    "0fc1fff5bcc835209390b87f609688f1": "欢呼号角",
    "7ef47758a435313180e6b78b056dda4e": "小心心",
    "4960c39f645d524beda5d50dc372510e": "你最好看",
}


@dataclass(slots=True)
class GiftImageEntry:
    image_key: str
    name: str = ""
    diamond_count: int = 0
    image_url: str = ""
    first_seen: str = ""
    last_seen: str = ""
    seen_count: int = 0
    source: str = "observed"

    @property
    def status(self) -> str:
        return "已映射" if self.name.strip() else "未知"


class GiftImageRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).resolve().parents[2] / "data" / "gift_image_mappings.json"
        self.entries: dict[str, GiftImageEntry] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.entries = {
                image_key: GiftImageEntry(image_key=image_key, name=name, source="default")
                for image_key, name in DEFAULT_GIFT_IMAGE_NAMES.items()
            }
            return
        self.entries = {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.entries = {
                image_key: GiftImageEntry(image_key=image_key, name=name, source="default")
                for image_key, name in DEFAULT_GIFT_IMAGE_NAMES.items()
            }
            return
        for item in payload.get("images", []):
            if not isinstance(item, dict):
                continue
            image_key = str(item.get("image_key") or "").strip()
            if not image_key:
                continue
            image_url = str(item.get("image_url") or "")
            name = str(item.get("name") or "")
            if image_url and not name.strip() and _is_non_gift_image_url(image_url):
                continue
            self.entries[image_key] = GiftImageEntry(
                image_key=image_key,
                name=name,
                diamond_count=int(item.get("diamond_count") or 0),
                image_url=image_url,
                first_seen=str(item.get("first_seen") or ""),
                last_seen=str(item.get("last_seen") or ""),
                seen_count=int(item.get("seen_count") or 0),
                source=str(item.get("source") or "observed"),
            )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "images": [
                asdict(entry)
                for entry in sorted(self.entries.values(), key=lambda item: (not item.name, item.name, item.image_key))
            ]
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_entries(self, sort_by: str = "name") -> list[GiftImageEntry]:
        if sort_by == "diamond":
            return sorted(self.entries.values(), key=lambda item: (-item.diamond_count, item.name, item.image_key))
        if sort_by == "seen":
            return sorted(self.entries.values(), key=lambda item: (-item.seen_count, item.name, item.image_key))
        return sorted(self.entries.values(), key=lambda item: (not item.name, item.name, item.image_key))

    def delete_entry(self, image_key: str) -> bool:
        if image_key in self.entries:
            del self.entries[image_key]
            self.save()
            return True
        return False

    def get_name(self, image_key: str) -> str | None:
        entry = self.entries.get(image_key)
        if not entry or not entry.name.strip():
            return None
        return entry.name.strip()

    def set_name(self, image_key: str, name: str) -> None:
        entry = self.entries.setdefault(image_key, GiftImageEntry(image_key=image_key))
        entry.name = name.strip()
        if entry.source == "default":
            entry.source = "manual"
        self.save()

    def set_diamond_count(self, image_key: str, count: int) -> None:
        entry = self.entries.setdefault(image_key, GiftImageEntry(image_key=image_key))
        entry.diamond_count = count
        self.save()

    def record_raw(self, raw: dict[str, Any], gift_name: str | None = None) -> bool:
        changed = False
        for image_url in gift_image_urls_from_raw(raw):
            image_key = gift_image_key(image_url)
            if not image_key:
                continue
            entry = self.entries.setdefault(image_key, GiftImageEntry(image_key=image_key))
            now = datetime.now().isoformat(timespec="seconds")
            if not entry.first_seen:
                entry.first_seen = now
            entry.last_seen = now
            entry.seen_count += 1
            if image_url and image_url != entry.image_url:
                entry.image_url = image_url
            if gift_name and not entry.name.strip():
                entry.name = gift_name.strip()
            changed = True
        if changed:
            self.save()
        return changed


def lookup_gift_name_from_url(image_url: str) -> str | None:
    image_key = gift_image_key(image_url)
    if not image_key:
        return None
    return GiftImageRegistry().get_name(image_key)


def gift_image_key(image_url: str) -> str | None:
    if not image_url:
        return None
    match = re.search(r"/([0-9a-f]{20,64})\.(?:png|webp|jpg|jpeg)", image_url, flags=re.IGNORECASE)
    if match:
        return match.group(1).lower()
    match = re.search(r"/([0-9a-f]{20,64})(?:~|\\?|$)", image_url, flags=re.IGNORECASE)
    if match:
        return match.group(1).lower()
    if "webcast" not in image_url:
        return None
    return re.sub(r"[?#].*$", "", image_url).strip()


def gift_image_urls_from_raw(raw: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    direct = raw.get("gift_image_url")
    if isinstance(direct, str) and direct.strip():
        urls.append(direct.strip())
    children = raw.get("childSummaries")
    if isinstance(children, list):
        for child in children:
            if not isinstance(child, dict):
                continue
            for key in ["src", "backgroundImage"]:
                value = str(child.get(key) or "").strip()
                if not value:
                    continue
                urls.extend(_urls_from_css_or_attr(value))
    return _unique_gift_urls(urls)


def _urls_from_css_or_attr(value: str) -> list[str]:
    if value.startswith("url("):
        return [match.group(1).strip("\"'") for match in re.finditer(r"url\(([^)]+)\)", value)]
    return [value]


def _unique_gift_urls(urls: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if _is_non_gift_image_url(url):
            continue
        if "webcast" not in url or not re.search(r"\.(png|webp|jpg|jpeg)", url, re.IGNORECASE):
            continue
        key = gift_image_key(url) or url
        if key in seen:
            continue
        seen.add(key)
        unique.append(url)
    return unique


def _is_non_gift_image_url(url: str) -> bool:
    return any(
        marker in url
        for marker in [
            "new_user_grade",
            "ranklist_fansclub",
            "fansclub",
            "user_grade",
            "badge",
            "fusion_emoji",
        ]
    )
