"""Read-only TempleOSRS client and response parsing."""

from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from math import isfinite
from typing import Any
from urllib.parse import quote

import aiohttp

BASE_URL = "https://templeosrs.com"
RSN_PATTERN = re.compile(r"[A-Za-z0-9 _-]{1,12}")


class TempleError(Exception):
    """A safe, user-facing TempleOSRS lookup error."""


def clean_rsn(name: str) -> str:
    """Normalize spaces used by Temple and Discord without changing the displayed case."""
    name = name.replace("\xa0", " ").replace("_", " ").strip()
    if not RSN_PATTERN.fullmatch(name) or not any(char.isalnum() for char in name):
        raise ValueError("Use an OSRS name of 1–12 letters, numbers, spaces, or hyphens.")
    return name


def group_url(group_id: int) -> str:
    return f"{BASE_URL}/groups/overview.php?id={group_id}"


def player_url(rsn: str) -> str:
    return f"{BASE_URL}/player/overview.php?player={quote(rsn)}"


@dataclass(frozen=True)
class ClanOverview:
    name: str
    member_count: int
    xp: int | None
    ehp: float | None
    ehb: float | None


@dataclass(frozen=True)
class PlayerStats:
    level: int | None
    xp: int | None
    ehp: float | None
    ehb: float | None
    date: str | None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if isfinite(number) and number >= 0 else None


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


class TempleClient:
    """Reuse a session, share a bounded cache, and back off on rate limiting."""

    CACHE_TTL = 60
    CACHE_SIZE = 256

    def __init__(self) -> None:
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        self._cache: OrderedDict[tuple[str, int | str], tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._retry_at = 0.0

    async def close(self) -> None:
        await self._session.close()

    async def _get(
        self,
        endpoint: str,
        value: int | str,
        params: dict[str, int | str],
    ) -> dict[str, Any]:
        key = (endpoint, value)
        async with self._lock:
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached and now < cached[0]:
                self._cache.move_to_end(key)
                return cached[1]
            if now < self._retry_at:
                raise TempleError("TempleOSRS is rate limiting requests. Try again shortly.")
            try:
                async with self._session.get(f"{BASE_URL}/api/{endpoint}", params=params, allow_redirects=False) as response:
                    if response.status == 429:
                        try:
                            retry_after = int(response.headers.get("Retry-After", "60"))
                        except ValueError:
                            retry_after = 60
                        self._retry_at = time.monotonic() + min(max(retry_after, 1), 3600)
                        raise TempleError("TempleOSRS is rate limiting requests. Try again shortly.")
                    if response.status in (400, 404):
                        raise TempleError("TempleOSRS could not find that group or player. Check the ID or RSN.")
                    if response.status != 200:
                        raise TempleError("TempleOSRS is unavailable right now. Try again later.")
                    payload = await response.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                raise TempleError("Could not reach TempleOSRS. Try again later.") from error
            except ValueError as error:
                raise TempleError("TempleOSRS returned an unreadable response. Try again later.") from error

            if isinstance(payload, dict) and payload.get("error"):
                raise TempleError("TempleOSRS rejected the lookup. Confirm it tracks that group or player.")
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                raise TempleError("TempleOSRS returned an unexpected response. Try again later.")

            data = payload["data"]
            self._cache[key] = (time.monotonic() + self.CACHE_TTL, data)
            self._cache.move_to_end(key)
            if len(self._cache) > self.CACHE_SIZE:
                self._cache.popitem(last=False)
            return data

    async def overview(self, group_id: int) -> ClanOverview:
        data = await self._get("group_info.php", group_id, {"id": group_id})
        info = data.get("info")
        if not isinstance(info, dict) or not isinstance(info.get("name"), str):
            raise TempleError("TempleOSRS did not return valid group information.")
        name = " ".join(info["name"].split())
        count = _integer(info.get("member_count"))
        if not name or count is None:
            raise TempleError("TempleOSRS did not return valid group information.")
        return ClanOverview(
            name, count, _integer(info.get("total_xp")), _number(info.get("total_ehp")), _number(info.get("total_ehb"))
        )

    async def roster(self, group_id: int) -> dict[str, str]:
        """Return normalized RSNs mapped to their display names."""
        data = await self._get("group_member_info.php", group_id, {"id": group_id, "details": 1})
        records = data.get("memberlist")
        if isinstance(records, dict):
            rows = records.items()
        elif isinstance(records, list):
            rows = (("", entry) for entry in records)
        else:
            raise TempleError("TempleOSRS did not return a valid member list.")
        members = {}
        for key, entry in rows:
            if not isinstance(entry, dict):
                raise TempleError("TempleOSRS did not return a valid member list.")
            name = entry.get("player_name_with_capitalization") or entry.get("player") or key
            if not isinstance(name, str):
                raise TempleError("TempleOSRS returned an invalid player name in the member list.")
            try:
                name = clean_rsn(name)
            except ValueError as error:
                raise TempleError("TempleOSRS returned an invalid player name in the member list.") from error
            members[name.casefold()] = name
        return members

    async def player(self, rsn: str) -> PlayerStats:
        data = await self._get("player_stats.php", rsn.casefold(), {"player": rsn, "bosses": 1})
        info = data.get("info")
        info = info if isinstance(info, dict) else {}
        xp = _integer(data.get("Overall"))
        level = _integer(data.get("Overall_level"))
        if xp is None and level is None:
            raise TempleError("TempleOSRS has no recorded stats for that player yet.")

        def hours(primary: str, fallback: str) -> float | None:
            field = info.get(primary) or data.get(primary)
            return _number(data.get(field)) if isinstance(field, str) and field in data else _number(data.get(fallback))

        date = data.get("date")
        return PlayerStats(
            level, xp, hours("Primary_ehp", "Ehp"), hours("Primary_ehb", "Ehb"), date if isinstance(date, str) else None
        )
