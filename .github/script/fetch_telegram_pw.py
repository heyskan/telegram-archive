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

def should_skip_url(url):
    """فقط چیزهایی که نباید دانلود بشن رو فیلتر کن"""
    if not url:
        return True
    
    url_lower = url.lower()
    
    # عکس پروفایل
    if 'user_photo' in url_lower:
        return True
    
    # عکس کوچک پروفایل
    if 'photo.jpg' in url_lower and '100x100' in url_lower:
        return True
    
    # آژیر و خبر فوری (کلمات کلیدی)
    alarm_words = ['alarm', 'alert', 'siren', 'urgent', 'breaking']
    for word in alarm_words:
        if word in url_lower:
            return True
    
    # اموجی
    if 'emoji' in url_lower:
        return True
    
    return False

def download_media(url, channel, msg_id):
    if should_skip_url(url):
        return None
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(url, timeout=30, headers=headers)
        
        if resp.status_code == 200:
            content_type = resp.headers.get('content-type', '')
            
            if 'image' in content_type:
                # تعیین پسوند
                if 'png' in content_type:
                    ext = 'png'
                elif 'gif' in content_type:
                    ext = 'gif'
                elif 'webp' in content_type:
                    ext = 'webp'
                else:
                    ext = 'jpg'
                
                filename = f"{channel}_{msg_id}_{hashlib.md5(url.encode()).hexdigest()[:8]}.{ext}"
                filepath = os.path.join(CONTENT_DIR, filename)
                with open(filepath, 'wb') as f:
                    f.write(resp.content)
                return f"telegram/content/{filename}"
            
            elif 'video' in content_type:
                filename = f"{channel}_{msg_id}_{hashlib.md5(url.encode()).hexdigest()[:8]}.mp4"
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
        # شناسه پیام
        link_elem = element.query_selector('a.tgme_widget_message_date')
        if not link_elem:
            return None
        link = link_elem.get_attribute('href')
        msg_id = int(link.split('/')[-1]) if link else 0
        
        # زمان
        time_elem = element.query_selector('time')
        msg_time = time_elem.get_attribute('datetime') if time_elem else ""
        
        # متن
        text_elem = element.query_selector('.tgme_widget_message_text')
        text = text_elem.inner_text().strip() if text_elem else ""
        
        # عکس‌ها (همه عکس‌های داخل پیام، به جز پروفایل)
        images = []
        for img in element.query_selector_all('img'):
            src = img.get_attribute('src')
            if src:
                # رد کردن عکس پروفایل
                if 'user_photo' in src:
                    continue
                downloaded = download_media(src, channel, msg_id)
                if downloaded:
                    images.append(downloaded)
        
        # ویدیوها
        videos = []
        for video in element.query_selector_all('video'):
            src = video.get_attribute('src')
            if src:
                downloaded = download_media(src, channel, msg_id)
                if downloaded:
                    videos.append(downloaded)
        
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
                
                for _ in range(3):
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
                
                print(f"   ✅ {new_count} پیام جدید (تعداد عکس‌ها: {total_images})")
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
            
            for vid in msg['videos']:
                f.write(f"▶️ [ویدیو]({vid})\n\n")
            
            f.write("---\n\n")
    
    print(f"\n✅ آرشیو ذخیره شد: {OUTPUT_FILE}")
    print(f"📊 مجموع پیام‌ها: {len(all_messages)}")

if __name__ == "__main__":
    main()
