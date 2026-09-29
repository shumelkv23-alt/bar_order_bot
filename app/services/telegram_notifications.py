"""Replace only status notifications; leave the guest's conversation intact."""

from __future__ import annotations

import logging

from aiogram import Bot

logger = logging.getLogger(__name__)


async def replace_status_message(
    bot: Bot,
    chat_id: int,
    previous_message_id: int | None,
    text: str,
) -> int | None:
    try:
        message = await bot.send_message(chat_id, text)
    except Exception as exc:
        logger.warning("Could not send status message: %s", exc)
        if previous_message_id is not None:
            try:
                await bot.edit_message_text(
                    text, chat_id=chat_id, message_id=previous_message_id
                )
            except Exception as edit_exc:
                logger.warning("Could not edit prior status message: %s", edit_exc)
        return previous_message_id
    if previous_message_id is None:
        return message.message_id
    try:
        await bot.delete_message(chat_id, previous_message_id)
        return message.message_id
    except Exception as exc:
        logger.warning("Could not delete prior status message: %s", exc)
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=previous_message_id)
    except Exception as exc:
        logger.warning("Could not edit prior status message: %s", exc)
        return message.message_id
    try:
        await bot.delete_message(chat_id, message.message_id)
    except Exception as exc:
        logger.warning("Could not delete duplicate status message %s: %s", message.message_id, exc)
    return previous_message_id
