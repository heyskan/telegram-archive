import os
import json
import time
import requests
import hashlib
import jdatetime
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

CHANNELS_FILE = "telegram/channels.json"
OUTPUT_FILE = "telegram.md"
CONTENT_DIR = "telegram/content"
LAST_IDS_FILE = "telegram/last_ids.json"

os.makedirs(CONTENT_DIR, exist_ok=True)

def load_json(path, default=None):
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return default if default is not None else {}

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def is_alarm_content(url):
    """تشخیص گیف آژیر و محتوای تکراری خبر فوری"""
    if not url:
        return True
    # کلمات کلیدی برای محتوای آژیر و خبر فوری
    alarm_keywords = ['alarm', 'alert', 'urgent', 'breaking', 'siren', 'warning', 'notification']
    for keyword in alarm_keywords:
        if keyword in url.lower():
            return True
    # اندازه‌های کوچک (آیکون‌های تکراری)
    if '100x100' in url or '200x200' in url or '50x50' in url:
        return True
    return False

def is_profile_photo(url):
    """تشخیص عکس پروفایل"""
    if not url:
        return True
    profile_keywords = ['avatar', 'profile', 'user_photo', 'channel_photo', 'photo.jpg']
    for keyword in profile_keywords:
        if keyword in url.lower():
            return True
    return False

def download_media(url, channel, msg_id):
    if not url:
        return None
    
    # رد کردن عکس پروفایل
    if is_profile_photo(url):
        return None
    
    # رد کردن آژیر و محتوای خبر فوری
    if is_alarm_content(url):
        return None
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        resp = requests.get(url, timeout=30, headers=headers)
        
        if resp.status_code == 200:
            content_type = resp.headers.get('content-type', '')
            
            if 'image' in content_type:
                # فقط JPG رو ذخیره کن (فرمت اصلی تلگرام)
                if 'jpeg' in content_type or 'jpg' in content_type:
                    ext = 'jpg'
                elif 'png' in content_type:
                    ext = 'png'
                else:
                    return None  # فقط jpg و png قبول کن
                
                filename = f"{channel}_{msg_id}_{hashlib.md5(url.encode()).hexdigest()[:8]}.{ext}"
                filepath = os.path.join(CONTENT_DIR, filename)
                with open(filepath, 'wb') as f:
                    f.write(resp.content)
                return f"telegram/content/{filename}"
                
    except Exception as e:
        pass
    
    return None

def convert_time(timestamp_str):
    try:
        if 'T' in timestamp_str:
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        else:
            dt = datetime.strptime(timestamp_str, "%d %b %Y at %H:%M")
        jd = jdatetime.datetime.fromgregorian(datetime=dt)
        return f"{jd.year}/{jd.month}/{jd.day} - {jd.hour:02d}:{jd.minute:02d}"
    except:
        return timestamp_str

def extract_message_info(element, channel):
    try:
        # گرفتن لینک پیام برای شناسه
        link_elem = element.query_selector('a.tgme_widget_message_date')
        if not link_elem:
            return None
        link = link_elem.get_attribute('href')
        msg_id = int(link.split('/')[-1]) if link else 0
        
        # زمان پیام
        time_elem = element.query_selector('time')
        msg_time = time_elem.get_attribute('datetime') if time_elem else ""
        
        # متن پیام
        text_elem = element.query_selector('.tgme_widget_message_text')
        text = text_elem.inner_text().strip() if text_elem else ""
        
        # ========== گرفتن فقط عکس اصلی پست ==========
        images = []
        
        # روش اصلی: عکس‌های داخل message_photo (عکس اصلی پست)
        photo_div = element.query_selector('.tgme_widget_message_photo')
        if photo_div:
            img = photo_div.query_selector('img')
            if img:
                src = img.get_attribute('src')
                if src:
                    downloaded = download_media(src, channel, msg_id)
                    if downloaded:
                        images.append(downloaded)
        
        # اگر عکس اصلی نبود، روش جایگزین
        if not images:
            image_div = element.query_selector('.tgme_widget_message_image')
            if image_div:
                img = image_div.query_selector('img')
                if img:
                    src = img.get_attribute('src')
                    if src:
                        downloaded = download_media(src, channel, msg_id)
                        if downloaded:
                            images.append(downloaded)
        
        # ========== ویدیوها (فقط ویدیوی اصلی، نه آژیر) ==========
        videos = []
        
        # فقط ویدیویی که داخل message_video هست (نه آژیرهای تکراری)
        video_div = element.query_selector('.tgme_widget_message_video')
        if video_div:
            video = video_div.query_selector('video')
            if video:
                src = video.get_attribute('src')
                if src and not is_alarm_content(src):
                    # دانلود ویدیو (اختیاری - می‌تونی غیرفعال کنی)
                    pass  # فعلاً ویدیو رو دانلود نکن
        
        return {
            'id': msg_id,
            'time': msg_time,
            'text': text,
            'images': images,
            'videos': videos
        }
    except Exception as e:
        return None

def main():
    with open(CHANNELS_FILE, 'r') as f:
        channels = json.load(f)
    
    last_ids = load_json(LAST_IDS_FILE, {})
    all_messages = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        for channel in channels:
            print(f"\n📡 پردازش کانال: {channel}")
            url = f"https://t.me/s/{channel}"
            
            try:
                page.goto(url, timeout=60000, wait_until='networkidle')
                time.sleep(3)
                
                # اسکرول برای بارگذاری
                for _ in range(5):
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    time.sleep(2)
                
                message_elements = page.query_selector_all('.tgme_widget_message')
                print(f"   تعداد پیام‌ها: {len(message_elements)}")
                
                last_seen = last_ids.get(channel, 0)
                new_count = 0
                total_images = 0
                
                for elem in message_elements:
                    msg_info = extract_message_info(elem, channel)
                    if msg_info and msg_info['id'] > last_seen:
                        msg_info['channel'] = channel
                        all_messages.append(msg_info)
                        new_count += 1
                        total_images += len(msg_info['images'])
                        if msg_info['id'] > last_ids.get(channel, 0):
                            last_ids[channel] = msg_info['id']
                
                print(f"   ✅ {new_count} پیام جدید (عکس: {total_images})")
                save_json(LAST_IDS_FILE, last_ids)
                
            except Exception as e:
                print(f"   ❌ خطا: {e}")
        
        browser.close()
    
    all_messages.sort(key=lambda x: x['id'], reverse=True)
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("# 📡 آرشیو خودکار کانال‌های تلگرام\n\n")
        f.write(f"آخرین به‌روزرسانی: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n")
        
        for msg in all_messages:
            f.write(f"## 📌 {msg['channel']}\n")
            f.write(f"**زمان:** {convert_time(msg['time'])} | **شناسه:** {msg['id']}\n\n")
            
            if msg['text']:
                f.write(f"{msg['text']}\n\n")
            
            for img in msg['images']:
                f.write(f"![تصویر]({img})\n\n")
            
            f.write("---\n\n")
    
    print(f"\n✅ آرشیو ذخیره شد: {OUTPUT_FILE}")
    print(f"📊 مجموع پیام‌ها: {len(all_messages)}")

if __name__ == "__main__":
    main()
