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

# Credenciales de Email (Publicación vía Correo Secreto de WordPress)
GMAIL_USER = (os.environ.get("GMAIL_USER") or "").strip()
GMAIL_APP_PASS = (os.environ.get("GMAIL_APP_PASS") or "").strip()
WP_SECRET_EMAIL = (os.environ.get("WP_SECRET_EMAIL") or "").strip()

HISTORIAL_FILE = "historial_temas.json"

FEEDS_TENDENCIAS = [
    "https://news.google.com/rss/search?q=viral+OR+tiktok+OR+telecinco+OR+reality&hl=es&gl=ES&ceid=ES:es",
    "https://20minutos.es/rss/"
]

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
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
    """Pide a Gemini 2-3 palabras clave en inglés sobre la temática."""
    prompt = f"""
    Analiza esta noticia: "{tema_viral}"
    Extrae de 2 a 3 palabras clave en inglés separadas por comas que describan al personaje o tema principal para buscar una fotografía temática de fondo.
    Ejemplos:
    - Si habla de un presentador o famosos en TV -> "television, presenter, celebrity"
    - Si habla de cantantes o conciertos -> "singer, concert, stage"
    - Si habla de facturas o dinero -> "money, bill, finance"

    Responde ÚNICAMENTE con las palabras clave en inglés.
    """
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json"}

    for modelo in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=15)
            if r.status_code == 200:
                kw = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                kw_limpias = ",".join([k.strip() for k in kw.split(",")[:3]])
                print(f"🔍 Palabras clave para la fotografía de fondo: '{kw_limpias}'")
                return kw_limpias
        except Exception:
            continue
    return "media,tv,news"

def generar_articulo_miri(tema_viral):
    modelos = obtener_modelos_disponibles()
    
    prompt = f"""
    Eres la redactora principal del portal "Miri te lo cuenta".
    Escribe un artículo ameno, cotilla, fresco y bien redactado sobre la siguiente tendencia:
    "{tema_viral}"

    REQUISITOS DEL TÍTULO:
    - Debe ser impecable ortográficamente, sin faltas ni caracteres extraños.
    - Atractivo y directo para captar la atención en redes sociales.

    REQUISITOS DEL CONTENIDO:
    - Redacción en español, cercano y estructurado con etiquetas HTML (<p>, <h2>, <h3>, <strong>).

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

def crear_imagen_destacada(titulo, keywords_foto):
    """Descarga la imagen temática de fondo y pinta el diseño con el titular."""
    width, height = 1200, 630
    print(f"🖼️ Descargando fotografía de stock temática relacionada con: '{keywords_foto}'...")
    
    bg_img = None
    urls_stock = [
        f"[https://loremflickr.com/1200/630/](https://loremflickr.com/1200/630/){keywords_foto}",
        "[https://loremflickr.com/1200/630/tv,news](https://loremflickr.com/1200/630/tv,news)"
    ]
    
    for url in urls_stock:
        try:
            res = requests.get(url, headers=HEADERS_BROWSER, timeout=12)
            if res.status_code == 200 and len(res.content) > 5000:
                bg_img = Image.open(BytesIO(res.content)).convert("RGBA")
                break
        except Exception:
            continue

    if not bg_img:
        bg_img = Image.new("RGBA", (width, height), (35, 35, 40, 255))

    # Filtro oscuro para garantizar lectura
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 115))
    img = Image.alpha_composite(bg_img, overlay)
    draw = ImageDraw.Draw(img)

    margin = 50
    box_x1, box_y1 = margin, 120
    box_x2, box_y2 = width - margin, height - 80

    # Sombra y caja amarilla
    draw.rectangle([box_x1 + 8, box_y1 + 8, box_x2 + 8, box_y2 + 8], fill=(22, 22, 22, 255))
    draw.rectangle([box_x1, box_y1, box_x2, box_y2], fill=(255, 216, 77, 255), outline=(22, 22, 22, 255), width=5)

    # Placa "MIRI TE LO CUENTA"
    badge_x1, badge_y1 = margin + 20, 50
    badge_x2, badge_y2 = margin + 380, 100
    draw.rectangle([badge_x1 + 4, badge_y1 + 4, badge_x2 + 4, badge_y2 + 4], fill=(22, 22, 22, 255))
    draw.rectangle([badge_x1, badge_y1, badge_x2, badge_y2], fill=(240, 68, 56, 255), outline=(22, 22, 22, 255), width=3)

    try:
        font_badge = ImageFont.truetype("DejaVuSans-Bold.ttf", 20)
        font_title = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
    except Exception:
        font_badge = ImageFont.load_default()
        font_title = ImageFont.load_default()

    draw.text((badge_x1 + 15, badge_y1 + 12), "MIRI TE LO CUENTA", fill=(255, 255, 255, 255), font=font_badge)

    lineas = textwrap.wrap(titulo, width=38)
    texto_formateado = "\n".join(lineas[:4])
    draw.multiline_text((box_x1 + 30, box_y1 + 35), texto_formateado, fill=(22, 22, 22, 255), font=font_title, spacing=12)

    img_filename = "miniatura_destacada.jpg"
    img.convert("RGB").save(img_filename, "JPEG", quality=92)
    print("✅ Miniatura temática generada con éxito en disco.")
    return img_filename

def publicar_en_wordpress(titulo, contenido_html, ruta_imagen):
    # Opción 1: API REST (si están configuradas las credenciales)
    if WP_URL and WP_USER and WP_APP_PASS:
        try:
            print("🚀 Intentando publicar vía API REST de WordPress...")
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
                print("🎉 ¡Publicado con éxito en WordPress vía API REST!")
                return True
        except Exception as e:
            print(f"⚠️ Falló la publicación por API REST: {e}. Probando vía correo secreto...")

    # Opción 2: Correo Secreto (Jetpack / WordPress Post-by-Email)
    if GMAIL_USER and GMAIL_APP_PASS and WP_SECRET_EMAIL:
        print("📧 Publicando vía correo electrónico en WordPress...")
        msg = MIMEMultipart()
        msg['From'] = GMAIL_USER
        msg['To'] = WP_SECRET_EMAIL
        msg['Subject'] = titulo

        # Adjuntar cuerpo HTML
        msg.attach(MIMEText(contenido_html, 'html'))

        # ADJUNTAR LA IMAGEN GENERADA AL CORREO DE NATIVO DE WORDPRESS
        if ruta_imagen and os.path.exists(ruta_imagen):
            with open(ruta_imagen, 'rb') as f:
                img_data = f.read()
                image_mime = MIMEImage(img_data, name=os.path.basename(ruta_imagen))
                image_mime.add_header('Content-Disposition', 'attachment', filename=os.path.basename(ruta_imagen))
                msg.attach(image_mime)
                print("📎 Imagen de miniatura adjuntada con éxito al correo.")

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASS)
        server.send_message(msg)
        server.quit()
        print("🎉 ¡Correo enviado con éxito a WordPress con la imagen adjunta!")
        return True

    raise Exception("Faltan credenciales de publicación de WordPress (API REST o Email).")

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
