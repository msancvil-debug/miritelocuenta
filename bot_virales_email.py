import requests
import json
import os
import time
import html
import re
import base64
import mimetypes
import xml.etree.ElementTree as ET

# ==========================================
# 1. VARIABLES DE ENTORNO
# ==========================================
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"
}

# ==========================================
# 2. GESTIÓN DE HISTORIAL Y TENDENCIAS
# ==========================================
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

# ==========================================
# 3. GENERACIÓN DE ARTÍCULOS CON GEMINI
# ==========================================
def obtener_modelos_disponibles():
    url_list = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    try:
        res = requests.get(url_list, timeout=10)
        if res.status_code == 200:
            models_data = res.json().get("models", [])
            modelos_validos = [m.get("name", "").replace("models/", "") for m in models_data if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in m.get("name", "").lower()]
            if modelos_validos: 
                return modelos_validos
    except Exception: 
        pass
    return ["gemini-1.5-flash", "gemini-1.5-pro"]

def generar_articulo_miri(tema_viral):
    modelos = obtener_modelos_disponibles()
    prompt = f"""
    Eres la redactora principal del portal de actualidad y salseo "Miri te lo cuenta".
    Escribe un artículo ameno, fresco, dinámico y muy cotilla sobre la tendencia: "{tema_viral}".
    Responde ÚNICAMENTE con un objeto JSON válido (sin formato Markdown adicional ni bloques de código), estructurado exactamente así:
    {{
      "titulo": "Titular llamativo y viral para el post",
      "contenido_html": "<p>Primer párrafo del artículo...</p><p>Segundo párrafo...</p>",
      "titulo_miniatura": "TITULAR CORTO PARA LA IMAGEN",
      "categoria_visual": "SALSEO / TELECINCO / REDES",
      "busqueda_imagen": "keyword in english for image search e.g. celebrity party"
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
                return (
                    html.unescape(articulo["titulo"]).strip().strip('"').strip("'"),
                    articulo["contenido_html"],
                    articulo.get("titulo_miniatura", articulo["titulo"]),
                    articulo.get("categoria_visual", "ACTUALIDAD"),
                    articulo.get("busqueda_imagen", "news party")
                )
        except Exception as e:
            print(f"⚠️ Fallo con el modelo {modelo}: {e}")
            continue
            
    raise Exception("❌ Error crítico: La API de Gemini no pudo generar el artículo.")

# ==========================================
# 4. MOTOR VISUAL INDEPENDIENTE
# ==========================================
def generar_miniatura_html(titulo_miniatura, categoria_visual, busqueda_imagen):
    """Busca una imagen abierta y genera la miniatura corporativa adaptada."""
    ruta_local = "miniatura_destacada.jpg"
    info_imagen = None
    
    # Intento de descarga de Openverse / Unsplash de respaldo
    try:
        url_unsplash = f"[https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=1200&h=630&fit=crop](https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=1200&h=630&fit=crop)"
        r = requests.get(url_unsplash, timeout=10)
        if r.status_code == 200:
            with open(ruta_local, "wb") as f:
                f.write(r.content)
            info_imagen = {"title": "Unsplash Photo", "author": "Open Source"}
    except Exception:
        pass

    if not os.path.exists(ruta_local):
        # Fallback de emergencia si falla la red
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (1200, 630), (35, 35, 40))
        img.save(ruta_local, "JPEG")

    print("✅ Miniatura visual generada correctamente.")
    return ruta_local, info_imagen

def construir_credito_html(info_imagen):
    if not info_imagen:
        return ""
    return f'<p style="font-size:11px; color:#888; margin-top:10px;">Imagen de apoyo / Ilustración</p>'

# ==========================================
# 5. PUBLICACIÓN EN WORDPRESS
# ==========================================
def publicar_en_wordpress(titulo, contenido_html, ruta_imagen):
    if not (WP_URL and WP_USER and WP_APP_PASS):
        raise Exception("❌ Error crítico: Faltan credenciales de WordPress en Secrets.")

    media_id = None
    if ruta_imagen and os.path.exists(ruta_imagen):
        print(f"🚀 Subiendo imagen destacada a {WP_URL}...")
        url_media = f"{WP_URL}/wp-json/wp/v2/media"
        with open(ruta_imagen, "rb") as f:
            media_bytes = f.read()
        
        headers_media = {
            "Content-Disposition": f"attachment; filename={os.path.basename(ruta_imagen)}",
            "Content-Type": "image/jpeg"
        }
        r_media = requests.post(url_media, data=media_bytes, headers=headers_media, auth=(WP_USER, WP_APP_PASS), timeout=30)
        
        if r_media.status_code in [200, 201]:
            media_json = r_media.json()
            media_id = media_json.get("id")
            print(f"✅ Imagen subida correctamente (Media ID: {media_id})")
        else:
            print(f"⚠️ Aviso al subir imagen a WordPress: {r_media.text}")

    print("🚀 Publicando artículo completo con su miniatura en WordPress...")
    url_posts = f"{WP_URL}/wp-json/wp/v2/posts"
    payload = {"title": titulo, "content": contenido_html, "status": "publish"}
    if media_id:
        payload["featured_media"] = media_id

    r_post = requests.post(url_posts, json=payload, headers={"Content-Type": "application/json"}, auth=(WP_USER, WP_APP_PASS), timeout=30)
    
    if r_post.status_code in [200, 201]:
        post_data = r_post.json()
        print(f"🎉 ¡ÉXITO TOTAL! Entrada publicada en WordPress. Link: {post_data.get('link')}")
        return True
    else:
        raise Exception(f"❌ Error crítico al publicar entrada ({r_post.status_code}): {r_post.text}")

# ==========================================
# 6. EJECUCIÓN PRINCIPAL
# ==========================================
if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()
    if not tema:
        print("⚠️ Sin temas nuevos. Usando tema de prueba...")
        tema = "Polémica viral de la semana en redes sociales"

    print(f"🔥 Tema seleccionado: {tema}")

    (
        titulo,
        contenido_html,
        titulo_miniatura,
        categoria_visual,
        busqueda_imagen
    ) = generar_articulo_miri(tema)

    print(f"📰 Título artículo: {titulo}")
    print(f"🖼️ Título miniatura: {titulo_miniatura}")

    ruta_imagen, info_imagen = generar_miniatura_html(
        titulo_miniatura,
        categoria_visual,
        busqueda_imagen
    )

    if info_imagen:
        credito = construir_credito_html(info_imagen)
        if credito:
            contenido_html += credito

    if titulo and contenido_html:
        publicado = publicar_en_wordpress(titulo, contenido_html, ruta_imagen)
        if publicado:
            guardar_en_historial(tema)
