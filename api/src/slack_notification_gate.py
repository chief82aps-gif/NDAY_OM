"""
System-wide Slack notification pause switch.

The codebase has no single shared Slack-sending adapter — every module
constructs its own `slack_sdk.WebClient` (24+ call sites across 15 files as
of 2026-07-13; see CLAUDE.md's architecture section). Rather than touch
every module individually to add a gate, this patches the SDK's outbound
send methods directly, so the gate is enforced regardless of which module
or function constructs the client.

Only outbound sends are affected — `chat_postMessage`, `chat_update`,
`chat_postEphemeral`. Read-only calls (`conversations_history`,
`files_list`, `users_info`, `auth_test`, etc.) are untouched, so file
detection/ingestion keeps working normally while notifications are paused.

Controlled by SLACK_NOTIFICATIONS_ACTIVE (default "false" — paused). This
sits above ROSTERING_ACTIVE and DRIVER_DM_ACTIVE: while this is off,
nothing goes out to Slack regardless of what those two flags say.
"""
import logging
import os

logger = logging.getLogger(__name__)

_PAUSED_RESPONSE = {"ok": True, "paused": True}


def apply_slack_send_gate() -> None:
    """Call once, as early as possible (module import time), before any
    request or background task can construct a WebClient and send."""
    if os.getenv("SLACK_NOTIFICATIONS_ACTIVE", "false").lower() == "true":
        logger.info("Slack notifications ACTIVE — sends are live.")
        return

    try:
        from slack_sdk import WebClient
    except ImportError:
        return

    def _paused(method_name):
        def _call(self, *args, **kwargs):
            logger.info(
                "Slack %s suppressed (SLACK_NOTIFICATIONS_ACTIVE=false): channel=%s",
                method_name, kwargs.get("channel"),
            )
            return _PAUSED_RESPONSE
        return _call

    WebClient.chat_postMessage = _paused("chat_postMessage")
    WebClient.chat_update = _paused("chat_update")
    WebClient.chat_postEphemeral = _paused("chat_postEphemeral")
    logger.warning(
        "Slack notifications PAUSED (SLACK_NOTIFICATIONS_ACTIVE unset/false) — "
        "no outbound Slack messages will be sent. Reads/ingestion are unaffected."
    )
