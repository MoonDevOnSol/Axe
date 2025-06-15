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
from cryptography.fernet import Fernet
from datetime import datetime
import base58

# ===== CONFIG =====
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
ENCRYPTION_KEY = Fernet.generate_key()
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# ===== DATABASE =====
def init_db():
    with sqlite3.connect('bot.db') as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            encrypted_key BLOB,
            wallet_address TEXT,
            referral_code TEXT,
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

# ===== SECURITY =====
cipher = Fernet(ENCRYPTION_KEY)

def encrypt_key(key: str) -> bytes:
    return cipher.encrypt(key.encode())

def decrypt_key(encrypted: bytes) -> str:
    return cipher.decrypt(encrypted).decode()

# ===== SOLANA =====
async def get_balance(address: str) -> float:
    async with AsyncClient(SOLANA_RPC) as client:
        balance = await client.get_balance(address)
        return balance.value / 10**9 if balance.value else 0.0

# ===== KEYBOARDS =====
def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔐Import Wallet", callback_data="import_wallet"),
            InlineKeyboardButton("📤Invite Friends", callback_data="invite_friends")
        ],
        [
            InlineKeyboardButton("💰Buy/Sell", callback_data="buy_sell"),
            InlineKeyboardButton("📊Asset", callback_data="asset")
        ],
        [
            InlineKeyboardButton("⚙Settings", callback_data="settings"),
            InlineKeyboardButton("👛Wallet", callback_data="wallet_info")
        ],
        [
            InlineKeyboardButton("📈Copy Trading", callback_data="copy_trading"),
            InlineKeyboardButton("⏱Limit Order", callback_data="limit_order")
        ],
        [
            InlineKeyboardButton("🌐Language", callback_data="language"),
            InlineKeyboardButton("❓Help", callback_data="help")
        ]
    ])

def back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]])

# ===== HANDLERS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    with sqlite3.connect('bot.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT wallet_address FROM users WHERE user_id = ?", (user.id,))
        wallet = cursor.fetchone()[0] if cursor.fetchone() else None
    
    balance = await get_balance(wallet) if wallet else 0.0
    
    await update.message.reply_text(
        f"# Axiom Trading Bot\n\n"
        f"**Wallet address:** {wallet or 'null'}\n"
        f"Wallet balance: **{balance:.2f} SOL**\n\n"
        "Select an option:",
        reply_markup=main_menu(),
        parse_mode='Markdown'
    )

async def handle_import_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Please enter your private key or mnemonic phrase to import your wallet:\n\n"
        "⚠️ Do not disclose your private key to others.",
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
        encrypted_key = encrypt_key(key_input)
        
        with sqlite3.connect('bot.db') as conn:
            conn.execute(
                "INSERT OR REPLACE INTO users (user_id, encrypted_key, wallet_address) VALUES (?, ?, ?)",
                (user.id, encrypted_key, wallet_address)
            )
        
        await update.message.reply_text(
            "✅ Wallet imported successfully!",
            reply_markup=main_menu()
        )
    except Exception as e:
        await update.message.reply_text(
            "❌ Invalid key. Please try again.",
            reply_markup=back_button()
        )
        return "AWAITING_WALLET"
    
    return ConversationHandler.END

async def handle_invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    with sqlite3.connect('bot.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT referral_code FROM users WHERE user_id = ?", (query.from_user.id,))
        ref_code = cursor.fetchone()[0]
    
    await query.edit_message_text(
        f"🔗 Invite link: https://t.me/YOUR_BOT_NAME?start={ref_code}\n\n"
        f"💵 Withdrawable: 0 SOL (0 pending)\n"
        f"💰 Total withdrawn: 0 SOL\n"
        f"👥 Total invited: 0 people\n\n"
        f"📖 Rules:\n"
        f"1. Earn 25% of invitees' trading fees permanently\n"
        f"2. Withdrawals start from 0.01 SOL",
        reply_markup=back_button()
    )

async def handle_buy_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Please enter the token contract address:",
        reply_markup=back_button()
    )
    return "AWAITING_CONTRACT"

async def handle_wallet_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    with sqlite3.connect('bot.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT wallet_address FROM users WHERE user_id = ?", (query.from_user.id,))
        wallet = cursor.fetchone()[0] if cursor.fetchone() else None
    
    if not wallet:
        await query.edit_message_text(
            "❌ Failed.\n\n"
            "⚠️ Error: You have no wallets. Please import a wallet first.",
            reply_markup=back_button()
        )
    else:
        balance = await get_balance(wallet)
        await query.edit_message_text(
            f"Wallet address: {wallet}\n"
            f"Balance: {balance:.2f} SOL",
            reply_markup=back_button()
        )

async def handle_copy_trading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    with sqlite3.connect('bot.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM copy_trades WHERE user_id = ?", (query.from_user.id,))
        count = cursor.fetchone()[0]
    
    await query.edit_message_text(
        f"Copy Trade wallets: {count}/10\n\n"
        "Click \"➕Add New Copy\" to set up copy trades.\n"
        "🟢 Active  🟠 Paused",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add New Copy", callback_data="add_copy_trade")],
            [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
        ])
    )

async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🌟 Bot Lagging?\n"
        "Switch to another bot instance.\n\n"
        "🌟 Reached Sniping Limit?\n"
        "Wait for cooldown or trade manually.\n\n"
        "🌟 Check Holdings\n"
        "Use /portfolio to view assets.\n\n"
        "🌟 Trading Fees\n"
        "1% fee on all trades.\n\n"
        "🌟 Withdrawals\n"
        "Min 0.01 SOL, processed daily.",
        reply_markup=back_button()
    )

# ===== MAIN APP =====
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation for wallet import
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_import_wallet, pattern="^import_wallet$")],
        states={
            "AWAITING_WALLET": [MessageHandler(filters.TEXT & ~filters.COMMAND, process_wallet_import)]
        },
        fallbacks=[CommandHandler("cancel", start)]
    )
    
    # Add all handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_invite_friends, pattern="^invite_friends$"))
    application.add_handler(CallbackQueryHandler(handle_buy_sell, pattern="^buy_sell$"))
    application.add_handler(CallbackQueryHandler(handle_wallet_info, pattern="^wallet_info$"))
    application.add_handler(CallbackQueryHandler(handle_copy_trading, pattern="^copy_trading$"))
    application.add_handler(CallbackQueryHandler(handle_help, pattern="^help$"))
    application.add_handler(CallbackQueryHandler(start, pattern="^main_menu$"))
    
    application.run_polling()

if __name__ == "__main__":
    main()
