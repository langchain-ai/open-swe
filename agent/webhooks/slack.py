"""Slack webhook handler — moved out of webapp.py (behavior-identical).

Helpers and constants stay in webapp.py; they are accessed through the module
object (``webapp.X``) so tests that monkeypatch them keep working.
"""

from typing import Any

import httpx
from langchain_core.messages.content import create_text_block

from agent import webapp


async def process_slack_mention(event_data: dict[str, Any], repo_config: dict[str, str]) -> None:
    """Process a Slack app mention by creating a run or queuing a mid-run message."""
    channel_id = event_data.get("channel_id", "")
    thread_ts = event_data.get("thread_ts", "")
    event_ts = event_data.get("event_ts", "")
    user_id = event_data.get("user_id", "")
    text = event_data.get("text", "")
    bot_user_id = event_data.get("bot_user_id", "")

    if not channel_id or not thread_ts or not event_ts:
        webapp.logger.warning(
            "Missing Slack event fields (channel_id=%s, thread_ts=%s, event_ts=%s)",
            channel_id,
            thread_ts,
            event_ts,
        )
        return

    await webapp.set_slack_assistant_status(channel_id, thread_ts)

    thread_id = webapp.generate_thread_id_from_slack_thread(channel_id, thread_ts)

    # Prime the user-mapping cache so login/email/slack-id lookups below are warm.
    try:
        await webapp.refresh_user_mapping_cache()
    except Exception:  # noqa: BLE001
        webapp.logger.debug("Could not refresh user mapping cache for Slack mention", exc_info=True)

    user_email = None
    user_name = ""
    if user_id:
        slack_user = await webapp.get_slack_user_info(user_id)
        if slack_user:
            profile = slack_user.get("profile", {})
            if isinstance(profile, dict):
                user_email = profile.get("email")
                user_name = (
                    profile.get("display_name")
                    or profile.get("real_name")
                    or slack_user.get("real_name")
                    or slack_user.get("name")
                    or ""
                )

    thread_messages = await webapp.fetch_slack_thread_messages(channel_id, thread_ts)
    if not any(str(message.get("ts")) == str(event_ts) for message in thread_messages):
        thread_messages.append({"ts": event_ts, "text": text, "user": user_id})

    context_messages, context_mode = webapp.select_slack_context_messages(
        thread_messages, event_ts, bot_user_id, webapp.SLACK_BOT_USERNAME
    )
    context_user_ids = [
        value
        for value in (message.get("user") for message in context_messages)
        if isinstance(value, str) and value
    ]
    user_names_by_id = await webapp.get_slack_user_names(context_user_ids)
    if user_id and user_name and user_id not in user_names_by_id:
        user_names_by_id[user_id] = user_name
    context_text = webapp.format_slack_messages_for_prompt(
        context_messages,
        user_names_by_id,
        bot_user_id=bot_user_id,
        bot_username=webapp.SLACK_BOT_USERNAME,
    )
    context_source = (
        "the previous message where I was tagged"
        if context_mode == "last_mention"
        else "the beginning of the thread"
    )
    clean_text = (
        webapp.strip_bot_mention(text, bot_user_id, bot_username=webapp.SLACK_BOT_USERNAME)
        or "(no text in mention)"
    )
    trigger_user = user_name or (f"<@{user_id}>" if user_id else "Unknown user")

    # Auto-resolve cross-posted Slack message links in context
    resolved_links_section, image_urls_from_links = await webapp.resolve_slack_links_in_context(
        context_messages, user_names_by_id
    )

    prompt = (
        "You were mentioned in Slack.\n\n"
        "## Default Repository Hint\n"
        f"{repo_config.get('owner')}/{repo_config.get('name')}\n"
        "Use this only if the Slack conversation does not identify a different repository.\n\n"
        f"## Triggered by\n{trigger_user}\n\n"
        f"## Slack Thread\n- Channel: {channel_id}\n- Thread TS: {thread_ts}\n"
        f"- Context starts at: {context_source}\n\n"
        f"## Conversation Context\n{context_text}\n\n"
        f"## Latest Mention Request\n{clean_text}\n\n"
        + (f"{resolved_links_section}\n\n" if resolved_links_section else "")
        + "Use `slack_thread_reply` to communicate in this Slack thread for clarifications, "
        "status updates, and final summaries. Use `slack_read_thread_messages` to read any "
        "Slack messages by providing channel_id and message_ts."
    )
    content_blocks: list[dict[str, Any]] = [create_text_block(prompt)]

    image_urls = webapp.dedupe_urls(
        [url for msg in context_messages for url in webapp.extract_image_urls(msg.get("text", ""))]
        + [
            f["url_private"]
            for msg in context_messages
            for f in msg.get("files", [])
            if isinstance(f, dict)
            and f.get("mimetype", "").startswith("image/")
            and f.get("url_private")
        ]
        + image_urls_from_links
    )

    mapped_login = await webapp.login_for_slack_id(user_id)
    if not mapped_login and user_email:
        mapped_login = await webapp.login_for_email(user_email)

    if image_urls:
        resolved_model_id = await webapp.resolve_agent_model_id(mapped_login)
        if webapp.model_supports_images(resolved_model_id):
            webapp.logger.info("Preparing %d image(s) for Slack mention", len(image_urls))
            async with httpx.AsyncClient(timeout=webapp.DEFAULT_HTTP_TIMEOUT) as http_client:
                for image_url in image_urls:
                    image_block = await webapp.fetch_image_block(image_url, http_client)
                    if image_block:
                        content_blocks.append(image_block)
        else:
            webapp.logger.warning(
                "Skipping %d image(s) for Slack mention: model %s does not support images",
                len(image_urls),
                resolved_model_id,
            )
            prompt += webapp.vision_not_supported_warning(resolved_model_id, len(image_urls))
            content_blocks[0] = create_text_block(prompt)
            image_urls = []

    # Open SWE opens PRs as the triggering user, so a run only proceeds when we
    # have a valid user GitHub token. Users who have never signed in with
    # GitHub, and users whose stored authorization is no longer usable, are
    # blocked and prompted to set up via the dashboard. Bot-token-only
    # deployments are exempt — they run on the installation token.
    user_token: str | None = None
    if mapped_login:
        try:
            user_token = await webapp.get_valid_access_token(mapped_login)
        except Exception:  # noqa: BLE001
            webapp.logger.debug(
                "Failed to resolve GitHub token for %s; treating as unauthenticated",
                mapped_login,
                exc_info=True,
            )
            user_token = None
    has_valid_user_token = bool(user_token)

    if not has_valid_user_token and not webapp.is_bot_token_only_mode():
        # A stored-but-unusable token means "sign in again"; no record at all
        # means the user has never connected GitHub + Slack via the dashboard.
        # Guard the store read like token resolution above so a transient
        # failure still yields an actionable prompt and clears the status.
        has_token_record = False
        if mapped_login:
            try:
                has_token_record = await webapp.has_access_token_record(mapped_login)
            except Exception:  # noqa: BLE001
                webapp.logger.debug(
                    "Failed to check GitHub token record for %s; prompting sign-in",
                    mapped_login,
                    exc_info=True,
                )
        reason = "revoked" if has_token_record else "unlinked"
        webapp.logger.info(
            "Blocking Slack run for thread %s: no valid user GitHub token (%s)",
            thread_id,
            reason,
        )
        if user_id:
            await webapp._post_account_link_prompt(
                channel_id, thread_ts, user_id, user_email, reason=reason
            )
        await webapp.set_slack_assistant_status(channel_id, thread_ts, status="")
        return

    configurable: dict[str, Any] = {
        "repo": repo_config,
        "slack_thread": {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "triggering_user_id": user_id,
            "triggering_user_name": user_name,
            "triggering_user_email": user_email,
            "triggering_event_ts": event_ts,
        },
        "user_email": user_email,
        "source": "slack",
    }
    if mapped_login:
        configurable["github_login"] = mapped_login

    thread_plan_mode = await webapp._get_thread_plan_mode(thread_id)
    if thread_plan_mode is not None:
        configurable["plan_mode"] = thread_plan_mode

    langgraph_client = webapp.get_client(url=webapp.LANGGRAPH_URL)
    is_first_mention = not await webapp._thread_exists(thread_id)
    await webapp._upsert_slack_thread_repo_metadata(thread_id, repo_config, langgraph_client)
    # Pass the login resolved above (from the stable Slack user id) so the thread is
    # always tagged with github_login — the key the dashboard searches by. Without
    # it, upsert re-resolves from the Slack profile email, which can miss.
    await webapp.upsert_agent_thread_owner_metadata(
        thread_id,
        source="slack",
        repo_config=repo_config,
        github_login=mapped_login or "",
        user_email=user_email or "",
        title=clean_text if is_first_mention else "",
        source_context={"slack_thread": configurable["slack_thread"]},
    )

    run = await webapp.dispatch_agent_run(
        thread_id,
        content_blocks,
        configurable,
        source="slack",
        metadata=webapp._AGENT_VERSION_METADATA,
        client=langgraph_client,
    )
    webapp.logger.info(
        "Slack LangGraph run %s dispatched for thread %s",
        webapp._run_id_for_logging(run),
        thread_id,
    )
    run_id = run.get("run_id")
    if is_first_mention:
        trace_message_ts = await webapp.post_slack_trace_reply(channel_id, thread_ts, thread_id)
        await webapp.set_slack_assistant_status(channel_id, thread_ts)
        if isinstance(run_id, str) and run_id:
            await webapp.store_slack_run_mapping(
                langgraph_client,
                channel_id,
                thread_ts,
                run_id,
                message_ts=trace_message_ts,
                triggering_user_id=user_id,
                trace_message_ts=trace_message_ts,
            )
    else:
        webapp.logger.info(
            "Skipping Slack trace reply for thread %s — agent will reply when run completes",
            thread_id,
        )
        if isinstance(run_id, str) and run_id:
            await webapp.store_slack_run_mapping(
                langgraph_client,
                channel_id,
                thread_ts,
                run_id,
                triggering_user_id=user_id,
            )
