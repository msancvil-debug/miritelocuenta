import requests
import json
import os
import time
import html
import random
import xml.etree.ElementTree as ET
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
                if raw_text.startswith("`"): raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                articulo = json.loads(raw_text)
                return html.unescape(articulo["titulo"]).strip().strip('"').strip("'"), articulo["contenido_html"]
        except: continue
    raise Exception("❌ Error: La API de Gemini no pudo generar el artículo.")

def generar_miniatura_canva_pro_api(titulo):
    """Conecta con la API oficial de Canva y muestra el error exacto si falla."""
    if not (CANVA_CLIENT_ID and CANVA_CLIENT_SECRET and CANVA_TEMPLATE_ID):
        print("❌ ERROR: Faltan credenciales de Canva (CANVA_CLIENT_ID, CANVA_CLIENT_SECRET o CANVA_TEMPLATE_ID) en GitHub Secrets.")
        return None

    print("🎨 Autenticando con la API de Canva Pro...")
    token_url = "https://api.canva.com/rest/v1/oauth/token"
    auth_data = {
        "grant_type": "client_credentials",
        "client_id": CANVA_CLIENT_ID,
        "client_secret": CANVA_CLIENT_SECRET
    }
    
    try:
        r_token = requests.post(token_url, data=auth_data, timeout=15)
        if r_token.status_code != 200:
            print(f"❌ ERROR DE AUTENTICACIÓN EN CANVA (Código {r_token.status_code}): {r_token.text}")
            return None
        access_token = r_token.json().get("access_token")
    except Exception as e:
        print(f"❌ Excepción al conectar con el token de Canva: {e}")
        return None

    headers_canva = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # 1. Descargar foto temática de fondo
    print("📥 Descargando imagen de fondo temática...")
    foto_bytes = None
    try:
        url_foto = random.choice(FOTOS_STOCK_TEMATICAS)
        r_foto = requests.get(url_foto, headers=HEADERS_BROWSER, timeout=15)
        if r_foto.status_code == 200:
            foto_bytes = r_foto.content
    except Exception as e:
        print(f"⚠️ No se pudo descargar foto temática: {e}")

    asset_id_foto = None
    if foto_bytes:
        print("🚀 Subiendo imagen a Canva Assets...")
        upload_init_url = "https://api.canva.com/rest/v1/uploads"
        headers_upload = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
            "X-Asset-Name": "fondo_noticia.jpg"
        }
        try:
            r_up = requests.post(upload_init_url, headers=headers_upload, data=foto_bytes, timeout=30)
            if r_up.status_code in [200, 201, 202]:
                up_data = r_up.json()
                asset_id_foto = up_data.get("job", {}).get("asset", {}).get("id") or up_data.get("asset", {}).get("id")
                print(f"✅ Imagen subida a Canva con Asset ID: {asset_id_foto}")
            else:
                print(f"⚠️ Error al subir imagen a Canva Assets (Código {r_up.status_code}): {r_up.text}")
        except Exception as e:
            print(f"⚠️ Excepción subiendo asset a Canva: {e}")

    # 2. Lanzar el trabajo de Autofill en la plantilla real
    print(f"🪄 Aplicando Autofill en la plantilla de Canva (ID: {CANVA_TEMPLATE_ID})...")
    autofill_url = "https://api.canva.com/rest/v1/autofills"
    
    data_payload = {
        "title": {"type": "text", "text": titulo}
    }
    if asset_id_foto:
        data_payload["background"] = {"type": "image", "asset_id": asset_id_foto}

    payload_autofill = {
        "brand_template_id": CANVA_TEMPLATE_ID,
        "data": data_payload
    }

    try:
        r_job = requests.post(autofill_url, headers=headers_canva, json=payload_autofill, timeout=20)
        if r_job.status_code not in [200, 201, 202]:
            print(f"❌ ERROR CRÍTICO EN CANVA AUTOFILL (Código {r_job.status_code}): {r_job.text}")
            return None
        
        job_json = r_job.json()
        job_id = job_json.get("job", {}).get("id")
        print(uto_id := f"⏳ Trabajo de Canva iniciado (Job ID: {job_id}). Esperando renderizado...")
        
        # 3. Esperar a que Canva renderice el diseño
        for _ in range(15):
            time.sleep(4)
            r_check = requests.get(f"https://api.canva.com/rest/v1/autofills/{job_id}", headers=headers_canva, timeout=15)
            if r_check.status_code == 200:
                job_data = r_check.json().get("job", {})
                status = job_data.get("status")
                print(f" Estado actual de Canva: {status}")
                if status == "success":
                    design_url = job_data.get("result", {}).get("url")
                    if design_url:
                        img_res = requests.get(design_url, timeout=20)
                        if img_res.status_code == 200:
                            ruta_local = "miniatura_canva_pro.jpg"
                            with open(ruta_local, "wb") as f:
                                f.write(img_res.content)
                            print("✅ ¡Miniatura de tu plantilla real de Canva generada y descargada con éxito!")
                            return ruta_local
                elif status == "failed":
                    print(f"❌ ERROR DETALLADO DE CANVA RENDER: {json.dumps(job_data, indent=2)}")
                    break
    except Exception as e:
        print(f"❌ Excepción grave en el proceso de Canva API: {e}")

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
            print(f"✅ Imagen subida correctamente (Media ID: {media_id})")
        else:
            print(f"⚠️ Aviso al subir imagen a WordPress: {r_media.text}")

    print("🚀 Publicando artículo completo con su imagen en WordPress...")
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
        raise Exception(f"❌ Error al publicar en WordPress (Código {r_post.status_code}): {r_post.text}")

if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()
    if not tema:
        print("⚠️ Sin temas nuevos. Usando tema de prueba...")
        tema = "Polémica viral de la semana en redes sociales"

    print(f"🔥 Tema seleccionado: {tema}")
    titulo, contenido_html = generar_articulo_miri(tema)
    
    # Modifica tu plantilla real de Canva Pro (Título + Fondo dinámico)
    ruta_imagen = generar_miniatura_canva_pro_api(titulo)

    if titulo and contenido_html:
        publicado = publicar_en_wordpress(titulo, contenido_html, ruta_imagen)
        if publicado:
            guardar_en_historial(tema)
