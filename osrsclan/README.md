# OSRS Clan

`osrsclan` is a Red-DiscordBot cog for **Old School RuneScape** clans. It reads a public TempleOSRS group's stats and roster, looks up individual clan members, and can manage Discord rank roles based on how long those members have been in the server. It does not change in-game clan ranks or write to TempleOSRS.

## Requirements

- **Red-DiscordBot 3.5+** and **Python 3.10+** (use a Python version supported by your Red installation).
- **`aiohttp`**, installed by Red's Downloader. The bot host needs HTTPS access to `templeosrs.com`. No API key, Temple login, or group password is required.
- A **public TempleOSRS group** with the members you want to track. Temple is the source of truth for the roster; this cog does not import it from Jagex.
- Discord permission to **View Channel** and **Send Messages** in command channels. For either manual or hourly role sync, enable **Server Members Intent** for the bot in the Discord Developer Portal, restart Red, and give the bot **Manage Roles**. Stats-only use does not need Manage Roles on the bot.
- Staff who run `osrsclan set` need both **Manage Server** and **Manage Roles**. Put the dedicated clan rank roles below the bot's highest role and below the staff member's highest role. Leave all server permissions off on these roles; use separate roles for moderation and access control.

## Install

Replace `[p]` with your bot's prefix, such as `!`. Run the following as a Red bot owner once the cog is available on the repository's default branch:

```text
[p]load downloader
[p]repo add fourwolvescogs https://github.com/fourwolves/TaakosCogs
[p]cog install fourwolvescogs osrsclan
[p]load osrsclan
```

If you already added that repository, use its existing alias and skip `repo add`. To preview a branch, use a new alias and pass the branch name after the repository URL in `repo add`.

## Quick setup

1. **Find your group ID.** Open your clan's TempleOSRS overview page (for example, `https://templeosrs.com/groups/overview.php?id=1234`) and copy the number after `id=`. Replace `1234` below with your actual ID:

   ```text
   [p]osrsclan set group 1234
   [p]osrsclan overview
   [p]osrsclan roster
   ```

   This is enough for stats and roster lookups. XP and efficiency numbers are Temple snapshots, not live hiscores. EHP means efficient hours played; EHB means efficient hours bossed.

2. **Choose rank thresholds.** Create dedicated Discord roles (no server permissions), then map each to a minimum number of full days in the Discord server. `0` is an optional entry rank. Replace the sample role mentions with your own:

   ```text
   [p]osrsclan set rank 0 @Recruit
   [p]osrsclan set rank 30 @Member
   [p]osrsclan set rank 90 @Veteran
   ```

3. **Approve a link.** Confirm the player owns the RuneScape account out of band (for example, by asking them to message you in game). Run the link command in a staff channel so the Discord-to-RSN mapping is not published unnecessarily. Use the actual Discord member mention and in-game display name; names with spaces do not need quotes:

   ```text
   [p]osrsclan set link @Alice Example RSN
   [p]osrsclan stats Example RSN
   [p]osrsclan tenure @Alice
   ```

   The cog requires the name to appear on the Temple roster and allows only one Discord account per RSN in this server. Roster membership is not proof of account ownership, which is why members cannot link themselves.

4. **Try manual sync before turning on automation.** Start with one non-staff member below the bot in the role hierarchy. Check the result in Discord. Then either keep syncing manually or opt into hourly sync:

   ```text
   [p]osrsclan set sync @Alice
   [p]osrsclan set autorank on
   ```

## Member commands

| Command | What it does |
| --- | --- |
| `[p]osrsclan` | List commands and help. |
| `[p]osrsclan overview` | Show the group's member count, total XP, and available EHP/EHB totals, with a Temple link. |
| `[p]osrsclan roster [page]` | List Temple roster names alphabetically, 20 per page. |
| `[p]osrsclan stats [RSN]` | Show a roster member's total level, XP, EHP, EHB, and available data-point date. Omit the RSN to look up your approved account. |
| `[p]osrsclan tenure [@member]` | Show the Discord join date, full days, current tenure tier, and next milestone. Defaults to you. This is a tenure estimate of the tier, not confirmation of Temple roster eligibility or a role assignment. |
| `[p]osrsclan ranks` | List the server's thresholds and whether automatic sync is on. |

## Staff commands

