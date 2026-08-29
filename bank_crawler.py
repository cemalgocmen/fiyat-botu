import sqlite3
import time
import asyncio
import os
import subprocess

try:
    import playwright
except ImportError:
    subprocess.check_call(["pip", "install", "playwright"])

try:
    import aiosqlite
except ImportError:
    subprocess.check_call(["pip", "install", "aiosqlite==0.19.0"])

from playwright.async_api import async_playwright
import aiosqlite
from datetime import datetime

DB_FILE = "products.db"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BANKS = [
    {"name": "Axess", "url": "https://www.axess.com.tr/axess/kampanyalar"},
    {"name": "Paraf", "url": "https://www.paraf.com.tr/tr/kampanyalar.html"},
    {"name": "CardFinans", "url": "https://www.cardfinans.com/kampanyalar"},
    {"name": "Enpara", "url": "https://www.qnb.com.tr/enpara/kampanyalar"},
    {"name": "Kuveyt Türk", "url": "https://www.kuveytturk.com.tr/kampanyalar"}
]

async def init_db():
    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bank_campaigns (
                bank_name TEXT PRIMARY KEY,
                has_amazon INTEGER,
                last_checked TIMESTAMP
            )
        ''')
        await conn.commit()

async def send_telegram_alert(bank_name, url):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        import requests
        msg = f"💳 YENİ BANKA KAMPANYASI 💳\n\n{bank_name} kampanya sayfasında 'Amazon' kelimesi tespit edildi! Yeni bir fırsat olabilir.\n\nLink: {url}"
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: requests.post(api_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}))
        except Exception as e:
            print(f"Telegram error: {e}")

async def crawl_bank(context, bank):
    page = await context.new_page()
    found_amazon = False
    try:
        await page.goto(bank['url'], timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        text = await page.evaluate("document.body.innerText")
        if 'amazon' in text.lower():
            found_amazon = True
    except Exception as e:
        print(f"Error checking {bank['name']}: {e}")
    finally:
        await page.close()
    return bank['name'], bank['url'], found_amazon

async def main():
    await init_db()
    
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT value FROM bot_state WHERE key='bank_scan_interval'")
        row = await cursor.fetchone()
        scan_interval = int(row[0]) if row else 12  # Default 12 hours
        
        cursor = await conn.execute("SELECT value FROM bot_state WHERE key='last_bank_scan'")
        row = await cursor.fetchone()
        last_scan = float(row[0]) if row else 0.0
        
        current_time = datetime.now().timestamp()
        if current_time - last_scan < (scan_interval * 3600):
            print(f"Bank scan interval ({scan_interval}h) not reached. Exiting.")
            return
            
        await conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('last_bank_scan', ?)", (str(current_time),))
        await conn.commit()

    print("Starting bank campaign scan...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        tasks = []
        for bank in BANKS:
            tasks.append(asyncio.create_task(crawl_bank(context, bank)))
            
        results = await asyncio.gather(*tasks)
        
        async with aiosqlite.connect(DB_FILE) as conn:
            for name, url, found_amazon in results:
                cursor = await conn.execute("SELECT has_amazon FROM bank_campaigns WHERE bank_name=?", (name,))
                row = await cursor.fetchone()
                
                previously_had_amazon = bool(row[0]) if row else False
                
                if found_amazon and not previously_had_amazon:
                    print(f"NEW AMAZON CAMPAIGN: {name}")
                    await send_telegram_alert(name, url)
                
                await conn.execute("INSERT OR REPLACE INTO bank_campaigns (bank_name, has_amazon, last_checked) VALUES (?, ?, ?)", 
                                  (name, int(found_amazon), current_time))
            await conn.commit()
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
