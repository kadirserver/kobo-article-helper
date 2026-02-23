import os
import json

from src.config import ARTICLES_DIR, DATA_DIR, IMAGES_DIR, MAX_ARTICLES, UUID_PATTERN, logger


# --- CLEANUP FUNCTIONS ---
def delete_article_data(filename):
    """Delete all data associated with an article (HTML, JSON, Images)"""
    try:
        uuid_name = filename.replace('.html', '')
        json_path = os.path.join(DATA_DIR, f"{uuid_name}.json")
        html_path = os.path.join(ARTICLES_DIR, filename)

        # 1. Delete images referenced in JSON
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                mapping = json.load(f)
            
            # Delete og_image
            og_local = mapping.get('og_image_local')
            if og_local:
                img_p = os.path.join(IMAGES_DIR, og_local)
                if os.path.exists(img_p): os.remove(img_p)
            
            # Delete body images
            body_maps = mapping.get('body_mappings', {})
            for local_img in body_maps.values():
                img_p = os.path.join(IMAGES_DIR, local_img)
                if os.path.exists(img_p): os.remove(img_p)
            
            # Delete JSON file
            os.remove(json_path)
            logger.info(f"JSON and local images deleted: {uuid_name}.json")

        # 2. Delete HTML file
        if os.path.exists(html_path):
            os.remove(html_path)
            logger.info(f"Article deleted: {filename}")
            
    except Exception as e:
        logger.error(f"Deletion error ({filename}): {e}")


def cleanup_old_articles():
    """Delete oldest articles when count exceeds limit"""
    try:
        # Get all HTML files
        files = [f for f in os.listdir(ARTICLES_DIR) if f.endswith('.html') and UUID_PATTERN.match(f)]
        
        if len(files) > MAX_ARTICLES:
            # Sort files by creation time (oldest first)
            files_with_time = []
            for f in files:
                file_path = os.path.join(ARTICLES_DIR, f)
                files_with_time.append((f, os.path.getctime(file_path)))
            
            files_with_time.sort(key=lambda x: x[1])
            
            # Delete excess articles
            files_to_delete = len(files) - MAX_ARTICLES
            for i in range(files_to_delete):
                delete_article_data(files_with_time[i][0])
            
            logger.info(f"{files_to_delete} old article(s) and their data cleaned up.")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
