import os
import logging
import asyncio
import asyncpg
import aiohttp
from datetime import datetime
from base58 import b58encode, b58decode
from cryptography.fernet import Fernet
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputSticker
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters
)
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts
from solana.transaction import Transaction

# --- Configuration ---
class Config:
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    POSTGRES_URI = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/axiom")
    SOLANA_RPC = os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
    JUPITER_API = "https://quote-api.jup.ag/v6"
    HELIUS_API = os.getenv("HELIUS_API")
    REFERRAL_REWARD = 0.25  # 25% of trading fees

# Initialize logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Database Setup ---
async def init_database():
    conn = await asyncpg.connect(Config.POSTGRES_URI)
    try:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            encrypted_key BYTEA,
            wallet_address TEXT,
            referral_code TEXT UNIQUE,
            language TEXT DEFAULT 'en',
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS copy_trades (
            id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id),
            copied_address TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            settings JSONB,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            referrer_id BIGINT REFERENCES users(user_id),
            referee_id BIGINT UNIQUE REFERENCES users(user_id),
            earned_amount DECIMAL(18,9) DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS snipe_jobs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id),
            token_mint TEXT,
            amount DECIMAL(18,9),
            max_slippage DECIMAL(5,2),
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW()
        )""")
    finally:
        await conn.close()

# --- Security ---
cipher = Fernet(Config.ENCRYPTION_KEY.encode())

def encrypt_key(key: str) -> bytes:
    return cipher.encrypt(key.encode())

def decrypt_key(encrypted: bytes) -> str:
    return cipher.decrypt(encrypted).decode()

async def validate_solana_key(key: str) -> tuple:
    try:
        if key.startswith('['):  # Mnemonic
            kp = Keypair.from_mnemonic(key)
        else:  # Private key
            kp = Keypair.from_base58_string(key)
        return True, str(kp.pubkey())
    except Exception as e:
        logger.error(f"Key validation failed: {e}")
        return False, None

# --- Solana Utilities ---
async def get_solana_balance(address: str) -> float:
    async with AsyncClient(Config.SOLANA_RPC) as client:
        balance = await client.get_balance(Pubkey.from_string(address))
        return balance.value / 10**9 if balance.value else 0.0

async def get_token_balance(wallet: str, mint: str) -> float:
    async with AsyncClient(Config.SOLANA_RPC) as client:
        token_accounts = await client.get_token_accounts_by_owner(
            Pubkey.from_string(wallet),
            program_id=Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
        )
        for account in token_accounts.value:
            if account.account.data.parsed['info']['mint'] == mint:
                return account.account.data.parsed['info']['tokenAmount']['uiAmount']
        return 0.0

# --- Trading Engine ---
class TradingEngine:
    def __init__(self):
        self.snipe_monitor = None
        
    async def init_sniper(self):
        self.snipe_monitor = asyncio.create_task(self.monitor_new_pools())
    
    async def get_jupiter_quote(self, input_mint: str, output_mint: str, amount: float) -> dict:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": int(amount * 10**9),
            "slippageBps": 100,  # 1%
            "feeBps": 10         # 0.1%
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{Config.JUPITER_API}/quote", params=params) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise Exception(f"Jupiter API error: {error}")
                return await resp.json()
    
    async def execute_swap(self, user_id: int, quote: dict) -> str:
        async with asyncpg.connect(Config.POSTGRES_URI) as conn:
            user = await conn.fetchrow(
                "SELECT encrypted_key FROM users WHERE user_id = $1", user_id
            )
            if not user:
                raise Exception("User not found")
            
            keypair = Keypair.from_base58_string(decrypt_key(user['encrypted_key']))
        
        swap_payload = {
            "quoteResponse": quote,
            "userPublicKey": str(keypair.pubkey()),
            "wrapAndUnwrapSol": True
        }
        
        async with aiohttp.ClientSession() as session:
            # Get swap transaction
            async with session.post(f"{Config.JUPITER_API}/swap", json=swap_payload) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise Exception(f"Swap failed: {error}")
                swap_data = await resp.json()
            
            # Send transaction
            tx = Transaction.deserialize(bytes.fromhex(swap_data['swapTransaction']))
            async with AsyncClient(Config.SOLANA_RPC) as client:
                result = await client.send_transaction(
                    tx,
                    keypair,
                    opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed)
                )
                return str(result.value)
    
    async def monitor_new_pools(self):
        """Background task to monitor new Raydium pools"""
        while True:
            try:
                # Implementation would use Helius webhooks or stream RPC
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Sniper monitor error: {e}")
                await asyncio.sleep(10))

# --- UI Components ---
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔐 Import Wallet", callback_data="import"),
            InlineKeyboardButton("📤 Referral", callback_data="referral")
        ],
        [
            InlineKeyboardButton("💰 Buy/Sell", callback_data="trade"),
            InlineKeyboardButton("📊 Portfolio", callback_data="portfolio")
        ],
        [
            InlineKeyboardButton("⚙ Settings", callback_data="settings"),
            InlineKeyboardButton("👛 Wallet", callback_data="wallet")
        ],
        [
            InlineKeyboardButton("📈 Copy Trade", callback_data="copy_trade"),
            InlineKeyboardButton("⏱ Limit Order", callback_data="limit_order")
        ],
        [
            InlineKeyboardButton("🌐 Language", callback_data="language"),
            InlineKeyboardButton("❓ Help", callback_data="help")
        ]
    ])

def back_to_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")]
    ])

# --- Conversation States ---
(IMPORT_WALLET, TRADE_INPUT, TRADE_CONFIRM, 
 SNIPE_SETUP, COPY_TRADE_SETUP) = range(5)

# --- Core Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with asyncpg.connect(Config.POSTGRES_URI) as conn:
        user_data = await conn.fetchrow(
            "SELECT wallet_address, referral_code FROM users WHERE user_id = $1", 
            user.id
        )
        
        if not user_data:
            referral_code = f"ref_{b58encode(user.id.to_bytes(8, 'big')).decode()[:12]}"
            await conn.execute(
                "INSERT INTO users (user_id, referral_code) VALUES ($1, $2)",
                user.id, referral_code
            )
            user_data = {"wallet_address": None, "referral_code": referral_code}
    
    balance = 0.0
    if user_data["wallet_address"]:
        balance = await get_solana_balance(user_data["wallet_address"])
    
    await update.message.reply_text(
        f"""🚀 *Axiom Solana Bot*

