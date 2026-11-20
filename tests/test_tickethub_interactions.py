"""Behavioral coverage for component-free TicketHub flows."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from redbot.core import commands

from tickethub.alternative_interactions import CHOICE_EMOJIS, CONTROL_REACTIONS
from tickethub.tickethub import (
    MODAL_SELECTS_SUPPORTED,
    TicketCloseReasonModal,
    TicketHub,
    TicketOpenModal,
    TicketReopenReasonModal,
)


def cog():
    instance = object.__new__(TicketHub)
    instance._conversation_tasks = {}
    instance._reaction_busy = set()
    instance._locks = {}
    return instance


@pytest.mark.parametrize("style", ["text", "reaction", "button", "dropdown"])
def test_profile_preserves_all_panel_modes(style):
    assert TicketHub._merge_profile({"panel_style": style})["panel_style"] == style


def test_legacy_profiles_keep_components_and_invalid_modes_are_normalized():
    for raw in (None, {"form_mode": "bogus", "control_mode": "bogus"}):
        profile = TicketHub._merge_profile(raw)
        assert profile["form_mode"] == "modal"
        assert profile["control_mode"] == "buttons"


@pytest.mark.parametrize(
    "field,value,answer",
    [
        ({"type": "choice", "choices": ["Billing", "Support"]}, "2", "Support"),
        ({"type": "choice", "choices": ["Billing", "Support"]}, "billing", "Billing"),
        ({"type": "boolean"}, "no", "No"),
        ({"required": False}, "skip", ""),
        ({"default": "Saved answer"}, "default", "Saved answer"),
        ({"min_length": 2, "max_length": 4}, " test ", "test"),
    ],
)
def test_dm_answer_validation(field, value, answer):
    assert TicketHub._form_answer(field, value) == answer


@pytest.mark.parametrize(
    "field,value",
    [
        ({"required": True}, "skip"),
        ({"min_length": 3}, "hi"),
        ({"max_length": 2}, "long"),
        ({"type": "boolean"}, "maybe"),
        ({"type": "choice", "choices": ["A", "B"]}, "3"),
    ],
)
def test_invalid_answers_do_not_advance(field, value):
    with pytest.raises(ValueError):
        TicketHub._form_answer(field, value)


def test_cancel_aborts_intake():
    with pytest.raises(commands.CommandError, match="cancelled"):
        TicketHub._form_answer({}, "cancel")


def test_dm_intake_passes_answers_to_existing_creation_service_and_cleans_session():
    async def run():
        instance = cog()
        profile = {"form_mode": "text", "creating_modal": [{"label": "Reason", "required": True}]}
        instance._validate_ticket_open_request = AsyncMock(return_value=("main", profile))
        instance._ask_dm = AsyncMock(side_effect=[ValueError("too short"), "Need help"])
        instance._create_ticket = AsyncMock(return_value=({"id": 4}, "channel"))
        member = SimpleNamespace(id=2, send=AsyncMock())
        await instance._open_without_components("guild", member, "main")
        kwargs = instance._create_ticket.call_args.kwargs
        assert kwargs["form_answers"] == [{"label": "Reason", "value": "Need help"}]
        assert kwargs["reason"] == "Need help"
        assert not instance._conversation_tasks
        assert instance._ask_dm.await_count == 2

    asyncio.run(run())


@pytest.mark.parametrize("error", [asyncio.TimeoutError(), commands.CommandError("cancelled")])
def test_failed_questionnaire_never_creates_ticket(error):
    async def run():
        instance = cog()
        instance._validate_ticket_open_request = AsyncMock(return_value=("main", {"creating_modal": [{"label": "Why?"}]}))
        instance._ask_dm = AsyncMock(side_effect=error)
        instance._create_ticket = AsyncMock()
        with pytest.raises(commands.CommandError):
            await instance._open_without_components(None, SimpleNamespace(id=2), "main")
        instance._create_ticket.assert_not_called()
        assert not instance._conversation_tasks

    asyncio.run(run())


def test_duplicate_dm_conversation_is_rejected_without_removing_original():
    async def run():
        instance = cog()
        instance._conversation_tasks[2] = "original"
        with pytest.raises(commands.CommandError, match="existing"):
            await instance._open_without_components(None, SimpleNamespace(id=2), "main")
        assert instance._conversation_tasks[2] == "original"

    asyncio.run(run())


def reaction_fixture():
    instance = cog()
    member = SimpleNamespace(id=2, bot=False, send=AsyncMock())
    channel = SimpleNamespace(permissions_for=lambda _: SimpleNamespace(view_channel=True))
    guild = SimpleNamespace(id=1, get_member=lambda _: member, get_channel_or_thread=lambda _: channel)
    instance.bot = SimpleNamespace(
        get_guild=lambda _: guild,
        cog_disabled_in_guild=AsyncMock(return_value=False),
        allowed_by_whitelist_blacklist=AsyncMock(return_value=True),
    )
    instance._get_profiles = AsyncMock(return_value={"main": {"control_mode": "reaction"}})
    record = {"id": 7, "channel_id": 3, "message_id": 4, "profile": "main", "owner_id": 2}
    data = SimpleNamespace(multi_panels=AsyncMock(return_value={}), tickets=AsyncMock(return_value={"7": record}))
    instance.config = SimpleNamespace(guild=lambda _: data)
    instance._reaction_action = AsyncMock()
    payload = SimpleNamespace(guild_id=1, channel_id=3, message_id=4, user_id=2, emoji="✅")
    return instance, member, record, payload


def test_uncached_reaction_routes_registered_control_to_existing_action():
    async def run():
        instance, member, record, payload = reaction_fixture()
        await instance.on_raw_reaction_add(payload)
        assert instance._reaction_action.call_args.args[1:] == (record, member, "claim")
        assert not instance._reaction_busy

    asyncio.run(run())


@pytest.mark.parametrize("change", ["message", "channel", "mode", "bot", "disabled", "blacklist", "busy"])
def test_unrelated_or_disallowed_reactions_do_not_act(change):
    async def run():
        instance, member, _record, payload = reaction_fixture()
        if change in {"message", "channel"}:
            setattr(payload, change + "_id", 999)
        elif change == "mode":
            instance._get_profiles.return_value = {"main": {"control_mode": "text"}}
        elif change == "bot":
            member.bot = True
        elif change == "disabled":
            instance.bot.cog_disabled_in_guild.return_value = True
        elif change == "blacklist":
            instance.bot.allowed_by_whitelist_blacklist.return_value = False
        else:
            instance._reaction_busy.add((4, 2))
        await instance.on_raw_reaction_add(payload)
        instance._reaction_action.assert_not_called()

    asyncio.run(run())


def test_reaction_panel_opens_correct_profile_without_message_cache():
    async def run():
        instance, _member, _record, payload = reaction_fixture()
        instance._get_profiles.return_value = {
            "billing": {
                "panel_style": "reaction",
                "panel_channel_id": 3,
                "panel_message_id": 4,
            }
        }
        instance._open_without_components = AsyncMock(return_value=({}, SimpleNamespace(mention="#private")))
        payload.emoji = CHOICE_EMOJIS[0]
        await instance.on_raw_reaction_add(payload)
        assert instance._open_without_components.call_args.args[2] == "billing"

    asyncio.run(run())


def test_unauthorized_confirmation_cannot_call_close_service():
    async def run():
        instance = cog()
        instance._get_profile = AsyncMock(return_value={})
        instance._is_support_member = Mock(return_value=False)
        instance._resolve_close_confirmation = AsyncMock()
        record = {"profile": "main", "owner_id": 1, "pending_close": {"requested_by": 2}}
        with pytest.raises(commands.CommandError, match="Only"):
            await instance._confirm_without_components(None, record, SimpleNamespace(id=99), True)
        instance._resolve_close_confirmation.assert_not_called()

    asyncio.run(run())


def test_reaction_transcript_enforces_owner_or_support():
    async def run():
        instance = cog()
        instance._get_profile = AsyncMock(return_value={})
        instance._is_support_member = Mock(return_value=False)
        instance._send_transcript_bundle = AsyncMock()
        with pytest.raises(commands.CommandError, match="Only"):
            await instance._reaction_action(None, {"profile": "main", "owner_id": 1}, SimpleNamespace(id=99), "transcript")
        instance._send_transcript_bundle.assert_not_called()

    asyncio.run(run())


def test_reaction_panel_limit_does_not_affect_text_panels():
    instance = cog()
    record = {
        "message_id": 1,
        "channel_id": 2,
        "style": "reaction",
        "options": [{"profile": f"p{i}", "label": f"Profile {i}"} for i in range(21)],
    }
    with pytest.raises(commands.BadArgument, match="20"):
        instance._build_multi_panel_view(record)
    record["style"] = "text"
    assert instance._build_multi_panel_view(record) is None


def test_every_reaction_control_has_an_existing_text_command():
    names = {command.name for command in TicketHub.tickethub.commands}
    assert set(CONTROL_REACTIONS.values()) <= names
    assert {"confirmclose", "cancelclose"} <= names


@pytest.mark.parametrize("reaction", [False, True])
def test_dm_waiters_scope_replies_and_cancel_the_unused_listener(reaction):
    async def run():
        instance = cog()
        futures = {}
        checks = {}

        async def wait_for(event, *, check, timeout):
            assert timeout == 180
            checks[event] = check
            futures[event] = asyncio.get_running_loop().create_future()
            return await futures[event]

        prompt = SimpleNamespace(id=10, add_reaction=AsyncMock())
        dm = SimpleNamespace(id=8, send=AsyncMock(return_value=prompt))
        member = SimpleNamespace(id=2, create_dm=AsyncMock(return_value=dm))
        instance.bot = SimpleNamespace(wait_for=wait_for)
        task = asyncio.create_task(
            instance._ask_dm(member, {"label": "Choose", "type": "boolean"}, "reaction" if reaction else "text")
        )
        for _ in range(10):
            await asyncio.sleep(0)
            if "message" in checks and (not reaction or "raw_reaction_add" in checks):
                break
        assert not checks["message"](SimpleNamespace(author=SimpleNamespace(id=99), channel=dm, id=11))
        assert not checks["message"](SimpleNamespace(author=member, channel=SimpleNamespace(id=9), id=11))
        assert not checks["message"](SimpleNamespace(author=member, channel=dm, id=9))
        if reaction:
            payload = SimpleNamespace(user_id=2, message_id=10, emoji=CHOICE_EMOJIS[1])
            assert checks["raw_reaction_add"](payload)
            assert not checks["raw_reaction_add"](SimpleNamespace(user_id=99, message_id=10, emoji=CHOICE_EMOJIS[1]))
            futures["raw_reaction_add"].set_result(payload)
        else:
            futures["message"].set_result(SimpleNamespace(content="No"))
        assert await task == "No"
        assert all(f.done() for f in futures.values())

    asyncio.run(run())


def test_reaction_close_checks_permission_before_prompting_and_refetches_after_reply():
    async def run():
        instance = cog()
        instance._validate_close_request = AsyncMock()
        instance._validate_reopen_request = AsyncMock()
        instance._ask_dm = AsyncMock(return_value="Resolved")
        fresh = {"id": 7, "status": "open"}
        instance._get_ticket_record_by_id = AsyncMock(return_value=fresh)
        instance._start_close_confirmation = AsyncMock()
        member = SimpleNamespace(id=2)
        await instance._lifecycle_without_modal("guild", {"id": 7}, member, "close")
        instance._start_close_confirmation.assert_awaited_once_with("guild", fresh, member, "Resolved")
        assert not instance._conversation_tasks
        instance._validate_close_request.side_effect = commands.CommandError("Denied")
        instance._ask_dm.reset_mock()
        with pytest.raises(commands.CommandError, match="Denied"):
            await instance._lifecycle_without_modal("guild", {"id": 7}, member, "close")
        instance._ask_dm.assert_not_called()

    asyncio.run(run())


def test_reaction_setup_rejects_missing_permissions():
    instance = cog()
    instance.bot = SimpleNamespace(intents=SimpleNamespace(reactions=True))
    channel = SimpleNamespace(
        guild=SimpleNamespace(me=None),
        permissions_for=lambda _: SimpleNamespace(
            add_reactions=False,
            read_message_history=True,
        ),
    )
    with pytest.raises(commands.BadArgument, match="Add Reactions"):
        instance._check_reaction_channel(channel)


def test_close_service_preserves_scheduled_timeout_and_rejects_untrusted_actor():
    async def run():
        instance = cog()
        guild = SimpleNamespace(id=1)
        actor = SimpleNamespace(id=9)
        record = {"profile": "main", "owner_id": 1, "pending_close": {"requested_by": 2, "expires_at": 50}}
        instance._get_ticket_record_by_id = AsyncMock(return_value=record)
        instance._get_profile = AsyncMock(return_value={})
        instance._is_support_member = Mock(return_value=False)
        instance._close_ticket = AsyncMock()
        with pytest.raises(commands.CommandError, match="Only"):
            await instance._resolve_close_confirmation(guild, 7, actor, confirmed=True)
        instance._close_ticket.assert_not_called()
        with pytest.raises(commands.CommandError, match="replaced"):
            await instance._resolve_close_confirmation(
                guild, 7, actor, confirmed=True, expected_expires_at=49, permission_checked=True
            )
        instance._close_ticket.assert_not_called()
        await instance._resolve_close_confirmation(
            guild, 7, actor, confirmed=True, expected_expires_at=50, permission_checked=True
        )
        instance._close_ticket.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize(
    "raw_type,expected",
    [
        ("text", "text"),
        ("string", "text"),
        ("short", "text"),
        ("paragraph", "text"),
        ("text_display", "text_display"),
        ("textdisplay", "text_display"),
        ("display", "text_display"),
        ("info", "text_display"),
        ("notice", "text_display"),
        ("choice", "choice"),
        ("dropdown", "choice"),
        ("select", "choice"),
        ("string_select", "choice"),
        ("boolean", "boolean"),
        ("bool", "boolean"),
        ("yesno", "boolean"),
        ("checkbox", "checkbox"),
        ("check", "checkbox"),
        ("toggle", "checkbox"),
        ("checkbox_group", "checkbox_group"),
        ("checkboxgroup", "checkbox_group"),
        ("multi_choice", "checkbox_group"),
        ("radio", "radio"),
        ("radio_group", "radio"),
        ("radiogroup", "radio"),
        ("user", "user"),
        ("user_select", "user"),
        ("member", "user"),
        ("role", "role"),
        ("role_select", "role"),
        ("mentionable", "mentionable"),
        ("mentionable_select", "mentionable"),
        ("channel", "channel"),
        ("channel_select", "channel"),
        ("file", "file"),
        ("file_upload", "file"),
        ("fileupload", "file"),
        ("upload", "file"),
        ("attachment", "file"),
    ],
)
def test_modal_type_name_components_v2(raw_type, expected):
    assert TicketHub._modal_type_name(raw_type) == expected


def test_sanitize_modal_fields_components_v2():
    raw = [
        {
            "label": "Notice",
            "type": "text_display",
            "description": "Rules and info",
            "default": "Important rules",
            "required": True,
        },
        {
            "label": "Upload Logs",
            "type": "file",
            "description": "Screenshots or logs",
            "min_values": 1,
            "max_values": 5,
        },
        {
            "label": "Radio Choice",
            "type": "radio",
            "choices": ["Opt1", "Opt2", "Opt3"],
        },
        {
            "label": "Multi Select",
            "type": "checkbox_group",
            "choices": ["A", "B", "C"],
            "min_values": 1,
            "max_values": 2,
        },
        {
            "label": "Agree",
            "type": "checkbox",
            "default": "true",
        },
    ]
    sanitized = TicketHub._sanitize_modal_fields(raw)
    assert len(sanitized) == 5
    assert sanitized[0]["type"] == "text_display"
    assert sanitized[0]["required"] is False
    assert sanitized[0]["description"] == "Rules and info"
    assert sanitized[1]["type"] == "file"
    assert sanitized[1]["min_values"] == 1
    assert sanitized[1]["max_values"] == 5
    assert sanitized[2]["type"] == "radio"
    assert len(sanitized[2]["choices"]) == 3
    assert sanitized[3]["type"] == "checkbox_group"
    assert sanitized[3]["min_values"] == 1
    assert sanitized[3]["max_values"] == 2
    assert sanitized[4]["type"] == "checkbox"
    assert sanitized[4]["default"] == "true"


def test_modal_summary_lines_components_v2():
    fields = [
        {"label": "Notice", "type": "text_display", "description": "Read this"},
        {"label": "Pick", "type": "radio", "choices": ["A", "B"]},
        {"label": "Terms", "type": "checkbox", "default": "true"},
        {"label": "Target", "type": "user", "placeholder": "Member"},
        {"label": "Proof", "type": "file", "min_values": 1, "max_values": 3},
    ]
    lines = TicketHub._modal_summary_lines(fields)
    assert any("text display" in line for line in lines)
    assert any("radio, required, options: A, B" in line for line in lines)
    assert any("checkbox, default: checked" in line for line in lines)
    assert any("user select" in line for line in lines)
    assert any("file upload, required, files: 1-3" in line for line in lines)


def test_ticket_open_modal_components_v2_construction():
    fields_group_1 = [
        {"label": "Notice", "type": "text_display", "default": "Guidelines"},
        {"label": "Title", "type": "text", "description": "Short title", "style": 1},
        {"label": "Category", "type": "choice", "choices": ["Billing", "Technical"], "description": "Select type"},
        {"label": "Upload", "type": "file", "description": "Attach file", "min_values": 0, "max_values": 2},
        {"label": "Urgency", "type": "radio", "choices": ["Low", "High"], "default": "Low"},
    ]
    owner = SimpleNamespace(id=42)
    modal_1 = TicketOpenModal(cog(), None, owner, "main", fields_group_1)
    payload_1 = modal_1.to_dict()
    assert payload_1["title"] == "Open Ticket"
    assert len(payload_1["components"]) == 5
    assert payload_1["components"][0]["type"] == 10
    assert payload_1["components"][0]["content"] == "Guidelines"
    assert payload_1["components"][1]["type"] == 18
    assert payload_1["components"][1]["component"]["type"] == 4
    assert payload_1["components"][1]["description"] == "Short title"
    assert payload_1["components"][2]["type"] == 18
    assert payload_1["components"][2]["component"]["type"] == 3
    assert payload_1["components"][3]["type"] == 18
    assert payload_1["components"][3]["component"]["type"] == 19
    assert payload_1["components"][4]["type"] == 18
    assert payload_1["components"][4]["component"]["type"] == 21

    fields_group_2 = [
        {"label": "Interests", "type": "checkbox_group", "choices": ["News", "Updates"], "default": "News"},
        {"label": "Confirmed", "type": "checkbox", "default": "true"},
        {"label": "Assignee", "type": "user"},
        {"label": "Team", "type": "role"},
        {"label": "Location", "type": "channel"},
    ]
    modal_2 = TicketOpenModal(cog(), None, owner, "main", fields_group_2)
    payload_2 = modal_2.to_dict()
    assert len(payload_2["components"]) == 5
    assert payload_2["components"][0]["component"]["type"] == 22
    assert payload_2["components"][1]["component"]["type"] == 23
    assert payload_2["components"][2]["component"]["type"] == 5
    assert payload_2["components"][3]["component"]["type"] == 6
    assert payload_2["components"][4]["component"]["type"] == 8


def test_ticket_open_modal_input_value_extraction():
    # 1. TextDisplay
    td = discord.ui.TextDisplay("Guideline text")
    assert TicketOpenModal._input_value(td) == ""

    # 2. TextInput
    ti = discord.ui.TextInput(label="Question")
    ti._value = "My response"
    assert TicketOpenModal._input_value(ti) == "My response"

    # 3. Checkbox
    cb = discord.ui.Checkbox()
    cb._value = True
    assert TicketOpenModal._input_value(cb) == "Yes"
    cb._value = False
    assert TicketOpenModal._input_value(cb) == "No"

    # 4. RadioGroup
    rg = discord.ui.RadioGroup(options=[discord.RadioGroupOption(label="OptA")])
    rg._value = "OptA"
    assert TicketOpenModal._input_value(rg) == "OptA"

    # 5. CheckboxGroup
    cg = discord.ui.CheckboxGroup(options=[discord.CheckboxGroupOption(label="OptB")])
    cg._values = ["OptB", "OptC"]
    assert TicketOpenModal._input_value(cg) == "OptB, OptC"

    # 6. FileUpload
    fu = discord.ui.FileUpload()
    att = SimpleNamespace(filename="test.png", url="https://cdn.example.com/test.png")
    fu._values = [att]
    assert TicketOpenModal._input_value(fu) == "[test.png](https://cdn.example.com/test.png)"

    # 7. UserSelect
    us = discord.ui.UserSelect()
    us._values = [discord.Object(id=111), discord.Object(id=222)]
    assert TicketOpenModal._input_value(us) == "<@111> <@222>"

    # 8. RoleSelect
    rs = discord.ui.RoleSelect()
    rs._values = [discord.Object(id=333)]
    assert TicketOpenModal._input_value(rs) == "<@&333>"

    # 9. ChannelSelect
    cs = discord.ui.ChannelSelect()
    cs._values = [discord.Object(id=444)]
    assert TicketOpenModal._input_value(cs) == "<#444>"

    # 10. StringSelect
    sel = discord.ui.Select(options=[discord.SelectOption(label="S")])
    sel._values = ["ValA", "ValB"]
    assert TicketOpenModal._input_value(sel) == "ValA, ValB"


def test_reason_modals_use_label_v2():
    close_modal = TicketCloseReasonModal(cog(), 1, 2, 3)
    dict_close = close_modal.to_dict()
    assert len(dict_close["components"]) == 1
    if MODAL_SELECTS_SUPPORTED:
        assert dict_close["components"][0]["type"] == 18
        assert dict_close["components"][0]["label"] == "Reason for closing"

    reopen_modal = TicketReopenReasonModal(cog(), 1, 2, 3)
    dict_reopen = reopen_modal.to_dict()
    assert len(dict_reopen["components"]) == 1
    if MODAL_SELECTS_SUPPORTED:
        assert dict_reopen["components"][0]["type"] == 18
        assert dict_reopen["components"][0]["label"] == "Reason for reopening"


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ({"type": "text_display"}, "anything", ""),
        ({"type": "checkbox"}, "yes", "Yes"),
        ({"type": "checkbox"}, "0", "No"),
        ({"type": "radio", "choices": ["Low", "High"]}, "1", "Low"),
        ({"type": "radio", "choices": ["Low", "High"]}, "high", "High"),
        ({"type": "checkbox_group", "choices": ["Alpha", "Beta"]}, "1, 2", "Alpha, Beta"),
        ({"type": "checkbox_group", "choices": ["Alpha", "Beta"]}, "alpha", "Alpha"),
        ({"type": "user"}, "<@123456789012345678>", "<@123456789012345678>"),
        ({"type": "user"}, "123456789012345678", "<@123456789012345678>"),
        ({"type": "role"}, "<@&123456789012345678>", "<@&123456789012345678>"),
        ({"type": "role"}, "123456789012345678", "<@&123456789012345678>"),
        ({"type": "channel"}, "<#123456789012345678>", "<#123456789012345678>"),
        ({"type": "channel"}, "123456789012345678", "<#123456789012345678>"),
    ],
)
def test_dm_answer_validation_components_v2(field, value, expected):
    assert TicketHub._form_answer(field, value) == expected


def test_dm_intake_skips_text_display_prompts():
    async def run():
        instance = cog()
        profile = {
            "form_mode": "text",
            "creating_modal": [
                {"label": "Guidelines", "type": "text_display", "default": "Be polite"},
                {"label": "Reason", "type": "text", "required": True},
            ],
        }
        instance._validate_ticket_open_request = AsyncMock(return_value=("main", profile))
        instance._ask_dm = AsyncMock(return_value="Issue details")
        instance._create_ticket = AsyncMock(return_value=({"id": 10}, "channel"))
        dm_channel = SimpleNamespace(send=AsyncMock())
        member = SimpleNamespace(id=5, create_dm=AsyncMock(return_value=dm_channel), send=AsyncMock())
        await instance._open_without_components("guild", member, "main")
        assert instance._ask_dm.await_count == 1
        dm_channel.send.assert_awaited_once()
        kwargs = instance._create_ticket.call_args.kwargs
        assert kwargs["form_answers"] == [{"label": "Reason", "value": "Issue details"}]

    asyncio.run(run())



def test_panel_embed_customization():
    instance = cog()
    guild = SimpleNamespace(name="Support Guild", icon=None)
    profile = {
        "panel_title": "Custom Help Desk",
        "panel_message": "Click below to contact an agent.",
        "panel_color": "#FF5733",
        "panel_footer": "24/7 Support",
        "panel_thumbnail_url": "https://example.com/thumb.png",
        "panel_image_url": "https://example.com/banner.png",
        "panel_show_details": False,
    }
    embed = instance._panel_embed(guild, "vip", profile)
    assert embed.title == "Custom Help Desk"
    assert embed.description == "Click below to contact an agent."
    assert embed.color.value == 0xFF5733
    assert embed.footer.text == "24/7 Support"
    assert embed.thumbnail.url == "https://example.com/thumb.png"
    assert embed.image.url == "https://example.com/banner.png"
    assert len(embed.fields) == 0

    profile["panel_show_details"] = True
    profile["max_open_tickets_by_member"] = 3
    embed_with_details = instance._panel_embed(guild, "vip", profile)
    assert len(embed_with_details.fields) == 2
    assert embed_with_details.fields[0].name == "Profile"
    assert embed_with_details.fields[0].value == "`vip`"
    assert embed_with_details.fields[1].name == "Max Open"
    assert embed_with_details.fields[1].value == "3"


def test_panel_view_for_style_custom_button():
    instance = cog()
    instance._panel_view = SimpleNamespace(is_default=True)
    instance._panel_select_view = SimpleNamespace(is_default=True)

    profile = {
        "panel_button_label": "Create Ticket",
        "panel_button_emoji": "🎫",
        "panel_button_style": "danger",
    }
    view = instance._panel_view_for_style("button", profile)
    button = view.children[0]
    assert button.label == "Create Ticket"
    assert button.emoji.name == "🎫"
    assert button.style == discord.ButtonStyle.danger
    assert button.custom_id == "taakoscogs:tickethub:open"

    default_profile = instance._default_profile()
    assert instance._panel_view_for_style("button", default_profile) is instance._panel_view


def test_panel_view_for_style_custom_dropdown():
    instance = cog()
    instance._panel_view = SimpleNamespace(is_default=True)
    instance._panel_select_view = SimpleNamespace(is_default=True)

    profile = {
        "panel_dropdown_placeholder": "Select your issue...",
        "panel_dropdown_label": "Billing Inquiry",
        "panel_dropdown_emoji": "💳",
        "panel_dropdown_description": "Invoices and payments",
    }
    view = instance._panel_view_for_style("dropdown", profile)
    select = view.children[0]
    assert select.placeholder == "Select your issue..."
    assert select.custom_id == "taakoscogs:tickethub:open-select"
    assert len(select.options) == 1
    assert select.options[0].label == "Billing Inquiry"
    assert select.options[0].emoji.name == "💳"
    assert select.options[0].description == "Invoices and payments"

    default_profile = instance._default_profile()
    assert instance._panel_view_for_style("dropdown", default_profile) is instance._panel_select_view


def test_ticketset_profile_panelappearance_command():
    async def run():
        instance = cog()
        profile = instance._default_profile()
        instance._ensure_profile = AsyncMock(return_value=profile)
        instance._set_profile = AsyncMock()
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), send=AsyncMock())
        cmd = instance.tickethub_profile_panel_appearance.callback

        await cmd(instance, ctx, "main", "color", value="#FF0000")
        assert profile["panel_color"] == 0xFF0000
        ctx.send.assert_awaited_with("Panel color set to `#FF0000` for `main`.")

        await cmd(instance, ctx, "main", "color", value="none")
        assert profile["panel_color"] is None

        await cmd(instance, ctx, "main", "buttonstyle", value="success")
        assert profile["panel_button_style"] == "success"
        await cmd(instance, ctx, "main", "buttonlabel", value="Need Support")
        assert profile["panel_button_label"] == "Need Support"

        await cmd(instance, ctx, "main", "showdetails", value="off")
        assert profile["panel_show_details"] is False

    asyncio.run(run())



def test_dashboard_modal_section_attributes_and_script():
    instance = cog()
    profile = instance._default_profile()
    profile["creating_modal"] = [
        {"label": "Summary", "type": "text", "style": 1, "required": True},
    ]
    html = instance._dashboard_modal_section("main", profile, "<csrf>")
    assert 'data-modal-card="1"' in html
    assert 'data-modal-enabled="1"' in html
    assert 'data-modal-type-select="1"' in html
    assert 'data-modal-field="style"' in html
    assert 'data-modal-field="text_length"' in html
    assert 'data-modal-field="choices"' in html
    assert 'data-modal-add-form="1"' in html

    script = instance._dashboard_tabs_script()
    assert "updateModalCard" in script
    assert "updatePanelStyle" in script
    assert "data-modal-type-select" in script
    assert "data-panel-style-select" in script


def test_dashboard_add_modal_question_min_max_length():
    async def run():
        instance = cog()
        profile = instance._default_profile()
        instance._ensure_profile = AsyncMock(return_value=profile)
        instance._set_profile = AsyncMock()

        form_data = {
            "selected_profile": "main",
            "add_modal_label": "Description",
            "add_modal_type": "text",
            "add_modal_style": "paragraph",
            "add_modal_required": "1",
            "add_modal_min_length": "10",
            "add_modal_max_length": "500",
        }
        await instance._dashboard_add_modal_question(SimpleNamespace(id=1), form_data)
        saved_fields = profile["creating_modal"]
        assert len(saved_fields) == 1
        assert saved_fields[0]["label"] == "Description"
        assert saved_fields[0]["min_length"] == 10
        assert saved_fields[0]["max_length"] == 500

    asyncio.run(run())


def test_dashboard_panels_section_customization_fields():
    instance = cog()
    profile = instance._default_profile()
    profile["panel_color"] = 0x5865F2
    profile["panel_button_style"] = "success"
    html = instance._dashboard_panels_section(
        SimpleNamespace(text_channels=[]),
        "main",
        profile,
        {},
        "<csrf>",
    )
    assert 'data-panel-form="1"' in html
    assert 'data-panel-style-select="1"' in html
    assert 'data-panel-component="button"' in html
    assert 'data-panel-component="dropdown"' in html
    assert 'name="post_panel_title"' in html
    assert 'name="post_panel_color"' in html
    assert 'name="post_panel_button_style"' in html
    assert 'name="post_panel_dropdown_placeholder"' in html


def test_dashboard_post_panel_updates_profile():
    async def run():
        instance = cog()
        profile = instance._default_profile()
        instance._ensure_profile = AsyncMock(return_value=profile)
        channel = Mock(spec=discord.TextChannel, id=123, mention="#support")
        guild = SimpleNamespace(id=1, get_channel=lambda cid: channel if cid == 123 else None)
        message = SimpleNamespace(id=456)
        instance._post_panel = AsyncMock(return_value=message)
        instance.config = SimpleNamespace(guild=lambda _: SimpleNamespace(enabled=SimpleNamespace(set=AsyncMock())))

        form_data = {
            "selected_profile": "main",
            "post_panel_channel_id": "123",
            "post_panel_style": "button",
            "post_panel_title": "VIP Support",
            "post_panel_color": "#00FF00",
            "post_panel_button_label": "Open VIP Ticket",
            "post_panel_button_style": "danger",
            "post_panel_show_details": "0",
        }
        name, msg = await instance._dashboard_post_panel(guild, form_data)
        assert name == "main"
        assert msg is message
        assert profile["panel_title"] == "VIP Support"
        assert profile["panel_color"] == 0x00FF00
        assert profile["panel_button_label"] == "Open VIP Ticket"
        assert profile["panel_button_style"] == "danger"
        assert profile["panel_show_details"] is False

    asyncio.run(run())


def test_dashboard_save_multi_panel_posts_new_message():
    async def run():
        instance = cog()
        sent_message = SimpleNamespace(id=999, channel=SimpleNamespace(id=123))
        channel = Mock(spec=discord.TextChannel, id=123, send=AsyncMock(return_value=sent_message))
        guild = SimpleNamespace(id=1, icon=None, name="Guild", get_channel=lambda cid: channel if cid == 123 else None)
        instance._get_profiles = AsyncMock(return_value={"main": instance._default_profile()})
        instance._save_multi_panel = AsyncMock()
        instance._set_profile = AsyncMock()
        instance.config = SimpleNamespace(guild=lambda _: SimpleNamespace(enabled=SimpleNamespace(set=AsyncMock())))

        form_data = {
            "multi_panel_channel_id": "123",
            "multi_panel_message_id": "",
            "multi_panel_title": "Multi Help",
            "multi_panel_message": "Pick an option",
            "multi_panel_color": "#123456",
            "multi_panel_style": "dropdown",
            "multi_panel_options": "main | 🎫 | Main Support | General help",
        }
        msg = await instance._dashboard_save_multi_panel(guild, form_data)
        assert msg is sent_message
        channel.send.assert_awaited_once()
        embed = channel.send.call_args.kwargs["embed"]
        assert embed.title == "Multi Help"
        assert embed.description == "Pick an option"
        assert embed.color.value == 0x123456

    asyncio.run(run())
