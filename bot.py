import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import aiosqlite
from dotenv import load_dotenv
from langdetect import detect, LangDetectException
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatPermissions
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler,
    ContextTypes, MessageHandler, filters
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
GROUP_ID = int(os.getenv("GROUP_ID", "0") or 0)
DB_PATH = os.getenv("DATABASE_PATH", "bot.sqlite3")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Paris"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", "8080"))
REQUIRED_VIDEOS = 60
OPEN_HOUR = 23
CLOSE_HOUR = 1

URL_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/|discord\.gg/|bit\.ly/|\.com\b|\.net\b|\.org\b)", re.I)

DEFAULT_SETTINGS = {
    "moderation": "1",
    "anti_links": "1",
    "anti_photo_mentions": "1",
    "anti_repost": "1",
    "auto_schedule": "1",
    "group_open": "0",
}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True) if os.path.dirname(DB_PATH) else None
    con = await aiosqlite.connect(DB_PATH)
    con.row_factory = aiosqlite.Row
    return con


async def init_db():
    async with await db() as con:
        await con.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS banned_words(word TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS violations(user_id INTEGER, kind TEXT, count INTEGER, PRIMARY KEY(user_id, kind));
        CREATE TABLE IF NOT EXISTS media_hashes(hash TEXT PRIMARY KEY, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS videos(slot INTEGER PRIMARY KEY, file_id TEXT NOT NULL, uploaded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS referrals(inviter_id INTEGER, invitee_id INTEGER PRIMARY KEY, joined_at TEXT, validated INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS reward_claims(user_id INTEGER PRIMARY KEY, sent_count INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS invite_links(link TEXT PRIMARY KEY, inviter_id INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS group_messages(message_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL);
        ''')
        for k, v in DEFAULT_SETTINGS.items():
            await con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        await con.commit()


async def get_setting(key: str) -> str:
    async with await db() as con:
        cur = await con.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else DEFAULT_SETTINGS.get(key, "0")


async def set_setting(key: str, value: str):
    async with await db() as con:
        await con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        await con.commit()


async def count_videos() -> int:
    async with await db() as con:
        cur = await con.execute("SELECT COUNT(*) c FROM videos")
        return (await cur.fetchone())["c"]


async def status_text() -> str:
    vids = await count_videos()
    return (
        "⚙️ PANEL ADMIN\n\n"
        f"🗄️ Base de données : ✅ branchée\n"
        f"👥 Groupe : {'✅ branché' if GROUP_ID else '❌ GROUP_ID manquant'}\n"
        f"🎁 Vidéos : {'✅' if vids >= REQUIRED_VIDEOS else '❌'} {vids}/{REQUIRED_VIDEOS}\n\n"
        "Choisis une action :"
    )


async def main_keyboard():
    async def onoff(k): return "ON" if await get_setting(k) == "1" else "OFF"
    vids = await count_videos()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🛡️ Modération {await onoff('moderation')}", callback_data="toggle:moderation")],
        [InlineKeyboardButton(f"🔗 Anti-liens {await onoff('anti_links')}", callback_data="toggle:anti_links")],
        [InlineKeyboardButton(f"🖼️ Photo+mention {await onoff('anti_photo_mentions')}", callback_data="toggle:anti_photo_mentions")],
        [InlineKeyboardButton("🚫 Mots interdits", callback_data="words")],
        [InlineKeyboardButton(f"⏰ Auto horaires {await onoff('auto_schedule')}", callback_data="toggle:auto_schedule")],
        [InlineKeyboardButton("🟢 Ouvrir maintenant", callback_data="open"), InlineKeyboardButton("🔴 Fermer maintenant", callback_data="close")],
        [InlineKeyboardButton(f"♻️ Anti-repost {await onoff('anti_repost')}", callback_data="toggle:anti_repost")],
        [InlineKeyboardButton(f"🎁 Vidéos récompenses {'✅' if vids >= REQUIRED_VIDEOS else '❌'} {vids}/60", callback_data="videos")],
        [InlineKeyboardButton("📊 Parrainage", callback_data="ref_stats"), InlineKeyboardButton("ℹ️ Info système", callback_data="info")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    # Admin : /start ouvre directement l’interface, aucune autre commande admin nécessaire.
    if is_admin(update.effective_user.id):
        await update.effective_message.reply_text(await status_text(), reply_markup=await main_keyboard())
        return
    # Utilisateur : bouton de parrainage.
    if context.args and context.args[0] == "ref":
        link = await context.bot.create_chat_invite_link(
            GROUP_ID,
            name=f"ref_{update.effective_user.id}_{int(datetime.now(TZ).timestamp())}",
            expire_date=datetime.now(TZ) + timedelta(days=7),
            creates_join_request=False,
        )
        async with await db() as con:
            await con.execute("INSERT OR REPLACE INTO invite_links(link,inviter_id,created_at) VALUES(?,?,?)", (link.invite_link, update.effective_user.id, datetime.now(TZ).isoformat()))
            await con.commit()
        await update.effective_message.reply_text(
            "🎁 Voici ton lien privé de partage :\n"
            f"{link.invite_link}\n\n"
            "Règles : 1 invité validé = 1 vidéo, 5 = 10 vidéos, 30 = 50 vidéos, 40 = 60 vidéos. "
            "Un invité est validé seulement s’il reste au moins 5 minutes et si son profil semble francophone."
        )
        return
    await update.effective_message.reply_text("Clique sur le bouton dans le groupe fermé pour obtenir ton lien privé.")


async def cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.from_user or not is_admin(q.from_user.id):
        return
    await q.answer()
    data = q.data
    if data.startswith("toggle:"):
        k = data.split(":", 1)[1]
        await set_setting(k, "0" if await get_setting(k) == "1" else "1")
        await q.edit_message_text(await status_text(), reply_markup=await main_keyboard())
    elif data == "open":
        await open_group(context)
        await q.edit_message_text("✅ Groupe ouvert manuellement.", reply_markup=await main_keyboard())
    elif data == "close":
        await close_group(context)
        await q.edit_message_text("✅ Groupe fermé manuellement.", reply_markup=await main_keyboard())
    elif data == "info":
        await q.edit_message_text(await status_text(), reply_markup=await main_keyboard())
    elif data == "videos":
        await q.edit_message_text(await videos_text(), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="info")]]))
    elif data == "words":
        await q.edit_message_text("🚫 Mots interdits\n\nEnvoie dans ce chat privé :\n+mot pour ajouter\n-mot pour supprimer\nliste pour voir", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="info")]]))
    elif data == "ref_stats":
        await q.edit_message_text(await referral_stats(), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="info")]]))


async def videos_text():
    n = await count_videos()
    return f"🎁 VIDÉOS RÉCOMPENSES\n\nStatut : {'✅' if n >= REQUIRED_VIDEOS else '❌'} {n}/{REQUIRED_VIDEOS}\n\nPour ajouter une vidéo : envoie-la directement ici au bot. Elle sera stockée dans le prochain slot libre."


async def referral_stats():
    async with await db() as con:
        cur = await con.execute("SELECT COUNT(*) c FROM referrals WHERE validated=1")
        valid = (await cur.fetchone())["c"]
    return f"📊 Parrainage\n\nInvitations validées : {valid}\nCondition : l’invité doit rester au moins 5 minutes dans le groupe."


async def private_admin_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id) or update.effective_chat.type != "private":
        return
    msg = update.effective_message
    text = (msg.text or "").strip()
    if msg.video:
        async with await db() as con:
            cur = await con.execute("SELECT COALESCE(MAX(slot),0)+1 s FROM videos")
            slot = (await cur.fetchone())["s"]
            if slot > REQUIRED_VIDEOS:
                await msg.reply_text("❌ Les 60 vidéos sont déjà uploadées.")
                return
            await con.execute("INSERT INTO videos(slot,file_id,uploaded_at) VALUES(?,?,?)", (slot, msg.video.file_id, datetime.now(TZ).isoformat()))
            await con.commit()
        await msg.reply_text(f"✅ Vidéo ajoutée : {slot}/{REQUIRED_VIDEOS}")
    elif text.startswith("+") and len(text) > 1:
        async with await db() as con:
            await con.execute("INSERT OR IGNORE INTO banned_words(word) VALUES(?)", (text[1:].lower(),))
            await con.commit()
        await msg.reply_text("✅ Mot ajouté.")
    elif text.startswith("-") and len(text) > 1:
        async with await db() as con:
            await con.execute("DELETE FROM banned_words WHERE word=?", (text[1:].lower(),))
            await con.commit()
        await msg.reply_text("✅ Mot supprimé.")
    elif text.lower() == "liste":
        async with await db() as con:
            cur = await con.execute("SELECT word FROM banned_words ORDER BY word")
            words = [r["word"] for r in await cur.fetchall()]
        await msg.reply_text("🚫 Mots interdits :\n" + (", ".join(words) if words else "Aucun"))


async def warn_group(context, text):
    if GROUP_ID:
        await context.bot.send_message(GROUP_ID, text)


async def ban_user(context, user_id, reason, name="cet utilisateur"):
    await context.bot.ban_chat_member(GROUP_ID, user_id)
    await warn_group(context, f"🚫 Je viens de bannir {name} : {reason}. Ne faites pas comme lui.")


async def mute_user(context, user_id, until, name="cet utilisateur"):
    perms = ChatPermissions(can_send_messages=False)
    await context.bot.restrict_chat_member(GROUP_ID, user_id, permissions=perms, until_date=until)
    await warn_group(context, f"🔇 {name} a été mute pour non-respect des règles.")


async def group_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or user.is_bot or update.effective_chat.id != GROUP_ID:
        return
    name = user.mention_html() if user.username else (user.full_name or "cet utilisateur")
    async with await db() as con:
        await con.execute("INSERT OR REPLACE INTO group_messages(message_id,created_at) VALUES(?,?)", (msg.message_id, datetime.now(TZ).isoformat()))
        await con.commit()
    if await get_setting("group_open") != "1" and not is_admin(user.id):
        try: await msg.delete()
        except Exception: pass
        return
    if await get_setting("moderation") != "1":
        return
    text = (msg.text or msg.caption or "")
    if await get_setting("anti_links") == "1" and URL_RE.search(text):
        await msg.delete()
        await ban_user(context, user.id, "lien interdit envoyé", name)
        return
    if await get_setting("anti_photo_mentions") == "1" and msg.photo and ("@" in text or msg.caption_entities):
        await msg.delete()
        await ban_user(context, user.id, "photo avec identification interdite", name)
        return
    await check_banned_words(msg, context, user, name, text)
    await check_media_repost(msg, context)


async def check_banned_words(msg, context, user, name, text):
    if not text:
        return
    async with await db() as con:
        cur = await con.execute("SELECT word FROM banned_words")
        words = [r["word"] for r in await cur.fetchall()]
    lowered = text.lower()
    if not any(w and w in lowered for w in words):
        return
    async with await db() as con:
        cur = await con.execute("SELECT count FROM violations WHERE user_id=? AND kind='word'", (user.id,))
        row = await cur.fetchone()
        count = (row["count"] if row else 0) + 1
        await con.execute("INSERT INTO violations(user_id,kind,count) VALUES(?,'word',?) ON CONFLICT(user_id,kind) DO UPDATE SET count=?", (user.id, count, count))
        await con.commit()
    await msg.delete()
    if count == 1:
        await mute_user(context, user.id, datetime.now(TZ) + timedelta(days=1), name)
    elif count == 2:
        await mute_user(context, user.id, datetime.now(TZ) + timedelta(days=7), name)
    else:
        await ban_user(context, user.id, "mots interdits répétés", name)


async def check_media_repost(msg, context):
    if await get_setting("anti_repost") != "1":
        return
    file_id = None
    if msg.photo: file_id = msg.photo[-1].file_unique_id
    elif msg.video: file_id = msg.video.file_unique_id
    elif msg.document: file_id = msg.document.file_unique_id
    if not file_id:
        return
    h = hashlib.sha256(file_id.encode()).hexdigest()
    cutoff = (datetime.now(TZ) - timedelta(days=4)).isoformat()
    async with await db() as con:
        await con.execute("DELETE FROM media_hashes WHERE created_at < ?", (cutoff,))
        cur = await con.execute("SELECT hash FROM media_hashes WHERE hash=?", (h,))
        exists = await cur.fetchone()
        if exists:
            await msg.delete()
            await warn_group(context, "♻️ C’est du vu et déjà vu.")
        else:
            await con.execute("INSERT INTO media_hashes(hash,created_at) VALUES(?,?)", (h, datetime.now(TZ).isoformat()))
        await con.commit()


async def new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.id != GROUP_ID:
        return
    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        # Telegram ne fournit pas toujours la bio. On vérifie surtout le code langue public si disponible.
        lang = (member.language_code or "").lower()
        if lang and not lang.startswith("fr"):
            await context.bot.ban_chat_member(GROUP_ID, member.id)
            await warn_group(context, "🚫 Un nouveau membre a été refusé car son profil ne semble pas francophone.")
            continue





async def chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmu = update.chat_member
    if not cmu or cmu.chat.id != GROUP_ID:
        return
    old = cmu.old_chat_member.status
    new = cmu.new_chat_member.status
    user = cmu.new_chat_member.user
    if old in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED] and new in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR]:
        invite = cmu.invite_link.invite_link if cmu.invite_link else None
        if not invite:
            return
        async with await db() as con:
            cur = await con.execute("SELECT inviter_id FROM invite_links WHERE link=?", (invite,))
            row = await cur.fetchone()
            if not row:
                return
            inviter = row["inviter_id"]
            if inviter == user.id:
                return
            await con.execute("INSERT OR IGNORE INTO referrals(inviter_id, invitee_id, joined_at) VALUES(?,?,?)", (inviter, user.id, datetime.now(TZ).isoformat()))
            await con.commit()
        context.job_queue.run_once(validate_referral, 300, data={"invitee": user.id, "inviter": inviter})

async def validate_referral(context):
    data = context.job.data
    try:
        cm = await context.bot.get_chat_member(GROUP_ID, data["invitee"])
        if cm.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            async with await db() as con:
                await con.execute("UPDATE referrals SET validated=1 WHERE invitee_id=?", (data["invitee"],))
                await con.commit()
            await deliver_rewards(context, data["inviter"])
    except Exception as e:
        log.warning("referral validation failed: %s", e)


def reward_count(validated):
    if validated >= 40: return 60
    if validated >= 30: return 50
    if validated >= 10: return 10
    if validated >= 5: return 10
    if validated >= 1: return 1
    return 0


async def deliver_rewards(context, user_id):
    async with await db() as con:
        cur = await con.execute("SELECT COUNT(*) c FROM referrals WHERE inviter_id=? AND validated=1", (user_id,))
        valid = (await cur.fetchone())["c"]
        target = reward_count(valid)
        cur = await con.execute("SELECT sent_count FROM reward_claims WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        sent = row["sent_count"] if row else 0
        if target <= sent: return
        cur = await con.execute("SELECT slot,file_id FROM videos WHERE slot>? AND slot<=? ORDER BY slot", (sent, target))
        videos = await cur.fetchall()
        await con.execute("INSERT INTO reward_claims(user_id,sent_count) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET sent_count=?", (user_id, target, target))
        await con.commit()
    for v in videos:
        await context.bot.send_video(user_id, v["file_id"], caption=f"🎁 Vidéo récompense {v['slot']}/{REQUIRED_VIDEOS}")


async def referral_button_message(context):
    botname = (await context.bot.get_me()).username
    text = ("🔒 Groupe fermé.\n\nSi tu partages le lien du groupe, tu peux recevoir des vidéos. "
            "Clique sur le bouton ci-dessous pour obtenir ton lien privé.\n\n"
            "Les invités doivent rester au moins 5 minutes et avoir un profil francophone pour être validés.")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎁 Obtenir mon lien privé", url=f"https://t.me/{botname}?start=ref")]])
    await context.bot.send_message(GROUP_ID, text, reply_markup=kb)


async def open_group(context):
    await set_setting("group_open", "1")
    perms = ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True)
    await context.bot.set_chat_permissions(GROUP_ID, perms)
    await warn_group(context, "🟢 Groupe ouvert, vous pouvez envoyer des messages et médias.")


async def close_group(context):
    await set_setting("group_open", "0")
    perms = ChatPermissions(can_send_messages=False)
    await context.bot.set_chat_permissions(GROUP_ID, perms)
    await warn_group(context, "🔴 Groupe fermé, vous ne pouvez plus envoyer.")
    # Telegram ne permet pas d’effacer tout l’historique arbitrairement. Le bot supprime les messages qu’il a vus et enregistrés.
    async with await db() as con:
        cur = await con.execute("SELECT message_id FROM group_messages")
        ids = [r["message_id"] for r in await cur.fetchall()]
        await con.execute("DELETE FROM group_messages")
        await con.commit()
    for mid in ids[-500:]:
        try:
            await context.bot.delete_message(GROUP_ID, mid)
        except Exception:
            pass
    await referral_button_message(context)


async def scheduler_tick(context):
    if await get_setting("auto_schedule") != "1":
        return
    now = datetime.now(TZ)
    if now.hour == OPEN_HOUR and now.minute == 0:
        await open_group(context)
    elif now.hour == CLOSE_HOUR and now.minute == 0:
        await close_group(context)
    elif now.minute == 0 and await get_setting("group_open") != "1":
        next_open = now.replace(hour=OPEN_HOUR, minute=0, second=0, microsecond=0)
        if next_open <= now: next_open += timedelta(days=1)
        hours = int((next_open - now).total_seconds() // 3600)
        await warn_group(context, f"⏳ Prochaine ouverture dans {hours} heure(s).")


async def post_init(app: Application):
    await init_db()
    app.job_queue.run_repeating(scheduler_tick, interval=60, first=5)


def build_app():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, private_admin_messages))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member))
    app.add_handler(ChatMemberHandler(chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, group_messages))
    return app


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN manquant")
    application = build_app()
    if WEBHOOK_URL:
        application.run_webhook(listen="0.0.0.0", port=PORT, url_path=BOT_TOKEN, webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
    else:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
