import os
import re
import io
import uuid
import requests
import numpy as np
import imagehash
from PIL import Image

from src.config import IMAGES_DIR

# --- IMAGE FILTERING HELPERS ---
seen_hashes = {}  # Duplicate detection: {hash: filename}


def is_low_color_variance(img, threshold=5):
    """Check for single-color / blank images"""
    try:
        arr = np.array(img.convert("L"))
        return arr.std() < threshold
    except Exception:
        return False


def is_bad_aspect_ratio(width, height, min_ratio=0.1, max_ratio=10):
    """Check for abnormal aspect ratio (very thin/elongated images)"""
    if height == 0: return True
    ratio = width / height
    return ratio < min_ratio or ratio > max_ratio


def get_html_attr_val(tag, attr):
    """Extract attribute value (px) from an HTML tag"""
    # Normal attribute: width="40"
    m = re.search(rf'{attr}=["\'](\d+)', tag, re.IGNORECASE)
    if m: return int(m.group(1))
    # Style attribute: width: 40px
    m = re.search(rf'{attr}:\s*(\d+)px', tag, re.IGNORECASE)
    if m: return int(m.group(1))
    return None


def is_avatar_tag(full_tag):
    """Determine if an img tag is an avatar or small icon"""
    # 1. border-radius: 50% (Definite avatar)
    if "border-radius" in full_tag and "50%" in full_tag:
        return True
    
    # 2. Small display size (width/height < 100)
    w = get_html_attr_val(full_tag, "width")
    h = get_html_attr_val(full_tag, "height")
    if (w is not None and w < 100) or (h is not None and h < 100):
        return True
        
    return False


def is_overcompressed(file_size, width, height, threshold=0.01):
    """Check for over-compression (low quality/corrupted images)"""
    pixel_count = width * height
    if pixel_count == 0: return True
    return (file_size / pixel_count) < threshold


def is_duplicate(img, target_format):
    """Image similarity check (Hash + Format based)"""
    try:
        img_hash = str(imagehash.phash(img))
        # Was the same image already processed in this format?
        cache_key = f"{img_hash}_{target_format}"
        return seen_hashes.get(cache_key)
    except Exception:
        return None


def register_image_hash(img, filename, target_format):
    """Register image hash with its format"""
    try:
        img_hash = str(imagehash.phash(img))
        cache_key = f"{img_hash}_{target_format}"
        seen_hashes[cache_key] = filename
    except Exception:
        pass


# --- THUMBNAIL PROCESSING FUNCTIONS ---
def extract_meta_tag(content, property_name):
    """Extract specified meta tag value from HTML content (regardless of property or name)"""
    # Search for tags containing property="..." or name="..." in flexible order
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
    """Extract og:image or alternative thumbnail tags from HTML content"""
    for tag in ['og:image', 'twitter:image', 'image', 'thumbnail']:
        url = extract_meta_tag(content, tag)
        if url: return url
    return None


def download_and_convert_thumbnail(img_url, target_format='PNG'):
    """Download image, convert to specified format, save with UUID and return new path"""
    try:
        print(f"⬇️ Downloading image: {img_url} (Target: {target_format})")
        response = requests.get(img_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        
        # Open image with Pillow
        img = Image.open(io.BytesIO(response.content))
        orig_format = img.format # PNG, JPEG, etc.
        
        width, height = img.size
        print(f"📏 Image size: {width}x{height} ({orig_format})")
        
        # --- 1️⃣ SIZE CHECK (100px rule) ---
        if width < 100 or height < 100:
            print(f"⚠️ Image too small ({width}x{height}), skipping.")
            return None, orig_format
            
        # --- 2️⃣ ASPECT RATIO CHECK ---
        if is_bad_aspect_ratio(width, height):
            print(f"⚠️ Abnormal aspect ratio ({width/height:.2f}), skipping.")
            return None, orig_format

        # --- 3️⃣ COLOR VARIANCE CHECK ---
        if is_low_color_variance(img):
            print("⚠️ Single-color / blank image detected, skipping.")
            return None, orig_format

        # --- 4️⃣ OVER-COMPRESSION CHECK ---
        file_size = len(response.content)
        if is_overcompressed(file_size, width, height):
            print("⚠️ Over-compressed / low quality image, skipping.")
            return None, orig_format

        # --- 5️⃣ DUPLICATE CHECK ---
        dup_filename = is_duplicate(img, target_format)
        if dup_filename:
            print(f"♻️ Same image already processed as {target_format} ({dup_filename}), reusing.")
            return dup_filename, orig_format

        # --- 6️⃣ FORMAT CONVERSION ---
        if target_format == 'JPEG':
            # Remove transparency (Alpha) - Add black background
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
        
        # Generate filename with UUID
        filename = f"{uuid.uuid4()}.{ext}"
        file_path = os.path.join(IMAGES_DIR, filename)
        
        # Save
        img.save(file_path, target_format, optimize=True)
        register_image_hash(img, filename, target_format)
        print(f"✅ Image saved: {filename} ({target_format})")
        
        return filename, orig_format
    except Exception as e:
        print(f"❌ Image processing error: {e}")
        return None, None
