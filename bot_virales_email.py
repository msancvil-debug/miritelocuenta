import requests
import json
import os
import time
import textwrap
import html
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

# 1. VARIABLES DE ENTORNO
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
WP_URL = (os.environ.get("WP_URL") or "").strip().rstrip("/")
WP_USER = (os.environ.get("WP_USER") or "").strip()
WP_APP_PASS = (os.environ.get("WP_APP_PASS") or "").strip().replace(" ", "")

# Credenciales de Email
GMAIL_USER = (os.environ.get("GMAIL_USER") or "").strip()
GMAIL_APP_PASS = (os.environ.get("GMAIL_APP_PASS") or "").strip()
WP_SECRET_EMAIL = (os.environ.get("WP_SECRET_EMAIL") or "").strip()

HISTORIAL_FILE = "historial_temas.json"

FEEDS_TENDENCIAS = [
    "https://news.google.com/rss/search?q=viral+OR+tiktok+OR+telecinco+OR+reality&hl=es&gl=ES&ceid=ES:es",
    "https://20minutos.es/rss/"
]

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

def cargar_historial():
    if os.path.exists(HISTORIAL_FILE):
        try:
            with open(HISTORIAL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def guardar_en_historial(tema):
    historial = cargar_historial()
    if tema not in historial:
        historial.append(tema)
    with open(HISTORIAL_FILE, "w", encoding="utf-8") as f:
        json.dump(historial, f, ensure_ascii=False, indent=2)

def obtener_nuevo_tema_viral():
    historial = cargar_historial()
    for feed_url in FEEDS_TENDENCIAS:
        try:
            res = requests.get(feed_url, headers=HEADERS_BROWSER, timeout=15)
            if res.status_code == 200:
                root = ET.fromstring(res.content)
                for item in root.findall(".//item"):
                    title_elem = item.find("title")
                    if title_elem is not None and title_elem.text:
                        title = title_elem.text.strip()
                        if title and title not in historial:
                            return title
        except Exception as e:
            print(f"⚠️ Error leyendo feed {feed_url}: {e}")
    return None

def obtener_modelos_disponibles():
    """Consulta dinámicamente qué modelos de Gemini están activos."""
    url_list = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    try:
        res = requests.get(url_list, timeout=10)
        if res.status_code == 200:
            models_data = res.json().get("models", [])
            modelos_validos = []
            for m in models_data:
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods:
                    nombre_corto = m.get("name", "").replace("models/", "")
                    if "gemini" in nombre_corto:
                        modelos_validos.append(nombre_corto)
            if modelos_validos:
                return modelos_validos
    except Exception:
        pass
    return ["gemini-1.5-flash", "gemini-1.5-pro"]

def extraer_keywords_foto(tema_viral, modelos):
    prompt = f"""
    Analiza esta noticia: "{tema_viral}"
    Extrae 1 palabra clave simple en inglés sobre el tema principal o personaje para buscar una foto.
    Ejemplos: "television", "celebrity", "singer", "money", "studio".
    Responde ÚNICAMENTE con la palabra clave.
    """
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json"}

    for modelo in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=15)
            if r.status_code == 200:
                kw = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                kw_limpia = kw.split(",")[0].strip()
                print(f"🔍 Búsqueda de fotografía de fondo: '{kw_limpia}'")
                return kw_limpia
        except Exception:
            continue
    return "television"

def generar_articulo_miri(tema_viral):
    modelos = obtener_modelos_disponibles()
    
    prompt = f"""
    Eres la redactora principal del portal "Miri te lo cuenta".
    Escribe un artículo ameno, cotilla, fresco e impecable sobre la siguiente tendencia:
    "{tema_viral}"

    REQUISITOS DEL TÍTULO:
    - Sin comillas raras ni entidades HTML.
    - Atractivo y directo para redes sociales.

    REQUISITOS DEL CONTENIDO:
    - Redacción en español con etiquetas HTML (<p>, <h2>, <h3>, <strong>).

    Responde ÚNICAMENTE con este JSON válido:
    {{
      "titulo": "Título SEO perfecto sin comillas",
      "contenido_html": "<p>Texto del artículo...</p>"
    }}
    """
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }

    for modelo in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=40)
            if response.status_code == 200:
                raw_text = response.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                if raw_text.startswith("```"):
                    raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                articulo = json.loads(raw_text)
                
                titulo_limpio = html.unescape(articulo["titulo"]).strip().strip('"').strip("'")
                keywords_foto = extraer_keywords_foto(tema_viral, modelos)
                return titulo_limpio, articulo["contenido_html"], keywords_foto
        except Exception:
            continue

    raise Exception("Error crítico: Ningún modelo de Gemini pudo generar el artículo.")

