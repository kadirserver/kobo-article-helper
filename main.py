import threading
from datetime import datetime, timezone, timedelta
import time
import os
import uuid
import re
import io
import json
import requests
import numpy as np
import imagehash
from flask import Flask, send_from_directory, abort, render_template_string
from imap_tools import MailBox, A
from dotenv import load_dotenv
from PIL import Image

# Türkiye saat dilimi (UTC+3)
TR_TZ = timezone(timedelta(hours=3))

# --- .ENV DOSYASINDAN AYARLARI YÜKLE ---
load_dotenv()

# --- YAPILANDIRMA ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(BASE_DIR, "articles")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
DATA_DIR = os.path.join(BASE_DIR, "data")
MAX_ARTICLES = 50  # Maksimum makale sayısı

IMAP_SERVER = os.getenv("IMAP_SERVER")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
INSTAPAPER_USER = os.getenv("INSTAPAPER_USER")
INSTAPAPER_PASS = os.getenv("INSTAPAPER_PASS")
VDS_IP = os.getenv("API")

raw_port = os.getenv("PORT", "5030")
WEB_PORT = int(raw_port) if raw_port and raw_port.strip() else 5030

if not os.path.exists(ARTICLES_DIR):
    os.makedirs(ARTICLES_DIR)
if not os.path.exists(IMAGES_DIR):
    os.makedirs(IMAGES_DIR)
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

app = Flask(__name__)

# --- GÖRSEL FİLTRELEME YARDIMCILARI ---
seen_hashes = {}  # duplicate kontrolü için: {hash: filename}

def is_low_color_variance(img, threshold=5):
    """Tek renk / boş görsel kontrolü"""
    try:
        arr = np.array(img.convert("L"))
        return arr.std() < threshold
    except Exception:
        return False

def is_bad_aspect_ratio(width, height, min_ratio=0.1, max_ratio=10):
    """Anormal en-boy oranı kontrolü (Çok ince/uzun resimler)"""
    if height == 0: return True
    ratio = width / height
    return ratio < min_ratio or ratio > max_ratio

def get_html_attr_val(tag, attr):
    """HTML tag'inden nitelik değerini (px) çeker"""
    # Normal attribute: width="40"
    m = re.search(f'{attr}=["\'](\\d+)', tag, re.IGNORECASE)
    if m: return int(m.group(1))
    # Style içindeki attribute: width: 40px
    m = re.search(f'{attr}:\\s*(\\d+)px', tag, re.IGNORECASE)
    if m: return int(m.group(1))
    return None

def is_avatar_tag(full_tag):
    """Bir img tag'inin avatar veya küçük ikon olup olmadığını belirler"""
    # 1. border-radius: 50% (Kesin avatar)
    if "border-radius" in full_tag and "50%" in full_tag:
        return True
    
    # 2. Küçük Display Boyutu (width/height < 100)
    w = get_html_attr_val(full_tag, "width")
    h = get_html_attr_val(full_tag, "height")
    if (w is not None and w < 100) or (h is not None and h < 100):
        return True
        
    return False

def is_overcompressed(file_size, width, height, threshold=0.01):
    """Aşırı sıkıştırma kontrolü (Kalitesiz/bozuk görseller)"""
    pixel_count = width * height
    if pixel_count == 0: return True
    return (file_size / pixel_count) < threshold

def is_duplicate(img, target_format):
    """Görsel benzerlik kontrolü (Hash + Format tabanlı)"""
    try:
        img_hash = str(imagehash.phash(img))
        # Aynı görsel, aynı formatta daha önce işlendi mi?
        cache_key = f"{img_hash}_{target_format}"
        return seen_hashes.get(cache_key)
    except Exception:
        return None

def register_image_hash(img, filename, target_format):
    """Görsel hash'ini formatıyla birlikte kaydeder"""
    try:
        img_hash = str(imagehash.phash(img))
        cache_key = f"{img_hash}_{target_format}"
        seen_hashes[cache_key] = filename
    except Exception:
        pass

