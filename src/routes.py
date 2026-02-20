import os
import re
import json

from flask import send_from_directory, abort, render_template_string

from src.config import app, ARTICLES_DIR, IMAGES_DIR, DATA_DIR, UUID_PATTERN, VDS_IP, WEB_PORT


# --- WEB SERVER (FLASK) ---

@app.route('/read/<filename>')
def serve_article(filename):
    """Serve the generated HTML file - with security checks"""
    # Security: Only allow UUID-formatted .html files
    if not UUID_PATTERN.match(filename):
        abort(403)  # Invalid filename format
    
    # Security: Prevent path traversal
    if '..' in filename or '/' in filename or '\\' in filename:
        abort(403)
    
    # Security: Whitelist check - get existing files and compare
    try:
        existing_articles = set(os.listdir(ARTICLES_DIR))
        if filename not in existing_articles:
            abort(404)  # File not found
    except Exception:
        abort(500)
    
    # Verify file is actually in the articles directory
    file_path = os.path.join(ARTICLES_DIR, filename)
    if not os.path.abspath(file_path).startswith(os.path.abspath(ARTICLES_DIR)):
        abort(403)
    
    # --- READ AND MODIFY FILE ---
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
        # print("💡 Emojis detected and marked.")

        # --- DIV TO P REPLACEMENT (E-Reader Compatibility) ---
        content = content.replace('<div', '<p').replace('</div', '</p')
        print("📝 DIV tags replaced with P tags.")

        # --- DYNAMIC OG METADATA INJECTION (VIA JSON) ---
        if mapping:
            og_tags = []
            
            # 1. og:image
            og_local = mapping.get('og_image_local')
            if og_local:
                local_img_url = f"{VDS_IP}/images/{og_local}"
                og_tags.append(f'<meta property="og:image" content="{local_img_url}">')
                og_tags.append(f'<meta name="twitter:image" content="{local_img_url}">')
                print(f"🔗 og:image injected: {og_local}")

            # 2. Other Metadata
            for key in ['og:title', 'og:description', 'og:type', 'og:url']:
                val = mapping.get(key.replace(':', '_'))
                if val:
                    og_tags.append(f'<meta property="{key}" content="{val}">')
            
            if og_tags:
                og_html = "\n".join(og_tags)
                # Clean existing og/twitter tags to prevent conflicts
                content = re.sub(r'<meta[^>]+property=["\']og:[^>]+>', '', content, flags=re.IGNORECASE)
                content = re.sub(r'<meta[^>]+name=["\']twitter:[^>]+>', '', content, flags=re.IGNORECASE)
                
                # Injection
                if '<head>' in content:
                    content = content.replace('<head>', f'<head>{og_html}', 1)
                elif '<html>' in content:
                    content = content.replace('<html>', f'<html><head>{og_html}</head>', 1)
                else:
                    content = f'{og_html}' + content

            # 3. Body Image Replacement (Existing logic)
            body_maps = mapping.get('body_mappings', {})
            for original_url, local_name in body_maps.items():
                local_url = f"{VDS_IP}/images/{local_name}"
                content = content.replace(f'src="{original_url}"', f'src="{local_url}"')
                content = content.replace(f"src='{original_url}'", f"src='{local_url}'")
            if body_maps:
                print(f"🔄 {len(body_maps)} body image(s) replaced with local links.")

        # --- FOOTER/HEADER LINK INJECTION ---

        # Read mail date from JSON
        date_str = mapping.get('mail_date', '')
        
        header_html = f'''
        <p style="
            font-style: italic;
            color: #666;
            margin: 10px 0;
            font-size: 0.9em;
        ">
            <a href="{public_url}" target="_blank" style="color: #0066cc; text-decoration: underline;">View article on website</a>
            <span style="margin-left: 8px; color: #999; font-size: 0.85em;">({date_str})</span>
        </p>
        '''

        # Injection Logic:
        # 1. Find first </h2> tag and add after it
        # 2. If no H2, find first <p> tag and add before it
        # 3. If none found, add to body start

        if '</h2>' in content:
            content = re.sub(r'(</h2>)', r'\1' + header_html, content, count=1, flags=re.IGNORECASE)
            print("🔗 Link added after H2.")
        elif '<p' in content:
            content = re.sub(r'(<p)', header_html + r'\1', content, count=1, flags=re.IGNORECASE)
            print("🔗 Link added before first P.")
        elif '<body>' in content:
            content = content.replace('<body>', f'<body>{header_html}', 1)
        else:
            content = header_html + content

        return content

    except Exception as e:
        print(f"Read error: {e}")
        abort(500)


@app.route('/images/<filename>')
def serve_image(filename):
    """Serve saved images"""
    return send_from_directory(IMAGES_DIR, filename)


def get_folder_stats(folder_path):
    """Calculate folder statistics: file count and total size"""
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
    """Convert bytes to human-readable format"""
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
    """Main page listing articles - Dark Mode"""
    try:
        # Get articles and sort by creation time (newest first)
        files = []
        for f in os.listdir(ARTICLES_DIR):
            if f.endswith('.html') and UUID_PATTERN.match(f):
                path = os.path.join(ARTICLES_DIR, f)
                files.append({
                    'name': f,
                    'time': os.path.getctime(path),
                    'display_name': f
                })
        
        files.sort(key=lambda x: x['time'], reverse=True)

        # Folder statistics
        articles_count, articles_size = get_folder_stats(ARTICLES_DIR)
        data_count, data_size = get_folder_stats(DATA_DIR)
        images_count, images_size = get_folder_stats(IMAGES_DIR)
        total_articles = len(files)

        html_template = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Article Panel | Kobo Article Helper</title>
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
                <h1>📚 My Articles</h1>

                <!-- Info Panel -->
                <div class="info-panel">
                    <button class="info-toggle" onclick="toggleInfo()" id="infoToggle">
                        <span>ℹ️ System Info</span>
                        <span class="arrow">▼</span>
                    </button>
                    <div class="info-content" id="infoContent">
                        <div class="total-badge">
                            <span class="count">{{ total_articles }}</span>
                            <span class="text">Total Articles</span>
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
                            📄 Article: {{ file.name[:8] }}...
                        </a>
                    </li>
                    {% endfor %}
                    {% if not files %}
                    <li class="empty-state">No articles found yet.</li>
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
    print(f"🌍 Web server started: {VDS_IP}:{WEB_PORT}")
    app.run(host='0.0.0.0', port=WEB_PORT, debug=False, use_reloader=False)
