import requests
import json
import os
import time
import textwrap
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# 1. VARIABLES DE ENTORNO
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
WP_URL = (os.environ.get("WP_URL") or "").strip().rstrip("/")
WP_USER = (os.environ.get("WP_USER") or "").strip()
WP_APP_PASS = (os.environ.get("WP_APP_PASS") or "").strip().replace(" ", "")

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

def obtener_palabras_clave_imagen(tema_viral):
    """Pide a Gemini 2 palabras clave en inglés para buscar una foto temática real."""
    prompt = f"Extrae 2 palabras clave sencillas en inglés separadas por coma para buscar una foto de stock libre de derechos representativa sobre esta noticia: '{tema_viral}' (Ejemplo: 'electricity,company' o 'television,studio'). Responde ÚNICAMENTE con las palabras clave."
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    try:
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        if r.status_code == 200:
            kw = r.json()['candidates'][0]['content']['parts'][0]['text'].strip().replace(" ", "")
            return kw
    except Exception:
        pass
    return "news,media"

def generar_articulo_miri(tema_viral):
    prompt = f"""
    Eres la redactora principal del proyecto "Miri te lo cuenta", un portal sobre tendencias de internet, vídeos virales, reality shows y cultura pop.
    Escribe un artículo ameno, explicativo, cotilla y optimizado para SEO sobre el siguiente tema viral:
    "{tema_viral}"

    REQUISITOS DEL ARTÍCULO:
    1. Tono: Fresco, cercano, directo y explicativo ("Te lo cuento detalladamente").
    2. Todo el HTML DEBE llevar estilos en línea (inline styles):
       - Paleta: Fondo Marfil (#FFF7EF), Bordes Negros (#161616), Amarillo (#FFD84D), Coral (#F04438).
       - Cajas destacadas: <div style="background-color: #FFD84D; border: 3px solid #161616; border-radius: 12px; padding: 16px; margin: 20px 0; box-shadow: 4px 4px 0px #161616;">
       - Títulos h2: <h2 style="font-size: 22px; font-weight: 800; color: #161616; background-color: #FFF7EF; border-left: 6px solid #F04438; padding: 8px 12px; margin-top: 25px;">
       - Títulos h3: <h3 style="font-size: 18px; font-weight: 700; color: #161616; margin-top: 20px;">
       - Texto normal: <p style="font-size: 16px; line-height: 1.6; color: #161616; margin-bottom: 15px;">

    Responde ÚNICAMENTE con un objeto JSON válido:
    {{
      "titulo": "Título SEO aquí",
      "contenido_html": "<p style='...'>Texto de introducción...</p><div style='...'>Resumen...</div>"
    }}
    """
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    res = requests.post(url, headers=headers, json=payload, timeout=40)
    if res.status_code == 200:
        raw_text = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        articulo = json.loads(raw_text)
        return articulo["titulo"], articulo["contenido_html"]
    raise Exception(f"Error Gemini API ({res.status_code}): {res.text}")

