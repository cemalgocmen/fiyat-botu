import sqlite3
import time
import asyncio
import os
import subprocess

# Kurulumları dinamik olarak yapıyoruz ki GitHub Actions'da çalışsın
try:
    import playwright_stealth
except ImportError:
    subprocess.check_call(["pip", "install", "playwright-stealth==1.0.6"])

from playwright.async_api import async_playwright
from datetime import datetime
import re
from playwright_stealth import stealth_async

DB_FILE = "products.db"
# Github Actions Secrets'tan alınacak
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

URLS = {
    "Amazon": [
        # Mevcut Kategoriler
        {"url": "https://www.amazon.com.tr/b?node=13709907031", "threshold": 15.0}, # Elektronik
        {"url": "https://www.amazon.com.tr/b?node=13709930031", "threshold": 15.0}, # Yapi Market
        {"url": "https://www.amazon.com.tr/b?node=13710034031", "threshold": 15.0}, # Outdoor & Kamp
        {"url": "https://www.amazon.com.tr/b?node=12503610031", "threshold": 15.0}, # Kucuk Ev Aletleri
        {"url": "https://www.amazon.com.tr/b?node=12503614031", "threshold": 15.0}, # Mutfak Aletleri
        {"url": "https://www.amazon.com.tr/s?k=bilgisayar", "threshold": 15.0}, # Bilgisayar
        {"url": "https://www.amazon.com.tr/s?k=televizyon", "threshold": 15.0}, # Televizyon
        {"url": "https://www.amazon.com.tr/s?k=giyim", "threshold": 15.0}, # Moda & Giyim
        {"url": "https://www.amazon.com.tr/s?k=oyuncak", "threshold": 15.0}, # Oyuncak
        {"url": "https://www.amazon.com.tr/s?k=kitap", "threshold": 15.0}, # Kitap
        {"url": "https://www.amazon.com.tr/s?k=otomobil", "threshold": 15.0}, # Otomotiv
        {"url": "https://www.amazon.com.tr/s?k=spor+aletleri", "threshold": 15.0}, # Spor
        
        # Yeni Eklenen Spesifik Aramalar (Sniper Hedefleri)
        {"url": "https://www.amazon.com.tr/s?k=termos", "threshold": 15.0},
        {"url": "https://www.amazon.com.tr/s?k=cadir", "threshold": 15.0},
        {"url": "https://www.amazon.com.tr/s?k=kamp+malzemeleri", "threshold": 15.0},
        {"url": "https://www.amazon.com.tr/s?k=pirinc", "threshold": 15.0},
        {"url": "https://www.amazon.com.tr/s?k=bulgur", "threshold": 15.0},
        {"url": "https://www.amazon.com.tr/s?k=cay", "threshold": 15.0},
        {"url": "https://www.amazon.com.tr/s?k=seker", "threshold": 15.0},
        {"url": "https://www.amazon.com.tr/s?k=bal", "threshold": 15.0},
        {"url": "https://www.amazon.com.tr/s?k=supermarket", "threshold": 15.0},
        {"url": "https://www.amazon.com.tr/s?k=sampuan", "threshold": 15.0},
        {"url": "https://www.amazon.com.tr/s?k=sac+kremi", "threshold": 15.0},
        {"url": "https://www.amazon.com.tr/s?k=dis+macunu", "threshold": 15.0},
    ]
}

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            site TEXT,
            current_price REAL,
            lowest_price REAL,
            last_checked TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS custom_keywords (
            keyword TEXT PRIMARY KEY,
            threshold REAL DEFAULT 15.0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()
    conn.close()

def send_telegram_alert(title, url, old_price, new_price, drop_percentage, site):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        import requests
        msg = f"🔥 BÜYÜK İNDİRİM ({site}) 🔥\n\nÜrün: {title}\nEski Fiyat: {old_price} TL\nYeni Fiyat: {new_price} TL\nİndirim: %{drop_percentage:.2f}\nLink: {url}"
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            requests.post(api_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
        except Exception as e:
            print(f"Telegram gönderim hatası: {e}")


def check_telegram_messages():
    if not TELEGRAM_BOT_TOKEN: return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT value FROM bot_state WHERE key='last_update_id'")
    row = cursor.fetchone()
    offset = int(row[0]) + 1 if row else 0
    
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=5"
    import requests
    try:
        resp = requests.get(api_url).json()
        if not resp.get("ok") or not resp.get("result"):
            conn.close()
            return
            
        max_update_id = offset - 1
        for update in resp["result"]:
            update_id = update["update_id"]
            if update_id > max_update_id:
                max_update_id = update_id
                
            if "message" in update and "text" in update["message"]:
                text = update["message"]["text"]
                chat_id = update["message"]["chat"]["id"]
                
                # Hashtag yakalama
                added = []
                removed = []
                words = text.split()
                
                if text.strip() == "/liste":
                    cursor.execute("SELECT keyword FROM custom_keywords")
                    kws = cursor.fetchall()
                    msg = "📋 **Özel Taramalarınız:**\\n" + "\\n".join([f"#{k[0]}" for k in kws]) if kws else "Liste boş."
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": msg})
                    continue
                
                for w in words:
                    if w.startswith("-#") and len(w) > 2:
                        kw = w[2:].lower().replace("_", "+")
                        cursor.execute("DELETE FROM custom_keywords WHERE keyword=?", (kw,))
                        removed.append(kw)
                    elif w.startswith("#") and len(w) > 1:
                        kw = w[1:].lower().replace("_", "+")
                        cursor.execute("INSERT OR IGNORE INTO custom_keywords (keyword) VALUES (?)", (kw,))
                        added.append(kw)
                
                if added or removed:
                    msg = ""
                    if added: msg += f"✅ Eklendi: {', '.join(added)}\\n"
                    if removed: msg += f"🗑️ Silindi: {', '.join(removed)}"
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": msg.strip()})
        
        cursor.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('last_update_id', ?)", (str(max_update_id),))
        conn.commit()
    except Exception as e:
        print("Telegram getUpdates hatasi:", e)
    finally:
        conn.close()

def parse_price(price_str):
    if not price_str: return None
    clean_str = re.sub(r'[^\d.,]', '', price_str)
    if ',' in clean_str and '.' in clean_str:
        if clean_str.rfind(',') > clean_str.rfind('.'):
            clean_str = clean_str.replace('.', '').replace(',', '.')
        else:
            clean_str = clean_str.replace(',', '')
    elif ',' in clean_str:
        clean_str = clean_str.replace(',', '.')
    
    try:
        return float(clean_str)
    except:
        return None

def process_product(product_id, title, url, site, current_price, threshold):
    if not current_price or not product_id:
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT current_price FROM products WHERE id=?", (product_id,))
    result = cursor.fetchone()
    now = datetime.now()
    
    if result:
        old_price = result[0]
        if current_price < old_price:
            drop_percentage = ((old_price - current_price) / old_price) * 100
            if drop_percentage >= threshold:
                send_telegram_alert(title, url, old_price, current_price, drop_percentage, site)
        cursor.execute('''
            UPDATE products 
            SET current_price=?, last_checked=?, lowest_price = MIN(lowest_price, ?) 
            WHERE id=?
        ''', (current_price, now, current_price, product_id))
    else:
        cursor.execute('''
            INSERT INTO products (id, title, url, site, current_price, last_checked, lowest_price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (product_id, title, url, site, current_price, now, current_price))
        
    conn.commit()
    conn.close()

async def scroll_down(page):
    for _ in range(5):
        await page.mouse.wheel(0, 1000)
        await asyncio.sleep(1)

async def crawl_site(page, url, site, threshold):
    print(f"\n{site} taranıyor: {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await scroll_down(page)
        
        products = []
        if site == "Hepsiburada":
            cards = await page.query_selector_all('li.productListContent-zAP0Y5msy8OHn5z7T_K_')
            if not cards: cards = await page.query_selector_all('[data-test-id="product-card-container"]')
            for c in cards:
                try:
                    title_el = await c.query_selector('[data-test-id="product-card-name"]')
                    title = await title_el.inner_text() if title_el else ""
                    link_el = await c.query_selector('a')
                    href = "https://www.hepsiburada.com" + await link_el.get_attribute('href') if link_el else ""
                    price_el = await c.query_selector('[data-test-id="price-current-price"]')
                    price = parse_price(await price_el.inner_text()) if price_el else None
                    products.append((href.split('-p-')[-1] if '-p-' in href else href, title, href, price))
                except: continue
                
        elif site == "Trendyol":
            cards = await page.query_selector_all('.p-card-wrppr')
            for c in cards:
                try:
                    title_el = await c.query_selector('.prdct-desc-cntnr-name')
                    title = await title_el.inner_text() if title_el else ""
                    link_el = await c.query_selector('a')
                    href = "https://www.trendyol.com" + await link_el.get_attribute('href') if link_el else ""
                    price_el = await c.query_selector('.prc-box-dscntd')
                    price = parse_price(await price_el.inner_text()) if price_el else None
                    products.append((href.split('?')[0].split('-p-')[-1] if '-p-' in href else href, title, href, price))
                except: continue
                
        elif site == "N11":
            cards = await page.query_selector_all('li.pro')
            for c in cards:
                try:
                    title_el = await c.query_selector('h3.productName')
                    title = await title_el.inner_text() if title_el else ""
                    link_el = await c.query_selector('a.pl')
                    href = await link_el.get_attribute('href') if link_el else ""
                    price_el = await c.query_selector('ins')
                    price = parse_price(await price_el.inner_text()) if price_el else None
                    products.append((href, title, href, price))
                except: continue
                
        elif site == "Amazon":
            cards = await page.query_selector_all('.s-result-item[data-component-type="s-search-result"], li.octopus-pc-item, div[class*="apbSearchResultItem"]')
            
            for c in cards:
                try:
                    title_el = await c.query_selector('.a-text-normal, .octopus-pc-asin-title span')
                    title = await title_el.inner_text() if title_el else ""
                    
                    link_el = await c.query_selector('.a-link-normal.s-no-outline, a.octopus-pc-item-link')
                    href = "https://www.amazon.com.tr" + await link_el.get_attribute('href') if link_el else ""
                    
                    price_whole = await c.query_selector('.a-price-whole')
                    price_fraction = await c.query_selector('.a-price-fraction')
                    if price_whole:
                        w = await price_whole.inner_text()
                        f = await price_fraction.inner_text() if price_fraction else "00"
                        price = parse_price(f"{w}{f}")
                    else:
                        price = None
                        
                    products.append((href.split('/dp/')[1].split('/')[0] if '/dp/' in href else href, title, href, price))
                except: continue

        print(f"{site} -> Bu sayfada {len(products)} ürün bulundu ve işleniyor...")
        for pid, title, link, price in products:
            if pid and title and price:
                process_product(f"{site}_{pid}", title, link, site, price)
                
    except Exception as e:
        print(f"{site} tarama hatası: {e}")

async def main():
    init_db()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await stealth_async(page)
        
        print(f"\\n--- GITHUB ACTIONS TARAMA TURU BAŞLIYOR: {datetime.now().strftime('%H:%M:%S')} ---")
        
        # Telegram mesajlarini oku
        check_telegram_messages()
        
        # Ozel kelimeleri URL listesine ekle
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT keyword, threshold FROM custom_keywords")
        custom_kws = cursor.fetchall()
        conn.close()
        
        for kw, thresh in custom_kws:
            search_url = f"https://www.amazon.com.tr/s?k={kw}"
            # Eger listede yoksa ekle
            if not any(item["url"] == search_url for item in URLS["Amazon"]):
                URLS["Amazon"].append({"url": search_url, "threshold": thresh})
                
        # Kanala her sabah 09:00 - 09:30 arası hayatta olduğunu haber ver
        now = datetime.now()
        if now.hour == 9 and now.minute < 30:
            if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                import requests
                try:
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": "🌅 GÜNLÜK KONTROL: Günaydın! Fiyat Botu sapasağlam hayatta ve dünden beri aralıksız taramaya devam ediyor. Bugün de nöbetteyiz! 🕵️‍♂️"
                    })
                except Exception as e:
                    pass
                
        for site, items in URLS.items():
            for item in items:
                base_url = item["url"]
                threshold = item["threshold"]
                for page_num in range(1, 4): # İlk 3 sayfa
                    if page_num == 1:
                        page_url = base_url
                    else:
                        sep = "&" if "?" in base_url else "?"
                        if site == "Trendyol":
                            page_url = f"{base_url}{sep}pi={page_num}"
                        elif site == "Hepsiburada":
                            page_url = f"{base_url}{sep}sayfa={page_num}"
                        elif site == "N11":
                            page_url = f"{base_url}{sep}pg={page_num}"
                        elif site == "Amazon":
                            page_url = f"{base_url}{sep}page={page_num}"
                            
                    await crawl_site(page, page_url, site, threshold)
                await asyncio.sleep(2) 
        
        print("\nTur tamamlandı, tarayıcı kapatılıyor.")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
