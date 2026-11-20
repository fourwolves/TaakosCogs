"""Red-Web-Dashboard integration for TicketHub."""

from __future__ import annotations

import contextlib
import html
import logging
import re
import typing

import discord
from redbot.core import commands

log = logging.getLogger("red.taakoscogs.tickethub.dashboard")

DEFAULT_PANEL_BUTTON_EMOJI = "\N{ENVELOPE WITH DOWNWARDS ARROW ABOVE}"


def dashboard_page(*args, **kwargs):
    """Dashboard page decorator compatible with Red-Web-Dashboard."""

    def decorator(func: typing.Callable):
        func.__dashboard_decorator_params__ = (args, kwargs)
        return func

    return decorator


class DashboardIntegration:
    """Dashboard integration mixin for TicketHub."""

    @commands.Cog.listener()
    async def on_dashboard_cog_add(self, dashboard_cog: commands.Cog) -> None:
        """Register TicketHub as a Red-Web-Dashboard third party."""
        handler = dashboard_cog.rpc.third_parties_handler
        try:
            handler.add_third_party(self, overwrite=True)
        except TypeError:
            handler.add_third_party(self)

    @dashboard_page(
        name=None,
        description="Configure TicketHub profiles, panels, tickets, and imports.",
        methods=("GET", "POST"),
    )
    async def dashboard_page(
        self,
        user: discord.User,
        guild: discord.Guild,
        **kwargs,
    ) -> dict[str, typing.Any]:
        """Render and process the TicketHub dashboard page."""
        member, can_manage = await self._dashboard_member_can_manage(user, guild)
        if not can_manage:
            return {
                "status": 1,
                "error_title": "Insufficient Permissions",
                "error_message": "You need Manage Server, Red admin, or bot owner access.",
            }

        notifications = []
        form_data = self._dashboard_form_data(kwargs)
        selected_profile = self._dash_value(
            form_data,
            "selected_profile",
        ) or self._dash_value(
            form_data,
            "profile_name",
        )

        if kwargs.get("method", "GET") == "POST":
            action = self._dash_value(form_data, "action")
            try:
                selected_profile, messages = await self._dashboard_handle_action(
                    guild,
                    member,
                    action,
                    form_data,
                    selected_profile,
                )
            except commands.CommandError as error:
                notifications.append({"message": str(error), "category": "error"})
            except Exception as error:
                log.exception("TicketHub dashboard action failed.")
                notifications.append(
                    {
                        "message": f"TicketHub dashboard action failed: {error}",
                        "category": "error",
                    },
                )
            else:
                notifications.extend(messages)

        source = await self._dashboard_source(guild, selected_profile, kwargs)
        return {
            "status": 0,
            "notifications": notifications,
            "web_content": {
                "source": source,
                "expanded": True,
            },
        }

    async def _dashboard_member_can_manage(
        self,
        user: discord.User,
        guild: discord.Guild,
    ) -> tuple[discord.Member | None, bool]:
        member = guild.get_member(user.id)
        is_owner = user.id in getattr(self.bot, "owner_ids", set())
        is_admin = member is not None and await self.bot.is_admin(member)
        can_manage = is_owner or is_admin or (member is not None and member.guild_permissions.manage_guild)
        return member, can_manage

    def _dashboard_form_data(self, kwargs: dict[str, typing.Any]) -> typing.Any:
        data = kwargs.get("data") or {}
        if isinstance(data, dict) and ("form" in data or "json" in data):
            return data.get("form") or data.get("json") or {}
        return data

    def _dashboard_active_tab(self, kwargs, action_tabs, default):
        form_data = self._dashboard_form_data(kwargs)
        selected = self._dash_value(form_data, "active_tab").lower()
        valid = set(action_tabs.values()) | {default}
        return selected if selected in valid else action_tabs.get(self._dash_value(form_data, "action").lower(), default)

    def _dashboard_tab_button(self, name: str, label: str, active: str) -> str:
        selected = name == active
        active_class = " active" if selected else ""
        aria_selected = str(selected).lower()
        tabindex = 0 if selected else -1
        return (
            f'<button type="button" class="dash-tab{active_class}" '
            f'data-tab="{self._h(name)}" role="tab" '
            f'aria-selected="{aria_selected}" tabindex="{tabindex}">'
            f"{self._h(label)}</button>"
        )

    @staticmethod
    def _dashboard_tabs_script() -> str:
        return """
<script>
(() => {
  const root = document.currentScript.closest("[data-dashboard-tabs]");
  if (!root) return;

  const tabs = Array.from(root.querySelectorAll("[data-tab]"));
  const panels = Array.from(root.querySelectorAll("[data-tab-panel]"));
  const names = new Set(tabs.map((tab) => tab.dataset.tab));

  const activate = (name, hash = false) => {
    if (!names.has(name)) return;
    tabs.forEach((tab) => {
      const on = tab.dataset.tab === name;
      tab.classList.toggle("active", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
    });
    panels.forEach((panel) => {
      const on = panel.dataset.tabPanel === name;
      panel.classList.toggle("active", on);
      panel.hidden = !on;
    });
    if (hash) history.replaceState(null, "", `#tab-${name}`);
  };

  const fromHash = () => {
    const hash = location.hash.slice(1);
    if (hash.startsWith("tab-") && names.has(hash.slice(4))) return hash.slice(4);
    const section = document.getElementById(hash);
    const panel = section ? section.closest("[data-tab-panel]") : null;
    return panel ? panel.dataset.tabPanel : null;
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(tab.dataset.tab, true));
    tab.addEventListener("keydown", (event) => {
      const move = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!move) return;
      event.preventDefault();
      const next = tabs[(index + move + tabs.length) % tabs.length];
      next.focus();
      activate(next.dataset.tab, true);
    });
  });

  root.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      let input = form.querySelector('input[name="active_tab"]');
      if (!input) {
        input = document.createElement("input");
        input.type = "hidden";
        input.name = "active_tab";
        form.appendChild(input);
      }
      input.value = root.querySelector("[data-tab].active").dataset.tab;
    });
  });

  activate(fromHash() || root.querySelector("[data-tab].active").dataset.tab);
  const conversation = root.querySelector("[data-ticket-conversation]");
  const refreshForm = root.querySelector("[data-ticket-refresh]");
  const replyForm = root.querySelector("[data-ticket-reply]");
  const replyFeedback = root.querySelector("[data-ticket-reply-feedback]");
  const liveStatus = root.querySelector("[data-ticket-live-status]");
  let refreshPending = false;
  let replyPending = false;

  const setLiveStatus = (label, state) => {
    if (!liveStatus) return;
    liveStatus.dataset.state = state;
    const text = liveStatus.querySelector("[data-ticket-live-text]");
    if (text) text.textContent = label;
  };

  const responseData = (text) => {
    try {
      const payload = JSON.parse(text);
      const candidates = [
        payload?.web_content?.source,
        payload?.data?.web_content?.source,
        payload?.result?.web_content?.source,
        payload?.data?.source,
        payload?.result?.source,
      ];
      const notifications =
        payload?.notifications || payload?.data?.notifications || payload?.result?.notifications || [];
      return {
        markup: candidates.find((candidate) => typeof candidate === "string") || "",
        notifications: Array.isArray(notifications) ? notifications : [],
      };
    } catch (_error) {
      return {markup: text, notifications: []};
    }
  };

  const updateConversation = (markup, forceLatest = false) => {
    const documentCopy = new DOMParser().parseFromString(markup, "text/html");
    const updated = documentCopy.querySelector("[data-ticket-conversation]");
    if (!updated) throw new Error("Conversation was missing from the dashboard response");

    const newCsrf = documentCopy.querySelector('input[name="csrf_token"]');
    if (newCsrf) {
      root.querySelectorAll('input[name="csrf_token"]').forEach((input) => {
        input.value = newCsrf.value;
      });
    }

    const distanceFromBottom = conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight;
    const followLatest = forceLatest || distanceFromBottom < 96;
    const previousScrollTop = conversation.scrollTop;
    if (conversation.innerHTML !== updated.innerHTML) {
      conversation.innerHTML = updated.innerHTML;
      conversation.scrollTop = followLatest ? conversation.scrollHeight : previousScrollTop;
    }
  };

  const submitDashboardForm = async (form) => {
    const formData = new FormData(form);
    formData.set("active_tab", "desk");
    const endpoint = form.getAttribute("action") || location.href;
    const response = await fetch(endpoint, {
      method: (form.method || "POST").toUpperCase(),
      body: formData,
      credentials: "same-origin",
      headers: {"X-Requested-With": "XMLHttpRequest"},
    });
    if (!response.ok) throw new Error(`Dashboard request failed (${response.status})`);
    return responseData(await response.text());
  };

  const refreshConversation = async () => {
    const deskTab = root.querySelector('[data-tab="desk"]');
    if (!conversation || !refreshForm || refreshPending) return;
    if (document.hidden || !deskTab?.classList.contains("active")) {
      setLiveStatus("Paused", "paused");
      return;
    }

    refreshPending = true;
    setLiveStatus("Updating...", "loading");
    try {
      const result = await submitDashboardForm(refreshForm);
      updateConversation(result.markup);
      setLiveStatus("Live", "live");
    } catch (_error) {
      setLiveStatus("Reconnect", "error");
    } finally {
      refreshPending = false;
    }
  };

  if (conversation) conversation.scrollTop = conversation.scrollHeight;
  if (conversation && replyForm) {
    replyForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (replyPending) return;

      const textarea = replyForm.querySelector('textarea[name="ticket_reply_content"]');
      const button = replyForm.querySelector('button[type="submit"]');
      if (!textarea?.value.trim()) return;

      replyPending = true;
      if (button) button.disabled = true;
      if (replyFeedback) replyFeedback.textContent = "Sending...";
      try {
        const result = await submitDashboardForm(replyForm);
        const error = result.notifications.find((item) => item?.category === "error");
        if (error) throw new Error(error.message || "The reply could not be sent.");
        updateConversation(result.markup, true);
        textarea.value = "";
        if (replyFeedback) replyFeedback.textContent = "";
        setLiveStatus("Live", "live");
      } catch (error) {
        if (replyFeedback) {
          replyFeedback.textContent = error?.message || "The reply could not be sent. Please try again.";
        }
        setLiveStatus("Send failed", "error");
      } finally {
        replyPending = false;
        if (button) button.disabled = false;
      }
    });
  }
  if (conversation && refreshForm) {
    window.setInterval(refreshConversation, 8000);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) setLiveStatus("Paused", "paused");
      else refreshConversation();
    });
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        if (tab.dataset.tab === "desk") refreshConversation();
        else setLiveStatus("Paused", "paused");
      });
    });
  }

  const updateModalCard = (card) => {
    const typeSelect = card.querySelector("[data-modal-type-select]");
    const enabledCheck = card.querySelector("[data-modal-enabled]");
    const fieldsWrap = card.querySelector("[data-modal-fields]");
    const summaryBadge = card.querySelector("[data-modal-summary]");
    const labelInput = card.querySelector('input[name$="_label"]');

    if (enabledCheck && fieldsWrap) {
      const isEnabled = enabledCheck.checked;
      card.classList.toggle("is-disabled", !isEnabled);
      fieldsWrap.style.display = isEnabled ? "" : "none";
    }

    if (!typeSelect) return;
    const type = typeSelect.value;

    if (summaryBadge) {
      const label = labelInput?.value.trim() || "Untitled";
      const isEnabled = enabledCheck ? enabledCheck.checked : true;
      summaryBadge.textContent = isEnabled ? `${label} • ${type}` : "Disabled";
    }

    const show = (selector, condition) => {
      card.querySelectorAll(selector).forEach((el) => {
        el.style.display = condition ? "" : "none";
      });
    };

    show('[data-modal-field="style"]', type === "text");
    show('[data-modal-field="description"]', type !== "text_display");
    show('[data-modal-field="required"]', type !== "text_display");
    show('[data-modal-field="placeholder"]', ["text", "choice", "user", "role", "mentionable", "channel"].includes(type));
    show('[data-modal-field="default"]', type !== "file");

    const defaultLabel = card.querySelector('[data-modal-field="default"] label');
    if (defaultLabel) {
      if (type === "text_display") {
        defaultLabel.textContent = "Notice Content / Display Text";
      } else if (type === "boolean" || type === "checkbox") {
        defaultLabel.textContent = "Default (true / false)";
      } else {
        defaultLabel.textContent = "Default Value";
      }
    }

    show('[data-modal-field="text_length"]', type === "text");
    show(
      '[data-modal-field="values_count"]',
      ["choice", "checkbox_group", "user", "role", "mentionable", "channel", "file"].includes(type),
    );
    show('[data-modal-field="choices"]', ["choice", "radio", "checkbox_group"].includes(type));
  };

  root.querySelectorAll("[data-modal-card]").forEach((card) => {
    updateModalCard(card);
    card.addEventListener("change", () => updateModalCard(card));
    card.addEventListener("input", () => updateModalCard(card));
  });

  const addForm = root.querySelector("[data-modal-add-form]");
  if (addForm) {
    updateModalCard(addForm);
    addForm.addEventListener("change", () => updateModalCard(addForm));
    addForm.addEventListener("input", () => updateModalCard(addForm));
  }

  const updateControlButtons = (card) => {
    const actionSelect = card.querySelector("[data-btn-action-select]");
    const enabledCheck = card.querySelector("[data-btn-enabled]");
    const fieldsWrap = card.querySelector("[data-btn-fields]");
    const summaryBadge = card.querySelector("[data-btn-summary]");
    const labelInput = card.querySelector('input[name$="_label"]');

    if (enabledCheck && fieldsWrap) {
      const isEnabled = enabledCheck.checked;
      card.classList.toggle("is-disabled", !isEnabled);
      fieldsWrap.style.display = isEnabled ? "" : "none";
    }

    if (!actionSelect) return;
    const action = actionSelect.value;
    const label = labelInput?.value.trim() || "Untitled";
    const isEnabled = enabledCheck ? enabledCheck.checked : true;
    if (summaryBadge) {
      summaryBadge.textContent = isEnabled ? `${label} • ${action}` : "Disabled";
    }

    const show = (selector, condition) => {
      card.querySelectorAll(selector).forEach((el) => {
        el.style.display = condition ? "" : "none";
      });
    };

    const isToggle = ["claim_toggle", "lock_toggle", "close_toggle"].includes(action);
    const isCustom = ["send_message", "ping_roles", "toggle_role"].includes(action);
    show('[data-btn-field="toggle_alt"]', isToggle);
    show('[data-btn-field="custom_action"]', isCustom);
    show('[data-btn-field="custom_message"]', isCustom);
    show('[data-btn-field="custom_embed"]', action === "send_message");
    show('[data-btn-field="custom_roles"]', ["ping_roles", "toggle_role"].includes(action));
    show('[data-btn-field="custom_ephemeral"]', isCustom);
  };

  root.querySelectorAll("[data-btn-card]").forEach((card) => {
    updateControlButtons(card);
    card.addEventListener("change", () => updateControlButtons(card));
    card.addEventListener("input", () => updateControlButtons(card));
  });


  const updatePanelStyle = (form) => {
    const styleSelect = form.querySelector("[data-panel-style-select]");
    if (!styleSelect) return;
    const style = styleSelect.value;
    const btnRow = form.querySelector('[data-panel-component="button"]');
    const dropRow = form.querySelector('[data-panel-component="dropdown"]');
    if (btnRow) btnRow.style.display = style === "button" ? "" : "none";
    if (dropRow) dropRow.style.display = style === "dropdown" ? "" : "none";
  };

  root.querySelectorAll("[data-panel-form]").forEach((form) => {
    updatePanelStyle(form);
    form.addEventListener("change", () => updatePanelStyle(form));
  });
})();
</script>
"""

    def _dash_value(
        self,
        form_data: typing.Any,
        key: str,
        default: str = "",
    ) -> str:
        if hasattr(form_data, "get"):
            value = form_data.get(key, default)
        else:
            return default
        if isinstance(value, (list, tuple)):
            value = value[0] if value else default
        if value is None:
            return default
        return str(value)

    def _dash_values(self, form_data: typing.Any, key: str) -> list[str]:
        if hasattr(form_data, "getlist"):
            values = form_data.getlist(key)
        elif hasattr(form_data, "get"):
            value = form_data.get(key, [])
            values = value if isinstance(value, (list, tuple)) else [value]
        else:
            values = []
        return [str(value) for value in values if str(value).strip()]

    def _dash_bool(self, form_data: typing.Any, key: str) -> bool:
        if hasattr(form_data, "__contains__") and key in form_data:
            value = self._dash_value(form_data, key, "on").lower()
            return value not in {"0", "false", "off", "no", ""}
        return False

    def _color_hex(self, value: typing.Any) -> str:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = self.DEFAULT_COLOR
        return f"#{number:06X}"

    def _dash_int(
        self,
        form_data: typing.Any,
        key: str,
        *,
        default: int | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
        optional: bool = False,
    ) -> int | None:
        value = self._dash_value(form_data, key).strip()
        if optional and value == "":
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            if default is not None:
                number = default
            else:
                raise commands.BadArgument(f"`{key}` must be a whole number.")
        if minimum is not None:
            number = max(minimum, number)
        if maximum is not None:
            number = min(maximum, number)
        return number

    def _dash_optional_id(self, form_data: typing.Any, key: str) -> int | None:
        value = self._dash_value(form_data, key).strip()
        if not value:
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise commands.BadArgument(f"`{key}` must be a Discord ID.") from exc

    def _dash_csrf(self, kwargs: dict[str, typing.Any]) -> str:
        csrf_token = kwargs.get("csrf_token")
        if not isinstance(csrf_token, (tuple, list)) or len(csrf_token) != 2:
            return ""
        return f'<input type="hidden" name="csrf_token" value="{html.escape(str(csrf_token[1]), quote=True)}">'

    async def _dashboard_handle_action(
        self,
        guild: discord.Guild,
        member: discord.Member | None,
        action: str,
        form_data: typing.Any,
        selected_profile: str,
    ) -> tuple[str, list[dict[str, str]]]:
        messages: list[dict[str, str]] = []

        if action == "save_global":
            await self.config.guild(guild).enabled.set(
                self._dash_bool(form_data, "enabled"),
            )
            next_ticket_id = self._dash_int(
                form_data,
                "next_ticket_id",
                default=1,
                minimum=1,
            )
            await self.config.guild(guild).next_ticket_id.set(next_ticket_id)
            messages.append(
                {"message": "TicketHub global settings saved.", "category": "success"},
            )

        elif action == "select_profile":
            selected_profile = self._clean_name(
                self._dash_value(form_data, "selected_profile", "main"),
            )

        elif action == "create_profile":
            selected_profile = await self._dashboard_create_profile(guild, form_data)
            messages.append(
                {
                    "message": f"Profile `{selected_profile}` created.",
                    "category": "success",
                },
            )

        elif action == "delete_profile":
            target = self._clean_name(
                self._dash_value(form_data, "selected_profile", "main"),
            )
            await self._dashboard_delete_profile(guild, target)
            selected_profile = "main"
            messages.append(
                {"message": f"Profile `{target}` deleted.", "category": "success"},
            )

        elif action == "save_profile":
            selected_profile = await self._dashboard_save_profile(guild, form_data)
            messages.append(
                {
                    "message": f"Profile `{selected_profile}` settings saved.",
                    "category": "success",
                },
            )
        elif action in {"save_control_button", "create_control_button"}:
            btn_def = self._dashboard_parse_control_button_form(form_data)
            if action == "create_control_button":
                stored = await self._get_stored_buttons(guild)
                if btn_def["id"] in stored and btn_def["id"] not in self._default_stored_buttons():
                    raise commands.BadArgument(
                        f"A button with ID `{btn_def['id']}` already exists. Use the edit form below to modify it."
                    )
            saved = await self._save_stored_button(guild, btn_def)
            messages.append(
                {
                    "message": f"Control button `{saved['id']}` saved.",
                    "category": "success",
                },
            )

        elif action == "delete_control_button":
            target_id = self._dash_value(form_data, "button_id").strip().lower()
            await self._delete_stored_button(guild, target_id)
            messages.append(
                {
                    "message": f"Control button `{target_id}` deleted / reset.",
                    "category": "success",
                },
            )

        elif action == "reset_control_buttons":
            await self._reset_stored_buttons(guild)
            messages.append(
                {
                    "message": "All control buttons reset to defaults.",
                    "category": "success",
                },
            )


        elif action == "save_modal":
            selected_profile = await self._dashboard_save_modal(guild, form_data)
            messages.append(
                {
                    "message": f"Profile `{selected_profile}` modal settings saved.",
                    "category": "success",
                },
            )

        elif action == "add_modal_question":
            selected_profile = await self._dashboard_add_modal_question(
                guild,
                form_data,
            )
            messages.append(
                {
                    "message": f"Modal question added to `{selected_profile}`.",
                    "category": "success",
                },
            )

        elif action == "remove_modal_question":
            (
                selected_profile,
                removed_label,
            ) = await self._dashboard_remove_modal_question(
                guild,
                form_data,
            )
            messages.append(
                {
                    "message": f"Removed `{removed_label}` from `{selected_profile}`.",
                    "category": "success",
                },
            )

        elif action == "default_reason_modal":
            selected_profile = self._clean_name(
                self._dash_value(form_data, "selected_profile", "main"),
            )
            profile = await self._ensure_profile(guild, selected_profile)
            profile["creating_modal"] = self._default_reason_modal()
            await self._set_profile(guild, selected_profile, profile)
            messages.append(
                {"message": "Default reason modal enabled.", "category": "success"},
            )

        elif action == "clear_modal":
            selected_profile = self._clean_name(
                self._dash_value(form_data, "selected_profile", "main"),
            )
            profile = await self._ensure_profile(guild, selected_profile)
            profile["creating_modal"] = None
            await self._set_profile(guild, selected_profile, profile)
            messages.append(
                {"message": "Modal questions cleared.", "category": "success"},
            )

        elif action == "post_panel":
            selected_profile, message = await self._dashboard_post_panel(
                guild,
                form_data,
            )
            messages.append(
                {
                    "message": f"Panel posted for `{selected_profile}`: {message.jump_url}",
                    "category": "success",
                },
            )

        elif action == "attach_panel":
            selected_profile, message = await self._dashboard_attach_panel(
                guild,
                form_data,
            )
            messages.append(
                {
                    "message": f"Panel attached for `{selected_profile}`: {message.jump_url}",
                    "category": "success",
                },
            )

        elif action == "clear_panel":
            await self._dashboard_clear_panel(guild, form_data)
            messages.append(
                {
                    "message": "Panel tracking and controls cleared.",
                    "category": "success",
                },
            )

        elif action == "save_multi_panel":
            message = await self._dashboard_save_multi_panel(guild, form_data)
            messages.append(
                {
                    "message": f"Multi-panel saved: {message.jump_url}",
                    "category": "success",
                },
            )

        elif action == "clear_multi_panel":
            await self._dashboard_clear_multi_panel(guild, form_data)
            messages.append({"message": "Multi-panel cleared.", "category": "success"})

        elif action == "import_aaa3a_panels":
            records = await self._collect_aaa3a_panel_records(guild)
            cleaned = await self._set_aaa3a_panel_records(guild, records)
            messages.append(
                {
                    "message": f"Imported {len(cleaned)} AAA3A panel record(s).",
                    "category": "success",
                },
            )

        elif action == "clear_aaa3a_panels":
            await self._dashboard_clear_aaa3a_panels(guild)
            messages.append(
                {
                    "message": "Imported AAA3A panel records cleared.",
                    "category": "success",
                },
            )

        elif action == "ticket_action":
            if member is None:
                raise commands.BadArgument(
                    "You must be in this server to manage tickets.",
                )
            message = await self._dashboard_ticket_action(guild, member, form_data)
            messages.append({"message": message, "category": "success"})

        elif action == "select_ticket":
            ticket_id = self._dash_int(
                form_data,
                "workspace_ticket_id",
                minimum=1,
            )
            await self._get_ticket_record_by_id(guild, ticket_id)

        elif action == "workspace_ticket_action":
            if member is None:
                raise commands.BadArgument(
                    "You must be in this server to manage tickets.",
                )
            message = await self._dashboard_ticket_action(guild, member, form_data)
            messages.append({"message": message, "category": "success"})

        elif action == "ticket_reply":
            if member is None:
                raise commands.BadArgument(
                    "You must be in this server to reply to tickets.",
                )
            message = await self._dashboard_ticket_reply(guild, member, form_data)
            messages.append({"message": message, "category": "success"})

        elif action == "create_ticket":
            message = await self._dashboard_create_ticket(guild, form_data)
            messages.append({"message": message, "category": "success"})

        elif action == "recover_ticket":
            if member is None:
                raise commands.BadArgument(
                    "You must be in this server to recover tickets.",
                )
            record = await self._dashboard_recover_ticket(guild, member, form_data)
            messages.append(
                {
                    "message": f"Recovered ticket #{record['id']}.",
                    "category": "success",
                },
            )

        elif action:
            raise commands.BadArgument("Unknown TicketHub dashboard action.")

        return selected_profile or "main", messages

    async def _dashboard_create_profile(
        self,
        guild: discord.Guild,
        form_data: typing.Any,
    ) -> str:
        profile_name = self._clean_name(self._dash_value(form_data, "new_profile_name"))
        profiles = await self._get_profiles(guild)
        if profile_name in profiles:
            raise commands.BadArgument(
                f"A profile named `{profile_name}` already exists.",
            )
        clone_name = self._dash_value(form_data, "clone_profile")
        profile = self._merge_profile(profiles.get(clone_name)) if clone_name in profiles else self._default_profile()
        profile["panel_channel_id"] = None
        profile["panel_message_id"] = None
        profile["next_profile_ticket_id"] = None
        await self._set_profile(guild, profile_name, profile)
        return profile_name

    async def _dashboard_delete_profile(
        self,
        guild: discord.Guild,
        profile_name: str,
    ) -> None:
        if profile_name == "main":
            raise commands.BadArgument("The default `main` profile cannot be deleted.")
        profiles = await self._get_profiles(guild)
        if profile_name not in profiles:
            raise commands.BadArgument(f"No profile named `{profile_name}` exists.")

        tickets = await self.config.guild(guild).tickets()
        if any(str(record.get("profile") or "main") == profile_name for record in tickets.values()):
            raise commands.BadArgument(
                "Delete or recover this profile's tickets before deleting it.",
            )

        multi_panels = await self.config.guild(guild).multi_panels()
        for message_id, raw_record in multi_panels.items():
            try:
                record = self._sanitize_multi_panel_record(
                    raw_record,
                    message_id=int(message_id),
                )
            except (TypeError, ValueError):
                record = None
            if record and any(option["profile"] == profile_name for option in record["options"]):
                raise commands.BadArgument(
                    "Remove this profile from multi-panels before deleting it.",
                )

        if profiles[profile_name].get("panel_message_id"):
            raise commands.BadArgument("Clear this profile's panel before deleting it.")

        async with self.config.guild(guild).profiles() as stored_profiles:
            for raw_name in list(stored_profiles):
                if self._clean_name(str(raw_name)) == profile_name:
                    stored_profiles.pop(raw_name, None)
                    break
            if not stored_profiles:
                stored_profiles["main"] = self._default_profile()

    async def _dashboard_save_profile(
        self,
        guild: discord.Guild,
        form_data: typing.Any,
    ) -> str:
        profile_name = self._clean_name(
            self._dash_value(form_data, "selected_profile", "main"),
        )
        profile = await self._ensure_profile(guild, profile_name)
        old_emojis = dict(profile.get("control_emojis") or {})
        old_control_mode = profile.get("control_mode", "buttons")
        profile["form_mode"] = self._parse_interaction_mode(
            self._dash_value(form_data, "form_mode", profile.get("form_mode", "modal")), form=True
        )
        profile["control_mode"] = self._parse_interaction_mode(self._dash_value(form_data, "control_mode", old_control_mode))

        profile["enabled"] = self._dash_bool(form_data, "profile_enabled")
        if "panel_style" in form_data:
            profile["panel_style"] = self._parse_panel_style(
                self._dash_value(form_data, "panel_style", profile.get("panel_style", "button")),
            )
        profile["ticket_mode"] = "thread" if self._dash_value(form_data, "ticket_mode") == "thread" else "channel"
        if "panel_channel_id" in form_data:
            profile["panel_channel_id"] = self._dash_optional_id(
                form_data,
                "panel_channel_id",
            )
        if "panel_message_id" in form_data:
            profile["panel_message_id"] = self._dash_optional_id(
                form_data,
                "panel_message_id",
            )
        profile["ticket_category_id"] = self._dash_optional_id(
            form_data,
            "ticket_category_id",
        )
        profile["closed_category_id"] = self._dash_optional_id(
            form_data,
            "closed_category_id",
        )
        profile["thread_parent_channel_id"] = self._dash_optional_id(
            form_data,
            "thread_parent_channel_id",
        )
        profile["log_channel_id"] = self._dash_optional_id(form_data, "log_channel_id")
        profile["transcript_channel_id"] = self._dash_optional_id(
            form_data,
            "transcript_channel_id",
        )
        profile["ticket_role_id"] = self._dash_optional_id(form_data, "ticket_role_id")
        profile["max_open_tickets_by_member"] = self._dash_int(
            form_data,
            "max_open_tickets_by_member",
            default=5,
            minimum=0,
            maximum=50,
        )
        profile["next_profile_ticket_id"] = self._dash_int(
            form_data,
            "next_profile_ticket_id",
            minimum=1,
            optional=True,
        )
        profile["channel_name"] = self._validate_channel_name_template(
            self._dash_value(form_data, "channel_name", "ticket-{id}-{owner_name}"),
        )
        if "panel_title" in form_data:
            profile["panel_title"] = self._clean_modal_text(self._dash_value(form_data, "panel_title"), 256) or "Need Help?"
        if "panel_message" in form_data:
            profile["panel_message"] = (
                self._clean_modal_text(self._dash_value(form_data, "panel_message"), 2048)
                or "Open a ticket and staff will help you as soon as possible."
            )
        if "panel_color" in form_data:
            profile["panel_color"] = self._sanitize_panel_color(self._dash_value(form_data, "panel_color"))
        if "panel_footer" in form_data:
            profile["panel_footer"] = self._clean_modal_text(self._dash_value(form_data, "panel_footer"), 2048) or "TicketHub"
        if "panel_image_url" in form_data:
            profile["panel_image_url"] = self._dash_value(form_data, "panel_image_url").strip() or None
        if "panel_thumbnail_url" in form_data:
            profile["panel_thumbnail_url"] = self._dash_value(form_data, "panel_thumbnail_url").strip() or None
        if "panel_show_details" in form_data:
            profile["panel_show_details"] = self._dash_bool(form_data, "panel_show_details")
        if "panel_button_label" in form_data:
            profile["panel_button_label"] = (
                self._clean_modal_text(self._dash_value(form_data, "panel_button_label"), 80) or "Open Ticket"
            )
        if "panel_button_emoji" in form_data:
            profile["panel_button_emoji"] = (
                self._dash_value(form_data, "panel_button_emoji").strip() or DEFAULT_PANEL_BUTTON_EMOJI
            )
        if "panel_button_style" in form_data:
            profile["panel_button_style"] = self._parse_button_style(
                self._dash_value(form_data, "panel_button_style", "primary"),
            )
        if "panel_dropdown_placeholder" in form_data:
            profile["panel_dropdown_placeholder"] = (
                self._clean_modal_text(self._dash_value(form_data, "panel_dropdown_placeholder"), 100) or "Open a ticket..."
            )
        if "panel_dropdown_label" in form_data:
            profile["panel_dropdown_label"] = (
                self._clean_modal_text(self._dash_value(form_data, "panel_dropdown_label"), 100)
                or profile.get("panel_button_label")
                or "Open Ticket"
            )
        if "panel_dropdown_emoji" in form_data:
            profile["panel_dropdown_emoji"] = (
                self._dash_value(form_data, "panel_dropdown_emoji").strip()
                or profile.get("panel_button_emoji")
                or DEFAULT_PANEL_BUTTON_EMOJI
            )
        if "panel_dropdown_description" in form_data:
            profile["panel_dropdown_description"] = (
                self._clean_modal_text(self._dash_value(form_data, "panel_dropdown_description"), 100) or None
            )
        profile["welcome_message"] = self._clean_modal_text(
            self._dash_value(form_data, "welcome_message"),
            1900,
        )
        profile["custom_message"] = self._clean_modal_text(
            self._dash_value(form_data, "custom_message"),
            1900,
        )
        profile["transcripts"] = self._dash_bool(form_data, "transcripts")
        profile["dm_transcript"] = self._dash_bool(form_data, "dm_transcript")
        profile["owner_can_close"] = self._dash_bool(form_data, "owner_can_close")
        profile["owner_can_reopen"] = self._dash_bool(form_data, "owner_can_reopen")
        profile["owner_can_add_members"] = self._dash_bool(
            form_data,
            "owner_can_add_members",
        )
        profile["owner_can_remove_members"] = self._dash_bool(
            form_data,
            "owner_can_remove_members",
        )
        profile["close_on_leave"] = self._dash_bool(form_data, "close_on_leave")
        profile["close_request_timeout_minutes"] = self._dash_int(
            form_data,
            "close_request_timeout_minutes",
            default=self.DEFAULT_CLOSE_REQUEST_TIMEOUT_MINUTES,
            minimum=self.MIN_CLOSE_REQUEST_TIMEOUT_MINUTES,
            maximum=self.MAX_CLOSE_REQUEST_TIMEOUT_MINUTES,
        )

        auto_delete = self._dash_value(form_data, "auto_delete_on_close_hours").strip().lower()
        if auto_delete in {"", "off", "none", "disabled", "disable"}:
            profile["auto_delete_on_close_hours"] = None
        else:
            profile["auto_delete_on_close_hours"] = self._dash_int(
                form_data,
                "auto_delete_on_close_hours",
                default=0,
                minimum=0,
                maximum=720,
            )

        for field in (
            "support_role_ids",
            "speak_role_ids",
            "view_role_ids",
            "ping_role_ids",
            "whitelist_role_ids",
            "blacklist_role_ids",
        ):
            profile[field] = sorted(
                {int(role_id) for role_id in self._dash_values(form_data, field)},
            )

        defaults = self._default_profile()["control_emojis"]
        configured = {}
        for action, default_emoji in defaults.items():
            value = self._dash_value(form_data, f"emoji_{action}").strip()
            if not value or value.lower() in {"default", "reset"} or value == default_emoji:
                continue
            try:
                parsed_emoji = discord.PartialEmoji.from_str(value)
            except (TypeError, ValueError) as exc:
                raise commands.BadArgument(f"`{action}` emoji is not valid.") from exc
            if parsed_emoji.id is not None and self.bot.get_emoji(parsed_emoji.id) is None:
                raise commands.BadArgument(
                    f"I cannot access the custom emoji for `{action}`.",
                )
            if len(value) > 100:
                raise commands.BadArgument(f"`{action}` emoji is too long.")
            configured[action] = value
        profile["control_emojis"] = configured

        has_slot_assignment = any(f"slot_{i}_button" in form_data for i in range(10))
        submitted_buttons = []
        if has_slot_assignment:
            stored_buttons = await self._get_stored_buttons(guild)
            for i in range(10):
                slot_btn_id = self._dash_value(form_data, f"slot_{i}_button").strip().lower()
                if slot_btn_id and slot_btn_id in stored_buttons:
                    btn = dict(stored_buttons[slot_btn_id])
                    btn["enabled"] = True
                    submitted_buttons.append(btn)
                else:
                    fallback_def = self._default_stored_buttons().get(f"custom_{i + 1}") or {
                        "id": f"slot_{i + 1}",
                        "action": "send_message",
                        "label": "",
                    }
                    btn = dict(fallback_def)
                    btn["id"] = f"slot_{i + 1}" if not btn.get("id") else btn["id"]
                    btn["enabled"] = False
                    submitted_buttons.append(btn)
        else:
            for i in range(10):
                id_key = f"btn_{i}_id"
                if id_key not in form_data:
                    continue
            btn_id = self._dash_value(form_data, id_key).strip().lower() or f"btn_{i + 1}"
            enabled = self._dash_bool(form_data, f"btn_{i}_enabled")
            action = self._dash_value(form_data, f"btn_{i}_action", "claim_toggle")
            label = self._clean_modal_text(self._dash_value(form_data, f"btn_{i}_label"), 80) or ""
            emoji_raw = self._dash_value(form_data, f"btn_{i}_emoji").strip()
            emoji = emoji_raw or None
            style = self._parse_button_style(self._dash_value(form_data, f"btn_{i}_style", "secondary"))
            alt_label = self._clean_modal_text(self._dash_value(form_data, f"btn_{i}_alt_label"), 80) or None
            alt_emoji_raw = self._dash_value(form_data, f"btn_{i}_alt_emoji").strip()
            alt_emoji = alt_emoji_raw or None
            alt_style_raw = self._dash_value(form_data, f"btn_{i}_alt_style")
            alt_style = self._parse_button_style(alt_style_raw) if alt_style_raw else None
            visibility = self._dash_value(form_data, f"btn_{i}_visibility", "open")
            if visibility not in {"open", "closed", "always"}:
                visibility = "open"
            row_raw = self._dash_value(form_data, f"btn_{i}_row")
            try:
                row_val = max(0, min(int(row_raw), 4)) if row_raw != "" and row_raw is not None else None
            except (ValueError, TypeError):
                row_val = None

            allowed_groups = []
            for grp in ("support", "admin", "owner", "claimed_by", "participants", "everyone"):
                if self._dash_bool(form_data, f"btn_{i}_group_{grp}"):
                    allowed_groups.append(grp)

            def _clean_ids_from_form(key):
                res = []
                for val in self._dash_values(form_data, key):
                    with contextlib.suppress(ValueError, TypeError):
                        res.append(int(val))
                return sorted(set(res))

            allowed_roles = _clean_ids_from_form(f"btn_{i}_allowed_roles")
            action_roles = _clean_ids_from_form(f"btn_{i}_action_roles")
            action_message = self._dash_value(form_data, f"btn_{i}_action_message").strip()[:2000]
            action_embed_title = self._clean_modal_text(self._dash_value(form_data, f"btn_{i}_action_embed_title"), 256) or None
            ephemeral = self._dash_bool(form_data, f"btn_{i}_ephemeral")

            submitted_buttons.append(
                {
                    "id": btn_id,
                    "action": action,
                    "enabled": enabled,
                    "label": label,
                    "emoji": emoji,
                    "style": style,
                    "alt_label": alt_label,
                    "alt_emoji": alt_emoji,
                    "alt_style": alt_style,
                    "visibility": visibility,
                    "row": row_val,
                    "allowed_groups": allowed_groups,
                    "allowed_role_ids": allowed_roles,
                    "action_message": action_message,
                    "action_embed_title": action_embed_title,
                    "action_role_ids": action_roles,
                    "ephemeral_response": ephemeral,
                }
            )

        if submitted_buttons:
            profile["control_buttons"] = self._sanitize_control_buttons(submitted_buttons)
            emojis = dict(profile.get("control_emojis") or {})
            for b in profile["control_buttons"]:
                b_id = b["id"]
                b_action = b["action"]
                if b_id in {"claim", "lock", "close", "members", "transcript", "delete"} and b.get("emoji"):
                    emojis[b_id] = b["emoji"]
                if b_action == "claim_toggle" and b.get("alt_emoji"):
                    emojis["unclaim"] = b["alt_emoji"]
                elif b_action == "lock_toggle" and b.get("alt_emoji"):
                    emojis["unlock"] = b["alt_emoji"]
                elif b_action in {"close_toggle", "close_reason", "close_confirm", "close_now"} and b.get("alt_emoji"):
                    emojis["reopen"] = b["alt_emoji"]
            profile["control_emojis"] = emojis

        await self._set_profile(guild, profile_name, profile)

        panel_view_fields = {
            "panel_style",
            "panel_button_label",
            "panel_button_emoji",
            "panel_button_style",
            "panel_dropdown_placeholder",
            "panel_dropdown_label",
            "panel_dropdown_emoji",
            "panel_dropdown_description",
        }
        panel_embed_fields = {
            "panel_title",
            "panel_message",
            "panel_color",
            "panel_footer",
            "panel_image_url",
            "panel_thumbnail_url",
            "panel_show_details",
            "panel_channel_id",
            "panel_message_id",
        }
        has_view_changes = any(field in form_data for field in panel_view_fields)
        has_embed_changes = any(field in form_data for field in panel_embed_fields)
        if has_view_changes:
            await self._dashboard_refresh_profile_panel(guild, profile_name, profile, update_view=True)
        elif has_embed_changes:
            await self._dashboard_refresh_profile_panel(guild, profile_name, profile, update_view=False)

        tickets = await self.config.guild(guild).tickets()
        if configured != old_emojis or profile["control_mode"] != old_control_mode or submitted_buttons:
            for record in tickets.values():
                if str(record.get("profile") or "main") == profile_name:
                    await self._update_ticket_message(guild, record, profile)
        for record in tickets.values():
            if str(record.get("profile") or "main") == profile_name and record.get("status") == "closed":
                self._schedule_ticket_auto_delete(guild.id, record, profile)
        return profile_name

    async def _dashboard_save_modal(
        self,
        guild: discord.Guild,
        form_data: typing.Any,
    ) -> str:
        profile_name = self._clean_name(
            self._dash_value(form_data, "selected_profile", "main"),
        )
        profile = await self._ensure_profile(guild, profile_name)
        fields = []
        for index in range(5):
            if not self._dash_bool(form_data, f"modal_{index}_enabled"):
                continue
            label = self._clean_modal_text(
                self._dash_value(form_data, f"modal_{index}_label"),
                45,
            )
            if not label:
                raise commands.BadArgument("Enabled modal questions need a label.")
            description = (
                self._clean_modal_text(
                    self._dash_value(form_data, f"modal_{index}_description"),
                    100,
                )
                or None
            )
            question_type = (
                self._modal_type_name(
                    self._dash_value(form_data, f"modal_{index}_type"),
                )
                or "text"
            )
            choices = self._clean_modal_choices(
                self._dash_value(form_data, f"modal_{index}_choices"),
            )
            if question_type in {"choice", "radio"} and len(choices) < 2:
                raise commands.BadArgument(
                    f"{question_type.replace('_', ' ').title()} questions need at least two choices.",
                )
            if question_type == "checkbox_group" and len(choices) < 1:
                raise commands.BadArgument(
                    "Checkbox group questions need at least one choice.",
                )
            style_name = self._dash_value(form_data, f"modal_{index}_style")
            style = discord.TextStyle.short.value if style_name == "short" else discord.TextStyle.paragraph.value
            min_length = self._dash_int(
                form_data,
                f"modal_{index}_min_length",
                minimum=0,
                maximum=4000,
                optional=True,
            )
            max_length = self._dash_int(
                form_data,
                f"modal_{index}_max_length",
                minimum=1,
                maximum=4000,
                optional=True,
            )
            min_values = self._dash_int(
                form_data,
                f"modal_{index}_min_values",
                minimum=0,
                maximum=25,
                optional=True,
            )
            max_values = self._dash_int(
                form_data,
                f"modal_{index}_max_values",
                minimum=1,
                maximum=25,
                optional=True,
            )
            fields.append(
                {
                    "label": label,
                    "description": description,
                    "type": question_type,
                    "style": style,
                    "required": (
                        False if question_type == "text_display" else self._dash_bool(form_data, f"modal_{index}_required")
                    ),
                    "default": self._clean_modal_text(
                        self._dash_value(form_data, f"modal_{index}_default"),
                        4000,
                    ),
                    "placeholder": self._clean_modal_text(
                        self._dash_value(form_data, f"modal_{index}_placeholder"),
                        100,
                    ),
                    "min_length": min_length,
                    "max_length": max_length,
                    "min_values": min_values,
                    "max_values": max_values,
                    "choices": choices,
                },
            )
        profile["creating_modal"] = self._sanitize_modal_fields(fields)
        await self._set_profile(guild, profile_name, profile)
        return profile_name

    async def _dashboard_add_modal_question(
        self,
        guild: discord.Guild,
        form_data: typing.Any,
    ) -> str:
        profile_name = self._clean_name(
            self._dash_value(form_data, "selected_profile", "main"),
        )
        profile = await self._ensure_profile(guild, profile_name)
        fields = list(profile.get("creating_modal") or [])
        if len(fields) >= 5:
            raise commands.BadArgument("A Discord modal can only have 5 questions.")

        label = self._clean_modal_text(
            self._dash_value(form_data, "add_modal_label"),
            45,
        )
        if not label:
            raise commands.BadArgument("New modal questions need a label.")
        description = (
            self._clean_modal_text(
                self._dash_value(form_data, "add_modal_description"),
                100,
            )
            or None
        )

        question_type = self._modal_type_name(self._dash_value(form_data, "add_modal_type")) or "text"
        choices = self._clean_modal_choices(
            self._dash_value(form_data, "add_modal_choices"),
        )
        if question_type in {"choice", "radio"} and len(choices) < 2:
            raise commands.BadArgument(f"{question_type.replace('_', ' ').title()} questions need at least two choices.")
        if question_type == "checkbox_group" and len(choices) < 1:
            raise commands.BadArgument("Checkbox group questions need at least one choice.")

        style_name = self._dash_value(form_data, "add_modal_style")
        style = discord.TextStyle.short.value if style_name == "short" else discord.TextStyle.paragraph.value
        min_length = self._dash_int(
            form_data,
            "add_modal_min_length",
            minimum=0,
            maximum=4000,
            optional=True,
        )
        max_length = self._dash_int(
            form_data,
            "add_modal_max_length",
            minimum=1,
            maximum=4000,
            optional=True,
        )
        min_values = self._dash_int(
            form_data,
            "add_modal_min_values",
            minimum=0,
            maximum=25,
            optional=True,
        )
        max_values = self._dash_int(
            form_data,
            "add_modal_max_values",
            minimum=1,
            maximum=25,
            optional=True,
        )
        fields.append(
            {
                "label": label,
                "description": description,
                "type": question_type,
                "style": style,
                "required": (False if question_type == "text_display" else self._dash_bool(form_data, "add_modal_required")),
                "default": self._clean_modal_text(
                    self._dash_value(form_data, "add_modal_default"),
                    4000,
                ),
                "placeholder": self._clean_modal_text(
                    self._dash_value(form_data, "add_modal_placeholder"),
                    100,
                ),
                "min_length": min_length,
                "max_length": max_length,
                "min_values": min_values,
                "max_values": max_values,
                "choices": choices,
            },
        )
        profile["creating_modal"] = self._sanitize_modal_fields(fields)
        await self._set_profile(guild, profile_name, profile)
        return profile_name

    async def _dashboard_remove_modal_question(
        self,
        guild: discord.Guild,
        form_data: typing.Any,
    ) -> tuple[str, str]:
        profile_name = self._clean_name(
            self._dash_value(form_data, "selected_profile", "main"),
        )
        profile = await self._ensure_profile(guild, profile_name)
        fields = list(profile.get("creating_modal") or [])
        if not fields:
            raise commands.BadArgument(f"`{profile_name}` has no modal questions.")

        index = self._dash_int(
            form_data,
            "remove_modal_index",
            minimum=1,
            maximum=len(fields),
        )
        removed = fields.pop(index - 1)
        removed_label = self._clean_modal_text(removed.get("label"), 45) or f"Question {index}"
        profile["creating_modal"] = self._sanitize_modal_fields(fields)
        await self._set_profile(guild, profile_name, profile)
        return profile_name, removed_label

    async def _dashboard_post_panel(
        self,
        guild: discord.Guild,
        form_data: typing.Any,
    ) -> tuple[str, discord.Message]:
        profile_name = self._clean_name(
            self._dash_value(form_data, "selected_profile", "main"),
        )
        profile = await self._ensure_profile(guild, profile_name)
        channel_id = self._dash_optional_id(form_data, "post_panel_channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            raise commands.BadArgument("Choose a text channel for the panel.")
        style = self._parse_panel_style(
            self._dash_value(form_data, "post_panel_style", profile.get("panel_style")),
        )
        if "post_panel_title" in form_data:
            profile["panel_title"] = (
                self._clean_modal_text(self._dash_value(form_data, "post_panel_title"), 256)
                or profile.get("panel_title")
                or "Need Help?"
            )
        if "post_panel_message" in form_data:
            profile["panel_message"] = (
                self._clean_modal_text(self._dash_value(form_data, "post_panel_message"), 2048)
                or profile.get("panel_message")
                or "Open a ticket and staff will help you as soon as possible."
            )
        if "post_panel_color" in form_data:
            profile["panel_color"] = self._sanitize_panel_color(self._dash_value(form_data, "post_panel_color"))
        if "post_panel_footer" in form_data:
            profile["panel_footer"] = (
                self._clean_modal_text(self._dash_value(form_data, "post_panel_footer"), 2048) or "TicketHub"
            )
        if "post_panel_image_url" in form_data:
            profile["panel_image_url"] = self._dash_value(form_data, "post_panel_image_url").strip() or None
        if "post_panel_thumbnail_url" in form_data:
            profile["panel_thumbnail_url"] = self._dash_value(form_data, "post_panel_thumbnail_url").strip() or None
        if "post_panel_show_details" in form_data or "post_panel_title" in form_data:
            profile["panel_show_details"] = self._dash_bool(form_data, "post_panel_show_details")
        if "post_panel_button_label" in form_data:
            profile["panel_button_label"] = (
                self._clean_modal_text(self._dash_value(form_data, "post_panel_button_label"), 80) or "Open Ticket"
            )
        if "post_panel_button_emoji" in form_data:
            profile["panel_button_emoji"] = (
                self._dash_value(form_data, "post_panel_button_emoji").strip() or DEFAULT_PANEL_BUTTON_EMOJI
            )
        if "post_panel_button_style" in form_data:
            profile["panel_button_style"] = self._parse_button_style(
                self._dash_value(form_data, "post_panel_button_style", "primary"),
            )
        if "post_panel_dropdown_placeholder" in form_data:
            profile["panel_dropdown_placeholder"] = (
                self._clean_modal_text(self._dash_value(form_data, "post_panel_dropdown_placeholder"), 100) or "Open a ticket..."
            )
        if "post_panel_dropdown_label" in form_data:
            profile["panel_dropdown_label"] = (
                self._clean_modal_text(self._dash_value(form_data, "post_panel_dropdown_label"), 100) or "Open Ticket"
            )
        if "post_panel_dropdown_emoji" in form_data:
            profile["panel_dropdown_emoji"] = (
                self._dash_value(form_data, "post_panel_dropdown_emoji").strip() or DEFAULT_PANEL_BUTTON_EMOJI
            )
        if "post_panel_dropdown_description" in form_data:
            profile["panel_dropdown_description"] = (
                self._clean_modal_text(self._dash_value(form_data, "post_panel_dropdown_description"), 100) or None
            )

        message = await self._post_panel(guild, profile_name, profile, channel, style)
        await self.config.guild(guild).enabled.set(True)
        return profile_name, message

    async def _dashboard_attach_panel(
        self,
        guild: discord.Guild,
        form_data: typing.Any,
    ) -> tuple[str, discord.Message]:
        profile_name = self._clean_name(
            self._dash_value(form_data, "selected_profile", "main"),
        )
        profile = await self._ensure_profile(guild, profile_name)
        message = await self._dashboard_fetch_message(
            guild,
            self._dash_value(form_data, "attach_panel_channel_id"),
            self._dash_value(form_data, "attach_panel_message_id"),
        )
        style = self._parse_panel_style(
            self._dash_value(
                form_data,
                "attach_panel_style",
                profile.get("panel_style"),
            ),
        )
        message = await self._attach_panel(guild, profile_name, profile, message, style)
        await self.config.guild(guild).enabled.set(True)
        return profile_name, message

    async def _dashboard_clear_panel(
        self,
        guild: discord.Guild,
        form_data: typing.Any,
    ) -> None:
        message = await self._dashboard_fetch_message(
            guild,
            self._dash_value(form_data, "clear_panel_channel_id"),
            self._dash_value(form_data, "clear_panel_message_id"),
        )
        tracked = False
        multi_panels = await self.config.guild(guild).multi_panels()
        if str(message.id) in multi_panels:
            await self._clear_multi_panel(guild, message)
            tracked = True
        profiles = await self._get_profiles(guild)
        for profile_name, profile in profiles.items():
            if str(profile.get("panel_message_id")) == str(message.id):
                profile["panel_message_id"] = None
                profile["panel_channel_id"] = None
                await self._set_profile(guild, profile_name, profile)
                tracked = True
        if not tracked:
            raise commands.BadArgument(
                "That message is not tracked as a TicketHub panel.",
            )
        try:
            await message.edit(view=None)
        except discord.HTTPException as exc:
            raise commands.CommandError(
                "Panel tracking was cleared, but I could not edit the message.",
            ) from exc

    async def _dashboard_save_multi_panel(
        self,
        guild: discord.Guild,
        form_data: typing.Any,
    ) -> discord.Message:
        channel_id_val = self._dash_value(form_data, "multi_panel_channel_id").strip()
        message_id_val = self._dash_value(form_data, "multi_panel_message_id").strip()
        if not message_id_val:
            channel_id = self._dash_optional_id(form_data, "multi_panel_channel_id")
            channel = guild.get_channel(channel_id) if channel_id else None
            if not isinstance(channel, discord.TextChannel):
                raise commands.BadArgument("Choose a valid text channel to post the multi-panel.")
            title = self._clean_modal_text(self._dash_value(form_data, "multi_panel_title"), 256) or "Need Help?"
            description = (
                self._clean_modal_text(self._dash_value(form_data, "multi_panel_message"), 2048)
                or "Select an option below to open a ticket."
            )
            color = self._sanitize_panel_color(self._dash_value(form_data, "multi_panel_color")) or self.DEFAULT_COLOR
            embed = discord.Embed(
                title=title,
                description=description,
                color=color,
                timestamp=self._now(),
            )
            if guild.icon:
                embed.set_author(name=guild.name, icon_url=guild.icon.url)
            else:
                embed.set_author(name=guild.name)
            embed.set_footer(text="TicketHub")
            try:
                message = await channel.send(embed=embed)
            except discord.HTTPException as exc:
                raise commands.CommandError("I could not post the multi-panel message.") from exc
        else:
            message = await self._dashboard_fetch_message(
                guild,
                channel_id_val,
                message_id_val,
            )
            if guild.me is None or message.author.id != guild.me.id:
                raise commands.BadArgument(
                    "I can only manage multi-panels on messages sent by this bot.",
                )

        profiles = await self._get_profiles(guild)
        options = []
        for raw_line in self._dash_value(form_data, "multi_panel_options").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split("|")]
            while len(parts) < 4:
                parts.append("")
            profile_name = self._clean_name(parts[0])
            if profile_name not in profiles:
                raise commands.BadArgument(f"No profile named `{profile_name}` exists.")
            emoji = parts[1] or None
            label = parts[2][:80]
            description = parts[3][:100] or None
            if not label:
                raise commands.BadArgument("Every multi-panel option needs a label.")
            options.append(
                {
                    "profile": profile_name,
                    "emoji": emoji,
                    "label": label,
                    "description": description,
                },
            )
            if len(options) >= 25:
                break
        if not options:
            raise commands.BadArgument("Provide at least one multi-panel option.")

        record = {
            "channel_id": message.channel.id,
            "message_id": message.id,
            "style": self._parse_panel_style(
                self._dash_value(form_data, "multi_panel_style", "button"),
            ),
            "placeholder": (
                self._clean_modal_text(
                    self._dash_value(form_data, "multi_panel_placeholder"),
                    100,
                )
                or "Choose a ticket type..."
            ),
            "options": options,
        }
        await self._save_multi_panel(guild, message, record)
        for option in options:
            profile = profiles[option["profile"]]
            profile["panel_channel_id"] = message.channel.id
            await self._set_profile(guild, option["profile"], profile)
        await self.config.guild(guild).enabled.set(True)
        return message

    async def _dashboard_clear_multi_panel(
        self,
        guild: discord.Guild,
        form_data: typing.Any,
    ) -> None:
        message = await self._dashboard_fetch_message(
            guild,
            self._dash_value(form_data, "multi_panel_channel_id"),
            self._dash_value(form_data, "multi_panel_message_id"),
        )
        await self._clear_multi_panel(guild, message)

    async def _dashboard_clear_aaa3a_panels(self, guild: discord.Guild) -> None:
        panels = await self.config.guild(guild).aaa3a_panels()
        for raw_record in panels.values():
            record = self._sanitize_aaa3a_panel_record(raw_record)
            if record is None:
                continue
            view = self._aaa3a_panel_views.pop(int(record["message_id"]), None)
            if view is not None:
                view.stop()
        await self.config.guild(guild).aaa3a_panels.set({})

    async def _dashboard_ticket_action(
        self,
        guild: discord.Guild,
        member: discord.Member,
        form_data: typing.Any,
    ) -> str:
        ticket_field = "workspace_ticket_id" if self._dash_value(form_data, "workspace_ticket_id").strip() else "ticket_id"
        ticket_id = self._dash_int(form_data, ticket_field, minimum=1)
        action = self._dash_value(form_data, "ticket_action")
        record = await self._get_ticket_record_by_id(guild, ticket_id)
        reason = self._dash_value(form_data, "ticket_reason").strip() or None

        if action == "claim":
            await self._claim_ticket(guild, record, member)
        elif action == "unclaim":
            await self._unclaim_ticket(guild, record, member)
        elif action == "lock":
            await self._lock_ticket(guild, record, member)
        elif action == "unlock":
            await self._unlock_ticket(guild, record, member)
        elif action == "close":
            await self._close_ticket(guild, record, member, reason=reason)
        elif action == "reopen":
            await self._reopen_ticket(guild, record, member, reason=reason)
        elif action == "delete":
            await self._delete_ticket_channel(guild, record, member, reason=reason)
        elif action == "transcript":
            profile = await self._get_profile(
                guild,
                str(record.get("profile") or "main"),
            )
            return await self._send_transcript_bundle(
                guild,
                record,
                profile,
                requested_by=member,
            )
        elif action in {"add_member", "remove_member"}:
            target_id = self._dash_int(form_data, "ticket_member_id", minimum=1)
            target = guild.get_member(target_id)
            if target is None:
                raise commands.BadArgument("That member is not in this server.")
            if action == "add_member":
                note = await self._add_ticket_member(guild, record, member, target)
                return f"Added {target} to ticket #{ticket_id}.{note}"
            await self._remove_ticket_member(guild, record, member, target)
            return f"Removed {target} from ticket #{ticket_id}."
        else:
            raise commands.BadArgument("Choose a valid ticket action.")
        return f"Ticket #{ticket_id} {action.replace('_', ' ')} completed."

    async def _dashboard_ticket_reply(
        self,
        guild: discord.Guild,
        member: discord.Member,
        form_data: typing.Any,
    ) -> str:
        """Send an attributed staff reply into an open ticket channel."""
        ticket_id = self._dash_int(
            form_data,
            "workspace_ticket_id",
            minimum=1,
        )
        record = await self._get_ticket_record_by_id(guild, ticket_id)
        if record.get("status") != "open":
            raise commands.CommandError("Reopen this ticket before replying.")
        profile = await self._get_profile(
            guild,
            str(record.get("profile") or "main"),
        )
        if not self._can_speak_in_ticket(member, profile):
            raise commands.CommandError(
                "You do not have permission to reply to this ticket.",
            )
        content = self._dash_value(form_data, "ticket_reply_content").strip()
        if not content:
            raise commands.BadArgument("Write a reply before sending.")
        if len(content) > 1800:
            raise commands.BadArgument(
                "Dashboard replies cannot be longer than 1,800 characters.",
            )
        channel = await self._fetch_ticket_channel(guild, record)
        if channel is None:
            raise commands.CommandError("This ticket's channel is unavailable.")
        if isinstance(channel, discord.Thread) and channel.archived:
            try:
                await channel.edit(
                    archived=False,
                    reason=f"TicketHub dashboard reply to ticket #{ticket_id}",
                )
            except discord.HTTPException as exc:
                raise commands.CommandError(
                    "I could not reopen this ticket thread to send the reply.",
                ) from exc
        reply_embed = discord.Embed(
            description=content,
            color=(member.color if member.color.value else discord.Color.blurple()),
            timestamp=discord.utils.utcnow(),
        )
        reply_embed.set_author(
            name=member.display_name,
            icon_url=member.display_avatar.url,
        )
        reply_embed.set_footer(
            text=f"TicketHub Dashboard • Ticket #{ticket_id}",
        )
        try:
            sent = await channel.send(
                embed=reply_embed,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                    replied_user=False,
                ),
            )
        except discord.HTTPException as exc:
            raise commands.CommandError(
                "I could not send that reply to the ticket channel.",
            ) from exc
        reply_event = {
            "type": "dashboard_reply",
            "actor_id": member.id,
            "message_id": sent.id,
            "at": self._now_ts(),
        }
        async with self.config.guild(guild).tickets() as tickets:
            saved_record = tickets.get(str(record["id"]), record)
            saved_record.setdefault("events", []).append(reply_event)
            tickets[str(record["id"])] = saved_record
        return f"Reply sent to ticket #{ticket_id}: {sent.jump_url}"

    async def _dashboard_create_ticket(
        self,
        guild: discord.Guild,
        form_data: typing.Any,
    ) -> str:
        owner_id = self._dash_int(form_data, "create_ticket_owner_id", minimum=1)
        owner = guild.get_member(owner_id)
        if owner is None:
            raise commands.BadArgument(
                "The ticket owner must be a member of this server.",
            )
        profile_name = self._clean_name(
            self._dash_value(form_data, "create_ticket_profile", "main"),
        )
        reason = self._dash_value(form_data, "create_ticket_reason").strip() or "Created from Dashboard."
        record, channel = await self._create_ticket(
            guild,
            owner,
            profile_name,
            reason=reason,
        )
        return f"Ticket #{record['id']} created for {owner}: {channel.mention}"

    async def _dashboard_recover_ticket(
        self,
        guild: discord.Guild,
        member: discord.Member,
        form_data: typing.Any,
    ) -> dict[str, typing.Any]:
        channel_id = self._dash_int(form_data, "recover_channel_id", minimum=1)
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise commands.BadArgument("Choose a ticket text channel or thread.")
        return await self._recover_ticket_record(guild, channel, member)

    async def _dashboard_fetch_message(
        self,
        guild: discord.Guild,
        channel_id: typing.Any,
        message_id: typing.Any,
    ) -> discord.Message:
        try:
            clean_channel_id = int(str(channel_id).strip())
            clean_message_id = int(str(message_id).strip())
        except (TypeError, ValueError) as exc:
            raise commands.BadArgument(
                "Provide both channel ID and message ID.",
            ) from exc
        channel = guild.get_channel(clean_channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise commands.BadArgument(
                "The channel ID must resolve to a text channel or thread.",
            )
        try:
            return await channel.fetch_message(clean_message_id)
        except discord.HTTPException as exc:
            raise commands.CommandError("I could not fetch that message.") from exc

    async def _dashboard_refresh_profile_panel(
        self,
        guild: discord.Guild,
        profile_name: str,
        profile: dict[str, typing.Any],
        update_view: bool = False,
    ) -> None:
        if not profile.get("panel_channel_id") or not profile.get("panel_message_id"):
            return
        try:
            message = await self._dashboard_fetch_message(
                guild,
                profile.get("panel_channel_id"),
                profile.get("panel_message_id"),
            )
            embed = self._panel_embed(guild, profile_name, profile)
            if update_view:
                view = self._panel_view_for_style(profile.get("panel_style"), profile=profile)
                message = await message.edit(
                    embed=embed,
                    view=view,
                )
                await self._alternative_panel(message, profile.get("panel_style"), [{"profile": profile_name, "label": profile_name}])
            else:
                await message.edit(embed=embed)
        except (commands.CommandError, discord.HTTPException):
            log.debug(
                "Could not refresh TicketHub panel for %s in %s.",
                profile_name,
                guild.id,
                exc_info=True,
            )

    async def _dashboard_source(
        self,
        guild: discord.Guild,
        selected_profile: str,
        kwargs: dict[str, typing.Any],
    ) -> str:
        enabled = await self.config.guild(guild).enabled()
        next_ticket_id = await self.config.guild(guild).next_ticket_id()
        profiles = await self._get_profiles(guild)
        tickets = await self.config.guild(guild).tickets()
        multi_panels = await self.config.guild(guild).multi_panels()
        aaa3a_panels = await self.config.guild(guild).aaa3a_panels()
        stored_buttons = await self._get_stored_buttons(guild)
        if selected_profile not in profiles:
            selected_profile = "main" if "main" in profiles else next(iter(profiles), "main")
        profile = profiles[selected_profile]
        csrf = self._dash_csrf(kwargs)

        open_count = sum(1 for record in tickets.values() if record.get("status") == "open")
        closed_count = sum(1 for record in tickets.values() if record.get("status") == "closed")
        claimed_count = sum(1 for record in tickets.values() if record.get("status") == "open" and record.get("claimed_by"))
        form_data = self._dashboard_form_data(kwargs)
        selected_ticket_value = (
            self._dash_value(form_data, "workspace_ticket_id").strip() or self._dash_value(form_data, "ticket_id").strip()
        )
        selected_ticket_id = None
        if selected_ticket_value:
            try:
                candidate_id = int(selected_ticket_value)
            except (TypeError, ValueError):
                candidate_id = 0
            if str(candidate_id) in tickets:
                selected_ticket_id = candidate_id
        if selected_ticket_id is None and tickets:
            ordered_records = sorted(
                tickets.values(),
                key=lambda item: (
                    item.get("status") == "open",
                    int(item.get("id") or 0),
                ),
                reverse=True,
            )
            selected_ticket_id = int(ordered_records[0].get("id") or 0) or None
        workspace = await self._dashboard_workspace_section(
            guild,
            tickets,
            selected_ticket_id,
            csrf,
        )
        active_tab = self._dashboard_active_tab(
            kwargs,
            {
                "save_global": "setup",
                "select_profile": "setup",
                "create_profile": "setup",
                "delete_profile": "setup",
                "save_profile": "setup",
                "save_control_button": "buttons",
                "create_control_button": "buttons",
                "delete_control_button": "buttons",
                "reset_control_buttons": "buttons",
                "save_modal": "modal",
                "add_modal_question": "modal",
                "remove_modal_question": "modal",
                "default_reason_modal": "modal",
                "clear_modal": "modal",
                "post_panel": "panels",
                "attach_panel": "panels",
                "clear_panel": "panels",
                "save_multi_panel": "panels",
                "clear_multi_panel": "panels",
                "ticket_action": "tickets",
                "select_ticket": "desk",
                "workspace_ticket_action": "desk",
                "ticket_reply": "desk",
                "create_ticket": "tickets",
                "recover_ticket": "tickets",
                "import_aaa3a_panels": "imports",
                "clear_aaa3a_panels": "imports",
            },
            "tickets",
        )

        return f"""
        <style>
            .th-wrap {{ max-width: 1180px; margin: 0 auto; color: #e5e7eb; }}
            .th-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }}
            .th-card {{ background: #1f2937; border: 1px solid #374151; border-radius: 8px; padding: 16px;
            margin-bottom: 16px; }}
            .th-card h2, .th-card h3 {{ margin: 0 0 12px 0; color: #f9fafb; }}
            .th-muted {{ color: #9ca3af; }}
            .th-stat {{ font-size: 1.5rem; font-weight: 700; color: #f9fafb; }}
            .th-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px;
            margin-bottom: 12px; }}
            .th-field label {{ display: block; font-weight: 600; margin-bottom: 4px; color: #d1d5db; }}
            .th-field input, .th-field select, .th-field textarea {{
                width: 100%; min-width: 0; max-width: 100%; box-sizing: border-box; border: 1px solid #4b5563; border-radius: 6px;
                background: #111827; color: #f9fafb; padding: 8px; min-height: 38px;
            }}
            .th-field textarea {{ min-height: 82px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
            .th-check {{ display: flex; align-items: center; gap: 8px; margin: 6px 0; color: #d1d5db; }}
            .th-check input {{ width: auto; }}
            .th-btn {{ background: #2563eb; color: white; border: 0; border-radius: 6px; padding: 9px 14px; cursor:
            pointer; font-weight: 700; }}
            .th-btn.secondary {{ background: #4b5563; }}
            .th-btn.danger {{ background: #dc2626; }}
            .dash-tabs {{ display: flex; gap: 4px; overflow-x: auto; position: sticky; top: 0; z-index: 10; margin: 0 0
            16px; padding: 5px; background: #111827; border: 1px solid #374151; border-radius: 8px; }}
            .dash-tab {{ flex: 0 0 auto; border: 0; border-radius: 6px; padding: 9px 13px; background: transparent;
            color: #9ca3af; cursor: pointer; font-weight: 700; white-space: nowrap; }}
            .dash-tab:hover {{ background: #1f2937; color: #f9fafb; }} .dash-tab.active {{ background: #2563eb; color:
            white; }}
            .dash-panel {{ display: none; }} .dash-panel.active {{ display: block; }}
            .th-table {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
            .th-table th, .th-table td {{ border-bottom: 1px solid #374151; padding: 8px; text-align: left;
            vertical-align: top; }}
            .th-table th {{ color: #d1d5db; }}
            .th-inline {{ display: inline; }}
            .th-desk-head {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: end;
            gap: 12px; }}
            .th-desk-head form {{ flex: 1 1 320px; }}
            .th-ticket-meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px;
            margin: 14px 0; }}
            .th-ticket-meta > div {{ padding: 10px 12px; background: #111827; border: 1px solid #374151;
            border-radius: 6px; }}
            .th-ticket-meta strong {{ display: block; margin-top: 3px; color: #f9fafb; overflow-wrap: anywhere; }}
            .th-conversation {{ max-height: 620px; overflow-y: auto; margin: 14px 0; padding: 8px 14px;
            background: #111827; border: 1px solid #374151; border-radius: 8px; }}
            .th-live-status {{ display: inline-flex; align-items: center; gap: 6px; margin-top: 6px; color: #86efac;
            font-size: .78rem; font-weight: 700; }}
            .th-live-dot {{ width: 7px; height: 7px; border-radius: 50%; background: #22c55e;
            box-shadow: 0 0 0 3px rgba(34, 197, 94, .13); }}
            .th-live-status[data-state="loading"] {{ color: #bfdbfe; }}
            .th-live-status[data-state="loading"] .th-live-dot {{ background: #60a5fa; }}
            .th-live-status[data-state="paused"] {{ color: #9ca3af; }}
            .th-live-status[data-state="paused"] .th-live-dot {{ background: #6b7280; box-shadow: none; }}
            .th-live-status[data-state="error"] {{ color: #fbbf24; }}
            .th-live-status[data-state="error"] .th-live-dot {{ background: #f59e0b; }}
            .th-reply-feedback {{ min-height: 1.25rem; color: #fca5a5; font-size: .82rem; }}
            .th-message {{ padding: 12px 0; border-bottom: 1px solid #293241; }}
            .th-message:last-child {{ border-bottom: 0; }}
            .th-message-head {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 7px; margin-bottom: 4px; }}
            .th-message-author {{ color: #f3f4f6; font-weight: 700; }}
            .th-message-time, .th-badge {{ color: #7f8998; font-size: .75rem; }}
            .th-badge {{ padding: 1px 5px; border: 1px solid #4b5563; border-radius: 4px; text-transform: uppercase; }}
            .th-message-body {{ color: #d1d5db; line-height: 1.5; overflow-wrap: anywhere; }}
            .th-attachments {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }}
            .th-attachments a {{ color: #93c5fd; }}
            .th-image-attachment {{ display: block; width: fit-content; max-width: min(100%, 520px); }}
            .th-image-attachment img {{ display: block; max-width: 100%; max-height: 380px; object-fit: contain;
            border: 1px solid #374151; border-radius: 8px; background: #0b1220; }}
            .th-actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
            .th-actions form {{ margin: 0; }}
            .th-reply textarea {{ min-height: 120px; }}
            .th-modal-card {{ transition: opacity 0.2s ease; }}
            .th-modal-card.is-disabled {{ opacity: 0.65; border-color: #2a3441; }}
            .th-modal-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
            .th-modal-badge {{ font-size: 0.75rem; text-transform: none; color: #9ca3af; }}
            .th-btn-card {{ transition: opacity 0.2s ease; margin-bottom: 12px; }}
            .th-btn-card.is-disabled {{ opacity: 0.65; border-color: #2a3441; }}
            .th-btn-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
            .th-btn-badge {{ font-size: 0.75rem; text-transform: none; color: #9ca3af; }}

        </style>
        <div class="th-wrap" data-dashboard-tabs="1">
            <div class="th-card">
                <h2>TicketHub Dashboard</h2>
                <div class="th-grid">
                    <div><div class="th-muted">Profiles</div><div class="th-stat">{len(profiles)}</div></div>
                    <div><div class="th-muted">Open tickets</div><div class="th-stat">{open_count}</div></div>
                    <div><div class="th-muted">Claimed tickets</div><div class="th-stat">{claimed_count}</div></div>
                    <div><div class="th-muted">Closed tickets</div><div class="th-stat">{closed_count}</div></div>
                </div>
            </div>
            <div class="dash-tabs" role="tablist" aria-label="TicketHub sections">
                {self._dashboard_tab_button("tickets", "Tickets", active_tab)}
                {self._dashboard_tab_button("desk", "Ticket Desk", active_tab)}
                {self._dashboard_tab_button("setup", "Profile Setup", active_tab)}
                {self._dashboard_tab_button("buttons", "Control Buttons", active_tab)}
                {self._dashboard_tab_button("modal", "Modal", active_tab)}
                {self._dashboard_tab_button("panels", "Panels", active_tab)}
                {self._dashboard_tab_button("imports", "AAA3A Imports", active_tab)}
            </div>
            <section class="dash-panel{" active" if active_tab == "tickets" else ""}"
            data-tab-panel="tickets">{self._dashboard_tickets_section(guild, profiles, tickets, selected_profile, csrf)}</section>
            <section class="dash-panel{" active" if active_tab == "desk" else ""}"
            data-tab-panel="desk">{workspace}</section>
            <section class="dash-panel{" active" if active_tab == "setup" else ""}"
            data-tab-panel="setup">{self._dashboard_global_section(enabled, next_ticket_id, csrf)}{
            self._dashboard_profile_selector(profiles, selected_profile, csrf)
        }{self._dashboard_profile_section(guild, selected_profile, profile, csrf, stored_buttons=stored_buttons)}</section>
            <section class="dash-panel{" active" if active_tab == "buttons" else ""}"
            data-tab-panel="buttons">{self._dashboard_buttons_tab(guild, stored_buttons, csrf)}</section>
            <section class="dash-panel{" active" if active_tab == "modal" else ""}"
            data-tab-panel="modal">{self._dashboard_modal_section(selected_profile, profile, csrf)}</section>
            <section class="dash-panel{" active" if active_tab == "panels" else ""}"
            data-tab-panel="panels">{
            self._dashboard_panels_section(guild, selected_profile, profile, multi_panels, csrf)
        }</section>
            <section class="dash-panel{" active" if active_tab == "imports" else ""}"
            data-tab-panel="imports">{self._dashboard_imports_section(aaa3a_panels, csrf)}</section>
            {self._dashboard_tabs_script()}
        </div>
        """

    def _dashboard_global_section(
        self,
        enabled: bool,
        next_ticket_id: int,
        csrf: str,
    ) -> str:
        return f"""
        <div id="global" class="th-card">
            <h3>Global</h3>
            <form method="POST">
                {csrf}
                <input type="hidden" name="action" value="save_global">
                <label class="th-check"><input type="checkbox" name="enabled" value="1" {self._checked(enabled)}>
                Enabled</label>
                <div class="th-row">
                    {self._input("next_ticket_id", "Next Global Ticket ID", next_ticket_id, "number", min_value=1)}
                </div>
                <button class="th-btn" type="submit">Save Global Settings</button>
            </form>
        </div>
        """

    def _dashboard_profile_selector(
        self,
        profiles: dict[str, dict[str, typing.Any]],
        selected_profile: str,
        csrf: str,
    ) -> str:
        options = "".join(
            f'<option value="{self._h(name)}" {self._selected(name, selected_profile)}>{self._h(name)}</option>'
            for name in sorted(profiles)
        )
        return f"""
        <div class="th-card">
            <h3>Profiles</h3>
            <div class="th-grid">
                <form method="POST">
                    {csrf}
                    <input type="hidden" name="action" value="select_profile">
                    <div class="th-field">
                        <label>Selected Profile</label>
                        <select name="selected_profile" onchange="this.form.submit()">{options}</select>
                    </div>
                </form>
                <form method="POST">
                    {csrf}
                    <input type="hidden" name="action" value="create_profile">
                    <div class="th-row">
                        {self._input("new_profile_name", "New Profile Name", "")}
                        <div class="th-field"><label>Clone From</label><select name="clone_profile"><option
                        value="">Default</option>{options}</select></div>
                    </div>
                    <button class="th-btn" type="submit">Create Profile</button>
                </form>
                <form method="POST">
                    {csrf}
                    <input type="hidden" name="action" value="delete_profile">
                    <input type="hidden" name="selected_profile" value="{self._h(selected_profile)}">
                    <button class="th-btn danger" type="submit">Delete Selected Profile</button>
                </form>
            </div>
        </div>
        """

    def _dashboard_profile_section(
        self,
        guild: discord.Guild,
        profile_name: str,
        profile: dict[str, typing.Any],
        csrf: str,
        stored_buttons: dict[str, dict[str, typing.Any]] | None = None,
    ) -> str:
        role_fields = "".join(
            self._multi_role_select(
                guild,
                field,
                label,
                profile.get(field) or [],
            )
            for field, label in (
                ("support_role_ids", "Support Roles"),
                ("speak_role_ids", "Speak Roles"),
                ("view_role_ids", "View Roles"),
                ("ping_role_ids", "Ping Roles"),
                ("whitelist_role_ids", "Whitelist Roles"),
                ("blacklist_role_ids", "Blacklist Roles"),
            )
        )
        return f"""
        <div id="profile" class="th-card">
            <h3>Profile: {self._h(profile_name)}</h3>
            <form method="POST">
                {csrf}
                <input type="hidden" name="action" value="save_profile">
                <input type="hidden" name="selected_profile" value="{self._h(profile_name)}">
                <div class="th-grid">
                    <div>
                        <label class="th-check"><input type="checkbox" name="profile_enabled" value="1"
                        {self._checked(profile.get("enabled"))}> Profile Enabled</label>
                        <label class="th-check"><input type="checkbox" name="transcripts" value="1"
                        {self._checked(profile.get("transcripts"))}> Transcripts on Delete</label>
                        <label class="th-check"><input type="checkbox" name="dm_transcript" value="1"
                        {self._checked(profile.get("dm_transcript"))}> DM Transcripts</label>
                        <label class="th-check"><input type="checkbox" name="close_on_leave" value="1"
                        {self._checked(profile.get("close_on_leave"))}> Close on Leave</label>
                    </div>
                    <div>
                        <label class="th-check"><input type="checkbox" name="owner_can_close" value="1"
                        {self._checked(profile.get("owner_can_close"))}> Owner Can Close</label>
                        <label class="th-check"><input type="checkbox" name="owner_can_reopen" value="1"
                        {self._checked(profile.get("owner_can_reopen"))}> Owner Can Reopen</label>
                        <label class="th-check"><input type="checkbox" name="owner_can_add_members" value="1"
                        {self._checked(profile.get("owner_can_add_members"))}> Owner Can Add Members</label>
                        <label class="th-check"><input type="checkbox" name="owner_can_remove_members" value="1"
                        {self._checked(profile.get("owner_can_remove_members"))}> Owner Can Remove Members</label>
                    </div>
                </div>
                <div class="th-row">
                    <div class="th-field"><label>Form input</label><select name="form_mode">{
            self._option("modal", "Modals", profile.get("form_mode", "modal"))
        }{self._option("text", "DM text", profile.get("form_mode"))}{
            self._option("reaction", "DM reactions and text", profile.get("form_mode"))
        }</select></div>
                    <div class="th-field"><label>Ticket controls</label><select name="control_mode">{
            self._option("buttons", "Buttons", profile.get("control_mode", "buttons"))
        }{self._option("text", "Text commands", profile.get("control_mode"))}{
            self._option("reaction", "Emoji reactions", profile.get("control_mode"))
        }</select></div>
                    <div class="th-field"><label>Ticket Mode</label><select name="ticket_mode">{
            self._option("channel", "Channel", profile.get("ticket_mode"))
        }{self._option("thread", "Thread", profile.get("ticket_mode"))}</select></div>
                    {
            self._input(
                "max_open_tickets_by_member",
                "Max Open Per Member",
                profile.get("max_open_tickets_by_member", 5),
                "number",
                min_value=0,
                max_value=50,
            )
        }
                    {
            self._input(
                "close_request_timeout_minutes",
                "Close Timeout Minutes",
                self._close_request_timeout_minutes(profile),
                "number",
                min_value=self.MIN_CLOSE_REQUEST_TIMEOUT_MINUTES,
                max_value=self.MAX_CLOSE_REQUEST_TIMEOUT_MINUTES,
            )
        }
                    {
            self._input(
                "auto_delete_on_close_hours",
                "Auto-Delete Hours",
                "" if profile.get("auto_delete_on_close_hours") is None else profile.get("auto_delete_on_close_hours"),
                "text",
            )
        }
                    {
            self._input(
                "next_profile_ticket_id",
                "Next Profile Ticket ID",
                "" if profile.get("next_profile_ticket_id") is None else profile.get("next_profile_ticket_id"),
                "number",
                min_value=1,
            )
        }
                </div>
                <div class="th-row">
                    {
            self._input("channel_name", "Channel Name Template", profile.get("channel_name") or "ticket-{id}-{owner_name}")
        }
                    {self._category_select(guild, "ticket_category_id", "Open Category", profile.get("ticket_category_id"))}
                    {self._category_select(guild, "closed_category_id", "Closed Category", profile.get("closed_category_id"))}
                    {
            self._channel_select(guild, "thread_parent_channel_id", "Thread Parent", profile.get("thread_parent_channel_id"))
        }
                    {self._channel_select(guild, "log_channel_id", "Log Channel", profile.get("log_channel_id"))}
                    {
            self._channel_select(guild, "transcript_channel_id", "Transcript Channel", profile.get("transcript_channel_id"))
        }
                    {self._role_select(guild, "ticket_role_id", "Ticket Role", profile.get("ticket_role_id"))}
                </div>
                <h3>Messages</h3>
                <div class="th-row">
                    {self._textarea("welcome_message", "Welcome Message", profile.get("welcome_message") or "", rows=3)}
                    {self._textarea("custom_message", "Custom Message", profile.get("custom_message") or "", rows=3)}
                </div>
                <h3>Roles</h3>
                <div class="th-row">{role_fields}</div>
                <h3>Ticket Control Buttons Assignment</h3>
                <p class="th-muted">Assign up to 10 control buttons for tickets created with this profile. You can customize actions, labels, emojis, and permissions in the <a href="#buttons" onclick="const b = document.querySelector('[data-tab=buttons]'); if (b) b.click();">Control Buttons</a> tab.</p>
                {self._dashboard_profile_buttons_assignment(guild, profile, stored_buttons)}
                <button class="th-btn" type="submit">Save Profile</button>
            </form>
        </div>
        """

    def _dashboard_button_action_options(self, action: str) -> str:
        builtin_actions = [
            ("claim_toggle", "Claim / Unclaim Toggle"),
            ("claim", "Claim Only"),
            ("unclaim", "Unclaim Only"),
            ("lock_toggle", "Lock / Unlock Toggle"),
            ("lock", "Lock Only"),
            ("unlock", "Unlock Only"),
            ("close_toggle", "Close / Reopen Toggle"),
            ("close_reason", "Close with Reason (Modal)"),
            ("close_confirm", "Close with Confirmation"),
            ("close_now", "Instant Close"),
            ("reopen", "Reopen with Reason (Modal)"),
            ("reopen_now", "Instant Reopen"),
            ("members", "Manage Members"),
            ("transcript", "Transcript Bundle"),
            ("delete", "Delete Ticket Channel"),
        ]
        custom_actions = [
            ("send_message", "Send Canned Message / Embed"),
            ("ping_roles", "Ping Roles"),
            ("toggle_role", "Toggle Member Role"),
        ]
        return (
            '<optgroup label="Built-in Actions">'
            + "".join(self._option(val, desc, action) for val, desc in builtin_actions)
            + '</optgroup><optgroup label="Custom Actions">'
            + "".join(self._option(val, desc, action) for val, desc in custom_actions)
            + "</optgroup>"
        )

    def _dashboard_button_permission_fields(
        self,
        guild: discord.Guild,
        index: typing.Any,
        allowed_groups: set[str],
        allowed_roles: list[int],
    ) -> str:
        prefix = f"btn_{index}_" if isinstance(index, int) else (f"{index}_" if not str(index).endswith("_") else str(index))
        group_checks = "".join(
            f'<label class="th-check"><input type="checkbox" name="{prefix}group_{grp}" value="1" '
            f"{self._checked(grp in allowed_groups)}> {glbl}</label>"
            for grp, glbl in [
                ("support", "Support Roles"),
                ("admin", "Administrators"),
                ("owner", "Ticket Owner"),
                ("claimed_by", "Claimed Staff"),
                ("participants", "Added Members"),
                ("everyone", "Everyone"),
            ]
        )
        return f"""
        <h4>Permissions</h4>
        <div class="th-row">
            <div>
                <label class="th-muted">Allowed Permission Groups (if none selected, action defaults apply):</label>
                <div class="th-grid" style="margin-top: 6px;">{group_checks}</div>
            </div>
            {self._multi_role_select(guild, f"{prefix}allowed_roles", "Additional Allowed Roles", allowed_roles)}
        </div>
        """

    def _dashboard_control_button_card(
        self,
        guild: discord.Guild,
        index: int,
        btn: dict[str, typing.Any],
    ) -> str:
        btn_id = btn.get("id") or f"custom_{index + 1}"
        enabled = btn.get("enabled", False)
        action = btn.get("action", "claim_toggle" if index == 0 else "send_message")
        label = btn.get("label", "")
        emoji = btn.get("emoji", "")
        style = btn.get("style", "secondary")
        alt_label = btn.get("alt_label", "")
        alt_emoji = btn.get("alt_emoji", "")
        alt_style = btn.get("alt_style", "secondary")
        visibility = btn.get("visibility", "open")
        row = "" if btn.get("row") is None else str(btn.get("row"))
        allowed_groups = set(btn.get("allowed_groups") or [])
        allowed_roles = btn.get("allowed_role_ids") or []
        action_roles = btn.get("action_role_ids") or []
        action_msg = btn.get("action_message", "")
        action_embed_title = btn.get("action_embed_title", "")
        ephemeral = btn.get("ephemeral_response", False)

        summary_text = f"{self._h(label or btn_id)} • {self._h(action)}" if enabled else "Disabled"
        action_opts = self._dashboard_button_action_options(action)
        perm_html = self._dashboard_button_permission_fields(guild, index, allowed_groups, allowed_roles)

        return f"""
        <div class="th-card th-btn-card{" is-disabled" if not enabled else ""}" data-btn-card="1">
            <div class="th-btn-header">
                <label class="th-check">
                    <input type="checkbox" name="btn_{index}_enabled" value="1" data-btn-enabled="1"
                    {self._checked(enabled)}> <strong>Button {index + 1}: {self._h(btn_id.title())}</strong>
                </label>
                <span class="th-badge th-btn-badge" data-btn-summary="1">{summary_text}</span>
            </div>
            <input type="hidden" name="btn_{index}_id" value="{self._h(btn_id)}">
            <div class="th-btn-fields" data-btn-fields="1">
                <div class="th-row">
                    {self._input(f"btn_{index}_label", "Button Label", label)}
                    {self._input(f"btn_{index}_emoji", "Button Emoji", emoji)}
                    <div class="th-field"><label>Button Style</label><select name="btn_{index}_style">{
            self._option("primary", "Primary (Blurple)", style)
        }{self._option("secondary", "Secondary (Grey)", style)}{self._option("success", "Success (Green)", style)}{
            self._option("danger", "Danger (Red)", style)
        }</select></div>
                    <div class="th-field"><label>Action</label>
                        <select name="btn_{index}_action" data-btn-action-select="1">{action_opts}</select></div>
                    <div class="th-field"><label>Visibility</label><select name="btn_{index}_visibility">{
            self._option("open", "Open Tickets Only", visibility)
        }{self._option("closed", "Closed Tickets Only", visibility)}{
            self._option("always", "Always Visible", visibility)
        }</select></div>
                    <div class="th-field"><label>Row Position</label><select name="btn_{index}_row">{
            self._option("", "Auto (Next available)", row)
        }{self._option("0", "Row 1", row)}{self._option("1", "Row 2", row)}{self._option("2", "Row 3", row)}{
            self._option("3", "Row 4", row)
        }{self._option("4", "Row 5", row)}</select></div>
                </div>
                <div class="th-row" data-btn-field="toggle_alt">
                    {self._input(f"btn_{index}_alt_label", "Toggled / Active Label", alt_label)}
                    {self._input(f"btn_{index}_alt_emoji", "Toggled Emoji", alt_emoji)}
                    <div class="th-field"><label>Toggled Style</label><select name="btn_{index}_alt_style">{
            self._option("secondary", "Secondary (Grey)", alt_style)
        }{self._option("primary", "Primary (Blurple)", alt_style)}{self._option("success", "Success (Green)", alt_style)}{
            self._option("danger", "Danger (Red)", alt_style)
        }</select></div>
                </div>
                <div data-btn-field="custom_action">
                    <div class="th-row">
                        <div data-btn-field="custom_message">
                            {self._textarea(f"btn_{index}_action_message", "Message / Content", action_msg, rows=2)}
                        </div>
                        <div data-btn-field="custom_embed">
                            {self._input(f"btn_{index}_action_embed_title", "Embed Title (optional)", action_embed_title)}
                        </div>
                    </div>
                    <div class="th-row">
                        <div data-btn-field="custom_roles">
                            {
            self._multi_role_select(guild, f"btn_{index}_action_roles", "Target Roles (for Ping / Toggle)", action_roles)
        }
                        </div>
                        <div data-btn-field="custom_ephemeral">
                            <label class="th-check"><input type="checkbox" name="btn_{index}_ephemeral" value="1"
                            {self._checked(ephemeral)}> Ephemeral response (only visible to clicking user)</label>
                        </div>
                    </div>
                </div>
                {perm_html}
            </div>
        </div>
        """

    def _dashboard_control_buttons_section(
        self,
        guild: discord.Guild,
        profile: dict[str, typing.Any],
    ) -> str:
        buttons = profile.get("control_buttons") or self._default_control_buttons()
        cards = []
        for index in range(10):
            btn = buttons[index] if index < len(buttons) else {}
            cards.append(self._dashboard_control_button_card(guild, index, btn))
        return "".join(cards)
    def _dashboard_profile_buttons_assignment(
        self,
        guild: discord.Guild,
        profile: dict[str, typing.Any],
        stored_buttons: dict[str, dict[str, typing.Any]] | None = None,
    ) -> str:
        if stored_buttons is None:
            stored_buttons = self._default_stored_buttons()

        profile_buttons = profile.get("control_buttons") or []
        rows = []
        for slot_idx in range(10):
            assigned_id = ""
            if slot_idx < len(profile_buttons):
                btn_record = profile_buttons[slot_idx]
                if btn_record.get("enabled", True):
                    assigned_id = str(btn_record.get("id") or "").strip().lower()

            options = ['<option value="">-- None / Disabled --</option>']
            for b_id, b_data in stored_buttons.items():
                lbl = b_data.get("label") or b_id
                action = b_data.get("action", "")
                emoji = b_data.get("emoji") or ""
                emoji_disp = f"{emoji} " if emoji else ""
                opt_text = self._h(f"{emoji_disp}{lbl} ({b_id}) [{action}]")
                selected_attr = " selected" if assigned_id == b_id else ""
                options.append(f'<option value="{self._h(b_id)}"{selected_attr}>{opt_text}</option>')

            options_html = "".join(options)
            rows.append(
                f"""
                <div class="th-field" style="margin-bottom: 8px;">
                    <label><strong>Slot {slot_idx + 1}</strong></label>
                    <select name="slot_{slot_idx}_button">{options_html}</select>
                </div>
                """
            )

        slots_grid = "".join(rows)
        return f"""
        <div class="th-card" style="margin-bottom: 16px;">
            <div class="th-grid" style="grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px;">
                {slots_grid}
            </div>
            <div style="margin-top: 12px;">
                <a href="#buttons" class="th-btn" style="text-decoration: none; display: inline-block;"
                   onclick="const btn = document.querySelector('[data-tab=buttons]'); if (btn) btn.click();">
                    Manage Button Definitions in Control Buttons Tab &rarr;
                </a>
            </div>
        </div>
        """

    def _dashboard_parse_control_button_form(
        self,
        form_data: typing.Any,
        prefix: str = "button_",
    ) -> dict[str, typing.Any]:
        raw_id = self._dash_value(form_data, f"{prefix}id").strip().lower()
        clean_id = re.sub(r"[^a-z0-9_-]", "_", raw_id)[:32]
        if not clean_id:
            raise commands.BadArgument("Button ID is required and must contain alphanumeric characters.")

        action = self._dash_value(form_data, f"{prefix}action", "claim_toggle").strip().lower()
        label = self._clean_modal_text(self._dash_value(form_data, f"{prefix}label"), 80) or ""
        emoji_raw = self._dash_value(form_data, f"{prefix}emoji").strip()
        emoji = emoji_raw or None
        style = self._parse_button_style(self._dash_value(form_data, f"{prefix}style", "secondary"))
        alt_label = self._clean_modal_text(self._dash_value(form_data, f"{prefix}alt_label"), 80) or None
        alt_emoji_raw = self._dash_value(form_data, f"{prefix}alt_emoji").strip()
        alt_emoji = alt_emoji_raw or None
        alt_style_raw = self._dash_value(form_data, f"{prefix}alt_style")
        alt_style = self._parse_button_style(alt_style_raw) if alt_style_raw else None
        visibility = self._dash_value(form_data, f"{prefix}visibility", "open").strip().lower()
        if visibility not in {"open", "closed", "always"}:
            visibility = "open"
        row_raw = self._dash_value(form_data, f"{prefix}row")
        try:
            row_val = max(0, min(int(row_raw), 4)) if row_raw != "" and row_raw is not None else None
        except (ValueError, TypeError):
            row_val = None

        allowed_groups = []
        for grp in ("support", "admin", "owner", "claimed_by", "participants", "everyone"):
            if self._dash_bool(form_data, f"{prefix}group_{grp}"):
                allowed_groups.append(grp)

        def _clean_ids_from_form(key):
            res = []
            for val in self._dash_values(form_data, key):
                with contextlib.suppress(ValueError, TypeError):
                    res.append(int(val))
            return sorted(set(res))

        allowed_roles = _clean_ids_from_form(f"{prefix}allowed_roles")
        action_roles = _clean_ids_from_form(f"{prefix}action_roles")
        action_message = self._dash_value(form_data, f"{prefix}action_message").strip()[:2000]
        action_embed_title = self._clean_modal_text(self._dash_value(form_data, f"{prefix}action_embed_title"), 256) or None
        ephemeral = self._dash_bool(form_data, f"{prefix}ephemeral")

        return {
            "id": clean_id,
            "action": action,
            "enabled": True,
            "label": label,
            "emoji": emoji,
            "style": style,
            "alt_label": alt_label,
            "alt_emoji": alt_emoji,
            "alt_style": alt_style,
            "visibility": visibility,
            "row": row_val,
            "allowed_groups": allowed_groups,
            "allowed_role_ids": allowed_roles,
            "action_message": action_message,
            "action_embed_title": action_embed_title,
            "action_role_ids": action_roles,
            "ephemeral_response": ephemeral,
        }

    def _dashboard_buttons_tab(
        self,
        guild: discord.Guild,
        stored_buttons: dict[str, dict[str, typing.Any]],
        csrf: str,
    ) -> str:
        cards = [
            f"""
            <div class="th-card">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;">
                    <div>
                        <h3>Ticket Control Buttons Library</h3>
                        <p class="th-muted" style="margin: 0;">Configure reusable control buttons for tickets in this server. Create custom buttons or modify defaults, then assign them to specific profiles in <strong>Profile Setup</strong>.</p>
                    </div>
                    <form method="POST">
                        {csrf}
                        <input type="hidden" name="action" value="reset_control_buttons">
                        <button class="th-btn th-btn-danger" type="submit" onclick="return confirm('Reset all stored control buttons to default definitions?')">Reset All to Defaults</button>
                    </form>
                </div>
            </div>
            """,
            self._dashboard_create_button_card(guild, csrf),
        ]
        for btn_id, btn in stored_buttons.items():
            cards.append(self._dashboard_stored_button_card(guild, btn_id, btn, csrf))
        return "".join(cards)

    def _dashboard_create_button_card(self, guild: discord.Guild, csrf: str) -> str:
        action_opts = self._dashboard_button_action_options("send_message")
        create_perm_html = self._dashboard_button_permission_fields(guild, "button", set(), [])
        return f"""
        <div class="th-card th-btn-card" data-btn-card="1">
            <div class="th-btn-header">
                <strong>+ Create New Control Button</strong>
                <span class="th-btn-badge" data-btn-summary="1">New Button</span>
            </div>
            <form method="POST">
                {csrf}
                <input type="hidden" name="action" value="create_control_button">
                <div class="th-row">
                    <div class="th-field">
                        <label>Button ID (slug, letters/numbers/underscores)</label>
                        <input type="text" name="button_id" placeholder="e.g. escalate, call_lead" required pattern="[a-zA-Z0-9_-]{{1,32}}">
                    </div>
                    <div class="th-field">
                        <label>Action</label>
                        <select name="button_action" data-btn-action-select="1">{action_opts}</select>
                    </div>
                    <div class="th-field">
                        <label>Button Label</label>
                        <input type="text" name="button_label" placeholder="e.g. Escalate Ticket">
                    </div>
                    <div class="th-field">
                        <label>Button Emoji</label>
                        <input type="text" name="button_emoji" placeholder="e.g. ⚠️">
                    </div>
                    <div class="th-field">
                        <label>Style</label>
                        <select name="button_style">
                            {self._option("secondary", "Secondary (Grey)", "secondary")}
                            {self._option("primary", "Primary (Blurple)", "secondary")}
                            {self._option("success", "Success (Green)", "secondary")}
                            {self._option("danger", "Danger (Red)", "secondary")}
                        </select>
                    </div>
                    <div class="th-field">
                        <label>Visibility</label>
                        <select name="button_visibility">
                            {self._option("open", "Open Tickets Only", "open")}
                            {self._option("closed", "Closed Tickets Only", "open")}
                            {self._option("always", "Always Visible", "open")}
                        </select>
                    </div>
                    <div class="th-field">
                        <label>Row Position</label>
                        <select name="button_row">
                            {self._option("", "Auto (Next available)", "")}
                            {self._option("0", "Row 1", "")}{self._option("1", "Row 2", "")}
                            {self._option("2", "Row 3", "")}{self._option("3", "Row 4", "")}
                            {self._option("4", "Row 5", "")}
                        </select>
                    </div>
                </div>
                <div class="th-row" data-btn-field="toggle_alt">
                    {self._input("button_alt_label", "Toggled / Active Label", "")}
                    {self._input("button_alt_emoji", "Toggled Emoji", "")}
                    <div class="th-field"><label>Toggled Style</label><select name="button_alt_style">
                        {self._option("secondary", "Secondary (Grey)", "secondary")}
                        {self._option("primary", "Primary (Blurple)", "secondary")}
                        {self._option("success", "Success (Green)", "secondary")}
                        {self._option("danger", "Danger (Red)", "secondary")}
                    </select></div>
                </div>
                <div data-btn-field="custom_action">
                    <div class="th-row">
                        <div data-btn-field="custom_message">
                            {self._textarea("button_action_message", "Message / Content", "", rows=2)}
                        </div>
                        <div data-btn-field="custom_embed">
                            {self._input("button_action_embed_title", "Embed Title (optional)", "")}
                        </div>
                    </div>
                    <div class="th-row">
                        <div data-btn-field="custom_roles">
                            {self._multi_role_select(guild, "button_action_roles", "Target Roles (for Ping / Toggle)", [])}
                        </div>
                        <div data-btn-field="custom_ephemeral">
                            <label class="th-check"><input type="checkbox" name="button_ephemeral" value="1"> Ephemeral response (only visible to clicking user)</label>
                        </div>
                    </div>
                </div>
                {create_perm_html}
                <button class="th-btn" type="submit">Create Button</button>
            </form>
        </div>
        """

    def _dashboard_stored_button_card(
        self,
        guild: discord.Guild,
        btn_id: str,
        btn: dict[str, typing.Any],
        csrf: str,
    ) -> str:
        action = btn.get("action", "claim_toggle")
        label = btn.get("label", "")
        emoji = btn.get("emoji", "")
        style = btn.get("style", "secondary")
        alt_label = btn.get("alt_label", "")
        alt_emoji = btn.get("alt_emoji", "")
        alt_style = btn.get("alt_style", "secondary")
        visibility = btn.get("visibility", "open")
        row = "" if btn.get("row") is None else str(btn.get("row"))
        allowed_groups = set(btn.get("allowed_groups") or [])
        allowed_roles = btn.get("allowed_role_ids") or []
        action_roles = btn.get("action_role_ids") or []
        action_msg = btn.get("action_message", "")
        action_embed_title = btn.get("action_embed_title", "")
        ephemeral = btn.get("ephemeral_response", False)

        summary_text = f"{self._h(label or btn_id)} • {self._h(action)}"
        action_opts = self._dashboard_button_action_options(action)
        perm_html = self._dashboard_button_permission_fields(guild, "button", allowed_groups, allowed_roles)
        defaults = self._default_stored_buttons()
        is_default = btn_id in defaults
        del_label = "Reset to Default" if is_default else "Delete Button"

        return f"""
        <div class="th-card th-btn-card" data-btn-card="1">
            <div class="th-btn-header">
                <strong>Button: {self._h(btn_id)} ({self._h(label or btn_id)})</strong>
                <span class="th-btn-badge" data-btn-summary="1">{summary_text}</span>
            </div>
            <form method="POST">
                {csrf}
                <input type="hidden" name="action" value="save_control_button">
                <input type="hidden" name="button_id" value="{self._h(btn_id)}">
                <div class="th-row">
                    <div class="th-field"><label>Action</label><select name="button_action" data-btn-action-select="1">{action_opts}</select></div>
                    <div class="th-field"><label>Button Label</label><input type="text" name="button_label" value="{self._h(label)}"></div>
                    <div class="th-field"><label>Button Emoji</label><input type="text" name="button_emoji" value="{self._h(emoji or '')}"></div>
                    <div class="th-field"><label>Style</label><select name="button_style">
                        {self._option("secondary", "Secondary (Grey)", style)}{self._option("primary", "Primary (Blurple)", style)}
                        {self._option("success", "Success (Green)", style)}{self._option("danger", "Danger (Red)", style)}
                    </select></div>
                    <div class="th-field"><label>Visibility</label><select name="button_visibility">
                        {self._option("open", "Open Tickets Only", visibility)}{self._option("closed", "Closed Tickets Only", visibility)}
                        {self._option("always", "Always Visible", visibility)}
                    </select></div>
                    <div class="th-field"><label>Row Position</label><select name="button_row">
                        {self._option("", "Auto", row)}{self._option("0", "Row 1", row)}{self._option("1", "Row 2", row)}
                        {self._option("2", "Row 3", row)}{self._option("3", "Row 4", row)}{self._option("4", "Row 5", row)}
                    </select></div>
                </div>
                <div class="th-row" data-btn-field="toggle_alt">
                    {self._input("button_alt_label", "Toggled / Active Label", alt_label or "")}
                    {self._input("button_alt_emoji", "Toggled Emoji", alt_emoji or "")}
                    <div class="th-field"><label>Toggled Style</label><select name="button_alt_style">
                        {self._option("secondary", "Secondary (Grey)", alt_style)}{self._option("primary", "Primary (Blurple)", alt_style)}
                        {self._option("success", "Success (Green)", alt_style)}{self._option("danger", "Danger (Red)", alt_style)}
                    </select></div>
                </div>
                <div data-btn-field="custom_action">
                    <div class="th-row">
                        <div data-btn-field="custom_message">{self._textarea("button_action_message", "Message / Content", action_msg, rows=2)}</div>
                        <div data-btn-field="custom_embed">{self._input("button_action_embed_title", "Embed Title (optional)", action_embed_title or "")}</div>
                    </div>
                    <div class="th-row">
                        <div data-btn-field="custom_roles">{self._multi_role_select(guild, "button_action_roles", "Target Roles (for Ping / Toggle)", action_roles)}</div>
                        <div data-btn-field="custom_ephemeral">
                            <label class="th-check"><input type="checkbox" name="button_ephemeral" value="1" {self._checked(ephemeral)}> Ephemeral response</label>
                        </div>
                    </div>
                </div>
                {perm_html}
                <div style="display: flex; gap: 8px; margin-top: 10px;">
                    <button class="th-btn" type="submit">Save Changes</button>
                </div>
            </form>
            <form method="POST" style="margin-top: 8px;">
                {csrf}
                <input type="hidden" name="action" value="delete_control_button">
                <input type="hidden" name="button_id" value="{self._h(btn_id)}">
                <button class="th-btn th-btn-danger" type="submit" onclick="return confirm('Are you sure you want to delete / reset this button?')">{del_label}</button>
            </form>
        </div>
        """





    def _modal_type_options(self, selected: str) -> str:
        types = [
            ("text", "Text"),
            ("text_display", "Text Display (Notice)"),
            ("choice", "Choice (Dropdown)"),
            ("radio", "Radio Group"),
            ("checkbox", "Checkbox Toggle"),
            ("checkbox_group", "Checkbox Group"),
            ("boolean", "Boolean (Yes/No)"),
            ("user", "User Select"),
            ("role", "Role Select"),
            ("mentionable", "Mentionable Select"),
            ("channel", "Channel Select"),
            ("file", "File Upload"),
        ]
        return "".join(self._option(t, label, selected) for t, label in types)

    def _dashboard_modal_section(
        self,
        profile_name: str,
        profile: dict[str, typing.Any],
        csrf: str,
    ) -> str:
        fields = list(profile.get("creating_modal") or [])
        rows = []
        for index in range(5):
            field = fields[index] if index < len(fields) else {}
            field_type = field.get("type") or "text"
            style = "short" if int(field.get("style") or 2) == discord.TextStyle.short.value else "paragraph"
            is_enabled = index < len(fields)
            summary_label = self._h(field.get("label") or "Question") if is_enabled else "Disabled"
            summary_text = f"{summary_label} • {self._h(field_type)}" if is_enabled else "Disabled"
            input_label = self._input(f"modal_{index}_label", "Label", field.get("label") or "")
            input_desc = self._input(
                f"modal_{index}_description",
                "Label Description (optional)",
                field.get("description") or "",
            )
            input_placeholder = self._input(
                f"modal_{index}_placeholder",
                "Placeholder",
                field.get("placeholder") or "",
            )
            input_default = self._input(f"modal_{index}_default", "Default Value", field.get("default") or "")
            input_min_len = self._input(
                f"modal_{index}_min_length",
                "Min Length",
                "" if field.get("min_length") is None else field.get("min_length"),
                "number",
                min_value=0,
                max_value=4000,
            )
            input_max_len = self._input(
                f"modal_{index}_max_length",
                "Max Length",
                "" if field.get("max_length") is None else field.get("max_length"),
                "number",
                min_value=1,
                max_value=4000,
            )
            input_min_val = self._input(
                f"modal_{index}_min_values",
                "Min Values",
                "" if field.get("min_values") is None else field.get("min_values"),
                "number",
                min_value=0,
                max_value=25,
            )
            input_max_val = self._input(
                f"modal_{index}_max_values",
                "Max Values",
                "" if field.get("max_values") is None else field.get("max_values"),
                "number",
                min_value=1,
                max_value=25,
            )
            input_choices = self._input(
                f"modal_{index}_choices",
                "Choices (comma-separated)",
                ", ".join(field.get("choices") or []),
            )
            rows.append(
                f"""
                <div class="th-card th-modal-card{" is-disabled" if not is_enabled else ""}" data-modal-card="1">
                    <div class="th-modal-header">
                        <label class="th-check">
                            <input type="checkbox" name="modal_{index}_enabled" value="1" data-modal-enabled="1"
                            {self._checked(is_enabled)}> <strong>Question {index + 1}</strong>
                        </label>
                        <span class="th-badge th-modal-badge" data-modal-summary="1">{summary_text}</span>
                    </div>
                    <div class="th-modal-fields" data-modal-fields="1">
                        <div class="th-row">
                            <div data-modal-field="label">{input_label}</div>
                            <div data-modal-field="description">{input_desc}</div>
                            <div class="th-field" data-modal-field="type">
                                <label>Type</label>
                                <select name="modal_{index}_type" data-modal-type-select="1">
                                    {self._modal_type_options(field_type)}
                                </select>
                            </div>
                            <div class="th-field" data-modal-field="style">
                                <label>Text Style</label>
                                <select name="modal_{index}_style">
                                    {self._option("paragraph", "Paragraph", style)}
                                    {self._option("short", "Short", style)}
                                </select>
                            </div>
                            <div data-modal-field="required">
                                <label class="th-check">
                                    <input type="checkbox" name="modal_{index}_required" value="1"
                                    {self._checked(field.get("required", True))}> Required
                                </label>
                            </div>
                        </div>
                        <div class="th-row">
                            <div data-modal-field="placeholder">{input_placeholder}</div>
                            <div data-modal-field="default">{input_default}</div>
                            <div data-modal-field="text_length">{input_min_len}</div>
                            <div data-modal-field="text_length">{input_max_len}</div>
                            <div data-modal-field="values_count">{input_min_val}</div>
                            <div data-modal-field="values_count">{input_max_val}</div>
                        </div>
                        <div data-modal-field="choices">{input_choices}</div>
                    </div>
                </div>
                """,
            )
        remove_options = "".join(
            f'<option value="{index}">{index}. {self._h(field.get("label") or "Question")}</option>'
            for index, field in enumerate(fields, start=1)
        )
        remove_form = (
            f"""
            <form method="POST">
                {csrf}
                <input type="hidden" name="action" value="remove_modal_question">
                <input type="hidden" name="selected_profile" value="{self._h(profile_name)}">
                <div class="th-row">
                    <div class="th-field">
                        <label>Remove Question</label>
                        <select name="remove_modal_index">{remove_options}</select>
                    </div>
                </div>
                <button class="th-btn danger" type="submit">Remove Question</button>
            </form>
            """
            if fields
            else '<p class="th-muted">No modal questions are currently configured.</p>'
        )
        return f"""
        <div id="modal" class="th-card">
            <h3>Modal Questions</h3>
            <form method="POST">
                {csrf}
                <input type="hidden" name="action" value="save_modal">
                <input type="hidden" name="selected_profile" value="{self._h(profile_name)}">
                {"".join(rows)}
                <button class="th-btn" type="submit">Save Modal</button>
            </form>
            <div class="th-grid">
                <form method="POST" data-modal-add-form="1">
                    {csrf}
                    <input type="hidden" name="action" value="add_modal_question">
                    <input type="hidden" name="selected_profile" value="{self._h(profile_name)}">
                    <div class="th-row">
                        <div data-modal-field="label">{self._input("add_modal_label", "New Question Label", "")}</div>
                        <div data-modal-field="description">{
            self._input("add_modal_description", "Label Description (optional)", "")
        }</div>
                        <div class="th-field" data-modal-field="type">
                            <label>Type</label>
                            <select name="add_modal_type" data-modal-type-select="1">
                                {self._modal_type_options("text")}
                            </select>
                        </div>
                        <div class="th-field" data-modal-field="style"><label>Text Style</label><select
                        name="add_modal_style">{self._option("paragraph", "Paragraph", "paragraph")}{
            self._option("short", "Short", "paragraph")
        }</select></div>
                        <div data-modal-field="required">
                            <label class="th-check">
                                <input type="checkbox" name="add_modal_required" value="1" checked> Required
                            </label>
                        </div>
                    </div>
                    <div class="th-row">
                        <div data-modal-field="placeholder">{self._input("add_modal_placeholder", "Placeholder", "")}</div>
                        <div data-modal-field="default">{self._input("add_modal_default", "Default Value", "")}</div>
                        <div data-modal-field="text_length">{
            self._input("add_modal_min_length", "Min Length", "", "number", min_value=0, max_value=4000)
        }</div>
                        <div data-modal-field="text_length">{
            self._input("add_modal_max_length", "Max Length", "", "number", min_value=1, max_value=4000)
        }</div>
                        <div data-modal-field="values_count">{
            self._input("add_modal_min_values", "Min Values", "", "number", min_value=0, max_value=25)
        }</div>
                        <div data-modal-field="values_count">{
            self._input("add_modal_max_values", "Max Values", "", "number", min_value=1, max_value=25)
        }</div>
                    </div>
                    <div data-modal-field="choices">{self._input("add_modal_choices", "Choices (comma-separated)", "")}</div>
                    <button class="th-btn secondary" type="submit">Add Question</button>
                </form>
                {remove_form}
            </div>
            <form class="th-inline" method="POST">{csrf}<input type="hidden" name="action"
            value="default_reason_modal"><input type="hidden" name="selected_profile"
            value="{self._h(profile_name)}"><button class="th-btn secondary" type="submit">Use Default Reason
            Modal</button></form>
            <form class="th-inline" method="POST">{csrf}<input type="hidden" name="action" value="clear_modal"><input
            type="hidden" name="selected_profile" value="{self._h(profile_name)}"><button class="th-btn danger"
            type="submit">Clear Modal</button></form>
        </div>
        """

    def _dashboard_panels_section(
        self,
        guild: discord.Guild,
        profile_name: str,
        profile: dict[str, typing.Any],
        multi_panels: dict[str, typing.Any],
        csrf: str,
    ) -> str:
        multi_rows = []
        for message_id, raw_record in multi_panels.items():
            try:
                record = self._sanitize_multi_panel_record(
                    raw_record,
                    message_id=int(message_id),
                )
            except (TypeError, ValueError):
                record = None
            if record is None:
                continue
            option_text = ", ".join(f"{option['label']} ({option['profile']})" for option in record["options"])
            multi_rows.append(
                f"<tr><td>{self._h(record['message_id'])}</td><td>{self._h(record['channel_id'])}</td><td>{self._h(record['style'])}</td><td>{self._h(option_text)}</td></tr>",
            )
        multi_table = "".join(multi_rows) or '<tr><td colspan="4" class="th-muted">No multi-panels configured.</td></tr>'
        return f"""
        <div id="panels" class="th-card">
            <h3>Panels</h3>
            <div class="th-grid">
                <form method="POST" data-panel-form="1">
                    {csrf}
                    <input type="hidden" name="action" value="post_panel">
                    <input type="hidden" name="selected_profile" value="{self._h(profile_name)}">
                    <div class="th-row">
                        {
            self._channel_select(guild, "post_panel_channel_id", "Post Panel Channel", profile.get("panel_channel_id"))
        }
                        <div class="th-field"><label>Style</label><select name="post_panel_style" data-panel-style-select="1">{
            self._option("button", "Button", profile.get("panel_style"))
        }{self._option("dropdown", "Dropdown", profile.get("panel_style"))}{
            self._option("text", "Text commands", profile.get("panel_style"))
        }{self._option("reaction", "Emoji reactions", profile.get("panel_style"))}</select></div>
                    </div>
                    <h4>Embed Appearance</h4>
                    <div class="th-row">
                        {self._input("post_panel_title", "Embed Title", profile.get("panel_title") or "Need Help?")}
                        {
            self._input(
                "post_panel_color",
                "Embed Color (Hex)",
                self._color_hex(profile.get("panel_color")) if profile.get("panel_color") is not None else "#5865F2",
            )
        }
                        {self._input("post_panel_footer", "Footer Text", profile.get("panel_footer") or "TicketHub")}
                    </div>
                    {
            self._textarea(
                "post_panel_message",
                "Embed Message / Description",
                profile.get("panel_message") or "Open a ticket and staff will help you as soon as possible.",
                rows=2,
            )
        }
                    <div class="th-row">
                        {
            self._input(
                "post_panel_thumbnail_url",
                "Thumbnail URL (optional, defaults to server icon)",
                profile.get("panel_thumbnail_url") or "",
            )
        }
                        {self._input("post_panel_image_url", "Main Image URL (optional)", profile.get("panel_image_url") or "")}
                        <label class="th-check"><input type="checkbox" name="post_panel_show_details" value="1" {
            self._checked(profile.get("panel_show_details", True))
        }> Include Profile & Max Open details</label>
                    </div>
                    <div data-panel-component="button">
                        <h4>Button Appearance</h4>
                        <div class="th-row">
                            {
            self._input("post_panel_button_label", "Button Label", profile.get("panel_button_label") or "Open Ticket")
        }
                            {
            self._input(
                "post_panel_button_emoji", "Button Emoji", profile.get("panel_button_emoji") or DEFAULT_PANEL_BUTTON_EMOJI
            )
        }
                            <div class="th-field"><label>Button Style</label><select name="post_panel_button_style">{
            self._option("primary", "Primary (Blurple)", profile.get("panel_button_style", "primary"))
        }{self._option("secondary", "Secondary (Grey)", profile.get("panel_button_style"))}{
            self._option("success", "Success (Green)", profile.get("panel_button_style"))
        }{self._option("danger", "Danger (Red)", profile.get("panel_button_style"))}</select></div>
                        </div>
                    </div>
                    <div data-panel-component="dropdown">
                        <h4>Dropdown Appearance</h4>
                        <div class="th-row">
                            {
            self._input(
                "post_panel_dropdown_placeholder",
                "Dropdown Placeholder",
                profile.get("panel_dropdown_placeholder") or "Open a ticket...",
            )
        }
                            {
            self._input("post_panel_dropdown_label", "Option Label", profile.get("panel_dropdown_label") or "Open Ticket")
        }
                            {
            self._input(
                "post_panel_dropdown_emoji", "Option Emoji", profile.get("panel_dropdown_emoji") or DEFAULT_PANEL_BUTTON_EMOJI
            )
        }
                            {
            self._input(
                "post_panel_dropdown_description",
                "Option Description (optional)",
                profile.get("panel_dropdown_description") or "",
            )
        }
                        </div>
                    </div>
                    <button class="th-btn" type="submit">Post Panel</button>
                </form>
                <form method="POST">
                    {csrf}
                    <input type="hidden" name="action" value="attach_panel">
                    <input type="hidden" name="selected_profile" value="{self._h(profile_name)}">
                    {self._input("attach_panel_channel_id", "Channel ID", profile.get("panel_channel_id") or "")}
                    {self._input("attach_panel_message_id", "Message ID", profile.get("panel_message_id") or "")}
                    <div class="th-field"><label>Style</label><select name="attach_panel_style">{
            self._option("button", "Button", profile.get("panel_style"))
        }{self._option("dropdown", "Dropdown", profile.get("panel_style"))}{
            self._option("text", "Text commands", profile.get("panel_style"))
        }{self._option("reaction", "Emoji reactions", profile.get("panel_style"))}</select></div>
                    <button class="th-btn" type="submit">Attach Panel</button>
                </form>
                <form method="POST">
                    {csrf}
                    <input type="hidden" name="action" value="clear_panel">
                    {self._input("clear_panel_channel_id", "Channel ID", profile.get("panel_channel_id") or "")}
                    {self._input("clear_panel_message_id", "Message ID", profile.get("panel_message_id") or "")}
                    <button class="th-btn danger" type="submit">Clear Panel</button>
                </form>
            </div>
            <h3>Multi-Panels</h3>
            <table
            class="th-table"><thead><tr><th>Message</th><th>Channel</th><th>Style</th><th>Options</th></tr></thead><tbody>{
            multi_table
        }</tbody></table>
            <form method="POST">
                {csrf}
                <input type="hidden" name="action" value="save_multi_panel">
                <div class="th-row">
                    {self._channel_select(guild, "multi_panel_channel_id", "Multi-Panel Channel", None)}
                    {self._input("multi_panel_message_id", "Message ID (leave empty to post new message)", "")}
                    <div class="th-field"><label>Style</label><select name="multi_panel_style">{
            self._option("button", "Button", "button")
        }{self._option("dropdown", "Dropdown", "button")}{self._option("text", "Text commands", "button")}{
            self._option("reaction", "Emoji reactions", "button")
        }</select></div>
                    {self._input("multi_panel_placeholder", "Dropdown Placeholder", "Choose a ticket type...")}
                </div>
                <div class="th-row">
                    {self._input("multi_panel_title", "Embed Title (when posting new)", "Need Help?")}
                    {self._input("multi_panel_color", "Embed Color (Hex)", "#5865F2")}
                </div>
                {
            self._textarea(
                "multi_panel_message", "Embed Message (when posting new)", "Select an option below to open a ticket.", rows=2
            )
        }
                {self._textarea("multi_panel_options", "Options: profile | emoji | label | description", "", rows=6)}
                <button class="th-btn" type="submit">Save Multi-Panel</button>
            </form>
            <form method="POST">
                {csrf}
                <input type="hidden" name="action" value="clear_multi_panel">
                <div class="th-row">
                    {self._input("multi_panel_channel_id", "Multi-Panel Channel ID", "")}
                    {self._input("multi_panel_message_id", "Multi-Panel Message ID", "")}
                </div>
                <button class="th-btn danger" type="submit">Clear Multi-Panel</button>
            </form>
        </div>
        """

    async def _dashboard_workspace_messages(
        self,
        channel: discord.TextChannel | discord.Thread,
    ) -> str:
        """Render the latest ticket messages for the dashboard workspace."""
        try:
            messages = [message async for message in channel.history(limit=50)]
        except (discord.Forbidden, discord.HTTPException):
            return '<div class="th-muted">I could not read this ticket channel\'s message history.</div>'
        if not messages:
            return '<div class="th-muted">No messages have been sent in this ticket yet.</div>'

        rendered = []
        for message in reversed(messages):
            author_name = getattr(message.author, "display_name", str(message.author))
            timestamp = message.created_at.strftime("%b %d, %Y %H:%M UTC")
            badge = '<span class="th-badge">Bot</span>' if message.author.bot else ""
            content = (message.clean_content or "").strip()
            if not content and message.embeds:
                embed = message.embeds[0]
                footer_text = str(embed.footer.text or "")
                is_dashboard_reply = footer_text.startswith("TicketHub Dashboard • Ticket #")
                if is_dashboard_reply and embed.author.name:
                    author_name = embed.author.name
                    badge = '<span class="th-badge">Dashboard</span>'
                content = str(embed.title or embed.description or "Embedded message")[:500]
                if not is_dashboard_reply:
                    content = f"[Embed] {content}"
            if not content and not message.attachments:
                content = "[Message without text]"
            body = f'<div class="th-message-body">{self._h(content).replace(chr(10), "<br>")}</div>' if content else ""
            attachment_links = []
            for attachment in message.attachments:
                attachment_url = html.escape(attachment.url, quote=True)
                filename = self._h(attachment.filename)
                content_type = str(attachment.content_type or "").lower()
                is_image = content_type.startswith("image/") or attachment.filename.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"),
                )
                if is_image:
                    attachment_links.append(
                        f'<a class="th-image-attachment" href="{attachment_url}" target="_blank" '
                        f'rel="noopener noreferrer" aria-label="Open {filename} full size">'
                        f'<img src="{attachment_url}" alt="{filename}" loading="lazy"></a>',
                    )
                else:
                    attachment_links.append(
                        f'<a href="{attachment_url}" target="_blank" rel="noopener noreferrer">{filename}</a>',
                    )
            attachments = f'<div class="th-attachments">{"".join(attachment_links)}</div>' if attachment_links else ""
            rendered.append(
                '<article class="th-message">'
                '<div class="th-message-head">'
                f'<span class="th-message-author">{self._h(author_name)}</span>'
                f"{badge}"
                f'<a class="th-message-time" href="{html.escape(message.jump_url, quote=True)}" '
                f'target="_blank" rel="noopener noreferrer">{self._h(timestamp)}</a>'
                "</div>"
                f"{body}{attachments}"
                "</article>",
            )
        return "".join(rendered)

    async def _dashboard_workspace_section(
        self,
        guild: discord.Guild,
        tickets: dict[str, dict[str, typing.Any]],
        selected_ticket_id: int | None,
        csrf: str,
    ) -> str:
        """Render a Discord-like ticket workspace for dashboard staff."""
        ordered = sorted(
            tickets.values(),
            key=lambda item: int(item.get("id") or 0),
            reverse=True,
        )[:200]
        ticket_options = "".join(
            '<option value="{ticket_id}" {selected}>#{ticket_id} · {status} · {profile}</option>'.format(
                ticket_id=self._h(record.get("id")),
                selected=self._selected(record.get("id"), selected_ticket_id),
                status=self._h(record.get("status") or "open"),
                profile=self._h(record.get("profile") or "main"),
            )
            for record in ordered
        )
        if selected_ticket_id is None:
            return """
            <div class="th-card">
                <h3>Ticket Desk</h3>
                <p class="th-muted">There are no tracked tickets to manage.</p>
            </div>
            """

        record = tickets[str(selected_ticket_id)]
        channel = await self._fetch_ticket_channel(guild, record)
        owner = guild.get_member(int(record.get("owner_id") or 0))
        claimed_by = guild.get_member(int(record.get("claimed_by") or 0))
        participants = [guild.get_member(int(member_id)) for member_id in (record.get("participants") or [])]
        participant_text = ", ".join(str(member) for member in participants if member is not None) or "None"
        channel_link = (
            f'<a href="{html.escape(channel.jump_url, quote=True)}" target="_blank" rel="noopener noreferrer">Open in Discord</a>'
            if channel is not None
            else "Channel unavailable"
        )
        conversation = (
            await self._dashboard_workspace_messages(channel)
            if channel is not None
            else '<div class="th-muted">This ticket channel is unavailable.</div>'
        )
        status = str(record.get("status") or "open")
        lifecycle_buttons = []
        if status == "open":
            lifecycle_buttons.append(
                ("unclaim" if record.get("claimed_by") else "claim").capitalize(),
            )
            lifecycle_buttons.append(
                ("unlock" if record.get("locked") else "lock").capitalize(),
            )
            lifecycle_buttons.append("Close")
        else:
            lifecycle_buttons.append("Reopen")
        lifecycle_buttons.extend(("Transcript", "Delete"))
        action_buttons = "".join(
            '<button class="th-btn{danger}" type="submit" name="ticket_action" value="{action}"{confirm}>{label}</button>'.format(
                danger=" danger" if label == "Delete" else " secondary",
                action=label.lower(),
                confirm=(" onclick=\"return confirm('Permanently delete this ticket channel?')\"" if label == "Delete" else ""),
                label=label,
            )
            for label in lifecycle_buttons
        )
        reply_disabled = " disabled" if status != "open" or channel is None else ""
        owner_text = self._h(owner or record.get("owner_id") or "Unknown")
        reopen_notice = '<span class="th-muted"> Reopen the ticket before replying.</span>' if status != "open" else ""

        return f"""
        <div id="ticket-desk" class="th-card">
            <div class="th-desk-head">
                <div><h3>Ticket Desk</h3><div class="th-muted">Read and manage a ticket without leaving the
                dashboard.</div><div class="th-live-status" data-ticket-live-status data-state="live" role="status"
                aria-live="polite"><span class="th-live-dot"></span><span data-ticket-live-text>Live</span></div></div>
                <form method="POST" data-ticket-refresh>
                    {csrf}
                    <input type="hidden" name="action" value="select_ticket">
                    <div class="th-field"><label>Selected ticket</label><select name="workspace_ticket_id"
                    onchange="this.form.submit()">{ticket_options}</select></div>
                    <button class="th-btn secondary" type="submit">Refresh conversation</button>
                </form>
            </div>
            <div class="th-ticket-meta">
                <div><span class="th-muted">Status</span><strong>{self._h(status.title())}</strong></div>
                <div><span class="th-muted">Profile</span><strong>{self._h(record.get("profile") or "main")}</strong></div>
                <div><span class="th-muted">Owner</span><strong>{owner_text}</strong></div>
                <div><span class="th-muted">Claimed by</span><strong>{self._h(claimed_by or "Nobody")}</strong></div>
                <div><span class="th-muted">Locked</span><strong>{"Yes" if record.get("locked") else "No"}</strong></div>
                <div><span class="th-muted">Location</span><strong>{channel_link}</strong></div>
            </div>
            <div><span class="th-muted">Participants:</span> {self._h(participant_text)}</div>
            <div class="th-conversation" data-ticket-conversation>{conversation}</div>
            <form class="th-reply" method="POST" data-ticket-reply>
                {csrf}
                <input type="hidden" name="action" value="ticket_reply">
                <input type="hidden" name="workspace_ticket_id" value="{selected_ticket_id}">
                {self._textarea("ticket_reply_content", "Reply", "", rows=5)}
                <div class="th-reply-feedback" data-ticket-reply-feedback role="status" aria-live="polite"></div>
                <button class="th-btn" type="submit"{reply_disabled}>Send Reply</button>
                {reopen_notice}
            </form>
        </div>
        <div class="th-grid">
            <div class="th-card">
                <h3>Ticket controls</h3>
                <form method="POST">
                    {csrf}
                    <input type="hidden" name="action" value="workspace_ticket_action">
                    <input type="hidden" name="workspace_ticket_id" value="{selected_ticket_id}">
                    {self._textarea("ticket_reason", "Close, reopen, or delete reason", "", rows=3)}
                    <div class="th-actions">{action_buttons}</div>
                </form>
            </div>
            <div class="th-card">
                <h3>Members</h3>
                <form method="POST">
                    {csrf}
                    <input type="hidden" name="action" value="workspace_ticket_action">
                    <input type="hidden" name="workspace_ticket_id" value="{selected_ticket_id}">
                    {self._input("ticket_member_id", "Member ID", "", "number", min_value=1)}
                    <div class="th-actions">
                        <button class="th-btn secondary" type="submit" name="ticket_action"
                        value="add_member">Add member</button>
                        <button class="th-btn secondary" type="submit" name="ticket_action"
                        value="remove_member">Remove member</button>
                    </div>
                </form>
            </div>
        </div>
        """

    def _dashboard_tickets_section(
        self,
        guild: discord.Guild,
        profiles: dict[str, dict[str, typing.Any]],
        tickets: dict[str, dict[str, typing.Any]],
        selected_profile: str,
        csrf: str,
    ) -> str:
        rows = []
        for record in sorted(
            tickets.values(),
            key=lambda item: int(item.get("id") or 0),
            reverse=True,
        )[:100]:
            channel_id = record.get("channel_id")
            channel = guild.get_channel(int(channel_id)) if channel_id else None
            channel_text = channel.mention if channel else self._h(channel_id or "missing")
            rows.append(
                "<tr>"
                f"<td>{self._h(record.get('id'))}</td>"
                f"<td>{self._h(record.get('profile') or 'main')}</td>"
                f"<td>{self._h(record.get('status') or 'open')}</td>"
                f"<td>{self._h(record.get('owner_id') or '')}</td>"
                f"<td>{self._h(record.get('claimed_by') or '')}</td>"
                f"<td>{'Yes' if record.get('locked') else 'No'}</td>"
                f"<td>{channel_text}</td>"
                "</tr>",
            )
        table = "".join(rows) or '<tr><td colspan="7" class="th-muted">No tickets tracked.</td></tr>'
        profile_options = "".join(
            f'<option value="{self._h(name)}" {self._selected(name, selected_profile)}>{self._h(name)}</option>'
            for name in sorted(profiles)
        )
        return f"""
        <div id="tickets" class="th-card">
            <h3>Tickets</h3>
            <table class="th-table"><thead><tr><th>ID</th><th>Profile</th><th>Status</th><th>Owner ID</th><th>Claimed
            By</th><th>Locked</th><th>Location</th></tr></thead><tbody>{table}</tbody></table>
            <div class="th-grid">
                <form method="POST">
                    {csrf}
                    <input type="hidden" name="action" value="ticket_action">
                    <div class="th-row">
                        {self._input("ticket_id", "Ticket ID", "", "number", min_value=1)}
                        <div class="th-field"><label>Action</label><select name="ticket_action">
                            <option value="claim">Claim</option><option value="unclaim">Unclaim</option>
                            <option value="lock">Lock</option><option value="unlock">Unlock</option>
                            <option value="close">Close Now</option><option value="reopen">Reopen</option>
                            <option value="transcript">Transcript</option><option value="delete">Delete</option>
                            <option value="add_member">Add Member</option><option value="remove_member">Remove
                            Member</option>
                        </select></div>
                        {self._input("ticket_member_id", "Member ID", "")}
                    </div>
                    {self._textarea("ticket_reason", "Reason", "", rows=3)}
                    <button class="th-btn" type="submit">Run Ticket Action</button>
                </form>
                <form method="POST">
                    {csrf}
                    <input type="hidden" name="action" value="create_ticket">
                    <div class="th-row">
                        {self._input("create_ticket_owner_id", "Owner Member ID", "")}
                        <div class="th-field"><label>Profile</label><select
                        name="create_ticket_profile">{profile_options}</select></div>
                    </div>
                    {self._textarea("create_ticket_reason", "Reason", "Created from Dashboard.", rows=3)}
                    <button class="th-btn" type="submit">Create Ticket</button>
                </form>
                <form method="POST">
                    {csrf}
                    <input type="hidden" name="action" value="recover_ticket">
                    {self._input("recover_channel_id", "Ticket Channel or Thread ID", "")}
                    <button class="th-btn secondary" type="submit">Recover Ticket</button>
                </form>
            </div>
        </div>
        """

    def _dashboard_imports_section(
        self,
        aaa3a_panels: dict[str, typing.Any],
        csrf: str,
    ) -> str:
        rows = []
        for key, raw_record in aaa3a_panels.items():
            record = self._sanitize_aaa3a_panel_record(raw_record, message_key=str(key))
            if record is None:
                continue
            option_count = len(record.get("buttons") or {}) + len(
                record.get("dropdown_options") or {},
            )
            rows.append(
                f"<tr><td>{self._h(key)}</td><td>{self._h(record['channel_id'])}</td><td>{self._h(record['message_id'])}</td><td>{option_count}</td></tr>",
            )
        table = "".join(rows) or '<tr><td colspan="4" class="th-muted">No imported AAA3A panels tracked.</td></tr>'
        return f"""
        <div id="imports" class="th-card">
            <h3>AAA3A Imports</h3>
            <table
            class="th-table"><thead><tr><th>Key</th><th>Channel</th><th>Message</th><th>Options</th></tr></thead><tbody>{table}</tbody></table>
            <form class="th-inline" method="POST">{csrf}<input type="hidden" name="action"
            value="import_aaa3a_panels"><button class="th-btn" type="submit">Import/Refresh AAA3A Panels</button></form>
            <form class="th-inline" method="POST">{csrf}<input type="hidden" name="action"
            value="clear_aaa3a_panels"><button class="th-btn danger" type="submit">Clear Imported Panels</button></form>
        </div>
        """

    def _channel_select(
        self,
        guild: discord.Guild,
        name: str,
        label: str,
        selected: typing.Any,
    ) -> str:
        options = ['<option value="">None</option>']
        for channel in sorted(guild.text_channels, key=lambda item: item.name.lower()):
            options.append(
                f'<option value="{channel.id}" {self._selected(channel.id, selected)}>#{self._h(channel.name)}</option>',
            )
        return (
            f'<div class="th-field"><label>{self._h(label)}</label>'
            f'<select name="{self._h(name)}">{"".join(options)}</select></div>'
        )

    def _category_select(
        self,
        guild: discord.Guild,
        name: str,
        label: str,
        selected: typing.Any,
    ) -> str:
        options = ['<option value="">None</option>']
        for category in sorted(guild.categories, key=lambda item: item.name.lower()):
            options.append(
                f'<option value="{category.id}" {self._selected(category.id, selected)}>{self._h(category.name)}</option>',
            )
        return (
            f'<div class="th-field"><label>{self._h(label)}</label>'
            f'<select name="{self._h(name)}">{"".join(options)}</select></div>'
        )

    def _role_select(
        self,
        guild: discord.Guild,
        name: str,
        label: str,
        selected: typing.Any,
    ) -> str:
        options = ['<option value="">None</option>']
        for role in sorted(
            [role for role in guild.roles if role.name != "@everyone"],
            key=lambda item: item.position,
            reverse=True,
        ):
            options.append(
                f'<option value="{role.id}" {self._selected(role.id, selected)}>{self._h(role.name)}</option>',
            )
        return (
            f'<div class="th-field"><label>{self._h(label)}</label>'
            f'<select name="{self._h(name)}">{"".join(options)}</select></div>'
        )

    def _multi_role_select(
        self,
        guild: discord.Guild,
        name: str,
        label: str,
        selected: typing.Sequence[int],
    ) -> str:
        selected_ids = {str(role_id) for role_id in selected}
        options = []
        for role in sorted(
            [role for role in guild.roles if role.name != "@everyone"],
            key=lambda item: item.position,
            reverse=True,
        ):
            selected_attr = "selected" if str(role.id) in selected_ids else ""
            options.append(
                f'<option value="{role.id}" {selected_attr}>{self._h(role.name)}</option>',
            )
        return (
            f'<div class="th-field"><label>{self._h(label)}</label>'
            f'<select name="{self._h(name)}" multiple size="8">'
            f"{''.join(options)}</select></div>"
        )

    def _input(
        self,
        name: str,
        label: str,
        value: typing.Any,
        input_type: str = "text",
        *,
        min_value: int | None = None,
        max_value: int | None = None,
    ) -> str:
        attrs = []
        if min_value is not None:
            attrs.append(f'min="{min_value}"')
        if max_value is not None:
            attrs.append(f'max="{max_value}"')
        return (
            f'<div class="th-field"><label>{self._h(label)}</label>'
            f'<input type="{self._h(input_type)}" name="{self._h(name)}" value="{self._h(value)}" {" ".join(attrs)}></div>'
        )

    def _textarea(
        self,
        name: str,
        label: str,
        value: typing.Any,
        *,
        rows: int = 4,
    ) -> str:
        return (
            f'<div class="th-field"><label>{self._h(label)}</label>'
            f'<textarea name="{self._h(name)}" rows="{rows}">{self._h(value)}</textarea></div>'
        )

    def _option(self, value: typing.Any, label: str, selected: typing.Any) -> str:
        return f'<option value="{self._h(value)}" {self._selected(value, selected)}>{self._h(label)}</option>'

    def _selected(self, value: typing.Any, selected: typing.Any) -> str:
        return "selected" if str(value) == str(selected) else ""

    def _checked(self, value: typing.Any) -> str:
        return "checked" if bool(value) else ""

    def _h(self, value: typing.Any) -> str:
        return html.escape("" if value is None else str(value), quote=True)
