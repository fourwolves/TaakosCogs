"""TempleOSRS response contracts and clan rank reconciliation."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import discord
import pytest
from discord.ext.commands.view import StringView
from redbot.__main__ import parse_cli_flags
from redbot.core import commands, data_manager
from redbot.core.bot import Red

from osrsclan import setup, temple
from osrsclan.osrsclan import rank_threshold, tenure_days
from osrsclan.temple import ClanOverview, PlayerStats, TempleClient, TempleError


def _http_session(monkeypatch, *replies):
    requests = []
    responses = iter(replies)

    @asynccontextmanager
    async def get(url, *, params, allow_redirects):
        requests.append((url.removeprefix(temple.BASE_URL), params, allow_redirects))
        status, headers, payload = next(responses)
        yield SimpleNamespace(status=status, headers=headers, json=AsyncMock(return_value=payload))

    session = SimpleNamespace(get=get, close=AsyncMock())
    monkeypatch.setattr(temple.aiohttp, "ClientSession", lambda **_kwargs: session)
    return requests


def test_temple_group_roster_and_player_response_contract(monkeypatch):
    requests = _http_session(
        monkeypatch,
        (
            200,
            {},
            {"data": {"info": {"name": "Example Clan", "member_count": "2", "total_xp": 4000000000, "total_ehp": "77.25"}}},
        ),
        (
            200,
            {},
            {
                "data": {
                    "memberlist": {
                        "example rsn": {"player": "example rsn", "player_name_with_capitalization": "Example\xa0RSN"},
                        "other": {"player": "Other"},
                    }
                }
            },
        ),
        (
            200,
            {},
            {
                "data": {
                    "info": {"Username": "Example RSN", "Primary_ehp": "Im_ehp", "Primary_ehb": "Im_ehb"},
                    "Overall_level": 1883,
                    "Overall": 105000000,
                    "Ehp": 280,
                    "Im_ehp": 350.25,
                    "Im_ehb": 2.5,
                    "date": "2026-10-24 06:19:42",
                }
            },
        ),
    )

    async def run():
        client = TempleClient()
        try:
            assert await client.overview(1234) == ClanOverview("Example Clan", 2, 4000000000, 77.25, None)
            assert await client.roster(1234) == {"example rsn": "Example RSN", "other": "Other"}
            stats = await client.player("Example RSN")
            assert stats == PlayerStats(1883, 105000000, 350.25, 2.5, "2026-10-24 06:19:42")
            assert await client.player("example rsn") == stats
        finally:
            await client.close()

    asyncio.run(run())
    assert requests == [
        ("/api/group_info.php", {"id": 1234}, False),
        ("/api/group_member_info.php", {"id": 1234, "details": 1}, False),
        ("/api/player_stats.php", {"player": "Example RSN", "bosses": 1}, False),
    ]


def test_temple_rate_limit_backs_off_and_rejects_bad_roster(monkeypatch):
    requests = _http_session(
        monkeypatch,
        (200, {}, {"data": {"memberlist": {"alice": None}}}),
        (429, {"Retry-After": "30"}, None),
    )

    async def run():
        client = TempleClient()
        try:
            with pytest.raises(TempleError, match="valid member list"):
                await client.roster(1)
            with pytest.raises(TempleError, match="rate limiting"):
                await client.roster(2)
            with pytest.raises(TempleError, match="rate limiting"):
                await client.player("Example RSN")
        finally:
            await client.close()

    asyncio.run(run())
    assert len(requests) == 2


def test_tenure_uses_complete_days_from_current_join():
    now = datetime(2026, 11, 19, 4, tzinfo=timezone.utc)
    thresholds = [90, 30, 7]
    assert rank_threshold(tenure_days(now - timedelta(days=30) + timedelta(seconds=1), now), thresholds) == 7
    assert rank_threshold(tenure_days(now - timedelta(days=30), now), thresholds) == 30
    assert rank_threshold(tenure_days(now - timedelta(hours=12), now), thresholds) is None


def _role(guild, role_id, position, permissions=0):
    return discord.Role(
        guild=guild,
        state=None,
        data={"id": str(role_id), "name": f"Role {role_id}", "position": position, "permissions": str(permissions)},
    )


def _member(guild, user_id, held):
    mutations = Mock()
    mutations.attach_mock(AsyncMock(), "add")
    mutations.attach_mock(AsyncMock(), "remove")
    return SimpleNamespace(
        id=user_id,
        guild=guild,
        bot=False,
        joined_at=discord.utils.utcnow() - timedelta(days=31),
        display_name=f"Player {user_id}",
        roles=held,
        top_role=max(held),
        add_roles=mutations.add,
        remove_roles=mutations.remove,
        mutations=mutations,
    )


def test_red_loads_and_syncs_only_approved_manageable_roster_members(tmp_path, monkeypatch):
    monkeypatch.setattr(
        data_manager,
        "basic_config",
        {
            "DATA_PATH": str(tmp_path),
            "COG_PATH_APPEND": "cogs",
            "CORE_PATH_APPEND": "core",
            "STORAGE_TYPE": "JSON",
            "STORAGE_DETAILS": {},
        },
    )
    guild = SimpleNamespace(id=1, owner_id=2, chunked=True, unavailable=False)
    recruit, veteran, unrelated, bot_role = (_role(guild, n, n) for n in (10, 20, 30, 40))
    guild.me = SimpleNamespace(top_role=bot_role, guild_permissions=discord.Permissions(manage_roles=True))
    roles = {role.id: role for role in (recruit, veteran, unrelated, bot_role)}
    guild.get_role = roles.get
    member = _member(guild, 100, [recruit, unrelated])
    off_roster = _member(guild, 200, [recruit])
    unlinked = _member(guild, 300, [recruit])
    guild.get_member = {m.id: m for m in (member, off_roster, unlinked)}.get
    owner = SimpleNamespace(id=guild.owner_id, guild=guild, top_role=bot_role)
    ctx = SimpleNamespace(guild=guild, author=owner, send=AsyncMock())

    async def run():
        intents = discord.Intents(guilds=True, members=True, message_content=True)
        async with Red(cli_flags=parse_cli_flags([]), bot_dir=tmp_path, intents=intents) as bot:
            await setup(bot)
            cog = bot.get_cog("OSRSClan")
            sync = bot.get_command("osrsclan set sync")
            assert sync is not None
            member.guild_permissions = discord.Permissions(manage_guild=True)
            member.voice = None
            channel = SimpleNamespace(id=40, type=discord.ChannelType.text, category=None)
            channel.permissions_for = lambda _: discord.Permissions.all()
            message = SimpleNamespace(_state=bot._connection, guild=guild, author=member, channel=channel)
            denied = commands.Context(bot=bot, command=sync, message=message, view=StringView(""))
            monkeypatch.setattr(cog.temple, "roster", AsyncMock(return_value={"example rsn": "Example RSN"}))
            monkeypatch.setattr(cog.temple, "overview", AsyncMock(return_value=ClanOverview("Example", 1, None, None, None)))
            conf = cog.config.guild(guild)
            try:
                # The nested sync command still checks its staff-only parent.
                assert not await sync.can_run(denied, check_all_parents=True)

                # Automatic sync does not contact Temple or change roles before opt-in.
                assert await cog._sync(guild, automatic=True) == {}
                cog.temple.roster.assert_not_awaited()

                await conf.auto_rank.set(True)
                await cog.set_group.callback(cog, ctx, 1234)
                assert await conf.group_id() == 1234
                assert not await conf.auto_rank()
                await conf.ranks.set({"0": recruit.id, "30": veteran.id})
                await cog.approve_link.callback(cog, ctx, member, rsn="example_rsn")
                with pytest.raises(commands.UserFeedbackCheckFailure, match="already linked"):
                    await cog.approve_link.callback(cog, ctx, unlinked, rsn="EXAMPLE RSN")
                async with conf.links() as links:
                    links[str(off_roster.id)] = "Former RSN"

                assert await cog._sync(guild, actor=owner) == {"updated": 1, "skipped": 1}
                reason = "OSRSClan Discord tenure rank sync"
                assert member.mutations.mock_calls == [call.add(veteran, reason=reason), call.remove(recruit, reason=reason)]
                assert not off_roster.mutations.mock_calls
                assert not unlinked.mutations.mock_calls

                member.mutations.reset_mock()
                cog.temple.roster.side_effect = TempleError("Temple unavailable")
                with pytest.raises(TempleError, match="Temple unavailable"):
                    await cog._sync(guild)
                assert not member.mutations.mock_calls

                roles[veteran.id] = _role(guild, veteran.id, veteran.position, discord.Permissions(administrator=True).value)
                with pytest.raises(commands.UserFeedbackCheckFailure, match="no server permissions"):
                    await cog._sync(guild)
                assert not member.mutations.mock_calls

                exports = await cog.red_get_data_for_user(user_id=member.id)
                assert json.load(exports["osrsclan.json"]) == {str(guild.id): "Example RSN"}
                await cog.red_delete_data_for_user(requester="discord_deleted_user", user_id=member.id)
                assert await conf.links() == {str(off_roster.id): "Former RSN"}
            finally:
                await bot.remove_cog("OSRSClan")
            assert bot.get_command("osrsclan") is None
            assert cog.temple._session.closed

    asyncio.run(run())
