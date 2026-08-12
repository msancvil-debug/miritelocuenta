import requests
import json
import os
import time
import random
import html
import xml.etree.ElementTree as ET

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
        except: pass
    return None

def obtener_modelos_disponibles():
    url_list = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    try:
        res = requests.get(url_list, timeout=10)
        if res.status_code == 200:
            models_data = res.json().get("models", [])
            modelos_validos = [m.get("name", "").replace("models/", "") for m in models_data if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in m.get("name", "").lower()]
            if modelos_validos: return modelos_validos
    except: pass
    return ["gemini-1.5-flash", "gemini-1.5-pro"]

def generar_articulo_miri(tema_viral):
    modelos = obtener_modelos_disponibles()
    prompt = f"""
    Eres la redactora principal del portal "Miri te lo cuenta".
    Escribe un artículo ameno, cotilla y fresco sobre la tendencia: "{tema_viral}"
    Responde ÚNICAMENTE con un JSON válido que contenga el título, el contenido en HTML y una palabra clave corta en inglés para buscar una foto relacionada (ejemplo: 'celebrity', 'red carpet', 'party', 'news'):
    {{
      "titulo": "Título atractivo sin comillas",
      "contenido_html": "<p>Texto del artículo...</p>",
      "keyword_foto": "party"
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
                if raw_text.startswith("`"): raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                articulo = json.loads(raw_text)
                return (
                    html.unescape(articulo["titulo"]).strip().strip('"').strip("'"),
                    articulo["contenido_html"],
                    articulo.get("keyword_foto", "news")
                )
        except: continue
    raise Exception("❌ Error: La API de Gemini no pudo generar el artículo.")

def generar_miniatura_canva_pro(titulo, keyword_foto):
    """Se conecta mediante la API de Canva para autorrellenar tu plantilla Pro de marca."""
    if not (CANVA_CLIENT_ID and CANVA_CLIENT_SECRET and CANVA_TEMPLATE_ID):
        print("⚠️ Faltan credenciales de Canva Connect en Secrets. Usando respaldo local...")
        return None

    print("🎨 Conectando con la API de Canva Pro para modificar tu plantilla...")
    
    # 1. Obtener token de acceso de Canva (OAuth 2.0 Client Credentials)
    token_url = "https://api.canva.com/rest/v1/oauth/token"
    auth_data = {
        "grant_type": "client_credentials",
        "client_id": CANVA_CLIENT_ID,
        "client_secret": CANVA_CLIENT_SECRET
    }
    try:
        r_token = requests.post(token_url, data=auth_data, timeout=15)
        if r_token.status_code != 200:
            print(f"❌ Error de autenticación en Canva: {r_token.text}")
            return None
        access_token = r_token.json().get("access_token")
    except Exception as e:
        print(f"❌ Excepción al conectar con Canva: {e}")
        return None

    headers_canva = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # 2. Iniciar el trabajo de autorrelleno (Autofill Job) en tu plantilla de Canva
    autofill_url = "https://api.canva.com/rest/v1/autofills"
    payload_autofill = {
        "brand_template_id": CANVA_TEMPLATE_ID,
        "data": {
            "title": {"type": "text", "text": titulo},
            "background": {"type": "image", "asset_id": keyword_foto} # Canva procesa la inserción dinámica
        }
    }
    
    try:
        r_job = requests.post(autofill_url, headers=headers_canva, json=payload_autofill, timeout=20)
        if r_job.status_code not in [200, 201, 202]:
            print(f"❌ Error al iniciar el autofill en Canva: {r_job.text}")
            return None
        
        job_id = r_job.json().get("job", {}).get("id")
        
        # 3. Esperar a que Canva termine de renderizar el diseño personalizado
        print("⏳ Esperando a que Canva renderice la imagen corporativa...")
        for _ in range(10):
            time.sleep(3)
            r_check = requests.get(f"https://api.canva.com/rest/v1/autofills/{job_id}", headers=headers_canva, timeout=15)
            if r_check.status_code == 200:
                job_data = r_check.json().get("job", {})
                status = job_data.get("status")
                if status == "success":
                    design_url = job_data.get("result", {}).get("url")
                    # Descargar el diseño resultante de Canva
                    img_res = requests.get(design_url, timeout=20)
                    if img_res.status_code == 200:
                        ruta_local = "miniatura_canva_final.jpg"
                        with open(ruta_local, "wb") as f:
                            f.write(img_res.content)
                        print("✅ ¡Miniatura de Canva Pro generada y descargada con éxito!")
                        return ruta_local
                elif status == "failed":
                    print("❌ El renderizado de Canva ha fallado.")
                    break
    except Exception as e:
        print(f"❌ Error en el proceso de Canva API: {e}")
        
    return None

def publicar_en_wordpress(titulo, contenido_html, ruta_imagen):
    if not (WP_URL and WP_USER and WP_APP_PASS):
        raise Exception("❌ Faltan credenciales de WordPress en GitHub Secrets.")

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

    print("🚀 Publicando artículo completo con su imagen en WordPress...")
    url_posts = f"{WP_URL}/wp-json/wp/v2/posts"
    payload = {"title": titulo, "content": contenido_html, "status": "publish"}
    if media_id:
        payload["featured_media"] = media_id

    r_post = requests.post(url_posts, json=payload, headers={"Content-Type": "application/json"}, auth=(WP_USER, WP_APP_PASS), timeout=30)
    
    if r_post.status_code in [200, 201]:
        print("🎉 ¡ÉXITO TOTAL! Artículo e imagen publicados en WordPress.")
        return True
    else:
        raise Exception(f"❌ Error al publicar (Código {r_post.status_code}): {r_post.text}")

if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()
    if not tema:
        print("⚠️ Sin temas nuevos. Usando tema de prueba...")
        tema = "Polémica viral de la semana en redes sociales"

    print(f"🔥 Tema seleccionado: {tema}")
    titulo, contenido_html, keyword_foto = generar_articulo_miri(tema)
    
    # Genera la miniatura conectando con tu Canva Pro
    ruta_imagen = generar_miniatura_canva_pro(titulo, keyword_foto)

    if titulo and contenido_html:
        publicado = publicar_en_wordpress(titulo, contenido_html, ruta_imagen)
        if publicado:
            guardar_en_historial(tema)
