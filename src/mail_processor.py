import os
import re
import uuid
import json
import time
from datetime import datetime, timezone

from imap_tools import MailBox, A

from src.config import (
    ARTICLES_DIR, DATA_DIR, IMAP_SERVER, EMAIL_USER, EMAIL_PASS,
    VDS_IP, WEB_PORT, TR_TZ
)
from src.image_utils import (
    seen_hashes, extract_meta_tag, extract_og_image,
    download_and_convert_thumbnail, is_avatar_tag
)
from src.cleanup import cleanup_old_articles
from src.instapaper import send_to_instapaper
from src.utils import format_turkish_date


def process_message(msg):
    """Process incoming mail message, eagerly prepare images and create JSON mapping"""
    # First clean up old articles
    cleanup_old_articles()

    # Reset duplicate check for this article (prevents cross-article deletion errors)
    seen_hashes.clear()
    
    # 1. Determine filename and paths
    uuid_name = str(uuid.uuid4())
    html_file = f"{uuid_name}.html"
    json_file = f"{uuid_name}.json"
    
    html_path = os.path.join(ARTICLES_DIR, html_file)
    json_path = os.path.join(DATA_DIR, json_file)

    # 2. Get content (in original form)
    content = msg.html if msg.html else f"<div>{msg.text}</div>"
    
    # 3. Image and Metadata Processing (EAGER)
    # Get mail date (Turkey time UTC+3)
    mail_date_str = ""
    if hasattr(msg, 'date') and msg.date:
        # Convert mail date to Turkey time
        mail_date = msg.date
        if mail_date.tzinfo is None:
            # If naive datetime, assume UTC and convert to TR
            mail_date = mail_date.replace(tzinfo=timezone.utc).astimezone(TR_TZ)
        else:
            # If timezone-aware, convert directly to TR
            mail_date = mail_date.astimezone(TR_TZ)
        
        # FIX: Check for year 1900 (common fallback or parsing error)
        if mail_date.year <= 1900:
            print(f"⚠️ Invalid date detected ({mail_date}), falling back to current time.")
            mail_date = datetime.now(TR_TZ)
            
        mail_date_str = format_turkish_date(mail_date)
    else:
        mail_date_str = format_turkish_date(datetime.now(TR_TZ))

    mapping = {
        "og_image_local": None,
        "og_title": extract_meta_tag(content, 'og:title'),
        "og_description": extract_meta_tag(content, 'og:description'),
        "og_type": extract_meta_tag(content, 'og:type') or 'article',
        "og_url": extract_meta_tag(content, 'og:url'),
        "mail_date": mail_date_str,
        "body_mappings": {}
    }

    # --- 🟢 PRE-SCAN: Detect Avatars and Small Icons ---
    # Find all img tags in body and determine which ones are avatars
    # These URLs will never be selected as thumbnails.
    avatar_url_blacklist = set()
    all_img_matches = re.finditer(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>', content, re.IGNORECASE)
    for match in all_img_matches:
        full_tag = match.group(0)
        img_url = match.group(1)
        if is_avatar_tag(full_tag):
            avatar_url_blacklist.add(img_url)
    
    # Use subject as title if no og:title found
    if not mapping["og_title"]:
        mapping["og_title"] = msg.subject

    # 3.1. Image download cache (prevents re-downloading the same image)
    download_cache = {}

    def get_thumb(url, fmt='PNG'):
        cache_key = f"{url}_{fmt}"
        if cache_key not in download_cache:
            download_cache[cache_key] = download_and_convert_thumbnail(url, target_format=fmt)
        return download_cache[cache_key]

    # 3.2. og:image detection
    og_url = extract_og_image(content)
    
    # A. Detect og:image and process as PNG
    if og_url:
        if og_url in avatar_url_blacklist:
            print(f"🚫 Skipping og:image (in avatar blacklist): {og_url}")
        else:
            print(f"📸 og:image detected, processing as PNG: {og_url}")
            mapping["og_image_local"], _ = get_thumb(og_url, 'PNG')

    # B. If no og:image, fallback (select from body)
    if not mapping["og_image_local"]:
        print("⚠️ No meta image or download failed, searching body for images...")
        img_tags = re.findall(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>', content, re.IGNORECASE)
        
        # Find best cover image (first image not in blacklist)
        for img_url in img_tags:
            if img_url in avatar_url_blacklist:
                continue # Don't use avatars as cover
                
            saved_png, _ = get_thumb(img_url, 'PNG')
            if saved_png:
                mapping["og_image_local"] = saved_png
                print(f"🖼️ Fallback thumbnail selected: {saved_png}")
                break

    # 3.3. Process all body images and clean up PNGs
    def body_img_processor(match):
        full_tag = match.group(0)
        img_url = match.group(1)
        
        # --- 🟢 DISPLAY-SIZE AND AVATAR CHECK (HTML Attribute based) ---
        if is_avatar_tag(full_tag):
            print(f"🗑️ Avatar/Icon detected, removing: {img_url}")
            return ""

        # --- 🔵 FILE SIZE AND FORMAT CHECK (Download/Processing based) ---
        # Download/convert as JPEG
        saved_jpg, orig_fmt = get_thumb(img_url, 'JPEG')
        
        if not saved_jpg:
            # Does not meet standards (below 100px) or download/open error
            if orig_fmt:
                print(f"🗑️ Small image ({orig_fmt}) removed: {img_url}")
                return "" # Remove small images from HTML regardless of format
            return full_tag # If download error, keep original link (may be temporary)
            
        mapping["body_mappings"][img_url] = saved_jpg
        return full_tag

    # Update content (re.sub populates mapping and removes small PNGs)
    # Note: re.sub callback runs sequentially, mapping["body_mappings"] will be populated.
    content = re.sub(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>', body_img_processor, content, flags=re.IGNORECASE)

    # 4. Save Files (HTML original, Mapping JSON)
    try:
        # Save HTML
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        # Save JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=4)
            
        print(f"✅ Article and mapping saved: {uuid_name}")
    except Exception as e:
        print(f"❌ Save error: {e}")

    # 5. Send to Instapaper
    public_link = f"{VDS_IP}/read/{html_file}"
    send_to_instapaper(public_link, msg.subject)


# --- MAIL LISTENER (IMAP) ---
def check_mail_loop():
    print(f"📧 Listening active. Broadcasting on {VDS_IP}:{WEB_PORT}...")
    while True:
        try:
            with MailBox(IMAP_SERVER).login(EMAIL_USER, EMAIL_PASS) as mailbox:
                for msg in mailbox.fetch(A(seen=False), mark_seen=True):
                    print(f"📩 New Mail: {msg.subject}")
                    
                    # Process the message
                    process_message(msg)
                    
                    
        except Exception as e:
            print(f"⚠️ Mail check error: {e}")
        
        print("🔍 Mail check complete, no new mail. Rechecking in 60 seconds.")
        time.sleep(60)
