import sys
import os
import sqlite3
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime

DB_NAME = "momentum_cache.db"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def init_database():
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
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram Simulation]: {message}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

def run_premarket(universe):
    init_database()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"🔍 Running Pre-Market Screener for {today_str}...")
    
    cursor.execute("DELETE FROM premarket_watchlist")
    qualified_list = []
    
    for ticker in universe:
        df = yf.download(ticker, interval="1d", period="6mo", auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna().copy()
        if len(df) < 50:
            continue
            
        df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
        df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
        df['Vol_MA_20'] = df['Volume'].rolling(window=20).mean()
        
        last_close = df['Close'].iloc[-1]
        ema_50 = df['EMA_50'].iloc[-1]
        ema_200 = df['EMA_200'].iloc[-1]
        
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
            qualified_list.append(f"• *{ticker}* | Trigger: ₹{trigger} | SL: ₹{sl}")

    conn.commit()
    conn.close()
    
    summary_msg = f"🌅 *Pre-Market Watchlist ({today_str})*\n\n" + ("\n".join(qualified_list) if qualified_list else "No stocks met criteria today.")
    send_telegram_alert(summary_msg)

def run_monitor():
    conn = sqlite3.connect(DB_NAME)
    watchlist_df = pd.read_sql_query("SELECT * FROM premarket_watchlist", conn)
    conn.close()
    
    if watchlist_df.empty:
        print("⚠️ Pre-market cache is empty.")
        return

    print(f"⚡ Checking {len(watchlist_df)} cached stocks on 15m data...")
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
                f"⚡ *Trigger:* ₹{trigger_price}\n"
                f"🔴 *SL:* ₹{row['stop_loss']}\n"
                f"🎯 *Target:* ₹{row['target_price']}\n"
                f"📊 *Current:* ₹{latest_close:.2f}"
            )
            send_telegram_alert(alert_msg)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "monitor"
    stock_universe = ["CUPID.NS", "SCHNEIDER.NS", "MAZDOCK.NS", "COCHINSHIP.NS", "HAL.NS", "BEL.NS"]
    
    if mode == "premarket":
        run_premarket(stock_universe)
    elif mode == "monitor":
        run_monitor()
