import logging
import sqlite3
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from solders.keypair import Keypair
from solana.rpc.async_api import AsyncClient
from datetime import datetime
import base58

# ===== CONFIG =====
BOT_TOKEN = "7650902215:AAHGd2ch6pNF49H3DHokEAFfYe5yYHordmc"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
LOG_CHANNEL_ID = -1002767755239  # Your log channel ID
ADMIN_IDS = [7641767864,7641767864]  # Admin user IDs

# ===== DATABASE =====
def init_db():
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        private_key TEXT,
        wallet_address TEXT,
        referral_code TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS copy_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        copied_address TEXT,
        is_active BOOLEAN DEFAULT 1
    )""")
    
    conn.commit()
    conn.close()

init_db()

# ===== LOGGING =====
async def log_action(context: ContextTypes.DEFAULT_TYPE, message: str, user: dict = None):
    """Log to channel and forward to admins"""
    try:
        # Format log message
        log_msg = f"📝 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        if user:
            log_msg += f"👤 User: {user.get('full_name', '')} (@{user.get('username', 'N/A')})\n"
            log_msg += f"🆔 ID: `{user.get('id', '')}`\n"
        log_msg += f"📌 Action: {message}"
        
        # Send to log channel
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=log_msg,
            parse_mode='Markdown'
        )
        
        # Forward to all admins
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🔔 {log_msg}",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logging.error(f"Failed to forward to admin {admin_id}: {e}")
                
    except Exception as e:
        logging.error(f"Logging failed: {e}")

# ===== SOLANA =====
async def get_balance(address: str) -> float:
    async with AsyncClient(SOLANA_RPC) as client:
        balance = await client.get_balance(address)
        return balance.value / 10**9 if balance.value else 0.0

# ===== KEYBOARDS =====
def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔐 Import Wallet", callback_data="import_wallet"),
            InlineKeyboardButton("📤 Invite Friends", callback_data="invite_friends")
        ],
        [
            InlineKeyboardButton("💰 Buy/Sell", callback_data="buy_sell"),
            InlineKeyboardButton("📊 Asset", callback_data="asset")
        ],
        [
            InlineKeyboardButton("⚙ Settings", callback_data="settings"),
            InlineKeyboardButton("👛 Wallet", callback_data="wallet_info")
        ],
        [
            InlineKeyboardButton("📈 Copy Trading", callback_data="copy_trading"),
            InlineKeyboardButton("⏱ Limit Order", callback_data="limit_order")
        ],
        [
            InlineKeyboardButton("🌐 Language", callback_data="language"),
            InlineKeyboardButton("❓ Help", callback_data="help")
        ]
    ])

def back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]])

def copy_trade_keyboard(user_id: int):
    with sqlite3.connect('bot.db') as conn:
        count = conn.execute("SELECT COUNT(*) FROM copy_trades WHERE user_id = ?", (user_id,)).fetchone()[0]
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"➕ Add New Copy ({count}/10)", callback_data="add_copy_trade")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
    ])

# ===== HANDLERS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    with sqlite3.connect('bot.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT wallet_address FROM users WHERE user_id = ?", (user.id,))
        wallet = cursor.fetchone()[0] if cursor.fetchone() else None
        
        # Generate referral code if new user
        if not cursor.fetchone():
            ref_code = f"ref_{base58.b58encode(str(user.id).encode()).decode()[:8]}"
            conn.execute(
                "INSERT INTO users (user_id, referral_code) VALUES (?, ?)",
                (user.id, ref_code)
            )
    
    balance = await get_balance(wallet) if wallet else 0.0
    
    # Log new user
    await log_action(context, "New user started bot", {
        'id': user.id,
        'full_name': user.full_name,
        'username': user.username
    })
    
    await update.message.reply_text(
        f"# Axiom Trading Bot\n\n"
        f"**Wallet address:** {wallet or 'Not connected'}\n"
        f"**Balance:** {balance:.2f} SOL\n\n"
        "Select an option:",
        reply_markup=main_menu(),
        parse_mode='Markdown'
    )

# ... [rest of your handlers remain unchanged]

# ===== MAIN APP =====
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation handlers
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_import_wallet, pattern="^import_wallet$"),
            CallbackQueryHandler(handle_buy_sell, pattern="^buy_sell$")
        ],
        states={
            "AWAITING_WALLET": [MessageHandler(filters.TEXT & ~filters.COMMAND, process_wallet_import)],
            "AWAITING_CONTRACT": [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.reply_text("Trade processing would go here"))]
        },
        fallbacks=[CommandHandler("cancel", start)]
    )
    
    # Add all handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_invite_friends, pattern="^invite_friends$"))
    application.add_handler(CallbackQueryHandler(handle_wallet_info, pattern="^wallet_info$"))
    application.add_handler(CallbackQueryHandler(handle_copy_trading, pattern="^copy_trading$"))
    application.add_handler(CallbackQueryHandler(handle_help, pattern="^help$"))
    application.add_handler(CallbackQueryHandler(start, pattern="^main_menu$"))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    application.run_polling()

if __name__ == "__main__":
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    main()