def recortar_y_escalar(img, width, height):
    target_ratio = width / height
    img_ratio = img.width / img.height

    if img_ratio > target_ratio:
        new_height = height
        new_width = int(height * img_ratio)
    else:
        new_width = width
        new_height = int(width / target_ratio)

    img_resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    left = (new_width - width) // 2
    top = (new_height - height) // 2
    return img_resized.crop((left, top, left + width, top + height))

def descargar_foto_fondo(keyword):
    print(f"🖼️ Descargando fotografía real de stock sobre: '{keyword}'...")

    # Búsqueda en Wikimedia Commons
    try:
        url_wiki = f"[https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch=](https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch=){keyword}&gsrnamespace=6&gsrlimit=5&prop=imageinfo&iiprop=url|mime&format=json"
        res_wiki = requests.get(url_wiki, headers=HEADERS_BROWSER, timeout=10)
        if res_wiki.status_code == 200:
            pages = res_wiki.json().get("query", {}).get("pages", {})
            for p_id, p_data in pages.items():
                info = p_data.get("imageinfo", [])
                if info:
                    img_url = info[0].get("url", "")
                    mime = info[0].get("mime", "")
                    if img_url and ("jpeg" in mime or "png" in mime or img_url.lower().endswith(('.jpg', '.jpeg'))):
                        r_img = requests.get(img_url, headers=HEADERS_BROWSER, timeout=12)
                        if r_img.status_code == 200 and len(r_img.content) > 10000:
                            img = Image.open(BytesIO(r_img.content)).convert("RGBA")
                            print("✅ Fotografía temática obtenida de Wikimedia.")
                            return recortar_y_escalar(img, 1200, 630)
    except Exception as e:
        print(f"⚠️ Wikimedia falló: {e}")

    # Backup con Picsum (Fotografía real garantizada)
    try:
        res_picsum = requests.get("[https://picsum.photos/1200/630](https://picsum.photos/1200/630)", headers=HEADERS_BROWSER, timeout=12, allow_redirects=True)
        if res_picsum.status_code == 200 and len(res_picsum.content) > 10000:
            img = Image.open(BytesIO(res_picsum.content)).convert("RGBA")
            print("✅ Fotografía descargada de Picsum.")
            return img
    except Exception:
        pass

    return Image.new("RGBA", (1200, 630), (220, 220, 225, 255))

def crear_imagen_destacada(titulo, keywords_foto):
    width, height = 1200, 630
    bg_img = descargar_foto_fondo(keywords_foto)

    # La foto ocupa el 100% de la pantalla sin capas oscuras gigantes
    img = bg_img.copy()
    draw = ImageDraw.Draw(img)

    try:
        font_badge = ImageFont.truetype("DejaVuSans-Bold.ttf", 20)
        font_title = ImageFont.truetype("DejaVuSans-Bold.ttf", 30)
    except Exception:
        font_badge = ImageFont.load_default()
        font_title = ImageFont.load_default()

    # 1. LOGO / PLACA SUPERIOR IZQUIERDA (Pequeño distintivo)
    badge_x1, badge_y1 = 40, 30
    badge_x2, badge_y2 = 360, 75
    draw.rectangle([badge_x1 + 4, badge_y1 + 4, badge_x2 + 4, badge_y2 + 4], fill=(22, 22, 22, 255))
    draw.rectangle([badge_x1, badge_y1, badge_x2, badge_y2], fill=(240, 68, 56, 255), outline=(22, 22, 22, 255), width=3)
    draw.text((badge_x1 + 15, badge_y1 + 10), "MIRI TE LO CUENTA", fill=(255, 255, 255, 255), font=font_badge)

    # 2. FALDÓN AMARILLO COMPACTO EN LA PARTE INFERIOR
    # Dejamos desde y=0 hasta y=420 (65% SUPERIOR) TOTALMENTE LIBRE para ver la foto
    box_x1, box_y1 = 40, 420
    box_x2, box_y2 = width - 40, height - 30  # De y=420 a y=600

    # Sombra negra y caja amarilla
    draw.rectangle([box_x1 + 6, box_y1 + 6, box_x2 + 6, box_y2 + 6], fill=(22, 22, 22, 255))
    draw.rectangle([box_x1, box_y1, box_x2, box_y2], fill=(255, 216, 77, 255), outline=(22, 22, 22, 255), width=4)

    # Texto en el faldón inferior
    lineas = textwrap.wrap(titulo, width=42)
    texto_formateado = "\n".join(lineas[:3])
    draw.multiline_text((box_x1 + 25, box_y1 + 20), texto_formateado, fill=(22, 22, 22, 255), font=font_title, spacing=8)

    img_filename = "miniatura_destacada.jpg"
    img.convert("RGB").save(img_filename, "JPEG", quality=92)
    print("✅ Portada ensamblada con la foto de fondo 100% visible.")
    return img_filename

