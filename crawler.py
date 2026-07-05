import sqlite3
import time
import asyncio
import os
from playwright.async_api import async_playwright
from datetime import datetime
import re

DB_FILE = "products.db"
# Github Actions Secrets'tan alınacak
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

URLS = {
    "Hepsiburada": [
        "https://www.hepsiburada.com/bilgisayarlar-c-2147483646",
        "https://www.hepsiburada.com/telefonlar-c-2147483642",
        "https://www.hepsiburada.com/tv-ses-sistemleri-c-2147483638",
        "https://www.hepsiburada.com/beyaz-esya-c-2147483637",
        "https://www.hepsiburada.com/kucuk-ev-aletleri-c-2147483633",
        "https://www.hepsiburada.com/giyim-ayakkabi-c-2147483636",
        "https://www.hepsiburada.com/kozmetik-kisisel-bakim-c-2147483634",
        "https://www.hepsiburada.com/anne-bebek-oyuncak-c-2147483639",
        "https://www.hepsiburada.com/spor-outdoor-c-2147483645",
        "https://www.hepsiburada.com/yapi-market-bahce-c-2147483643"
    ],
    "Trendyol": [
        "https://www.trendyol.com/elektronik-x-c104052",
        "https://www.trendyol.com/kadin-x-c99",
        "https://www.trendyol.com/erkek-x-c108",
        "https://www.trendyol.com/cocuk-x-c118",
        "https://www.trendyol.com/ev-yasam-x-c116",
        "https://www.trendyol.com/supermarket-x-c104192",
        "https://www.trendyol.com/kozmetik-x-c117",
        "https://www.trendyol.com/ayakkabi-canta-x-c114",
        "https://www.trendyol.com/saat-aksesuar-x-c34"
    ],
    "N11": [
        "https://www.n11.com/bilgisayar",
        "https://www.n11.com/telefon-ve-aksesuarlari",
        "https://www.n11.com/televizyon-ve-ses-sistemleri",
        "https://www.n11.com/beyaz-esya",
        "https://www.n11.com/elektrikli-ev-aletleri",
        "https://www.n11.com/giyim-ayakkabi",
        "https://www.n11.com/kozmetik-kisisel-bakim",
        "https://www.n11.com/anne-bebek-oyuncak",
        "https://www.n11.com/spor-outdoor"
    ],
    "Amazon": [
        "https://www.amazon.com.tr/b?node=13709907031", 
        "https://www.amazon.com.tr/b?node=13709880031", 
        "https://www.amazon.com.tr/b?node=13710034031", 
        "https://www.amazon.com.tr/b?node=13710038031", 
        "https://www.amazon.com.tr/b?node=13710899031", 
        "https://www.amazon.com.tr/b?node=13710762031", 
        "https://www.amazon.com.tr/b?node=13710041031", 
        "https://www.amazon.com.tr/b?node=13711090031", 
        "https://www.amazon.com.tr/b?node=13710871031"  
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
            last_checked TIMESTAMP,
            lowest_price REAL
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

def process_product(product_id, title, url, site, current_price):
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
            if drop_percentage >= 10.0:
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

async def crawl_site(page, url, site):
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
            cards = await page.query_selector_all('.s-result-item')
            for c in cards:
                try:
                    title_el = await c.query_selector('.a-text-normal')
                    title = await title_el.inner_text() if title_el else ""
                    link_el = await c.query_selector('.a-link-normal.s-no-outline')
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
        
        print(f"\n--- GITHUB ACTIONS TARAMA TURU BAŞLIYOR: {datetime.now().strftime('%H:%M:%S')} ---")
        # Kanala sadece haftada 1 kez (Pazartesi 12:00 - 12:30 arası) hayatta olduğunu haber ver
        now = datetime.now()
        if now.weekday() == 0 and now.hour == 12 and now.minute < 30:
            if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                import requests
                try:
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": "🤖 HAFTALIK KONTROL: Fiyat Botu sapasağlam hayatta ve arka planda çalışmaya devam ediyor! Gözüm yüksek indirimlerde! 🕵️‍♂️"
                    })
                except Exception as e:
                    pass
                
        for site, links in URLS.items():
            for url in links:
                await crawl_site(page, url, site)
                await asyncio.sleep(2) 
        
        print("\nTur tamamlandı, tarayıcı kapatılıyor.")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
