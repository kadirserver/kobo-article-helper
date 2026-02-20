from datetime import datetime
import locale

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