def publicar_en_wordpress(titulo, contenido_html, ruta_imagen):
    # Publicación por API REST (Si existen credenciales)
    if WP_URL and WP_USER and WP_APP_PASS:
        try:
            print("🚀 Publicando vía API REST...")
            url_media = f"{WP_URL}/wp-json/wp/v2/media"
            with open(ruta_imagen, "rb") as f:
                media_bytes = f.read()
            
            headers_media = {
                "Content-Disposition": f"attachment; filename={os.path.basename(ruta_imagen)}",
                "Content-Type": "image/jpeg"
            }
            r_media = requests.post(url_media, data=media_bytes, headers=headers_media, auth=(WP_USER, WP_APP_PASS), timeout=30)
            
            media_id = None
            url_foto = ""
            if r_media.status_code in [200, 201]:
                media_json = r_media.json()
                media_id = media_json.get("id")
                url_foto = media_json.get("source_url", "")

            html_final = f"""
            <div style="margin-bottom: 20px; text-align: center;">
                <img src="{url_foto}" alt="{titulo}" style="max-width: 100%; height: auto; border-radius: 8px; border: 2px solid #161616;" />
            </div>
            {contenido_html}
            """

            url_posts = f"{WP_URL}/wp-json/wp/v2/posts"
            payload = {"title": titulo, "content": html_final, "status": "publish"}
            if media_id:
                payload["featured_media"] = media_id

            r_post = requests.post(url_posts, json=payload, headers={"Content-Type": "application/json"}, auth=(WP_USER, WP_APP_PASS), timeout=30)
            if r_post.status_code in [200, 201]:
                print("🎉 Publicado con éxito vía API REST.")
                return True
        except Exception as e:
            print(f"⚠️ API REST falló: {e}")

    # Publicación por Email Secreto
    if GMAIL_USER and GMAIL_APP_PASS and WP_SECRET_EMAIL:
        print("📧 Publicando vía correo secreto...")
        msg = MIMEMultipart()
        msg['From'] = GMAIL_USER
        msg['To'] = WP_SECRET_EMAIL
        msg['Subject'] = titulo

        msg.attach(MIMEText(contenido_html, 'html'))

        if ruta_imagen and os.path.exists(ruta_imagen):
            with open(ruta_imagen, 'rb') as f:
                img_data = f.read()
                image_mime = MIMEImage(img_data, name=os.path.basename(ruta_imagen))
                image_mime.add_header('Content-Disposition', 'attachment', filename=os.path.basename(ruta_imagen))
                msg.attach(image_mime)

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASS)
        server.send_message(msg)
        server.quit()
        print("🎉 Correo enviado con éxito.")
        return True

    raise Exception("Sin credenciales de publicación.")

if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()
    if not tema:
        print("⚠️ Sin temas nuevos. Usando tema por defecto...")
        tema = "Tendencias y polémica viral de la semana en redes sociales"

    print(f"🔥 Tema seleccionado: {tema}")
    titulo, contenido_html, keywords_foto = generar_articulo_miri(tema)
    ruta_imagen = crear_imagen_destacada(titulo, keywords_foto)

    if titulo and contenido_html and ruta_imagen:
        publicado = publicar_en_wordpress(titulo, contenido_html, ruta_imagen)
        if publicado:
            guardar_en_historial(tema)
