"""Install the OSRS clan management cog."""

from .osrsclan import OSRSClan


async def setup(bot):
    await bot.add_cog(OSRSClan(bot))
