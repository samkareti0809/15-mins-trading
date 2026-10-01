import sys
import os
import sqlite3
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone, timedelta

# --- CONFIGURATION ---
DB_NAME = "momentum_cache.db"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def init_database():
    """Initializes SQLite database to store pre-market cached levels safely."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS premarket_watchlist (
            ticker TEXT PRIMARY KEY,
            trigger_price REAL,
            stop_loss REAL,
            target_price REAL,
            cache_date TEXT
        )
    ''')
    conn.commit()
    conn.close()

def send_telegram_alert(message):
    """Sends instant push notifications to Telegram or simulates them in console."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"\n[Telegram Simulation Alert]:\n{message}\n")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code != 200:
            print(f"❌ Telegram Error: {response.text}")
    except Exception as e:
        print(f"❌ Connection error sending Telegram alert: {e}")

# =========================================================================
# MODE 1: PRE-MARKET SCREENER & CACHER (Runs once daily)
# =========================================================================
def run_premarket(universe):
    init_database()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"🔍 Running Pre-Market Screener for {today_str}...")
    
    # Clear previous cache to ensure fresh daily setups
    cursor.execute("DELETE FROM premarket_watchlist")
    qualified_list = []
    
    for ticker in universe:
        df = yf.download(ticker, interval="1d", period="6mo", auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        df = df.dropna().copy()
        if len(df) < 50:
            continue
            
        # Daily Indicators
        df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
        df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
        df['Vol_MA_20'] = df['Volume'].rolling(window=20).mean()
        
        last_close = df['Close'].iloc[-1]
        ema_50 = df['EMA_50'].iloc[-1]
        ema_200 = df['EMA_200'].iloc[-1]
        
        # Trend & Breakout Logic
        trend_ok = (last_close > ema_50) and (last_close > ema_200)
        high_20 = df['High'].iloc[-20:-1].max()
        is_near_high = last_close >= (high_20 * 0.98)
        recent_vol_spike = (df['Volume'].iloc[-1] > df['Vol_MA_20'].iloc[-1] * 1.2) or \
                           (df['Volume'].iloc[-2] > df['Vol_MA_20'].iloc[-2] * 1.2)
                           
        if trend_ok and is_near_high and recent_vol_spike:
            trigger = round(high_20, 2)
            sl = round(trigger * 0.975, 2)
            tp = round(trigger * 1.06, 2)
            
            cursor.execute('''
                INSERT INTO premarket_watchlist (ticker, trigger_price, stop_loss, target_price, cache_date)
                VALUES (?, ?, ?, ?, ?)
            ''', (ticker, trigger, sl, tp, today_str))
            
            qualified_list.append(f"• *{ticker}* | Trigger: INR {trigger} | SL: INR {sl}")

    conn.commit()
    conn.close()
    
    if qualified_list:
        summary_msg = f"🌅 *Pre-Market Watchlist Ready ({today_str})*\n\n" + "\n".join(qualified_list)
    else:
        summary_msg = f"🌅 *Pre-Market Watchlist ({today_str})*\nNo stocks met strict momentum criteria today. Sit tight in cash."
        
    send_telegram_alert(summary_msg)
    print("Pre-market screening complete. Cache updated successfully.")

# =========================================================================
# MODE 2: 15-MINUTE INTRADAY MONITOR (Runs every 15 mins during market hours)
# =========================================================================
def run_monitor():
    init_database()  # Guaranteed table creation to prevent missing table errors
    conn = sqlite3.connect(DB_NAME)
    try:
        watchlist_df = pd.read_sql_query("SELECT * FROM premarket_watchlist", conn)
    except Exception as e:
        print(f"⚠️ Database error or missing table: {e}")
        watchlist_df = pd.DataFrame()
    finally:
        conn.close()
    
    if watchlist_df.empty:
        print("⚠️ Pre-market cache is empty. No stocks to monitor.")
        return

    print(f"⚡ Checking {len(watchlist_df)} cached stocks on 15m live data...")
    
    for _, row in watchlist_df.iterrows():
        ticker = row['ticker']
        trigger_price = row['trigger_price']
        
        df = yf.download(ticker, interval="15m", period="1d", auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        if df.empty:
            continue
            
        latest_close = df['Close'].iloc[-1]
        latest_high = df['High'].iloc[-1]
        
        if latest_high >= trigger_price:
            alert_msg = (
                f"🚨 *15M BREAKOUT TRIGGERED!*\n\n"
                f"📈 *Stock:* `{ticker}`\n"
                f"⚡ *Trigger Level:* INR {trigger_price}\n"
                f"🔴 *Stop-Loss:* INR {row['stop_loss']}\n"
                f"🎯 *Target:* INR {row['target_price']}\n"
                f"📊 *Current Price:* INR {latest_close:.2f}"
            )
            send_telegram_alert(alert_msg)
            print(f"✅ Alert sent for {ticker}!")
        else:
            print(f"⏳ {ticker}: Monitoring... (Current High: INR {latest_high} | Trigger: INR {trigger_price})")

# =========================================================================
# SMART ENTRY POINT (Auto-detects mode based on IST time or CLI argument)
# =========================================================================
if __name__ == "__main__":
    stock_universe = ["CUPID.NS", "SCHNEIDER.NS", "MAZDOCK.NS", "COCHINSHIP.NS", "HAL.NS", "BEL.NS", "TITAN.NS"]
    
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        IST = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(IST)
        
        current_hour = now_ist.hour
        current_minute = now_ist.minute
        
        print(f"Current IST Time: {now_ist.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Pre-market window: Run between 8:40 AM and 9:00 AM IST
        if current_hour == 8 and 40 <= current_minute <= 59:
            mode = "premarket"
        else:
            mode = "monitor"
            
    print(f"Executing mode: {mode}")
    
    if mode == "premarket":
        run_premarket(stock_universe)
    elif mode == "monitor":
        run_monitor()