# --- THUMBNAIL İŞLEME FONKSİYONLARI ---
def extract_meta_tag(content, property_name):
    """HTML içeriğinden belirtilen meta tag değerini çıkarır (property veya name fark etmeksizin)"""
    # property="..." veya name="..." içeren tagleri esnek sırada ara
    patterns = [
        r'<meta[^>]+(?:property|name)=["\']' + re.escape(property_name) + r'["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']' + re.escape(property_name) + r'["\']'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            val = match.group(1).strip()
            if val: return val
    return None

def extract_og_image(content):
    """HTML içeriğinden og:image veya alternatif thumbnail taglerini çıkarır"""
    for tag in ['og:image', 'twitter:image', 'image', 'thumbnail']:
        url = extract_meta_tag(content, tag)
        if url: return url
    return None

def download_and_convert_thumbnail(img_url, target_format='PNG'):
    """Görseli indir, belirtilen formatta dönüştür, UUID ile kaydet ve yeni yolu döndür"""
    try:
        print(f"⬇️ Görsel indiriliyor: {img_url} (Hedef: {target_format})")
        response = requests.get(img_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        
        # Görseli Pillow ile aç
        img = Image.open(io.BytesIO(response.content))
        orig_format = img.format # PNG, JPEG, etc.
        
        width, height = img.size
        print(f"📏 Görsel boyutu: {width}x{height} ({orig_format})")
        
        # --- 1️⃣ BOYUT KONTROLÜ (100px kuralı) ---
        if width < 100 or height < 100:
            print(f"⚠️ Görsel çok küçük ({width}x{height}), atlanıyor.")
            return None, orig_format
            
        # --- 2️⃣ ASPECT RATIO KONTROLÜ ---
        if is_bad_aspect_ratio(width, height):
            print(f"⚠️ Anormal en-boy oranı ({width/height:.2f}), atlanıyor.")
            return None, orig_format

        # --- 3️⃣ RENK VARYANSI KONTROLÜ ---
        if is_low_color_variance(img):
            print("⚠️ Tek renk / boş görsel tespit edildi, atlanıyor.")
            return None, orig_format

        # --- 4️⃣ AŞIRI SIKIŞTIRMA KONTROLÜ ---
        file_size = len(response.content)
        if is_overcompressed(file_size, width, height):
            print("⚠️ Aşırı sıkıştırılmış/kalitesiz görsel, atlanıyor.")
            return None, orig_format

        # --- 5️⃣ DUPLICATE KONTROLÜ ---
        dup_filename = is_duplicate(img, target_format)
        if dup_filename:
            print(f"♻️ Aynı görsel {target_format} olarak daha önce işlendi ({dup_filename}), yeniden kullanılıyor.")
            return dup_filename, orig_format

        # --- 6️⃣ FORMAT DÖNÜŞÜMÜ ---
        if target_format == 'JPEG':
            # Şeffaflığı (Alpha) kaldır - Siyah arka plan ekle
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGBA')
                new_img = Image.new("RGB", img.size, (0, 0, 0))
                new_img.paste(img, mask=img.split()[3]) # 3 is alpha channel
                img = new_img
            else:
                img = img.convert('RGB')
            ext = "jpg"
        else: # Default PNG
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGBA')
            else:
                img = img.convert('RGB')
            ext = "png"
        
        # UUID ile dosya adı oluştur
        filename = f"{uuid.uuid4()}.{ext}"
        file_path = os.path.join(IMAGES_DIR, filename)
        
        # Kaydet
        img.save(file_path, target_format, optimize=True)
        register_image_hash(img, filename, target_format)
        print(f"✅ Görsel kaydedildi: {filename} ({target_format})")
        
        return filename, orig_format
    except Exception as e:
        print(f"❌ Görsel işleme hatası: {e}")
        return None, None

# --- TEMİZLİK FONKSİYONLARI ---
def delete_article_data(filename):
    """Bir makaleye ait tüm verileri (HTML, JSON, Resimler) siler"""
    try:
        uuid_name = filename.replace('.html', '')
        json_path = os.path.join(DATA_DIR, f"{uuid_name}.json")
        html_path = os.path.join(ARTICLES_DIR, filename)

        # 1. JSON içindeki resimleri sil
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                mapping = json.load(f)
            
            # og_image sil
            og_local = mapping.get('og_image_local')
            if og_local:
                img_p = os.path.join(IMAGES_DIR, og_local)
                if os.path.exists(img_p): os.remove(img_p)
            
            # body images sil
            body_maps = mapping.get('body_mappings', {})
            for local_img in body_maps.values():
                img_p = os.path.join(IMAGES_DIR, local_img)
                if os.path.exists(img_p): os.remove(img_p)
            
            # JSON dosyasını sil
            os.remove(json_path)
            print(f"🗑️ JSON ve yerel resimler silindi: {uuid_name}.json")

        # 2. HTML dosyasını sil
        if os.path.exists(html_path):
            os.remove(html_path)
            print(f"🗑️ Makale silindi: {filename}")
            
    except Exception as e:
        print(f"⚠️ Silme hatası ({filename}): {e}")

def cleanup_old_articles():
    """50'den fazla article varsa en eskilerini siler"""
    try:
        # Tüm HTML dosyalarını al
        files = [f for f in os.listdir(ARTICLES_DIR) if f.endswith('.html') and UUID_PATTERN.match(f)]
        
        if len(files) > MAX_ARTICLES:
            # Dosyaları oluşturulma tarihine göre sırala (en eski önce)
            files_with_time = []
            for f in files:
                file_path = os.path.join(ARTICLES_DIR, f)
                files_with_time.append((f, os.path.getctime(file_path)))
            
            files_with_time.sort(key=lambda x: x[1])
            
            # Fazla olanları sil
            files_to_delete = len(files) - MAX_ARTICLES
            for i in range(files_to_delete):
                delete_article_data(files_with_time[i][0])
            
            print(f"✅ {files_to_delete} eski makale ve verileri temizlendi.")
    except Exception as e:
        print(f"⚠️ Temizlik hatası: {e}")

# --- WEB SUNUCUSU (FLASK) ---
# UUID formatı için regex pattern
UUID_PATTERN = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\.html$')

@app.route('/read/<filename>')
def serve_article(filename):
    """Oluşturulan HTML dosyasını sunar - güvenlik kontrollü"""
    # Güvenlik: Sadece UUID formatındaki .html dosyalarına izin ver
    if not UUID_PATTERN.match(filename):
        abort(403)  # Geçersiz dosya adı formatı
    
    # Güvenlik: Path traversal engelle
    if '..' in filename or '/' in filename or '\\' in filename:
        abort(403)
    
    # Güvenlik: Whitelist kontrolü - mevcut dosyaları al ve karşılaştır
    try:
        existing_articles = set(os.listdir(ARTICLES_DIR))
        if filename not in existing_articles:
            abort(404)  # Dosya bulunamadı
    except Exception:
        abort(500)
    
    # Dosyanın gerçekten articles klasöründe olduğunu doğrula
    file_path = os.path.join(ARTICLES_DIR, filename)
    if not os.path.abspath(file_path).startswith(os.path.abspath(ARTICLES_DIR)):
        abort(403)
    
    # --- DOSYAYI OKU VE DÜZENLE ---
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        uuid_name = filename.replace('.html', '')
        json_path = os.path.join(DATA_DIR, f"{uuid_name}.json")
        mapping = {}
        
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    mapping = json.load(f)
            except Exception:
                pass

        # --- PUBLIC LINK INJECTION ---
        public_url = f"{VDS_IP}/read/{filename}"

        # --- EMOJI DETECTION AND WRAPPING ---
        # Emoji regex (Common ranges)
        emoji_pattern = re.compile(
            "["
            "\U0001f600-\U0001f64f"  # emoticons
            "\U0001f300-\U0001f5ff"  # symbols & pictographs
            "\U0001f680-\U0001f6ff"  # transport & map symbols
            "\U0001f1e0-\U0001f1ff"  # flags (iOS)
            "\U00002702-\U000027b0"  # dingbats
            "\U000024c2-\U0001f251"
            "]+", flags=re.UNICODE
        )
        # content = emoji_pattern.sub(lambda m: f'<span class="emoji">{m.group(0)}</span>', content)
        # print("💡 Emoji'ler tespit edildi ve işaretlendi.")

        # --- DIV TO P REPLACEMENT (E-Reader Compatibility) ---
        content = content.replace('<div', '<p').replace('</div', '</p')
        print("📝 DIV etiketleri P ile değiştirildi.")

        # --- DİNAMİK OG METADATA ENJEKSİYONU (JSON ÜZERİNDEN) ---
        if mapping:
            og_tags = []
            
            # 1. og:image
            og_local = mapping.get('og_image_local')
            if og_local:
                local_img_url = f"{VDS_IP}/images/{og_local}"
                og_tags.append(f'<meta property="og:image" content="{local_img_url}">')
                og_tags.append(f'<meta name="twitter:image" content="{local_img_url}">')
                print(f"🔗 og:image enjekte edildi: {og_local}")

            # 2. Diğer Meta Veriler
            for key in ['og:title', 'og:description', 'og:type', 'og:url']:
                val = mapping.get(key.replace(':', '_'))
                if val:
                    og_tags.append(f'<meta property="{key}" content="{val}">')
            
            if og_tags:
                og_html = "\n".join(og_tags)
                # Mevcut og/twitter tag'lerini temizleyelim ki çakışmasın (Opsiyonel ama temizlik iyidir)
                content = re.sub(r'<meta[^>]+property=["\']og:[^>]+>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'<meta[^>]+name=["\']twitter:[^>]+>', '', content, flags=re.IGNORECASE)
                
                # Enjeksiyon
                if '<head>' in content:
                    content = content.replace('<head>', f'<head>{og_html}', 1)
                elif '<html>' in content:
                    content = content.replace('<html>', f'<html><head>{og_html}</head>', 1)
                else:
                    content = f'{og_html}' + content

            # 3. Body Image Değişimi (Mevcut mantık)
            body_maps = mapping.get('body_mappings', {})
            for original_url, local_name in body_maps.items():
                local_url = f"{VDS_IP}/images/{local_name}"
                content = content.replace(f'src="{original_url}"', f'src="{local_url}"')
                content = content.replace(f"src='{original_url}'", f"src='{local_url}'")
            if body_maps:
                print(f"🔄 {len(body_maps)} gövde resmi yerel link ile değiştirildi.")

        # --- FOOTER/HEADER LINK INJECTION ---

        # Mail tarihini JSON'dan oku
        date_str = mapping.get('mail_date', '')
        
        header_html = f'''
        <p style="
            font-style: italic;
            color: #666;
            margin: 10px 0;
            font-size: 0.9em;
        ">
            <a href="{public_url}" target="_blank" style="color: #0066cc; text-decoration: underline;">Makaleyi web sitesinde görüntüle</a>
            <span style="margin-left: 8px; color: #999; font-size: 0.85em;">({date_str})</span>
        </p>
        '''

        # Enjeksiyon Mantığı:
        # 1. İlk </h1> etiketini bul ve sonrasına ekle
        # 2. H1 yoksa, ilk <p> etiketini bul ve öncesine ekle
        # 3. Hiçbiri yoksa body başına ekle

        if '</h2>' in content:
            # </h2> sonrasına ekle (case insensitive için re kullanıyoruz)
            # Not: H2 replace edilmişti, ama </h2> etiketi hala orada duruyor.
            content = re.sub(r'(</h2>)', r'\1' + header_html, content, count=1, flags=re.IGNORECASE)
            print("🔗 Link H2 sonrasına eklendi.")
        elif '<p' in content:
            # <p öncesine ekle
            content = re.sub(r'(<p)', header_html + r'\1', content, count=1, flags=re.IGNORECASE)
            print("🔗 Link ilk P öncesine eklendi.")
        elif '<body>' in content:
            content = content.replace('<body>', f'<body>{header_html}', 1)
        else:
            content = header_html + content

        return content

    except Exception as e:
        print(f"Okuma hatası: {e}")
        abort(500)

@app.route('/images/<filename>')
def serve_image(filename):
    """Kaydedilen resimleri sunar"""
    return send_from_directory(IMAGES_DIR, filename)

def get_folder_stats(folder_path):
    """Klasör istatistiklerini hesaplar: dosya sayısı ve toplam boyut"""
    total_size = 0
    file_count = 0
    try:
        for f in os.listdir(folder_path):
            fp = os.path.join(folder_path, f)
            if os.path.isfile(fp):
                total_size += os.path.getsize(fp)
                file_count += 1
    except Exception:
        pass
    return file_count, total_size

def format_size(size_bytes):
    """Byte değerini okunabilir formata çevirir"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

@app.route('/')
def index():
    """Makaleleri listeleyen ana sayfa - Dark Mode"""
    try:
        # Makaleleri al ve oluşturulma tarihine göre sırala (en yeni üstte)
        files = []
        for f in os.listdir(ARTICLES_DIR):
            if f.endswith('.html') and UUID_PATTERN.match(f):
                path = os.path.join(ARTICLES_DIR, f)
                files.append({
                    'name': f,
                    'time': os.path.getctime(path),
                    'display_name': f  # İleride subject çekilebilir ama şimdilik ID
                })
        
        files.sort(key=lambda x: x['time'], reverse=True)

        # Klasör istatistikleri
        articles_count, articles_size = get_folder_stats(ARTICLES_DIR)
        data_count, data_size = get_folder_stats(DATA_DIR)
        images_count, images_size = get_folder_stats(IMAGES_DIR)
        total_articles = len(files)

        html_template = """
        <!DOCTYPE html>
        <html lang="tr">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Makale Paneli | Kobo Article Helper</title>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet">
            <style>
                :root {
                    --bg: #0f172a;
                    --card-bg: #1e293b;
                    --text: #f1f5f9;
                    --primary: #38bdf8;
                    --accent: #0ea5e9;
                }
                body {
                    background-color: var(--bg);
                    color: var(--text);
                    font-family: 'Inter', sans-serif;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    min-height: 100vh;
                    margin: 0;
                    padding: 40px 20px;
                }
                .container {
                    width: 100%;
                    max-width: 600px;
                }
                h1 {
                    font-weight: 600;
                    margin-bottom: 2rem;
                    color: var(--primary);
                    text-align: center;
                }

                /* Info Panel */
                .info-panel {
                    background: var(--card-bg);
                    border: 1px solid #334155;
                    border-radius: 12px;
                    margin-bottom: 24px;
                    overflow: hidden;
                }
                .info-toggle {
                    width: 100%;
                    background: none;
                    border: none;
                    color: var(--text);
                    font-family: 'Inter', sans-serif;
                    font-size: 0.95rem;
                    font-weight: 600;
                    padding: 16px 20px;
                    cursor: pointer;
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    transition: background 0.2s;
                }
                .info-toggle:hover {
                    background: #334155;
                }
                .info-toggle .arrow {
                    transition: transform 0.3s;
                    font-size: 0.8rem;
                    color: #64748b;
                }
                .info-toggle.open .arrow {
                    transform: rotate(180deg);
                }
                .info-content {
                    max-height: 0;
                    overflow: hidden;
                    transition: max-height 0.3s ease;
                }
                .info-content.open {
                    max-height: 400px;
                }
                .info-grid {
                    display: grid;
                    grid-template-columns: 1fr 1fr 1fr;
                    gap: 12px;
                    padding: 0 20px 20px;
                }
                .info-card {
                    background: #0f172a;
                    border-radius: 10px;
                    padding: 14px;
                    text-align: center;
                    border: 1px solid #1e3a5f;
                }
                .info-card .icon {
                    font-size: 1.4rem;
                    margin-bottom: 6px;
                }
                .info-card .label {
                    font-size: 0.7rem;
                    color: #94a3b8;
                    text-transform: uppercase;
                    letter-spacing: 0.5px;
                    margin-bottom: 4px;
                }
                .info-card .value {
                    font-size: 1.1rem;
                    font-weight: 600;
                    color: var(--primary);
                }
                .info-card .sub {
                    font-size: 0.75rem;
                    color: #64748b;
                    margin-top: 2px;
                }
                .total-badge {
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 8px;
                    padding: 12px 20px;
                    border-bottom: 1px solid #334155;
                }
                .total-badge .count {
                    font-size: 1.5rem;
                    font-weight: 600;
                    color: var(--primary);
                }
                .total-badge .text {
                    font-size: 0.85rem;
                    color: #94a3b8;
                }

                .article-list {
                    list-style: none;
                    padding: 0;
                }
                .article-item {
                    background: var(--card-bg);
                    margin-bottom: 12px;
                    border-radius: 12px;
                    transition: transform 0.2s, background 0.2s;
                    border: 1px solid #334155;
                }
                .article-item:hover {
                    transform: translateY(-2px);
                    background: #334155;
                    border-color: var(--primary);
                }
                .article-link {
                    display: block;
                    padding: 20px;
                    color: var(--text);
                    text-decoration: none;
                    font-size: 1.1rem;
                    text-align: center;
                }
                .empty-state {
                    text-align: center;
                    padding: 40px;
                    color: #94a3b8;
                    border: 2px dashed #334155;
                    border-radius: 16px;
                }
                .footer {
                    margin-top: auto;
                    padding: 20px;
                    color: #64748b;
                    font-size: 0.9rem;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📚 Makalelerim</h1>

                <!-- Info Panel -->
                <div class="info-panel">
                    <button class="info-toggle" onclick="toggleInfo()" id="infoToggle">
                        <span>ℹ️ Sistem Bilgisi</span>
                        <span class="arrow">▼</span>
                    </button>
                    <div class="info-content" id="infoContent">
                        <div class="total-badge">
                            <span class="count">{{ total_articles }}</span>
                            <span class="text">Toplam Makale</span>
                        </div>
                        <div class="info-grid">
                            <div class="info-card">
                                <div class="icon">📄</div>
                                <div class="label">Articles</div>
                                <div class="value">{{ articles_count }}</div>
                                <div class="sub">{{ articles_size }}</div>
                            </div>
                            <div class="info-card">
                                <div class="icon">📊</div>
                                <div class="label">Data</div>
                                <div class="value">{{ data_count }}</div>
                                <div class="sub">{{ data_size }}</div>
                            </div>
                            <div class="info-card">
                                <div class="icon">🖼️</div>
                                <div class="label">Images</div>
                                <div class="value">{{ images_count }}</div>
                                <div class="sub">{{ images_size }}</div>
                            </div>
                        </div>
                    </div>
                </div>

                <ul class="article-list">
                    {% for file in files %}
                    <li class="article-item">
                        <a href="/read/{{ file.name }}" class="article-link">
                            📄 Makale: {{ file.name[:8] }}...
                        </a>
                    </li>
                    {% endfor %}
                    {% if not files %}
                    <li class="empty-state">Henüz makale bulunmuyor.</li>
                    {% endif %}
                </ul>
            </div>
            <div class="footer">Kobo Article Helper v1.0</div>
            <script>
                function toggleInfo() {
                    const btn = document.getElementById('infoToggle');
                    const content = document.getElementById('infoContent');
                    btn.classList.toggle('open');
                    content.classList.toggle('open');
                }
            </script>
        </body>
        </html>
        """
        return render_template_string(html_template,
            files=files,
            total_articles=total_articles,
            articles_count=articles_count,
            articles_size=format_size(articles_size),
            data_count=data_count,
            data_size=format_size(data_size),
            images_count=images_count,
            images_size=format_size(images_size)
        )
    except Exception as e:
        return f"Error: {e}", 500

def run_web_server():
    print(f"🌍 Web sunucusu başlatıldı: {VDS_IP}:{WEB_PORT}")
    app.run(host='0.0.0.0', port=WEB_PORT, debug=False, use_reloader=False)

# --- INSTAPAPER API ---
def send_to_instapaper(url, title):
    """Instapaper Simple API kullanarak linki ekler"""
    api_url = "https://www.instapaper.com/api/add"
    
    payload = {
        'username': INSTAPAPER_USER,
        'password': INSTAPAPER_PASS,
        'url': url,
        'title': title
    }
    
    print(f"🚀 API İsteği gönderiliyor: {url}")
    try:
        response = requests.get(api_url, params=payload)
        
        if response.status_code == 201:
            print(f"✅ BAŞARILI! Instapaper kabul etti: {title}")
            return True
        elif response.status_code == 403:
            print("❌ HATA: Şifre yanlış veya IP engelli.")
        elif response.status_code == 400:
            print("❌ HATA: Instapaper linke ulaşamadı (Port kapalı olabilir).")
        else:
            print(f"❌ HATA Kodu: {response.status_code} - {response.text}")
            
    except Exception as e:
        print(f"❌ Bağlantı hatası: {e}")
    return False

def process_message(msg):
    """Gelen mail nesnesini işler, resimleri EAGER şekilde hazırlar ve JSON mapping oluşturur"""
    # Önce eski makaleleri temizle
    cleanup_old_articles()

    # Duplicate kontrolünü bu makale özelinde sıfırla (cross-article silme hatalarını önlemek için)
    seen_hashes.clear()
    
    # 1. Dosya adını ve yolları belirle
    uuid_name = str(uuid.uuid4())
    html_file = f"{uuid_name}.html"
    json_file = f"{uuid_name}.json"
    
    html_path = os.path.join(ARTICLES_DIR, html_file)
    json_path = os.path.join(DATA_DIR, json_file)

    # 2. İçeriği al (Orijinal haliyle)
    content = msg.html if msg.html else f"<div>{msg.text}</div>"
    
    # 3. Görsel ve Metadata İşleme (EAGER)
    # Mail tarihini al (Türkiye saati UTC+3)
    mail_date_str = ""
    if hasattr(msg, 'date') and msg.date:
        # Mail tarihi UTC ise Türkiye saatine çevir
        mail_date = msg.date
        if mail_date.tzinfo is None:
            # Naive datetime ise UTC kabul et ve TR'ye çevir
            mail_date = mail_date.replace(tzinfo=timezone.utc).astimezone(TR_TZ)
        else:
            # Timezone-aware ise direkt TR'ye çevir
            mail_date = mail_date.astimezone(TR_TZ)
        mail_date_str = mail_date.strftime("%d.%m.%Y %H:%M")
    else:
        mail_date_str = datetime.now(TR_TZ).strftime("%d.%m.%Y %H:%M")

    mapping = {
        "og_image_local": None,
        "og_title": extract_meta_tag(content, 'og:title'),
        "og_description": extract_meta_tag(content, 'og:description'),
        "og_type": extract_meta_tag(content, 'og:type') or 'article',
        "og_url": extract_meta_tag(content, 'og:url'),
        "mail_date": mail_date_str,
        "body_mappings": {}
    }

    # --- 🟢 ÖN TARAMA: Avatar ve Küçük İkonları Tespit Et ---
    # Body içindeki tüm img taglerini bul ve hangilerinin avatar olduğunu belirle
    # Bu URL'ler asla Thumbnail seçilmeyecek.
    avatar_url_blacklist = set()
    all_img_matches = re.finditer(r'<img[^>]+src=["\'](http[^"\']+)["\'][^>]*>', content, re.IGNORECASE)
    for match in all_img_matches:
        full_tag = match.group(0)
        img_url = match.group(1)
        if is_avatar_tag(full_tag):
            avatar_url_blacklist.add(img_url)
    
    # Subject eğer title yoksa title olarak kullanılsın
    if not mapping["og_title"]:
        mapping["og_title"] = msg.subject

    # 3.1. Görsel indirme önbelleği (Aynı resmin tekrar indirilmemesi için)
    download_cache = {}

    def get_thumb(url, fmt='PNG'):
        cache_key = f"{url}_{fmt}"
        if cache_key not in download_cache:
            download_cache[cache_key] = download_and_convert_thumbnail(url, target_format=fmt)
        return download_cache[cache_key]

    # 3.2. og:image tespiti
    og_url = extract_og_image(content)
    
    # A. og:image tespiti ve PNG olarak işle
    if og_url:
        if og_url in avatar_url_blacklist:
            print(f"🚫 og:image pas geçiliyor (Avatar kara listesinde): {og_url}")
        else:
            print(f"📸 og:image tespit edildi, PNG olarak işleniyor: {og_url}")
            mapping["og_image_local"], _ = get_thumb(og_url, 'PNG')

    # B. og:image yoksa Fallback (Body'den seç)
    if not mapping["og_image_local"]:
        print("⚠️ Meta image yok veya indirilemedi, body'den görsel aranıyor...")
        img_tags = re.findall(r'<img[^>]+src=["\'](http[^"\']+)["\'][^>]*>', content, re.IGNORECASE)
        
        # En iyi kapak resmini bul (Kara listede olmayan ilk resim)
        for img_url in img_tags:
            if img_url in avatar_url_blacklist:
                continue # Avatar olanları kapak yapma
                
            saved_png, _ = get_thumb(img_url, 'PNG')
            if saved_png:
                mapping["og_image_local"] = saved_png
                print(f"🖼️ Fallback thumbnail seçildi: {saved_png}")
                break

    # 3.3. Tüm Body resimlerini işle ve PNG temizliği yap
    def body_img_processor(match):
        full_tag = match.group(0)
        img_url = match.group(1)
        
        # --- 🟢 DISPLAY-SIZE VE AVATAR KONTROLÜ (HTML Attribute tabanlı) ---
        if is_avatar_tag(full_tag):
            print(f"🗑️ Avatar/İkon tespit edildi, uçuruluyor: {img_url}")
            return ""

        # --- 🔵 DOSYA BOYUTU VE FORMAT KONTROLÜ (İndirme/İşleme tabanlı) ---
        # JPEG olarak indir/dönüştür
        saved_jpg, orig_fmt = get_thumb(img_url, 'JPEG')
        
        if not saved_jpg:
            # Standarta uymuyor (100px altı) veya indirme/açma hatası
            if orig_fmt:
                print(f"🗑️ Küçük görsel ({orig_fmt}) uçuruluyor: {img_url}")
                return "" # Format fark etmeksizin küçük resimleri HTML'den kaldır
            return full_tag # İndirme hatası ise orijinal link kalsın (belki geçicidir)
            
        mapping["body_mappings"][img_url] = saved_jpg
        return full_tag

    # İçeriği güncelle (re.sub ile hem mapping dolduruyoruz hem de küçük PNG'leri siliyoruz)
    # Not: re.sub callback'i sırayla çalışır, mapping["body_mappings"] dolmuş olur.
    content = re.sub(r'<img[^>]+src=["\'](http[^"\']+)["\'][^>]*>', body_img_processor, content, flags=re.IGNORECASE)

    # 4. Dosyaları Kaydet (HTML orijinal, Mapping JSON)
    try:
        # HTML Kaydet
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        # JSON Kaydet
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=4)
            
        print(f"✅ Makale ve Mapping kaydedildi: {uuid_name}")
    except Exception as e:
        print(f"❌ Kayıt hatası: {e}")

    # 5. Instapaper'a gönder
    public_link = f"{VDS_IP}/read/{html_file}"
    send_to_instapaper(public_link, msg.subject)

# --- MAIL DİNLEYİCİ (IMAP) ---
def check_mail_loop():
    print(f"📧 Dinleme aktif. {VDS_IP}:{WEB_PORT} üzerinden yayın yapılıyor...")
    while True:
        try:
            with MailBox(IMAP_SERVER).login(EMAIL_USER, EMAIL_PASS) as mailbox:
                for msg in mailbox.fetch(A(seen=False), mark_seen=True):
                    print(f"📩 Yeni Mail: {msg.subject}")
                    
                    # Dosyayı oluştur
                    process_message(msg)
                    
                    
        except Exception as e:
            print(f"⚠️ Mail kontrol hatası: {e}")
        
        print("🔍 Mail kontrolü tamamlandı, yeni mail yok. 60 saniye içinde tekrar kontrol edilecek.")
        time.sleep(60)

# --- ANA ÇALIŞTIRMA ---
if __name__ == "__main__":
    # Başlangıçta temizlik yap
    cleanup_old_articles()
    
    # Web sunucusunu ayrı thread'de başlat
    t1 = threading.Thread(target=run_web_server)
    t1.daemon = True
    t1.start()
    
    # Mail dinleyicisini başlat
    check_mail_loop()
