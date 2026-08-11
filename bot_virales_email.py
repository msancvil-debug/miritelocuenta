import requests
import json
import os
import time
import textwrap
import html
import random
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

# Variables Canva Connect API (Pro)
CANVA_CLIENT_ID = (os.environ.get("CANVA_CLIENT_ID") or "").strip()
CANVA_CLIENT_SECRET = (os.environ.get("CANVA_CLIENT_SECRET") or "").strip()
CANVA_TEMPLATE_ID = (os.environ.get("CANVA_TEMPLATE_ID") or "").strip()

# Credenciales Email Secreto WordPress (Fallback)
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

def extraer_keyword_foto(tema_viral, modelos):
    prompt = f"""
    Noticia: "{tema_viral}"
    Extrae 1 palabra clave simple en inglés sobre el tema principal o personaje para buscar una foto en Canva.
    Ejemplos: "television", "celebrity", "singer", "money", "studio".
    Responde ÚNICAMENTE con la palabra en inglés.
    """
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json"}

    for modelo in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=15)
            if r.status_code == 200:
                kw = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                return kw.replace('"', '').replace("'", "")
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
                keyword_foto = extraer_keyword_foto(tema_viral, modelos)
                return titulo_limpio, articulo["contenido_html"], keyword_foto
        except Exception:
            continue

    raise Exception("Error crítico: Ningún modelo de Gemini pudo generar el artículo.")

def generar_miniatura_canva_pro(titulo, keyword_foto):
    """
    Pide a la API Connect de Canva Pro que inserte el titular y la imagen temática en tu plantilla.
    """
    ruta_local = "miniatura_destacada.jpg"

    if CANVA_CLIENT_ID and CANVA_CLIENT_SECRET and CANVA_TEMPLATE_ID:
        try:
            print(f"🎨 Conectando con Canva Pro API para autorrellenar la plantilla con '{keyword_foto}'...")
            url_autofill = "[https://api.canva.com/v1/autofills](https://api.canva.com/v1/autofills)"
            headers_canva = {
                "Authorization": f"Bearer {CANVA_CLIENT_SECRET}",
                "Content-Type": "application/json"
            }
            payload_canva = {
                "brand_template_id": CANVA_TEMPLATE_ID,
                "data": {
                    "TITULAR": {"type": "text", "text": titulo},
                    "FONDO": {"type": "image", "asset_id": keyword_foto}
                }
            }
            res_canva = requests.post(url_autofill, headers=headers_canva, json=payload_canva, timeout=30)
            if res_canva.status_code in [200, 201]:
                job_id = res_canva.json().get("job", {}).get("id")
                # Poll de estado del renderizado
                url_job = f"[https://api.canva.com/v1/autofills/](https://api.canva.com/v1/autofills/){job_id}"
                for _ in range(10):
                    time.sleep(3)
                    res_job = requests.get(url_job, headers=headers_canva, timeout=15)
                    if res_job.status_code == 200:
                        status = res_job.json().get("job", {}).get("status")
                        if status == "success":
                            export_url = res_job.json().get("job", {}).get("result", {}).get("design", {}).get("url")
                            if export_url:
                                r_img = requests.get(export_url, timeout=20)
                                if r_img.status_code == 200:
                                    with open(ruta_local, "wb") as f:
                                        f.write(r_img.content)
                                    print("🎉 ¡Miniatura oficial renderizada con éxito por Canva Pro!")
                                    return ruta_local
                        elif status == "failed":
                            break
        except Exception as e:
            print(f"⚠️ Canva API no respondió a tiempo ({e}). Usando plantilla local...")

    # Generación de respaldo segura si la API de Canva no responde a tiempo
    img = Image.new("RGB", (1200, 630), (255, 216, 77))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 32)
    except Exception:
        font = ImageFont.load_default()
    
    draw.text((50, 50), "MIRI TE LO CUENTA", fill=(240, 68, 56), font=font)
    lineas = textwrap.wrap(titulo, width=35)
    draw.multiline_text((50, 150), "\n".join(lineas[:4]), fill=(22, 22, 22), font=font, spacing=10)
    img.save(ruta_local, "JPEG", quality=90)
    return ruta_local

def publicar_en_wordpress(titulo, contenido_html, ruta_imagen):
    if WP_URL and WP_USER and WP_APP_PASS:
        try:
            print("🚀 Publicando vía API REST de WordPress...")
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
            print(f"⚠️ API REST falló: {e}")

    if GMAIL_USER and GMAIL_APP_PASS and WP_SECRET_EMAIL:
        print("📧 Publicando vía correo secreto a WordPress...")
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
        print("🎉 Correo enviado con éxito a WordPress.")
        return True

    raise Exception("Sin credenciales de publicación.")

if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()
    if not tema:
        print("⚠️ Sin temas nuevos. Usando tema por defecto...")
        tema = "Tendencias y polémica viral de la semana en redes sociales"

    print(f"🔥 Tema seleccionado: {tema}")
    titulo, contenido_html, keyword_foto = generar_articulo_miri(tema)
    ruta_imagen = generar_miniatura_canva_pro(titulo, keyword_foto)

    if titulo and contenido_html and ruta_imagen:
        publicado = publicar_en_wordpress(titulo, contenido_html, ruta_imagen)
        if publicado:
            guardar_en_historial(tema)
