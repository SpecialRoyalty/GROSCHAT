from sqlalchemy import select
from aiogram import Bot
from aiogram.types import Message
from app.db.session import SessionLocal
from app.db.models import TrackedMessage, TrustedAction
from app.config import get_settings
from app.services.moderation import ban, restrict, record_media, delete
from app.services.state import log_error


TRUSTED_COMMANDS = ['/supprime', '/mineur', '/pasfr', '/pedo', '/clean', '/info']


async def trusted_command(bot: Bot, msg: Message):
    if not msg.from_user:
        return False

    text = msg.text or ''
    if not text.strip().startswith('/'):
        return False

    cmd = text.split()[0].lower().split('@')[0]
    if cmd not in TRUSTED_COMMANDS:
        return False

    s = get_settings()
    uid = msg.from_user.id

    if uid not in s.all_admin_ids:
        print(
            f"TRUSTED COMMAND REFUSED: user_id={uid} "
            f"username=@{msg.from_user.username or 'none'} "
            f"name={msg.from_user.full_name!r} "
            f"cmd={cmd} "
            f"admin_ids={sorted(s.admin_id_set)} "
            f"trusted_ids={sorted(s.trusted_id_set)}"
        )
        return False

    try:
        await bot.delete_message(msg.chat.id, msg.message_id)
    except Exception as e:
        await log_error('trusted_delete_command', e)

    target = msg.reply_to_message

    async with SessionLocal() as db:
        db.add(
            TrustedAction(
                trusted_user_id=uid,
                trusted_username=msg.from_user.username or msg.from_user.full_name or '',
                command=cmd,
                target_user_id=target.from_user.id if target and target.from_user else None,
            )
        )
        await db.commit()

    if cmd == '/clean':
        n = 50
        parts = text.split()
        if len(parts) > 1 and parts[1].isdigit():
            n = min(int(parts[1]), 300)

        for mid in range(msg.message_id - 1, max(msg.message_id - n, 0), -1):
            try:
                await bot.delete_message(msg.chat.id, mid)
            except Exception:
                pass
        return True

    if cmd == '/info':
        if target and target.from_user:
            try:
                await bot.send_message(
                    uid,
                    f'👤 {target.from_user.full_name}\n'
                    f'@{target.from_user.username or "sans username"}\n'
                    f'ID interne masqué dans le groupe.'
                )
            except Exception as e:
                await log_error('trusted_info_dm', e)
        return True

    if not target or not target.from_user:
        return True

    target_uid = target.from_user.id

    if cmd == '/supprime':
        await delete(bot, target)

    elif cmd == '/mineur':
        await delete(bot, target)
        await restrict(bot, msg.chat.id, target_uid, 1)

    elif cmd == '/pasfr':
        await delete(bot, target)
        await restrict(bot, msg.chat.id, target_uid, 1)

    elif cmd == '/pedo':
        await ban(bot, msg.chat.id, target_uid)

        async with SessionLocal() as db:
            res = await db.execute(
                select(TrackedMessage).where(
                    TrackedMessage.chat_id == msg.chat.id,
                    TrackedMessage.user_id == target_uid,
                    TrackedMessage.deleted == False,
                )
            )
            for tm in res.scalars().all():
                try:
                    await bot.delete_message(tm.chat_id, tm.message_id)
                    tm.deleted = True
                except Exception:
                    pass
            await db.commit()

        await record_media(target, banned=True)

    return True
