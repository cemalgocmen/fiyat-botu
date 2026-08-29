import sqlite3
import time
import asyncio
import os
import subprocess
import json

# Kurulumları dinamik olarak yapıyoruz ki GitHub Actions'da çalışsın
try:
    import playwright_stealth
except ImportError:
    subprocess.check_call(["pip", "install", "playwright-stealth==1.0.6"])

try:
    import aiosqlite
except ImportError:
    subprocess.check_call(["pip", "install", "aiosqlite==0.19.0"])

from playwright.async_api import async_playwright
from datetime import datetime
import re
from playwright_stealth import stealth_async
import aiosqlite

DB_FILE = "products.db"
# Github Actions Secrets'tan alınacak
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

URLS = {
    "Amazon": []
}

async def init_db():
    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute('''
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
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS custom_keywords (
                keyword TEXT PRIMARY KEY,
                threshold REAL DEFAULT 20.0
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS sent_messages (
                message_id INTEGER PRIMARY KEY,
                chat_id TEXT,
                sent_at TIMESTAMP
            )
        ''')
        
        await conn.execute("UPDATE custom_keywords SET threshold = 20.0 WHERE threshold < 20.0")
        
        try:
            await conn.execute("ALTER TABLE products ADD COLUMN last_alert_date TEXT")
        except:
            pass 

        await conn.commit()

async def send_telegram_alert(title, url, old_price, new_price, drop_percentage, site):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        import requests
        if site == "Amazon_Depo" and drop_percentage == 0:
            msg = f"📦 DEPO FIRSATI 📦\n\nÜrün: {title}\nFiyat: {new_price} TL\nLink: {url}"
        else:
            msg = f"🔥 İNDİRİM ({site}) 🔥\n\nÜrün: {title}\nEski Fiyat: {old_price} TL\nYeni Fiyat: {new_price} TL\nİndirim: %{drop_percentage:.2f}\nLink: {url}"
            
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: requests.post(api_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}).json())
            
            if resp.get("ok"):
                message_id = resp["result"]["message_id"]
                async with aiosqlite.connect(DB_FILE) as conn:
                    await conn.execute("INSERT INTO sent_messages (message_id, chat_id, sent_at) VALUES (?, ?, ?)", 
                              (message_id, str(TELEGRAM_CHAT_ID), datetime.now().timestamp()))
                    await conn.commit()
        except Exception as e:
            print(f"Telegram gönderim hatası: {e}")

