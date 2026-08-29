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

# We only care about these banks
TARGET_KEYWORDS = ["paraf", "axess", "qnb", "finans", "enpara", "kuveyt", "saglam"]

async def init_db():
    async with aiosqlite.connect(DB_FILE) as conn:
        # We will use href as the unique identifier for campaigns now
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bank_campaigns_v2 (
                href TEXT PRIMARY KEY,
                title TEXT,
                bank_name TEXT,
                found_date TIMESTAMP
            )
        ''')
        # Just in case bot_state is not created yet
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        await conn.commit()

async def send_telegram_alert(bank_name, title, url):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        import requests
        msg = f"💳 YENİ KAMPANYA ({bank_name.upper()}) 💳\n\n📌 {title}\n\nLink: {url}"
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: requests.post(api_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}))
        except Exception as e:
            print(f"Telegram error: {e}")

async def crawl_aggregator(context):
    page = await context.new_page()
    found_campaigns = []
    try:
        await page.goto("https://www.getkampania.com/markalar/amazon-kampanyalari", timeout=30000, wait_until="networkidle")
        
        campaigns = await page.evaluate('''() => {
            let arr = [];
            for(let a of document.querySelectorAll('a')) {
                if(a.innerText.trim().length > 10 && a.innerText.toUpperCase().includes('AMAZON')) {
                    arr.push({text: a.innerText.trim(), href: a.href});
                }
            }
            return arr;
        }''')
        
        for c in campaigns:
            text = c['text']
            href = c['href']
            
            # Split text to get a clean title (often second line)
            lines = [line.strip() for line in text.split('\\n') if line.strip()]
            title = lines[1] if len(lines) > 1 else lines[0]
            
            # Check if it matches our target banks
            matched_bank = None
            search_str = (href + " " + text).lower()
            
            for keyword in TARGET_KEYWORDS:
                if keyword in search_str:
                    matched_bank = keyword
                    break
                    
            if matched_bank:
                if matched_bank == "finans": matched_bank = "qnb"
                if matched_bank == "saglam": matched_bank = "kuveyt"
                
                found_campaigns.append({
                    "href": href,
                    "title": title,
                    "bank": matched_bank
                })
                
    except Exception as e:
        print(f"Error checking aggregator: {e}")
    finally:
        await page.close()
        
    return found_campaigns

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

    print("Starting bank campaign scan (Aggregator)...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        campaigns = await crawl_aggregator(context)
        
        async with aiosqlite.connect(DB_FILE) as conn:
            for c in campaigns:
                href = c['href']
                title = c['title']
                bank = c['bank']
                
                cursor = await conn.execute("SELECT 1 FROM bank_campaigns_v2 WHERE href=?", (href,))
                row = await cursor.fetchone()
                
                if not row:
                    print(f"NEW AMAZON CAMPAIGN FOUND: [{bank}] {title}")
                    await send_telegram_alert(bank, title, href)
                    await conn.execute("INSERT INTO bank_campaigns_v2 (href, title, bank_name, found_date) VALUES (?, ?, ?, ?)", 
                                      (href, title, bank, current_time))
            await conn.commit()
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
