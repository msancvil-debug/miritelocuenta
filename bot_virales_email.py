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

# 1. VARIABLES DE ENTORNO
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
WP_URL = (os.environ.get("WP_URL") or "").strip().rstrip("/")
WP_USER = (os.environ.get("WP_USER") or "").strip()
WP_APP_PASS = (os.environ.get("WP_APP_PASS") or "").strip().replace(" ", "")

# Credenciales Canva Connect API (Pro)
CANVA_CLIENT_ID = (os.environ.get("CANVA_CLIENT_ID") or "").strip()
CANVA_CLIENT_SECRET = (os.environ.get("CANVA_CLIENT_SECRET") or "").strip()
CANVA_TEMPLATE_ID = (os.environ.get("CANVA_TEMPLATE_ID") or "").strip()

HISTORIAL_FILE = "historial_temas.json"

FEEDS_TENDENCIAS = [
    "https://news.google.com/rss/search?q=viral+OR+tiktok+OR+telecinco+OR+reality&hl=es&gl=ES&ceid=ES:es",
    "https://20minutos.es/rss/"
]

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"
}

FOTOS_STOCK_TEMATICAS = [
    "https://images.unsplash.com/photo-1598899134739-24c46f58b8c0?w=1200&h=630&fit=crop",
    "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=1200&h=630&fit=crop",
    "https://images.unsplash.com/photo-1522869635100-9f4c5e86aa37?w=1200&h=630&fit=crop",
    "https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=1200&h=630&fit=crop"
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

def generar_articulo_miri(tema_viral):
    modelos = obtener_modelos_disponibles()
    prompt = f"""
    Eres la redactora principal del portal "Miri te lo cuenta".
    Escribe un artículo ameno, cotilla y fresco sobre la tendencia: "{tema_viral}"

    Responde ÚNICAMENTE con un JSON válido:
    {{
      "titulo": "Título atractivo sin comillas",
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
                return titulo_limpio, articulo["contenido_html"]
        except Exception:
            continue
    raise Exception("❌ Error crítico: La API de Gemini no pudo generar el artículo.")

def generar_miniatura_canva_pro(titulo):
    """Llama a la API Connect de Canva Pro para editar la plantilla corporativa."""
    ruta_local = "miniatura_destacada.jpg"
    
    if CANVA_CLIENT_ID and CANVA_CLIENT_SECRET and CANVA_TEMPLATE_ID:
        try:
            print("🎨 Conectando con Canva Pro API para autorrellenar la plantilla...")
            url_autofill = "[https://api.canva.com/v1/autofills](https://api.canva.com/v1/autofills)"
            headers_canva = {
                "Authorization": f"Bearer {CANVA_CLIENT_SECRET}",
                "Content-Type": "application/json"
            }
            url_foto_fondo = random.choice(FOTOS_STOCK_TEMATICAS)
            
            payload_canva = {
                "brand_template_id": CANVA_TEMPLATE_ID,
                "data": {
                    "TITULAR": {"type": "text", "text": titulo},
                    "FONDO": {"type": "image", "url": url_foto_fondo}
                }
            }
            res_canva = requests.post(url_autofill, headers=headers_canva, json=payload_canva, timeout=30)
            
            if res_canva.status_code in [200, 201]:
                job_id = res_canva.json().get("job", {}).get("id")
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
                                    print("🎉 ¡Miniatura oficial generada por Canva Pro con éxito!")
                                    return ruta_local
                        elif status == "failed":
                            break
        except Exception as e:
            print(f"⚠️ Canva API no respondió ({e}). Usando generador local de respaldo...")

    # Generación de respaldo local con Pillow si la API de Canva falla o no está disponible
    width, height = 1200, 630
    img = Image.new("RGB", (width, height), (255, 216, 77))
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
    if not (WP_URL and WP_USER and WP_APP_PASS):
        raise Exception("❌ Faltan las variables WP_URL, WP_USER o WP_APP_PASS en GitHub Secrets.")

    print(f"🚀 Publicando vía API REST en {WP_URL}...")
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
        print("🎉 ¡ÉXITO TOTAL! Entrada y miniatura de Canva publicadas en WordPress.")
        return True
    else:
        raise Exception(f"❌ ERROR DE PUBLICACIÓN (Código {r_post.status_code}): {r_post.text}")

if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()
    if not tema:
        print("⚠️ Sin temas nuevos en el feed. Usando tema por defecto...")
        tema = "Tendencias y polémica viral de la semana en redes sociales"

    print(f"🔥 Tema seleccionado: {tema}")
    titulo, contenido_html = generar_articulo_miri(tema)
    ruta_imagen = generar_miniatura_canva_pro(titulo)

    if titulo and contenido_html and ruta_imagen:
        publicado = publicar_en_wordpress(titulo, contenido_html, ruta_imagen)
        if publicado:
            guardar_en_historial(tema)