async def check_telegram_messages():
    if not TELEGRAM_BOT_TOKEN: return
    
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT value FROM bot_state WHERE key='last_update_id'")
        row = await cursor.fetchone()
        offset = int(row[0]) + 1 if row else 0
        
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=5"
        import requests
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(None, lambda: requests.get(api_url).json())
            if not resp.get("ok") or not resp.get("result"):
                return
                
            max_update_id = offset - 1
            for update in resp["result"]:
                update_id = update["update_id"]
                if update_id > max_update_id:
                    max_update_id = update_id
                    
                msg_data = None
                if "message" in update and "text" in update["message"]:
                    msg_data = update["message"]
                elif "edited_message" in update and "text" in update["edited_message"]:
                    msg_data = update["edited_message"]
                    
                if msg_data:
                    text = msg_data["text"]
                    chat_id = msg_data["chat"]["id"]
                    
                    added = []
                    removed = []
                    words = text.split()
                    
                    if text.strip() == "/liste":
                        cursor = await conn.execute("SELECT keyword FROM custom_keywords")
                        kws = await cursor.fetchall()
                        msg = "📋 **Özel Taramalarınız:**\n" + "\n".join([f"#{k[0]}" for k in kws]) if kws else "Liste boş."
                        await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": msg}))
                        continue
                    
                    if text.strip() == "/test":
                        if TELEGRAM_CHAT_ID:
                            test_msg = "🚀 Sistem Testi: Amazon İndirim Botu aktif ve bu gruba başarıyla mesaj gönderebiliyor!"
                            await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": test_msg}))
                            await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "✅ Test mesajı başarıyla indirim grubuna ateşlendi!"}))
                        else:
                            await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "❌ Hata: Grup ID'si bulunamadı!"}))
                        continue

                    if text.strip() == "/yardim":
                        help_text = (
                            "🤖 *Amazon Fiyat Botu - Kullanım Kılavuzu*\n\n"
                            "📌 *Ürün Arama Komutları*\n"
                            "• `#kelime`: Aranacak kelimeyi ekler (Örn: `#telefon`)\n"
                            "• `-#kelime`: Kelimeyi siler (Örn: `-#telefon`)\n"
                            "• `/liste`: Takip edilen kelimeleri gösterir.\n\n"
                            "⚙️ *İndirim ve Tarama Ayarları*\n"
                            "• `/oran <yüzde>`: İndirim eşiğini ayarlar (Örn: `/oran 15`)\n"
                            "• `/sure <dakika>`: Tarama sıklığını ayarlar (Örn: `/sure 60`)\n\n"
                            "🧹 *Spam Önlemleri*\n"
                            "• `/cooldown <gün>`: Tekrarlayan indirimler için bekleme süresi (Örn: `/cooldown 3`)\n"
                            "• `/sil <saat>`: Atılan indirim mesajları kaç saat sonra silinsin (Örn: `/sil 24` veya `/sil kapat`)\n\n"
                            "🚀 *Test*\n"
                            "• `/test`: Sistemin gruba bağlantısını test eder."
                        )
                        await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": help_text, "parse_mode": "Markdown"}))
                        continue
                    
                    if text.strip().startswith("/depooran"):
                        parts = text.strip().split()
                        if len(parts) > 1:
                            oran_str = parts[1].replace('%', '')
                            if oran_str.isdigit() or oran_str.replace('.','',1).isdigit():
                                yeni_oran = float(oran_str)
                                await conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('depo_threshold', ?)", (str(yeni_oran),))
                                msg = f"✅ Depo içi fiyat düşüşleri için indirim oranı %{yeni_oran} olarak güncellendi! (Not: Yeni eklenen depo ürünleri anında bildirilmeye devam edecektir)."
                                await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": msg}))
                                continue
                        
                        await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "❌ Hatalı kullanım. Örnek: /depooran 5"}))
                        continue
                    
                    if text.strip().startswith("/oran"):
                        parts = text.strip().split()
                        if len(parts) > 1:
                            oran_str = parts[1].replace('%', '')
                            if oran_str.isdigit():
                                yeni_oran = float(oran_str)
                                await conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('global_threshold', ?)", (str(yeni_oran),))
                                msg = f"✅ Global indirim oranı %{yeni_oran} olarak güncellendi! Tüm taramalar (ana kategoriler dahil) bu orana göre yapılacak."
                                await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": msg}))
                                continue
                        
                        await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "❌ Hatalı kullanım. Örnek: /oran 15 veya /oran %20"}))
                        continue
                    
                    if text.strip().startswith("/sure"):
                        parts = text.strip().split()
                        if len(parts) > 1 and parts[1].isdigit():
                            yeni_sure = int(parts[1])
                            await conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('scan_interval', ?)", (str(yeni_sure),))
                            msg = f"⏱️ Tarama sıklığı {yeni_sure} dakika olarak güncellendi! Bot artık {yeni_sure} dakikada bir Amazon'a bağlanacak."
                            await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": msg}))
                            continue
                        await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "❌ Hatalı kullanım. Örnek: /sure 120"}))
                        continue
                    
                    if text.strip().startswith("/sil"):
                        parts = text.strip().split()
                        if len(parts) > 1 and parts[1].isdigit():
                            sil_sure = int(parts[1])
                            await conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('auto_delete_hours', ?)", (str(sil_sure),))
                            msg = f"🧹 Otomatik silme aktif! Bundan sonra gönderilen indirim mesajları {sil_sure} saat sonra gruptan silinecek."
                            await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": msg}))
                            continue
                        elif len(parts) > 1 and parts[1].lower() == "kapat":
                            await conn.execute("DELETE FROM bot_state WHERE key='auto_delete_hours'")
                            msg = f"🛑 Otomatik silme kapatıldı! Mesajlar artık kalıcı olacak."
                            await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": msg}))
                            continue
                        await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "❌ Hatalı kullanım. Örnek: /sil 12 veya /sil 24 (Kapatmak için: /sil kapat)"}))
                        continue
                    
                    if text.strip().startswith("/cooldown"):
                        parts = text.strip().split()
                        if len(parts) > 1 and parts[1].isdigit():
                            yeni_gun = int(parts[1])
                            await conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('cooldown_days', ?)", (str(yeni_gun),))
                            msg = f"⏳ Soğuma süresi {yeni_gun} gün olarak ayarlandı! Aynı ürün (en düşük fiyat rekoru kırmadığı sürece) {yeni_gun} gün boyunca tekrar indirim mesajı atmayacak."
                            await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": msg}))
                            continue
                        await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "❌ Hatalı kullanım. Örnek: /cooldown 3 veya /cooldown 5"}))
                        continue
                    
                    for w in words:
                        if w.startswith("-#") and len(w) > 2:
                            kw = w[2:].lower().replace("_", "+")
                            await conn.execute("DELETE FROM custom_keywords WHERE keyword=?", (kw,))
                            removed.append(kw)
                        elif w.startswith("#") and len(w) > 1:
                            kw = w[1:].lower().replace("_", "+")
                            await conn.execute("INSERT OR IGNORE INTO custom_keywords (keyword) VALUES (?)", (kw,))
                            added.append(kw)
                    
                    if added or removed:
                        msg = ""
                        if added: msg += f"✅ Eklendi: {', '.join(added)}\n"
                        if removed: msg += f"🗑️ Silindi: {', '.join(removed)}"
                        await loop.run_in_executor(None, lambda: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": msg.strip()}))
            
            await conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('last_update_id', ?)", (str(max_update_id),))
            await conn.commit()
        except Exception as e:
            print("Telegram getUpdates hatasi:", e)

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

async def process_product(product_id, title, url, site, current_price, threshold):
    if not current_price or not product_id:
        return

    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT value FROM bot_state WHERE key='cooldown_days'")
        cd_row = await cursor.fetchone()
        cooldown_days = int(cd_row[0]) if cd_row else 3
        
        cursor = await conn.execute("SELECT value FROM bot_state WHERE key='depo_threshold'")
        dt_row = await cursor.fetchone()
        depo_threshold = float(dt_row[0]) if dt_row else 0.0
        
        cursor = await conn.execute("SELECT current_price, last_alert_date, lowest_price FROM products WHERE id=?", (product_id,))
        result = await cursor.fetchone()
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        
        if result:
            old_price = result[0]
            last_alert_date = result[1] if len(result) > 1 else None
            lowest_price = result[2] if len(result) > 2 else old_price
            
            if current_price < old_price:
                drop_percentage = ((old_price - current_price) / old_price) * 100
                
                effective_threshold = threshold
                if site == "Amazon_Depo":
                    effective_threshold = depo_threshold # Kullanıcının belirlediği depo düşüş oranı
                    
                if drop_percentage >= effective_threshold:
                    days_since_alert = cooldown_days + 1
                    if last_alert_date:
                        try:
                            last_alert_dt = datetime.strptime(last_alert_date, "%Y-%m-%d")
                            days_since_alert = (now - last_alert_dt).days
                        except:
                            pass
                    
                    if days_since_alert >= cooldown_days or current_price < lowest_price:
                        await send_telegram_alert(title, url, old_price, current_price, drop_percentage, site)
                        await conn.execute("UPDATE products SET last_alert_date=? WHERE id=?", (today_str, product_id))
            await conn.execute('''
                UPDATE products 
                SET current_price=?, last_checked=?, lowest_price = MIN(lowest_price, ?) 
                WHERE id=?
            ''', (current_price, now, current_price, product_id))
        else:
            if site == "Amazon_Depo":
                # Depoda yeni gorulen urunler direkt Telegrama atilir (eski fiyat sifir kabul edilir, yuzde hesabi yapilmaz)
                await send_telegram_alert(title, url, current_price, current_price, 0.0, site)
                await conn.execute('''
                    INSERT INTO products (id, title, url, site, current_price, last_checked, lowest_price, last_alert_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (product_id, title, url, site, current_price, now, current_price, today_str))
            else:
                await conn.execute('''
                    INSERT INTO products (id, title, url, site, current_price, last_checked, lowest_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (product_id, title, url, site, current_price, now, current_price))
            
        await conn.commit()

async def scroll_down(page):
    for _ in range(5):
        await page.mouse.wheel(0, 1000)
        await asyncio.sleep(1)

async def crawl_site(context, url, site, threshold, semaphore, is_depo=False):
    async with semaphore:
        print(f"\n{site} taranıyor: {url}")
        page = await context.new_page()
        try:
            await stealth_async(page)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await scroll_down(page)
            
            products = []
            if site == "Amazon":
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
                            sec_price = await c.query_selector('[data-cy="secondary-offer-recipe"] .a-color-base')
                            if sec_price:
                                pt = await sec_price.inner_text()
                                price = parse_price(pt)
                            else:
                                price = None
                            
                        products.append((href.split('/dp/')[1].split('/')[0] if '/dp/' in href else href, title, href, price))
                    except: continue

            print(f"{site} -> Bu sayfada {len(products)} ürün bulundu ve işleniyor...")
            for pid, title, link, price in products:
                if pid and title and price:
                    actual_site = "Amazon_Depo" if is_depo else site
                    await process_product(f"{actual_site}_{pid}", title, link, actual_site, price, threshold)
                    
        except Exception as e:
            print(f"{site} tarama hatası: {e}")
        finally:
            await page.close()

async def cleanup_old_messages():
    if not TELEGRAM_BOT_TOKEN: return
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT value FROM bot_state WHERE key='auto_delete_hours'")
        row = await cursor.fetchone()
        if not row:
            return
            
        auto_delete_hours = int(row[0])
        threshold_ts = datetime.now().timestamp() - (auto_delete_hours * 3600)
        
        try:
            cursor = await conn.execute("SELECT message_id, chat_id FROM sent_messages WHERE sent_at < ?", (threshold_ts,))
            old_messages = await cursor.fetchall()
            
            import requests
            loop = asyncio.get_event_loop()
            for msg_id, chat_id in old_messages:
                try:
                    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
                    await loop.run_in_executor(None, lambda: requests.post(api_url, json={"chat_id": chat_id, "message_id": msg_id}))
                    await conn.execute("DELETE FROM sent_messages WHERE message_id=?", (msg_id,))
                except:
                    pass
        except:
            pass 
            
        await conn.commit()

async def cleanup_database():
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("DELETE FROM products WHERE last_checked < datetime('now', '-7 days')")
            await conn.commit()
            await conn.execute("VACUUM")
            await conn.commit()
    except Exception as e:
        print("Veritabani temizlik hatasi:", e)

async def main():
    await init_db()
    await cleanup_database()
    await cleanup_old_messages()
    
    await check_telegram_messages()
    
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT value FROM bot_state WHERE key='scan_interval'")
        row = await cursor.fetchone()
        scan_interval = int(row[0]) if row else 120 
        
        cursor = await conn.execute("SELECT value FROM bot_state WHERE key='last_full_scan'")
        row = await cursor.fetchone()
        last_full_scan = float(row[0]) if row else 0.0
        
        current_time = datetime.now().timestamp()
        if current_time - last_full_scan < (scan_interval * 60):
            print(f"Tarama sıklığı ({scan_interval} dk) henüz dolmadı. Sadece mesajlar okundu. Çıkılıyor.")
            return
            
        await conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('last_full_scan', ?)", (str(current_time),))
        await conn.commit()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        print(f"\n--- GITHUB ACTIONS TARAMA TURU BAŞLIYOR: {datetime.now().strftime('%H:%M:%S')} ---")
        
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT keyword, threshold FROM custom_keywords")
            custom_kws = await cursor.fetchall()
            
            cursor = await conn.execute("SELECT value FROM bot_state WHERE key='global_threshold'")
            row = await cursor.fetchone()
            global_threshold = float(row[0]) if row else None

        for kw, thresh in custom_kws:
            search_url_amz = f"https://www.amazon.com.tr/s?k={kw}&rh=p_6%3AA1UNQM1SR2CHM"
            if not any(item["url"] == search_url_amz for item in URLS["Amazon"]):
                URLS["Amazon"].append({"url": search_url_amz, "threshold": thresh, "is_depo": False})
                
            search_url_depo = f"https://www.amazon.com.tr/s?k={kw}&rh=n%3A44219324031"
            if not any(item["url"] == search_url_depo for item in URLS["Amazon"]):
                URLS["Amazon"].append({"url": search_url_depo, "threshold": thresh, "is_depo": True})

        # Ayni anda 3 sayfa taramasi icin semaphore
        semaphore = asyncio.Semaphore(3)
        tasks = []

        for site, items in URLS.items():
            for item in items:
                base_url = item["url"]
                is_depo_flag = item.get("is_depo", False)
                threshold = global_threshold if global_threshold else item["threshold"]
                # Tarama sayfasini sistemi yormamak icin 10 sayfa olarak revize ettik
                for page_num in range(1, 11):
                    if page_num == 1:
                        page_url = base_url
                    else:
                        sep = "&" if "?" in base_url else "?"
                        if site == "Amazon":
                            page_url = f"{base_url}{sep}page={page_num}"
                            
                    task = asyncio.create_task(crawl_site(context, page_url, site, threshold, semaphore, is_depo=is_depo_flag))
                    tasks.append(task)
        
        if tasks:
            await asyncio.gather(*tasks)
        
        print("\nTur tamamlandı, tarayıcı kapatılıyor.")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
