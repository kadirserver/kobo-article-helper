import os
import re
from datetime import timezone, timedelta
from flask import Flask
from dotenv import load_dotenv

# Turkey timezone (UTC+3)
TR_TZ = timezone(timedelta(hours=3))

# --- LOAD SETTINGS FROM .ENV FILE ---
load_dotenv()

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTICLES_DIR = os.path.join(BASE_DIR, "articles")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
DATA_DIR = os.path.join(BASE_DIR, "data")
MAX_ARTICLES = 50  # Maximum number of articles

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

# UUID format regex pattern
UUID_PATTERN = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\.html$')

# Flask application
app = Flask(__name__, 
            template_folder=os.path.join(BASE_DIR, 'templates'),
            static_folder=os.path.join(BASE_DIR, 'static'))
