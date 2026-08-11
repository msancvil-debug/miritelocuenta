import requests
import json
import os
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import xml.etree.ElementTree as ET

# 1. Comprobación estricta de variables de entorno
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS")
WP_SECRET_EMAIL = os.environ.get("WP_SECRET_EMAIL")

print("--- DIAGNÓSTICO DE VARIABLES DE ENTORNO ---")
print(f"GEMINI_API_KEY: {'Detectada' if GEMINI_API_KEY else '❌ FALTA EN GITHUB SECRETS'}")
print(f"GMAIL_USER: {GMAIL_USER if GMAIL_USER else '❌ FALTA EN GITHUB SECRETS'}")
print(f"GMAIL_APP_PASS: {'Detectada' if GMAIL_APP_PASS else '❌ FALTA EN GITHUB SECRETS'}")
print(f"WP_SECRET_EMAIL: {WP_SECRET_EMAIL if WP_SECRET_EMAIL else '❌ FALTA EN GITHUB SECRETS'}")

if not all([GEMINI_API_KEY, GMAIL_USER, GMAIL_APP_PASS, WP_SECRET_EMAIL]):
    raise Exception("ERROR CRÍTICO: Falta una o varias variables en GitHub Secrets. Revisa los nombres exactos.")

HISTORIAL_FILE = "historial_temas.json"
FEEDS_TENDENCIAS = [
    "https://news.google.com/rss/search?q=viral+OR+tiktok+OR+telecinco+OR+reality&hl=es&gl=ES&ceid=ES:es",
    "https://20minutos.es/rss/"
]

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Amplia lista de modelos activos para probar automáticamente en orden
MODELOS_RESERVA = [
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-pro",
    "gemini-2.5-pro",
    "gemini-1.5-flash-8b",
    "gemini-1.5-flash-latest"
]

API_VERSIONS = ["v1beta", "v1"]

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
            print(f"📡 Intentando leer noticias de: {feed_url}...")
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

def generar_articulo_miri(tema_viral):
    prompt = f"""
    Eres la redactora principal del proyecto "Miri te lo cuenta", un portal sobre tendencias de internet, vídeos virales, reality shows y cultura pop en redes sociales (TikTok, X, Instagram, YouTube).

    Escribe un artículo ameno, explicativo, cotilla y optimizado para SEO sobre el siguiente tema viral:
    "{tema_viral}"

    REQUISITOS DEL ARTÍCULO Y ESTÉTICA NEO-BRUTALISTA (INLINE STYLES):
    1. Tono: Fresco, cercano, directo y explicativo ("Te lo cuento detalladamente").
    2. Todo el HTML DEBE llevar estilos en línea (inline styles) para mantener la estética Neo-brutalista de la web:
       - Paleta de colores: Fondo Marfil (#FFF7EF), Bordes Negros (#161616), Amarillo (#FFD84D), Coral (#F04438).
       - Cajas destacadas (Resumen o Claves): <div style="background-color: #FFD84D; border: 3px solid #161616; border-radius: 12px; padding: 16px; margin: 20px 0; box-shadow: 4px 4px 0px #161616;">
       - Títulos h2: <h2 style="font-size: 22px; font-weight: 800; color: #161616; background-color: #FFF7EF; border-left: 6px solid #F04438; padding: 8px 12px; margin-top: 25px;">
       - Títulos h3: <h3 style="font-size: 18px; font-weight: 700; color: #161616; margin-top: 20px;">
       - Texto normal en párrafos: <p style="font-size: 16px; line-height: 1.6; color: #161616; margin-bottom: 15px;">
       - Sección FAQ al final en una caja Neo-brutalista: <div style="background-color: #FFF7EF; border: 3px solid #161616; border-radius: 12px; padding: 18px; margin-top: 30px; box-shadow: 4px 4px 0px #161616;">
    3. Responde ÚNICAMENTE con un objeto JSON válido (sin marcas markdown ni código extra).
    
    Formato JSON esperado:
    {{
      "titulo": "Título SEO aquí",
      "contenido_html": "<p style='...'>Texto de introducción...</p><div style='...'>Resumen...</div>"
    }}
    """
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }

    ultimo_error = ""

    # Probamos las versiones de API y la lista de modelos hasta dar con uno activo
    for api_version in API_VERSIONS:
        for modelo in MODELOS_RESERVA:
            url = f"https://generativelanguage.googleapis.com/{api_version}/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
            print(f"🤖 Probando [{api_version}] con modelo IA: {modelo}...")

            try:
                response = requests.post(url, headers=headers, json=payload, timeout=40)
                if response.status_code == 200:
                    res_data = response.json()
                    raw_text = res_data['candidates'][0]['content']['parts'][0]['text'].strip()
                    if raw_text.startswith("```"):
                        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                    articulo = json.loads(raw_text)
                    print(f"✅ ¡Éxito con {modelo} [{api_version}]! Artículo generado por la IA.")
                    return articulo["titulo"], articulo["contenido_html"]
                elif response.status_code == 429:
                    print(f"⚠️ Cuota temporal alcanzada en {modelo}. Probando siguiente modelo...")
                    ultimo_error = f"Código 429 (Límite de cuota): {response.text}"
                    time.sleep(5)
                else:
                    ultimo_error = f"Código {response.status_code}: {response.text}"
                    print(f"⚠️ [{api_version}] {modelo} devolvió {response.status_code}. Probando siguiente...")
            except Exception as e:
                ultimo_error = str(e)
                print(f"⚠️ Excepción con {modelo}: {e}")

    raise Exception(f"ERROR CRÍTICO: Ningún modelo de Gemini respondió con 200 OK. Último error: {ultimo_error}")

def enviar_por_email_a_wordpress(titulo, contenido_html):
    print(f"📧 Preparando envío de correo...")
    print(f"   De (Remitente): {GMAIL_USER}")
    print(f"   Para (WordPress secreto): {WP_SECRET_EMAIL}")
    print(f"   Asunto: {titulo}")

    msg = MIMEMultipart()
    msg['From'] = GMAIL_USER
    msg['To'] = WP_SECRET_EMAIL
    msg['Subject'] = titulo
    msg.attach(MIMEText(contenido_html, 'html'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASS)
        server.send_message(msg)
        server.quit()
        print("🎉 EXITO TOTAL: El correo fue enviado desde Gmail a WordPress.")
        return True
    except Exception as e:
        raise Exception(f"ERROR CRÍTICO AL ENVIAR CORREO DESDE GMAIL: {e}")

if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()
    if not tema:
        print("⚠️ No se encontró tema nuevo en los feeds. Usando tema por defecto...")
        tema = "Polémica y tendencias virales en TikTok de esta semana"

    print(f"🔥 Tema seleccionado: {tema}")
    titulo, contenido_html = generar_articulo_miri(tema)
    enviar_por_email_a_wordpress(titulo, contenido_html)
    guardar_en_historial(tema)
