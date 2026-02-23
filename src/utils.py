from datetime import datetime
import locale
import re

def format_turkish_date(dt):
    """
    Format a datetime object into the requested Turkish format:
    '1 ocak 2024 Salı Saat 22.15'
    """
    if not dt:
        return ""
        
    # Turkish month and day names
    months = [
        "", "ocak", "şubat", "mart", "nisan", "mayıs", "haziran",
        "temmuz", "ağustos", "eylül", "ekim", "kasım", "aralık"
    ]
    days = [
        "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"
    ]
    
    day_num = dt.day
    month_name = months[dt.month]
    year = dt.year
    day_name = days[dt.weekday()]
    time_str = dt.strftime("%H.%M")
    
    return f"{day_num} {month_name} {year} {day_name} Saat {time_str}"

def extract_snippet_from_html(html_content, max_length=150):
    """
    Extracts a plain text snippet from the first <p> tag in the HTML.
    If the text exceeds max_length, it is truncated with an ellipsis.
    """
    if not html_content:
        return ""
    
    # Find the first <p>...</p> block
    match = re.search(r'<p[^>]*>(.*?)</p>', html_content, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
        
    # Strip inner HTML tags from the paragraph
    raw_text = match.group(1)
    clean_text = re.sub(r'<[^>]+>', '', raw_text)
    
    # Clean up whitespace and newlines
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    
    # Truncate if necessary
    if len(clean_text) > max_length:
        return clean_text[:max_length].strip() + "..."
    
    return clean_text