| Command | What it does |
| --- | --- |
| `[p]osrsclan set group <group ID>` | Validate and set the Temple group. Changing the ID switches auto-rank off; existing links are kept and must match the new roster to sync. |
| `[p]osrsclan set rank <days> <@role>` | Add or replace a threshold. Up to 20 distinct thresholds are supported, and each role can be mapped to only one. |
| `[p]osrsclan set unrank <days>` | Remove a threshold; does not remove its former role from Discord members. Removing the last threshold switches auto-rank off. |
| `[p]osrsclan set link <@member> <RSN>` | Approve or update a member's account link after verifying ownership. Use it again after an OSRS name change. |
| `[p]osrsclan set unlink <@user or user ID>` | Delete the link, including after the member leaves. Does not remove existing Discord roles. |
| `[p]osrsclan set sync [@member]` | Apply ranks to one approved member or, without an argument, all approved links. Reports updated, unchanged, skipped, and failed counts. |
| `[p]osrsclan set autorank <on/off>` | Enable or disable hourly sync. Manual sync works with this setting off. |

If Red-Web-Dashboard is installed, staff can also **review** the Temple group, rank thresholds, link count, and sync setting there. Changes and role sync are performed in Discord.

## How rank sync works

- **Tenure uses Discord's current server join timestamp.** Every 24 hours counts as one full day, including time before the cog was installed or while Red was offline. Leaving and rejoining resets the clock. OSRS account age, time in the in-game clan, and Discord message activity do not count.
- A linked human member must still be in the Discord server and on the configured Temple roster. Sync uses the highest threshold the member meets. It adds that role before removing other currently mapped rank roles. An eligible linked member below the first threshold has no mapped rank role. Unrelated Discord roles are never changed.
- Sync skips departed or unlinked members, members off the Temple roster, bots, the server owner, members without a join date, and members outside the applicable role hierarchy. If roster lookup fails, no roles are changed. Sync does not revoke a role just because a link or Temple membership disappears; staff should manually remove obsolete roles when offboarding members or replacing a rank mapping.
- Automatic sync is **off by default** and runs hourly after Red is ready when enabled. A member crossing a threshold won't see the new role until the next successful sync. Run `set sync @member` for an immediate check.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Group or player not found | Check the numeric group ID and RSN on Temple's website. A newly added player may not have a recorded data point yet. |
| Member is on Discord but missing from the roster | Add or rename the player in TempleOSRS first, then run `set link` if necessary. Group and player responses are cached for reuse up to 60 seconds. |
| Rank was skipped or didn't change | Check that the account is approved and on the Temple roster, that the member has reached the threshold, and that the member and role are below the bot's top role. The `tenure` command reports the milestone independently of role sync. |
| Sync says the member cache isn't ready | Enable Server Members Intent in the Discord Developer Portal, ensure Red was not started with that intent disabled, and wait for Red to finish loading the server's members. |
| Rank configuration error | Give the bot Manage Roles and check that all mapped roles are ordinary, permissionless roles below the bot. Replace or remove deleted mappings before retrying. Check Red's logs if Discord rejects a role change. |
| TempleOSRS is unreachable or rate limited | Confirm the bot host can connect to `templeosrs.com` and wait before trying again. The cog uses request timeouts and backs off on HTTP 429. Stats may be temporarily unavailable; join dates still work. |

## Data and privacy

Red stores per-server settings (Temple group ID, rank thresholds, Discord role IDs, auto-rank setting) and approved Discord user ID-to-RSN links. Join dates are read from Discord on demand, not stored by the cog. Temple receives only group IDs and RSNs for public API lookups, not Discord IDs or join dates. The cog supports Red's user data export and deletion hooks. Unlinking or deleting a record does not remove Discord roles; remove those separately if needed.

See the [TempleOSRS API documentation](https://templeosrs.com/api_doc.php) for the read-only Group Information, Group Member Information, and Player Stats endpoints.

## What to add next

- **Weekly leaderboards and competitions:** Post XP, EHP, or EHB gain summaries and standings for clan events.
- **Link requests and rename review:** Let members submit a request to staff instead of coordinating through messages. Add a review step for roster departures and name changes.
- **Rank preview and audit channel:** Show planned role changes before a bulk sync and optionally post promotion or offboarding notices in a staff channel.
- **More rank criteria:** Allow optional skill totals, boss kill counts, or event participation alongside tenure, without changing the existing tenure-only default.