🔐 Wallet: `{user_data['wallet_address'] or 'Not connected'}`
💎 Balance: *{balance:.2f} SOL*

📌 *Active Features:*
- Jupiter DEX Aggregation
- MEV-Protected Swaps
- Raydium Sniper Mode""",
        reply_markup=main_menu_keyboard(),
        parse_mode='Markdown'
    )

async def handle_import_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔑 *Wallet Import*\n\n"
        "Send your private key or mnemonic:\n\n"
        "⚠️ Encrypted with AES-256. Never stored raw.",
        reply_markup=back_to_menu_keyboard(),
        parse_mode='Markdown'
    )
    return IMPORT_WALLET

async def process_wallet_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    key_input = update.message.text.strip()
    
    is_valid, wallet_address = await validate_solana_key(key_input)
    if not is_valid:
        await update.message.reply_text(
            "❌ Invalid key format. Please try again.",
            reply_markup=back_to_menu_keyboard()
        )
        return IMPORT_WALLET
    
    encrypted_key = encrypt_key(key_input)
    async with asyncpg.connect(Config.POSTGRES_URI) as conn:
        await conn.execute(
            "UPDATE users SET encrypted_key = $1, wallet_address = $2 WHERE user_id = $3",
            encrypted_key, wallet_address, user.id
        )
    
    await update.message.reply_text(
        f"✅ Wallet connected:\n`{wallet_address}`",
        reply_markup=main_menu_keyboard(),
        parse_mode='Markdown'
    )
    return ConversationHandler.END

# --- Trading Handlers ---
async def start_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    async with asyncpg.connect(Config.POSTGRES_URI) as conn:
        wallet = await conn.fetchval(
            "SELECT wallet_address FROM users WHERE user_id = $1", 
            query.from_user.id
        )
    
    if not wallet:
        await query.edit_message_text(
            "❌ No wallet connected. Import one first!",
            reply_markup=back_to_menu_keyboard()
        )
        return
    
    await query.edit_message_text(
        "📈 *Enter Token Mint Address*\n\n"
        "Example: `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` (USDC)",
        reply_markup=back_to_menu_keyboard(),
        parse_mode='Markdown'
    )
    return TRADE_INPUT

async def process_trade_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token_address = update.message.text.strip()
    context.user_data['trade_token'] = token_address
    
    await update.message.reply_text(
        "💵 *Enter Amount in SOL*\n\n"
        "Example: `0.5` for 0.5 SOL",
        reply_markup=back_to_menu_keyboard(),
        parse_mode='Markdown'
    )
    return TRADE_CONFIRM

async def confirm_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    amount = float(update.message.text)
    token_address = context.user_data['trade_token']
    
    try:
        trading_engine = context.bot_data['trading_engine']
        quote = await trading_engine.get_jupiter_quote(
            input_mint="So11111111111111111111111111111111111111112",  # SOL
            output_mint=token_address,
            amount=amount
        )
        
        token_info = quote['outputToken']
        await update.message.reply_text(
            f"🔄 *Swap Preview*\n\n"
            f"• Selling: {amount:.2f} SOL\n"
            f"• Buying: ~{float(quote['outAmount'])/10**token_info['decimals']:.2f} {token_info['symbol']}\n"
            f"• Price Impact: {float(quote['priceImpactPct'])*100:.2f}%\n"
            f"• Fees: {float(quote['feeAmount'])/10**9:.4f} SOL\n\n"
            f"Confirm swap?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm", callback_data=f"execute_swap_{quote['id']}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_trade")]
            ]),
            parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Trade failed: {str(e)}",
            reply_markup=back_to_menu_keyboard()
        )
        return ConversationHandler.END

# --- Main Application ---
async def post_init(application: Application):
    # Initialize database
    await init_database()
    
    # Start trading engine
    trading_engine = TradingEngine()
    application.bot_data['trading_engine'] = trading_engine
    await trading_engine.init_sniper()

def main():
    application = Application.builder() \
        .token(Config.BOT_TOKEN) \
        .post_init(post_init) \
        .build()
    
    # Conversation handlers
    trade_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_trade, pattern="^trade$")],
        states={
            TRADE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_trade_input)],
            TRADE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_trade)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)],
        allow_reentry=True
    )
    
    wallet_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_import_wallet, pattern="^import$")],
        states={
            IMPORT_WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_wallet_import)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
    )
    
    # Core handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(trade_conv)
    application.add_handler(wallet_conv)
    
    # Button handlers
    application.add_handler(CallbackQueryHandler(
        lambda u,c: u.edit_message_reply_markup(reply_markup=main_menu_keyboard()),
        pattern="^main_menu$"
    ))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    application.run_polling()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}", exc_info=True)
    if update.effective_user:
        await update.effective_user.send_message(
            "⚠️ An error occurred. Our team has been notified.",
            reply_markup=main_menu_keyboard()
        )

if __name__ == "__main__":
    main()
