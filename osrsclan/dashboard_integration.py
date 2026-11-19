"""Optional read-only Red-Web-Dashboard view for clan setup."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Any

from redbot.core import commands

from .temple import group_url

if TYPE_CHECKING:
    import discord


def dashboard_page(func):
    func.__dashboard_decorator_params__ = (
        (),
        {"name": None, "description": "Review TempleOSRS clan and rank settings.", "methods": ("GET",)},
    )
    return func


class DashboardIntegration:
    @commands.Cog.listener()
    async def on_dashboard_cog_add(self, dashboard_cog: commands.Cog) -> None:
        handler = dashboard_cog.rpc.third_parties_handler
        try:
            handler.add_third_party(self, overwrite=True)
        except TypeError:
            handler.add_third_party(self)

    @dashboard_page
    async def dashboard_page(self, user: discord.User, guild: discord.Guild, **_kwargs: Any) -> dict[str, Any]:
        member = guild.get_member(user.id)
        if member is None or not (member.guild_permissions.manage_guild and member.guild_permissions.manage_roles):
            return {
                "status": 1,
                "error_title": "Insufficient Permissions",
                "error_message": "Manage Server and Manage Roles are required.",
            }
        settings = await self.config.guild(guild).all()
        group_id = settings["group_id"]
        group = f'<a href="{group_url(group_id)}">TempleOSRS group {group_id}</a>' if group_id else "Not configured"
        ranks = []
        for days, role_id in sorted(settings["ranks"].items(), key=lambda item: int(item[0])):
            role = guild.get_role(role_id)
            ranks.append(f"<li>{days} days: {escape(role.name) if role else 'Deleted role'}</li>")
        items = "".join(ranks) or "<li>No thresholds configured.</li>"
        enabled = "On" if settings["auto_rank"] else "Off"
        return {
            "status": 0,
            "web_content": {
                "source": (
                    f"<h2>OSRS Clan</h2><p>Group: {group}</p><p>Hourly rank sync: {enabled}</p>"
                    f"<p>Approved links: {len(settings['links'])}</p><h3>Rank thresholds</h3><ul>{items}</ul>"
                    "<p>Use <code>osrsclan set</code> in Discord to change settings or sync roles.</p>"
                )
            },
        }
