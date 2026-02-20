import requests

from src.config import INSTAPAPER_USER, INSTAPAPER_PASS


# --- INSTAPAPER API ---
def send_to_instapaper(url, title):
    """Add a link using the Instapaper Simple API"""
    api_url = "https://www.instapaper.com/api/add"
    
    payload = {
        'username': INSTAPAPER_USER,
        'password': INSTAPAPER_PASS,
        'url': url,
        'title': title
    }
    
    print(f"🚀 Sending API request: {url}")
    try:
        response = requests.get(api_url, params=payload)
        
        if response.status_code == 201:
            print(f"✅ SUCCESS! Instapaper accepted: {title}")
            return True
        elif response.status_code == 403:
            print("❌ ERROR: Wrong password or IP blocked.")
        elif response.status_code == 400:
            print("❌ ERROR: Instapaper could not reach the link (port may be closed).")
        else:
            print(f"❌ ERROR Code: {response.status_code} - {response.text}")
            
    except Exception as e:
        print(f"❌ Connection error: {e}")
    return False
