"""TempleOSRS clan stats and Discord-tenure ranks."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from collections import Counter, defaultdict
from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING, Literal, TypedDict

import discord
from discord.ext import tasks
from redbot.core import Config, commands

from .dashboard_integration import DashboardIntegration
from .temple import TempleClient, TempleError, clean_rsn, group_url, player_url

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from redbot.core.bot import Red

log = logging.getLogger("red.taakoscogs.osrsclan")
MAX_RANKS = 20
PAGE_SIZE = 20
RankOutcome = Literal["updated", "unchanged", "skipped", "failed"]


class ClanSettings(TypedDict):
    group_id: int | None
    ranks: dict[str, int]
    links: dict[str, str]
    auto_rank: bool


def tenure_days(joined_at: datetime, now: datetime) -> int:
    return max(0, (now - joined_at).days)


def rank_threshold(days: int, thresholds: Iterable[int]) -> int | None:
    return max((threshold for threshold in thresholds if threshold <= days), default=None)


def _text(value: str, limit: int = 100) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(" ".join(value.split())[:limit]))


def _metric(value: float | None, *, decimals: int = 0) -> str:
    return "Unavailable" if value is None else format(value, f",.{decimals}f")


class OSRSClan(DashboardIntegration, commands.Cog):
    """Look up TempleOSRS clan stats and manage Discord roles based on server tenure."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2026111901, force_registration=True)
        self.config.register_guild(group_id=None, ranks={}, links={}, auto_rank=False)
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.temple: TempleClient

    async def cog_load(self) -> None:
        self.temple = TempleClient()
        self.rank_loop.start()

    async def cog_unload(self) -> None:
        task = self.rank_loop.get_task()
        self.rank_loop.cancel()
        if task is not None and not task.done():
            with suppress(asyncio.CancelledError):
                await task
        await self.temple.close()

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        return True

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, TempleError):
            await self._send(ctx, str(original))
        else:
            await self.bot.on_command_error(ctx, error, unhandled_by_cog=True)

    async def red_get_data_for_user(self, *, user_id: int) -> dict[str, io.BytesIO]:
        links = {
            str(guild_id): settings["links"][str(user_id)]
            for guild_id, settings in (await self.config.all_guilds()).items()
            if str(user_id) in settings["links"]
        }
        return {"osrsclan.json": io.BytesIO(json.dumps(links, indent=2).encode())} if links else {}

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        for guild_id in await self.config.all_guilds():
            async with self._locks[guild_id], self.config.guild_from_id(guild_id).links() as links:
                links.pop(str(user_id), None)

    @staticmethod
    async def _send(ctx: commands.Context, message: str) -> None:
        await ctx.send(message, allowed_mentions=discord.AllowedMentions.none())

    @staticmethod
    def _group_id(settings: ClanSettings) -> int:
        if settings["group_id"] is None:
            raise commands.UserFeedbackCheckFailure("A server manager must configure a TempleOSRS group first.")
        return settings["group_id"]

    @staticmethod
    def _role_label(guild: discord.Guild, role_id: int) -> str:
        role = guild.get_role(role_id)
        return role.mention if role else f"deleted role ({role_id})"

    async def _roster_name(self, group_id: int, rsn: str) -> str:
        try:
            key = clean_rsn(rsn).casefold()
        except ValueError as error:
            raise commands.BadArgument(str(error)) from error
        canonical = (await self.temple.roster(group_id)).get(key)
        if canonical is None:
            raise commands.UserFeedbackCheckFailure("That RSN is not on the configured TempleOSRS roster.")
        return canonical

    @staticmethod
    def _actor_can_manage(member_or_role: discord.Member | discord.Role, actor: discord.Member) -> bool:
        if actor.id == actor.guild.owner_id:
            return True
        if isinstance(member_or_role, discord.Role):
            return member_or_role < actor.top_role
        return member_or_role.id == actor.id or member_or_role.top_role < actor.top_role

    @staticmethod
    def _check_role(guild: discord.Guild, role: discord.Role, actor: discord.Member | None = None) -> None:
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            raise commands.UserFeedbackCheckFailure("Give the bot Manage Roles before configuring or syncing ranks.")
        if role.is_default() or role.managed or role >= me.top_role:
            raise commands.UserFeedbackCheckFailure(f"Rank role {role.id} must be an ordinary role below the bot's top role.")
        if role.permissions.value:
            raise commands.UserFeedbackCheckFailure(f"Rank role {role.id} must have no server permissions.")
        if actor is not None and not OSRSClan._actor_can_manage(role, actor):
            raise commands.UserFeedbackCheckFailure("A rank role must be below your highest role.")

    def _rank_roles(
        self,
        guild: discord.Guild,
        configured: dict[str, int],
        actor: discord.Member | None = None,
    ) -> dict[int, discord.Role]:
        roles = {}
        for days, role_id in configured.items():
            role = guild.get_role(role_id)
            if role is None:
                raise commands.UserFeedbackCheckFailure(f"The {days}-day role was deleted. Remove or replace that rank mapping.")
            self._check_role(guild, role, actor)
            roles[int(days)] = role
        return roles

    def _check_sync(self, guild: discord.Guild) -> None:
        if not self.bot.intents.members:
            raise commands.UserFeedbackCheckFailure("Enable Server Members Intent and restart Red before syncing ranks.")
        if guild.unavailable or not guild.chunked:
            raise commands.UserFeedbackCheckFailure(
                "The Discord member cache is not ready. Try again after Red has finished loading."
            )

    async def _apply_rank(
        self,
        member: discord.Member,
        roles: dict[int, discord.Role],
        now: datetime,
        actor: discord.Member | None,
    ) -> RankOutcome:
        guild = member.guild
        me = guild.me
        if (
            member.bot
            or member.id == guild.owner_id
            or member.joined_at is None
            or me is None
            or member.top_role >= me.top_role
            or (actor is not None and not self._actor_can_manage(member, actor))
        ):
            return "skipped"
        threshold = rank_threshold(tenure_days(member.joined_at, now), roles)
        desired = roles.get(threshold) if threshold is not None else None
        held = set(member.roles)
        obsolete = [role for role in roles.values() if role in held and role != desired]
        reason = "OSRSClan Discord tenure rank sync"
        added = desired is not None and desired not in held
        try:
            if added:
                await member.add_roles(desired, reason=reason)
            if obsolete:
                await member.remove_roles(*obsolete, reason=reason)
        except discord.HTTPException as error:
            log.warning("Discord rejected an OSRSClan rank change in guild %s (HTTP %s).", guild.id, error.status)
            return "failed"
        return "updated" if added or obsolete else "unchanged"

    async def _sync(
        self,
        guild: discord.Guild,
        *,
        target: discord.Member | None = None,
        actor: discord.Member | None = None,
        automatic: bool = False,
    ) -> Counter[RankOutcome]:
        counts: Counter[RankOutcome] = Counter()
        async with self._locks[guild.id]:
            settings: ClanSettings = await self.config.guild(guild).all()
            if automatic and not settings["auto_rank"]:
                return counts
            group_id = self._group_id(settings)
            roles = self._rank_roles(guild, settings["ranks"], actor)
            if not roles:
                raise commands.UserFeedbackCheckFailure("Configure at least one rank threshold before syncing.")
            self._check_sync(guild)
            candidates = [str(target.id)] if target is not None else settings["links"]
            if not candidates:
                return counts
            roster = await self.temple.roster(group_id)
            now = discord.utils.utcnow()
            for member_id in candidates:
                rsn = settings["links"].get(member_id)
                member = guild.get_member(int(member_id))
                if member is None or rsn is None or rsn.casefold() not in roster:
                    counts["skipped"] += 1
                    continue
                counts[await self._apply_rank(member, roles, now, actor)] += 1
        return counts

    @tasks.loop(hours=1)
    async def rank_loop(self) -> None:
        for guild in self.bot.guilds:
            if not await self.config.guild(guild).auto_rank() or await self.bot.cog_disabled_in_guild(self, guild):
                continue
            try:
                await self._sync(guild, automatic=True)
            except (TempleError, commands.UserFeedbackCheckFailure) as error:
                log.warning("Skipped OSRSClan rank sync for guild %s: %s", guild.id, error)

    @rank_loop.before_loop
    async def before_rank_loop(self) -> None:
        await self.bot.wait_until_red_ready()

    @commands.group(name="osrsclan", invoke_without_command=True)
    async def osrsclan(self, ctx: commands.Context) -> None:
        """Look up Old School RuneScape clan stats and Discord ranks."""
        await ctx.send_help()

    @osrsclan.command(name="overview")
    @commands.cooldown(2, 10, commands.BucketType.guild)
    async def overview(self, ctx: commands.Context) -> None:
        """Show the TempleOSRS group's tracked membership, XP, and efficiency totals."""
        group_id = self._group_id(await self.config.guild(ctx.guild).all())
        overview = await self.temple.overview(group_id)
        lines = [
            f"**{_text(overview.name)}** — TempleOSRS group {group_id}",
            f"Members: **{overview.member_count:,}** | Total XP: **{_metric(overview.xp)}**",
            f"Total EHP: **{_metric(overview.ehp, decimals=2)}** | Total EHB: **{_metric(overview.ehb, decimals=2)}**",
            f"<{group_url(group_id)}>",
        ]
        await self._send(ctx, "\n".join(lines))

    @osrsclan.command(name="roster")
    @commands.cooldown(3, 10, commands.BucketType.guild)
    async def roster(self, ctx: commands.Context, page: int = 1) -> None:
        """List TempleOSRS group members, 20 per page."""
        group_id = self._group_id(await self.config.guild(ctx.guild).all())
        if page < 1:
            raise commands.UserFeedbackCheckFailure("Page numbers start at 1.")
        names = sorted((await self.temple.roster(group_id)).values(), key=str.casefold)
        pages = max(1, (len(names) + PAGE_SIZE - 1) // PAGE_SIZE)
        if page > pages:
            raise commands.UserFeedbackCheckFailure(f"Choose a page from 1 to {pages}.")
        start = (page - 1) * PAGE_SIZE
        body = "\n".join(f"{index}. {_text(name)}" for index, name in enumerate(names[start : start + PAGE_SIZE], start + 1))
        title = f"**TempleOSRS roster** ({len(names):,} members; page {page}/{pages})"
        await self._send(ctx, f"{title}\n{body or 'No members tracked.'}")

    @osrsclan.command(name="stats")
    @commands.cooldown(3, 10, commands.BucketType.guild)
    async def player_stats(self, ctx: commands.Context, *, rsn: str | None = None) -> None:
        """Show stats for a roster RSN, or for your approved account if omitted."""
        settings: ClanSettings = await self.config.guild(ctx.guild).all()
        group_id = self._group_id(settings)
        rsn = rsn if rsn is not None else settings["links"].get(str(ctx.author.id))
        if rsn is None:
            raise commands.UserFeedbackCheckFailure("Enter an RSN, or ask clan staff to link your account.")
        canonical = await self._roster_name(group_id, rsn)
        stats = await self.temple.player(canonical)
        lines = [
            f"**{_text(canonical)}** — TempleOSRS stats",
            f"Total level: **{_metric(stats.level)}** | Total XP: **{_metric(stats.xp)}**",
            f"Primary EHP: **{_metric(stats.ehp, decimals=2)}** | Primary EHB: **{_metric(stats.ehb, decimals=2)}**",
        ]
        if stats.date:
            lines.append(f"Temple data point: {_text(stats.date)}")
        lines.append(f"<{player_url(canonical)}>")
        await self._send(ctx, "\n".join(lines))

    @osrsclan.command(name="tenure", ignore_extra=False)
    async def tenure(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        """Show current Discord server tenure and its rank threshold."""
        member = member or ctx.author
        if member.joined_at is None:
            raise commands.UserFeedbackCheckFailure("Discord has not provided a join date for this member.")
        settings: ClanSettings = await self.config.guild(ctx.guild).all()
        days = tenure_days(member.joined_at, discord.utils.utcnow())
        ranks = {int(threshold): role_id for threshold, role_id in settings["ranks"].items()}
        current = rank_threshold(days, ranks)
        following = min((threshold for threshold in ranks if threshold > days), default=None)
        lines = [
            f"**{_text(member.display_name)}** joined {discord.utils.format_dt(member.joined_at, 'D')} ({days:,} full days ago).",
            "Tenure tier: " + (self._role_label(ctx.guild, ranks[current]) if current is not None else "None"),
        ]
        if following is not None:
            next_date = discord.utils.format_dt(member.joined_at + timedelta(days=following), "R")
            lines.append(f"Next tier: {self._role_label(ctx.guild, ranks[following])} at {following:,} full days ({next_date}).")
        linked = str(member.id) in settings["links"]
        lines.append(
            "Rank sync also requires current TempleOSRS roster membership."
            if linked
            else "No approved OSRS account link. Ask clan staff to link an account before rank sync."
        )
        await self._send(ctx, "\n".join(lines))

    @osrsclan.command(name="ranks")
    async def ranks(self, ctx: commands.Context) -> None:
        """List the server's tenure thresholds and automatic sync setting."""
        settings: ClanSettings = await self.config.guild(ctx.guild).all()
        lines = [f"**OSRS clan ranks** (automatic sync: {'on' if settings['auto_rank'] else 'off'})"]
        lines.extend(
            f"{days:>4} days: {self._role_label(ctx.guild, role_id)}"
            for days, role_id in sorted(settings["ranks"].items(), key=lambda item: int(item[0]))
        )
        if not settings["ranks"]:
            lines.append("No thresholds configured.")
        await self._send(ctx, "\n".join(lines))

    @osrsclan.group(name="set", invoke_without_command=True)
    @commands.has_guild_permissions(manage_guild=True, manage_roles=True)
    async def clan_settings(self, ctx: commands.Context) -> None:
        """Configure the Temple group, approved links, and role sync."""
        await ctx.send_help()

    @clan_settings.command(name="group")
    async def set_group(self, ctx: commands.Context, group_id: commands.Range[int, 1, 2147483647]) -> None:
        """Set the public numeric TempleOSRS group ID."""
        overview = await self.temple.overview(group_id)
        async with self._locks[ctx.guild.id], self.config.guild(ctx.guild).all() as settings:
            changed = settings["group_id"] != group_id
            settings["group_id"] = group_id
            if changed:
                settings["auto_rank"] = False
        suffix = " Automatic rank sync is off until you enable it." if changed else ""
        await self._send(ctx, f"TempleOSRS group: **{_text(overview.name)}** ({group_id}).{suffix}")

    @clan_settings.command(name="link")
    async def approve_link(self, ctx: commands.Context, member: discord.Member, *, rsn: str) -> None:
        """Approve or update a member's verified, unique OSRS account link."""
        if member.bot:
            raise commands.UserFeedbackCheckFailure("Bots cannot be linked.")
        if not self._actor_can_manage(member, ctx.author):
            raise commands.UserFeedbackCheckFailure("You can only link yourself or a member below your highest role.")
        async with self._locks[ctx.guild.id]:
            conf = self.config.guild(ctx.guild)
            settings: ClanSettings = await conf.all()
            group_id = self._group_id(settings)
            self._rank_roles(ctx.guild, settings["ranks"], ctx.author)
            canonical = await self._roster_name(group_id, rsn)
            member_id = str(member.id)
            async with conf.links() as links:
                if any(other != member_id and name.casefold() == canonical.casefold() for other, name in links.items()):
                    raise commands.UserFeedbackCheckFailure("That RSN is already linked to another Discord member.")
                links[member_id] = canonical
        await self._send(ctx, f"Linked **{_text(member.display_name)}** to **{_text(canonical)}**.")

    @clan_settings.command(name="unlink")
    async def remove_link(self, ctx: commands.Context, user: int | discord.User) -> None:
        """Remove an approved link by Discord mention or user ID, even after departure."""
        user_id = user if isinstance(user, int) else user.id
        async with self._locks[ctx.guild.id], self.config.guild(ctx.guild).links() as links:
            removed = links.pop(str(user_id), None)
        if removed is None:
            raise commands.UserFeedbackCheckFailure("That Discord user has no approved link.")
        await self._send(ctx, "Removed the approved link. Existing Discord roles were not changed.")

    @clan_settings.command(name="rank")
    async def set_rank(self, ctx: commands.Context, days: commands.Range[int, 0, 36500], role: discord.Role) -> None:
        """Map a minimum full-day threshold to one dedicated Discord role."""
        self._check_role(ctx.guild, role, ctx.author)
        async with self._locks[ctx.guild.id], self.config.guild(ctx.guild).ranks() as ranks:
            key = str(days)
            if key not in ranks and len(ranks) >= MAX_RANKS:
                raise commands.UserFeedbackCheckFailure(f"Configure at most {MAX_RANKS} rank thresholds.")
            if any(other != key and role_id == role.id for other, role_id in ranks.items()):
                raise commands.UserFeedbackCheckFailure("A role can only be used at one threshold.")
            previous = ranks.get(key)
            ranks[key] = role.id
        suffix = " Remove the old role manually from members who still have it." if previous not in (None, role.id) else ""
        await self._send(ctx, f"At {days:,} full days: {role.mention}.{suffix}")

    @clan_settings.command(name="unrank")
    async def remove_rank(self, ctx: commands.Context, days: int) -> None:
        """Remove a threshold without removing the old Discord role from anyone."""
        async with self._locks[ctx.guild.id], self.config.guild(ctx.guild).all() as settings:
            previous = settings["ranks"].pop(str(days), None)
            if previous is not None and not settings["ranks"]:
                settings["auto_rank"] = False
        if previous is None:
            raise commands.UserFeedbackCheckFailure("No rank is mapped at that threshold.")
        await self._send(ctx, "Removed the rank mapping. Remove its old role manually from members who still have it.")

    @clan_settings.command(name="autorank")
    async def set_automatic(self, ctx: commands.Context, enabled: bool) -> None:
        """Turn hourly rank sync on or off. Manual sync remains available."""
        async with self._locks[ctx.guild.id]:
            conf = self.config.guild(ctx.guild)
            if enabled:
                settings: ClanSettings = await conf.all()
                self._group_id(settings)
                if not self._rank_roles(ctx.guild, settings["ranks"], ctx.author):
                    raise commands.UserFeedbackCheckFailure("Configure at least one rank threshold before enabling auto-rank.")
                self._check_sync(ctx.guild)
            await conf.auto_rank.set(enabled)
        await self._send(ctx, f"Automatic rank sync is {'on' if enabled else 'off'}.")

    @clan_settings.command(name="sync", ignore_extra=False)
    async def sync_ranks(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        """Reconcile the rank of one approved member, or all approved links."""
        async with ctx.typing():
            counts = await self._sync(ctx.guild, target=member, actor=ctx.author)
        await self._send(
            ctx,
            f"Rank sync: {counts['updated']} updated, {counts['unchanged']} already correct, "
            f"{counts['skipped']} skipped, {counts['failed']} failed.\n"
            "Skipped members are absent, unlinked, off the Temple roster, or not manageable. "
            "For failed changes, check the bot logs and Discord role hierarchy.",
        )