def crear_imagen_destacada(titulo, tema_viral):
    width, height = 1200, 630
    keywords = obtener_palabras_clave_imagen(tema_viral)
    print(f"🖼️ Buscando fotografía de stock temática relacionada con: '{keywords}'...")
    
    bg_img = None
    urls_stock = [
        f"[https://loremflickr.com/1200/630/](https://loremflickr.com/1200/630/){keywords}",
        "[https://loremflickr.com/1200/630/news,media](https://loremflickr.com/1200/630/news,media)"
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
        bg_img = Image.new("RGBA", (width, height), (30, 30, 35, 255))

    # Oscurecer suavemente el fondo para resaltar la lectura
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 110))
    img = Image.alpha_composite(bg_img, overlay)
    draw = ImageDraw.Draw(img)

    margin = 50
    box_x1, box_y1 = margin, 120
    box_x2, box_y2 = width - margin, height - 80

    # Sombra y caja amarilla del titular
    draw.rectangle([box_x1 + 8, box_y1 + 8, box_x2 + 8, box_y2 + 8], fill=(22, 22, 22, 255))
    draw.rectangle([box_x1, box_y1, box_x2, box_y2], fill=(255, 216, 77, 255), outline=(22, 22, 22, 255), width=5)

    # Distintivo "MIRI TE LO CUENTA"
    badge_x1, badge_y1 = margin + 20, 50
    badge_x2, badge_y2 = margin + 380, 100
    draw.rectangle([badge_x1 + 4, badge_y1 + 4, badge_x2 + 4, badge_y2 + 4], fill=(22, 22, 22, 255))
    draw.rectangle([badge_x1, badge_y1, badge_x2, badge_y2], fill=(240, 68, 56, 255), outline=(22, 22, 22, 255), width=3)

    try:
        font_badge = ImageFont.truetype("DejaVuSans-Bold.ttf", 20)
        font_title = ImageFont.truetype("DejaVuSans-Bold.ttf", 36)
    except Exception:
        font_badge = ImageFont.load_default()
        font_title = ImageFont.load_default()

    draw.text((badge_x1 + 15, badge_y1 + 12), "MIRI TE LO CUENTA", fill=(255, 255, 255, 255), font=font_badge)

    lineas = textwrap.wrap(titulo, width=38)
    texto_formateado = "\n".join(lineas[:4])
    draw.multiline_text((box_x1 + 30, box_y1 + 35), texto_formateado, fill=(22, 22, 22, 255), font=font_title, spacing=12)

    img_filename = "miniatura_destacada.jpg"
    img.convert("RGB").save(img_filename, "JPEG", quality=90)
    print("🖼️ Imagen de portada generada correctamente sobre fotografía temática.")
    return img_filename

def subir_imagen_wordpress(ruta_imagen):
    print("📤 Subiendo foto a la Biblioteca de Medios de WordPress...")
    url_media = f"{WP_URL}/wp-json/wp/v2/media"
    with open(ruta_imagen, "rb") as f:
        media_data = f.read()
    headers = {
        "Content-Disposition": f"attachment; filename={os.path.basename(ruta_imagen)}",
        "Content-Type": "image/jpeg"
    }
    response = requests.post(url_media, data=media_data, headers=headers, auth=(WP_USER, WP_APP_PASS), timeout=30)
    if response.status_code in [200, 201]:
        res_json = response.json()
        return res_json.get("id"), res_json.get("source_url")
    return None, None

def publicar_articulo_wordpress(titulo, contenido_html, media_id, url_imagen_publica):
    print("🚀 Publicando artículo en WordPress vía API REST...")
    url_posts = f"{WP_URL}/wp-json/wp/v2/posts"

    # Insertar la foto con URL pública en la primera línea para que Metricool la detecte al 100% por RSS
    html_con_imagen_publica = f"""
    <div style="margin-bottom: 25px; text-align: center;">
        <img src="{url_imagen_publica}" style="max-width: 100%; height: auto; border: 3px solid #161616; border-radius: 12px; box-shadow: 4px 4px 0px #161616;" alt="{titulo}" />
    </div>
    {contenido_html}
    """

    payload = {
        "title": titulo,
        "content": html_con_imagen_publica,
        "status": "publish"
    }

    if media_id:
        payload["featured_media"] = media_id

    headers = {"Content-Type": "application/json"}
    response = requests.post(url_posts, json=payload, headers=headers, auth=(WP_USER, WP_APP_PASS), timeout=30)

    if response.status_code in [200, 201]:
        post_data = response.json()
        print(f"🎉 ¡ÉXITO TOTAL! Entrada publicada en WordPress. Link: {post_data.get('link')}")
        return True
    else:
        raise Exception(f"ERROR AL PUBLICAR EN WORDPRESS ({response.status_code}): {response.text}")

if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()
    if not tema:
        print("⚠️ No se encontró tema nuevo. Usando tema por defecto...")
        tema = "Polémica y tendencias virales en TikTok de esta semana"

    print(f"🔥 Tema seleccionado: {tema}")
    titulo, contenido_html = generar_articulo_miri(tema)
    
    ruta_imagen = crear_imagen_destacada(titulo, tema)
    media_id, url_imagen_publica = subir_imagen_wordpress(ruta_imagen)
    
    if media_id and url_imagen_publica:
        publicar_articulo_wordpress(titulo, contenido_html, media_id, url_imagen_publica)
        guardar_en_historial(tema)
    else:
        print("❌ Falló la subida de imagen a WordPress.")
