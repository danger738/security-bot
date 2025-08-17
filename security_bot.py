#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Group Moderator Bot (python-telegram-bot v20+)

Modified for Render deployment
"""

# ============================= IMPORTS =============================
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any
from bot_website import start_website
from telegram import (
    Update,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ============================= CONFIG =============================
BOT_TOKEN = os.getenv('BOT_TOKEN', '8244198957:AAHhMCNztIAoMC2DMIhRlZU3j9HQRCxVMB4')
MAIN_ADMIN_ID = int(os.getenv('MAIN_ADMIN_ID', '6337462762'))
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL')  # Will be set in Render

STORE_FILE = Path("allowed.json")  # persistent store for admins/users/words


# (You shared these; they are filled in for immediate use.)
BOT_TOKEN = "8244198957:AAHhMCNztIAoMC2DMIhRlZU3j9HQRCxVMB4"   # <-- Your bot token
MAIN_ADMIN_ID = 6337462762                                       # <-- Your Telegram user ID (admin)

STORE_FILE = Path("allowed.json")  # persistent store for admins/users/words

DEFAULT_STORE: Dict[str, Any] = {
    "admins": [MAIN_ADMIN_ID],  # add more admin IDs here or via file
    "users": [],                # user IDs that bypass moderation
    "words": []                 # words that bypass length/link checks (NOT bad words)
}

# ============================= LOGGING =============================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("g2k_mod_bot")

# ============================= PERSISTENCE =============================
def load_store() -> Dict[str, Any]:
    """Load persistent store or initialize defaults."""
    if STORE_FILE.exists():
        try:
            with open(STORE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Ensure keys exist
            for k, v in DEFAULT_STORE.items():
                if k not in data:
                    data[k] = v
            return data
        except Exception as e:
            logger.error(f"Failed to load {STORE_FILE}: {e}")
    save_store(DEFAULT_STORE)
    return DEFAULT_STORE.copy()

def save_store(data: Dict[str, Any]) -> None:
    """Persist store to disk."""
    try:
        with open(STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to save {STORE_FILE}: {e}")

STORE = load_store()

def is_admin(user_id: int) -> bool:
    """Check if a user is an admin per store."""
    return user_id in STORE.get("admins", [])

# ============================= REGEX (compiled) =============================
# Your full list + extra common slang. Very aggressive patterns (e.g., '@') included per your request.
BAD_WORDS_PATTERNS = [
    r'\b(?:f[u@#\*]ck|f[\*]+ck)\b',
    r'\bsh[i!1\*]t\b',
    r'\bb[i!1\*]tch\b',
    r'\b(?:asshole|[a@]sshole|[a@]ss)\b',
    r'\bf[a@]gg[o0\*]t\b',
    r'\bsl[u@\*]t\b',
    r'\bd[i!1\*]ck\b',
    r'\bp[u@\*]ssy\b',
    r'\bb[o0\*][o0\*]bs\b',
    r'\bh[o0\*]rny\b',
    r'\bsex\b',
    r'\bp[o0\*]rn\b',
    r'\bn[u@\*]de\b',
    r'\bxxx\b',
    r'\br[a@\*]pe\b',
    r'\bnsfw\b',
    r'\bchut[i!1\*]ya\b',
    r'\bmadarch[o0\*]d\b',
    r'\bbhench[o0\*]d\b',
    r'\bbsdk\b',
    r'\bmc\b',
    r'\bmkc\b',
    r'\bjoin\s+channel\b',
    r'\bcome\s+to\s+my\s+group\b',
    r'\b(?:dm|D-M|d-m)\b',
    r'\bid\s+sell\b',
    r'\bsell\b',
    r'\bbuy\b',
    r'\bmessage\s+me\b',
    r'\bh[a@\*]cker\b',
    r'@'
]
BAD_WORDS = [re.compile(p, re.IGNORECASE) for p in BAD_WORDS_PATTERNS]
LINK_REGEX = re.compile(r"(https?://|www\.)", re.IGNORECASE)

# ============================= UTILITIES =============================
def html_mention(user) -> str:
    """Safe HTML mention for a telegram.User."""
    try:
        return user.mention_html()
    except Exception:
        name = (user.full_name or str(user.id)).replace("<", "&lt;").replace(">", "&gt;")
        return f"<a href=\"tg://user?id={user.id}\">{name}</a>"

def delete_after(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay_sec: int) -> None:
    """Delete a message after delay using a background thread to avoid blocking."""
    def _do_delete():
        try:
            context.bot.delete_message(chat_id, message_id)
        except Exception:
            pass
    threading.Timer(delay_sec, _do_delete).start()

async def send_ephemeral(update: Update, text: str, secs: int = 12, reply_markup=None) -> None:
    """Send a temporary reply that auto-deletes after `secs`."""
    try:
        msg = await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
        delete_after(update.get_bot(), msg.chat_id, msg.message_id, secs)
    except Exception as e:
        logger.debug(f"send_ephemeral failed: {e}")

async def mute_user(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, minutes: int) -> None:
    """Mute a user for N minutes."""
    until = datetime.utcnow() + timedelta(minutes=minutes)
    await context.bot.restrict_chat_member(
        chat_id,
        user_id,
        permissions=ChatPermissions(can_send_messages=False),
        until_date=until,
    )

async def ban_user_1d(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    """Ban a user for 1 day."""
    until = datetime.utcnow() + timedelta(days=1)
    await context.bot.ban_chat_member(chat_id, user_id, until_date=until)

def admin_button(text: str, data: str) -> InlineKeyboardMarkup:
    """Create an inline button keyboard (admin-only enforced in callback)."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=data)]])

