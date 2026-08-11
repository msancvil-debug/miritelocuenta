import requests
import json
import os
import time
import textwrap
import html
import random
import urllib.parse
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

# Imágenes de respaldo 100% luminosas y de estudio de TV
FOTOS_LUMINOSAS_RESPALDO = [
    "https://images.unsplash.com/photo-1598899134739-24c46f58b8c0?w=1200&h=630&fit=crop", # Plató con luces brillantes
    "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=1200&h=630&fit=crop", # Estudio / Micrófono iluminado
    "https://images.unsplash.com/photo-1522869635100-9f4c5e86aa37?w=1200&h=630&fit=crop", # Luces de plató luminosas
    "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=1200&h=630&fit=crop"  # Escenario de luces brillantes
]

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

def generar_prompt_imagen_ai(tema_viral, modelos):
    prompt = f"""
    Lee esta noticia: "{tema_viral}"
    Crea una descripción visual corta en inglés (máximo 12 palabras) para generar una imagen de fondo brillante, clara y muy iluminada de estilo prensa/TV.
    Ejemplos:
    - "brightly lit television game show studio set with vivid colors, HD photo"
    - "bright broadcast television studio interview set with studio lights, HD photo"
    - "bright entertainment news studio background with colorful lights, HD photo"

    Responde ÚNICAMENTE con la frase en inglés.
    """
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json"}

    for modelo in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=15)
            if r.status_code == 200:
                p_text = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                p_limpio = p_text.replace('"', '').replace("'", "")
                print(f"🎨 Prompt de IA generado para la imagen: '{p_limpio}'")
                return p_limpio
        except Exception:
            continue
    return "brightly lit television broadcast studio set with vivid colors, HD photo"

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
                prompt_foto = generar_prompt_imagen_ai(tema_viral, modelos)
                return titulo_limpio, articulo["contenido_html"], prompt_foto
        except Exception:
            continue

    raise Exception("Error crítico: Ningún modelo de Gemini pudo generar el artículo.")

def crear_fondo_luminoso_marca(width, height):
    """Crea un fondo gráfico claro, vibrante y luminoso con la estética de Miri."""
    img = Image.new("RGBA", (width, height), (255, 247, 239, 255)) # Marfil brillante
    draw = ImageDraw.Draw(img)

    # Franjas decorativas luminosas
    draw.rectangle([0, 0, width, 15], fill=(240, 68, 56, 255)) # Rojo Coral
    draw.rectangle([0, 15, width, 25], fill=(255, 216, 77, 255)) # Amarillo
    draw.rectangle([0, height - 15, width, height], fill=(22, 22, 22, 255))

    return img

def descargar_foto_ia(prompt_ingles):
    """Intenta generar/descargar una fotografía clara. Si falla, activa el fondo luminoso de marca."""
    print(f"🖼️ Generando fotografía luminosa con IA basada en: '{prompt_ingles}'...")
    
    prompt_encoded = urllib.parse.quote(prompt_ingles)
    seed_azar = random.randint(100, 99999)
    url_pollinations = f"[https://image.pollinations.ai/prompt/](https://image.pollinations.ai/prompt/){prompt_encoded}?width=1200&height=630&seed={seed_azar}&nologo=true&model=flux"

    try:
        res = requests.get(url_pollinations, headers=HEADERS_BROWSER, timeout=20)
        if res.status_code == 200 and len(res.content) > 15000:
            img = Image.open(BytesIO(res.content)).convert("RGBA")
            print("✅ Fotografía temática generada con éxito por la IA.")
            return img
    except Exception as e:
        print(f"⚠️ Pollinations no respondió a tiempo: {e}. Probando fotos de estudio iluminadas...")

    # Respaldo 1: Unsplash estudio brillante
    for url_res in FOTOS_LUMINOSAS_RESPALDO:
        try:
            r_u = requests.get(url_res, headers=HEADERS_BROWSER, timeout=10)
            if r_u.status_code == 200 and len(r_u.content) > 10000:
                img = Image.open(BytesIO(r_u.content)).convert("RGBA")
                print("✅ Fotografía de plató iluminado cargada con éxito.")
                return img
        except Exception:
            continue

    # Respaldo 2: Fondo brillante de marca (Garantizado NUNCA oscuro)
    print("✨ Activando fondo gráfico claro de marca Miri...")
    return crear_fondo_luminoso_marca(1200, 630)

def crear_imagen_destacada(titulo, prompt_foto):
    width, height = 1200, 630
    bg_img = descargar_foto_ia(prompt_foto)

    img = bg_img.copy()
    draw = ImageDraw.Draw(img)

    try:
        font_badge = ImageFont.truetype("DejaVuSans-Bold.ttf", 20)
        font_title = ImageFont.truetype("DejaVuSans-Bold.ttf", 30)
    except Exception:
        font_badge = ImageFont.load_default()
        font_title = ImageFont.load_default()

    # 1. PLACA SUPERIOR IZQUIERDA ("MIRI TE LO CUENTA")
    badge_x1, badge_y1 = 40, 30
    badge_x2, badge_y2 = 360, 75
    draw.rectangle([badge_x1 + 4, badge_y1 + 4, badge_x2 + 4, badge_y2 + 4], fill=(22, 22, 22, 255))
    draw.rectangle([badge_x1, badge_y1, badge_x2, badge_y2], fill=(240, 68, 56, 255), outline=(22, 22, 22, 255), width=3)
    draw.text((badge_x1 + 15, badge_y1 + 10), "MIRI TE LO CUENTA", fill=(255, 255, 255, 255), font=font_badge)

    # 2. FALDÓN AMARILLO INFERIOR CON BORDE NEGRO
    box_x1, box_y1 = 40, 420
    box_x2, box_y2 = width - 40, height - 30

    draw.rectangle([box_x1 + 6, box_y1 + 6, box_x2 + 6, box_y2 + 6], fill=(22, 22, 22, 255))
    draw.rectangle([box_x1, box_y1, box_x2, box_y2], fill=(255, 216, 77, 255), outline=(22, 22, 22, 255), width=4)

    lineas = textwrap.wrap(titulo, width=42)
    texto_formateado = "\n".join(lineas[:3])
    draw.multiline_text((box_x1 + 25, box_y1 + 20), texto_formateado, fill=(22, 22, 22, 255), font=font_title, spacing=8)

    img_filename = "miniatura_destacada.jpg"
    img.convert("RGB").save(img_filename, "JPEG", quality=92)
    print("✅ Portada ensamblada correctamente.")
    return img_filename

def publicar_en_wordpress(titulo, contenido_html, ruta_imagen):
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
    titulo, contenido_html, prompt_foto = generar_articulo_miri(tema)
    ruta_imagen = crear_imagen_destacada(titulo, prompt_foto)

    if titulo and contenido_html and ruta_imagen:
        publicado = publicar_en_wordpress(titulo, contenido_html, ruta_imagen)
        if publicado:
            guardar_en_historial(tema)
