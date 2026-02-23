import threading

from src.config import app, ARTICLES_DIR, IMAGES_DIR, DATA_DIR, logger
from src.routes import run_web_server
from src.mail_processor import process_message, check_mail_loop
from src.cleanup import cleanup_old_articles, delete_article_data
from src.instapaper import send_to_instapaper
from src.image_utils import download_and_convert_thumbnail

# Register routes (importing activates Flask decorators)
import src.routes  # noqa: F401

# --- MAIN ENTRY POINT ---
if __name__ == "__main__":
    logger.info("Application starting up...")
    # Initial cleanup
    cleanup_old_articles()
    
    # Start web server in a separate thread
    t1 = threading.Thread(target=run_web_server)
    t1.daemon = True
    t1.start()
    
    # Start mail listener
    check_mail_loop()