# ============================= COMMANDS =============================
async def cmd_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show commands; admins see full panel, members see limited help."""
    uid = update.effective_user.id if update.effective_user else 0
    if is_admin(uid):
        txt = (
            "📜 <b>Admin Commands</b>\n"
            "/mute20 – (reply) Mute user 20 min\n"
            "/quiet20 – (reply) Alias of /mute20\n"
            "/ban &lt;reason&gt; – (reply) Ban user 1 day with optional reason\n"
            "/allowuser &lt;user_id&gt; – Allow a user (bypass moderation)\n"
            "/removeuser &lt;user_id&gt; – Remove allowed user\n"
            "/allowword &lt;word&gt; – Allow a word (bypass length/link)\n"
            "/removeword &lt;word&gt; – Remove allowed word\n"
            "/listallowed – Show allowed users & words\n"
            "/commands – Show this help\n\n"
            "🛡️ <b>Auto</b>:\n"
            "• Abuse/promotion/links ➜ Delete + 1‑day ban\n"
            "• Long messages (&gt;30 chars) ➜ Delete\n"
            "• Buttons: Unban/Unmute (admin only)"
        )
    else:
        txt = (
            "👋 <b>Group Help</b>\n"
            "/report (or /g2kreport) – (reply) Report a message to admins\n\n"
            "🚫 Auto Rules:\n"
            "• Abuse / promotion / links ➜ 1‑day ban\n"
            "• Very long messages ➜ Deleted"
        )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

cmd_help = cmd_commands  # alias

async def cmd_mute20(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mute a replied user for 20 minutes (admin only)."""
    if not is_admin(update.effective_user.id):
        return
    if not update.message.reply_to_message:
        return await send_ephemeral(update, "Reply to a user's message to mute them.")
    target = update.message.reply_to_message.from_user
    try:
        await mute_user(context, update.effective_chat.id, target.id, 20)
        kb = admin_button("🔓 Unmute", f"unmute:{target.id}")
        msg = await update.message.reply_text(
            f"🔇 Muted {html_mention(target)} for <b>20 minutes</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        delete_after(context, msg.chat_id, msg.message_id, 20)
        logger.info(f"Muted {target.id} for 20 minutes in chat {update.effective_chat.id}")
    except Exception as e:
        logger.error(f"Mute error: {e}")

async def cmd_quiet20(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias of /mute20."""
    await cmd_mute20(update, context)

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ban a replied user for 1 day with optional reason (admin only)."""
    if not is_admin(update.effective_user.id):
        return
    if not update.message.reply_to_message:
        return await send_ephemeral(update, "Reply to a user's message to ban them for 1 day.")
    target = update.message.reply_to_message.from_user
    reason = " ".join(context.args) if context.args else "No reason provided"
    try:
        await ban_user_1d(context, update.effective_chat.id, target.id)
        kb = admin_button("♻️ Unban", f"unban:{target.id}")
        msg = await update.message.reply_text(
            f"🚫 Banned {html_mention(target)} for <b>1 day</b>.\n<b>Reason:</b> {reason}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        delete_after(context, msg.chat_id, msg.message_id, 30)
        logger.info(f"Banned {target.id} (manual) in chat {update.effective_chat.id} — Reason: {reason}")
    except Exception as e:
        logger.error(f"Ban error: {e}")

async def cmd_allowuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a user ID to allowed users (admin only)."""
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return await send_ephemeral(update, "Usage: <code>/allowuser &lt;user_id&gt;</code>")
    try:
        uid = int(context.args[0])
        if uid not in STORE["users"]:
            STORE["users"].append(uid)
            save_store(STORE)
        await send_ephemeral(update, f"✅ User <code>{uid}</code> added to allow-list.")
    except ValueError:
        await send_ephemeral(update, "User ID must be an integer.")

async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a user ID from allowed users (admin only)."""
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return await send_ephemeral(update, "Usage: <code>/removeuser &lt;user_id&gt;</code>")
    try:
        uid = int(context.args[0])
        if uid in STORE["users"]:
            STORE["users"].remove(uid)
            save_store(STORE)
            await send_ephemeral(update, f"🗑️ Removed <code>{uid}</code> from allow-list.")
        else:
            await send_ephemeral(update, f"User <code>{uid}</code> not in allow-list.")
    except ValueError:
        await send_ephemeral(update, "User ID must be an integer.")

async def cmd_allowword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a word to allowed words (admin only)."""
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return await send_ephemeral(update, "Usage: <code>/allowword &lt;word&gt;</code>")
    word = " ".join(context.args).strip().lower()
    if word and word not in STORE["words"]:
        STORE["words"].append(word)
        save_store(STORE)
    await send_ephemeral(update, f"✅ Word <code>{word}</code> added to allow-list.")

async def cmd_removeword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a word from allowed words (admin only)."""
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return await send_ephemeral(update, "Usage: <code>/removeword &lt;word&gt;</code>")
    word = " ".join(context.args).strip().lower()
    if word in STORE["words"]:
        STORE["words"].remove(word)
        save_store(STORE)
        await send_ephemeral(update, f"🗑️ Removed <code>{word}</code> from allow-list.")
    else:
        await send_ephemeral(update, f"Word <code>{word}</code> not in allow-list.")

async def cmd_listallowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show allow-listed users & words (admin only)."""
    if not is_admin(update.effective_user.id):
        return
    users = ", ".join(f"<code>{u}</code>" for u in STORE["users"]) or "—"
    words = ", ".join(f"<code>{w}</code>" for w in STORE["words"]) or "—"
    await update.message.reply_text(
        f"👥 <b>Allowed users</b>: {users}\n📝 <b>Allowed words</b>: {words}",
        parse_mode=ParseMode.HTML,
    )

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Users can report a replied message to all admins via DM."""
    if not update.message.reply_to_message:
        return await send_ephemeral(update, "Reply to the message you want to report, then send /report.")
    tgt = update.message.reply_to_message
    text_html = tgt.text_html or (tgt.caption_html if tgt.caption else "[Media]")
    report = (
        f"📢 <b>Report</b>\n"
        f"From: {html_mention(update.effective_user)}\n"
        f"Group: <b>{(update.effective_chat.title or '').replace('<','&lt;').replace('>','&gt;')}</b>\n"
        f"Message: {text_html}"
    )
    for admin_id in STORE["admins"]:
        try:
            await context.bot.send_message(admin_id, report, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    await send_ephemeral(update, "✅ Report sent to admins.", secs=8)

# g2kreport alias for convenience
cmd_g2kreport = cmd_report

# ============================= CALLBACK HANDLER =============================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline button handler: enforce admin-only clicks."""
    query = update.callback_query
    await query.answer()
    clicker_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(clicker_id):
        return  # ignore non-admin clicks silently

    try:
        action, target_id_str = (query.data or "").split(":")
        target_id = int(target_id_str)
        chat_id = query.message.chat_id
        if action == "unmute":
            await context.bot.restrict_chat_member(
                chat_id, target_id, permissions=ChatPermissions(can_send_messages=True)
            )
            await query.edit_message_text("✅ User unmuted by admin.")
        elif action == "unban":
            await context.bot.unban_chat_member(chat_id, target_id)
            await query.edit_message_text("✅ User unbanned by admin.")
    except Exception as e:
        logger.error(f"Callback error: {e}")

# ============================= AUTO MODERATION =============================
async def moderation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Auto moderation flow:
      • Skip allowed users entirely.
      • If text contains any allowed word (substring), skip link/length checks (not bad words).
      • If BAD_WORDS pattern matches ➜ delete + ban 1 day (with Unban button).
      • If LINK detected ➜ delete + ban 1 day.
      • If length > 30 ➜ delete only.
    """
    msg = update.message
    if not msg:
        return

    user = msg.from_user
    chat_id = msg.chat_id

    # Bypass allowed users
    if user and user.id in STORE["users"]:
        return

    # Consider both text and caption
    raw_text = (msg.text or msg.caption or "")
    text = raw_text.strip()
    if not text:
        return

    text_lower = text.lower()

    # Allow-list words bypass length/link checks (NOT bad words)
    if any(w in text_lower for w in STORE["words"]):
        return

    # 1) Bad words / promo phrases → Delete + 1-day ban
    for pattern in BAD_WORDS:
        if pattern.search(text_lower):
            try:
                await msg.delete()
            except Exception:
                pass
            try:
                await ban_user_1d(context, chat_id, user.id)
                logger.info(f"Auto-ban: {user.id} for pattern {pattern.pattern} in chat {chat_id}")
                kb = admin_button("♻️ Unban", f"unban:{user.id}")
                note = await context.bot.send_message(
                    chat_id,
                    f"🚫 {html_mention(user)} banned for <b>1 day</b> (abuse/promotion).",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
                delete_after(context, note.chat_id, note.message_id, 15)
            except Exception as e:
                logger.error(f"Auto-ban failed: {e}")
            return

    # 2) Links → Delete + 1-day ban
    if LINK_REGEX.search(text_lower):
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            await ban_user_1d(context, chat_id, user.id)
            logger.info(f"Auto-ban: {user.id} for link in chat {chat_id}")
            kb = admin_button("♻️ Unban", f"unban:{user.id}")
            note = await context.bot.send_message(
                chat_id,
                f"🚫 {html_mention(user)} banned for <b>1 day</b> (promotion link).",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            delete_after(context, note.chat_id, note.message_id, 15)
        except Exception as e:
            logger.error(f"Auto-ban (link) failed: {e}")
        return

    # 3) Long message → Delete only
    if len(text) > 30:
        try:
            await msg.delete()
            logger.info(f"Deleted long message from {user.id} in chat {chat_id}")
        except Exception:
            pass

# ============================= ERROR HANDLER =============================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler prevents crashes."""
    logger.error("Exception while handling an update", exc_info=context.error)

## [Rest of your original code remains exactly the same until the main() function]

# ============================= MAIN =============================
def main() -> None:
    # Initialize the bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Core commands
    app.add_handler(CommandHandler(["commands", "help"], cmd_commands))
    app.add_handler(CommandHandler("mute20", cmd_mute20))
    app.add_handler(CommandHandler("quiet20", cmd_quiet20))
    app.add_handler(CommandHandler("ban", cmd_ban))

    # Allow-list management
    app.add_handler(CommandHandler("allowuser", cmd_allowuser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("allowword", cmd_allowword))
    app.add_handler(CommandHandler("removeword", cmd_removeword))
    app.add_handler(CommandHandler("listallowed", cmd_listallowed))

    # Reporting (both names supported)
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("g2kreport", cmd_g2kreport))

    # Moderation + callbacks
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, moderation))
    app.add_handler(CallbackQueryHandler(on_button))

    # Global error handler
    app.add_error_handler(on_error)

    if RENDER_EXTERNAL_URL:
        # Webhook mode for Render
        logger.info("Starting in webhook mode...")
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.getenv('PORT', '10000')),
            url_path=BOT_TOKEN,
            webhook_url=f"{RENDER_EXTERNAL_URL}/{BOT_TOKEN}"
        )
    else:
        # Polling mode for local development
        logger.info("Starting in polling mode...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
start.website()
if __name__ == "__main__":
    main()
# ============================= CONFIG =============================