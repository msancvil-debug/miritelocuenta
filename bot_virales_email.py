import requests
import json
import os
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import xml.etree.ElementTree as ET

# 1. COMPROBACIÓN DE VARIABLES DE ENTORNO
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
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

# Lista de modelos de reserva por orden de preferencia
MODELOS_GEMINI = [
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-2.5-flash"
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

def generar_articulo_miri(tema_viral):
    prompt = f"""
    Eres la redactora principal del proyecto "Miri te lo cuenta", un portal sobre tendencias de internet, vídeos virales, reality shows y cultura pop.
    Escribe un artículo ameno, explicativo, cotilla y optimizado para SEO sobre el siguiente tema viral:
    "{tema_viral}"

    REQUISITOS DEL ARTÍCULO:
    1. Tono: Fresco, cercano, directo y explicativo ("Te lo cuento detalladamente").
    2. Utiliza etiquetas HTML limpias (<p>, <h2>, <h3>, <ul>, <li>, <strong>).

    Responde ÚNICAMENTE con un objeto JSON válido con esta estructura exacta:
    {{
      "titulo": "Título SEO aquí",
      "contenido_html": "<p>Texto de introducción...</p>"
    }}
    """
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }

    ultimo_error = ""

    # Probamos sucesivamente con los modelos activos
    for modelo in MODELOS_GEMINI:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        try:
            print(f"🤖 Intentando generar contenido con {modelo}...")
            response = requests.post(url, headers=headers, json=payload, timeout=40)
            if response.status_code == 200:
                raw_text = response.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                if raw_text.startswith("```"):
                    raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                articulo = json.loads(raw_text)
                print(f"✅ Generación exitosa usando {modelo}")
                return articulo["titulo"], articulo["contenido_html"]
            else:
                ultimo_error = f"HTTP {response.status_code}: {response.text}"
                print(f"⚠️ {modelo} devolvió error {response.status_code}. Intentando siguiente modelo...")
        except Exception as e:
            ultimo_error = str(e)
            print(f"⚠️ Excepción con {modelo}: {e}. Intentando siguiente...")

    raise Exception(f"Error crítico Gemini API: Ningún modelo funcionó. Último error: {ultimo_error}")

def enviar_por_email_a_wordpress(titulo, contenido_html):
    if not GMAIL_USER or not GMAIL_APP_PASS or not WP_SECRET_EMAIL:
        raise Exception("Faltan variables de entorno: GMAIL_USER, GMAIL_APP_PASS o WP_SECRET_EMAIL.")

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
        print("🎉 ÉXITO: El correo fue enviado desde Gmail a WordPress.")
        return True
    except Exception as e:
        raise Exception(f"Error enviando correo a WordPress: {e}")

if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()
    if not tema:
        print("⚠️ No se encontró un tema nuevo en los feeds. Usando tema por defecto...")
        tema = "Polémica y tendencias virales en TikTok de esta semana"

    print(f"🔥 Tema seleccionado: {tema}")
    titulo, contenido_html = generar_articulo_miri(tema)

    if titulo and contenido_html:
        enviado = enviar_por_email_a_wordpress(titulo, contenido_html)
        if enviado:
            guardar_en_historial(tema)
