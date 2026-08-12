import requests
import json
import os
import time
import html
import xml.etree.ElementTree as ET

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
        except Exception as e:
            pass
    return None

def obtener_modelos_disponibles():
    if not GEMINI_API_KEY:
        print("⚠️ ALERTA: No se ha detectado GEMINI_API_KEY. El programa fallará.")
        return ["gemini-1.5-flash"]
        
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
        print(f"🤖 Intentando generar artículo con el modelo: {modelo}...")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=40)
            if response.status_code == 200:
                raw_text = response.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                
                # Súper limpieza de formato por si Gemini devuelve basura alrededor del JSON
                raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                if raw_text.startswith("`"): raw_text = raw_text.strip("`")
                
                articulo = json.loads(raw_text)
                titulo_limpio = html.unescape(articulo["titulo"]).strip().strip('"').strip("'")
                print("✅ Artículo generado correctamente por Gemini.")
                return titulo_limpio, articulo["contenido_html"]
            else:
                print(f"❌ Error devuelto por Gemini ({response.status_code}): {response.text}")
        except Exception as e:
            print(f"⚠️ Fallo al leer la respuesta del modelo {modelo}: {e}")
            continue
            
    raise Exception("❌ Error crítico: La API de Gemini falló. Revisa los mensajes de arriba para ver el error exacto.")

def publicar_solo_texto_wordpress(titulo, contenido_html):
    if not (WP_URL and WP_USER and WP_APP_PASS):
        raise Exception("❌ Faltan credenciales de WordPress en GitHub Secrets.")

    print(f"🚀 Publicando artículo (SIN IMAGEN) vía API REST en {WP_URL} para que Make lo detecte...")
    url_posts = f"{WP_URL}/wp-json/wp/v2/posts"
    payload = {"title": titulo, "content": contenido_html, "status": "publish"}
    r_post = requests.post(url_posts, json=payload, headers={"Content-Type": "application/json"}, auth=(WP_USER, WP_APP_PASS), timeout=30)
    
    if r_post.status_code in [200, 201]:
        print("🎉 ¡ÉXITO! Entrada publicada. Ahora Make entrará en acción para añadir la imagen de Canva.")
        return True
    else:
        raise Exception(f"❌ ERROR DE PUBLICACIÓN (Código {r_post.status_code}): {r_post.text}")

if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()
    if not tema:
        print("⚠️ Sin temas nuevos. Usando tema de prueba...")
        tema = "Polémica viral de la semana en redes sociales"

    print(f"🔥 Tema seleccionado: {tema}")
    titulo, contenido_html = generar_articulo_miri(tema)

    if titulo and contenido_html:
        publicado = publicar_solo_texto_wordpress(titulo, contenido_html)
        if publicado:
            guardar_en_historial(tema)
