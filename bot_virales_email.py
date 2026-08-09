import requests
import json
import os
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import xml.etree.ElementTree as ET

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS")
WP_SECRET_EMAIL = os.environ.get("WP_SECRET_EMAIL")

HISTORIAL_FILE = "historial_temas.json"
FEEDS_TENDENCIAS = [
    "https://trends.google.com/trending/rss?geo=ES",
    "https://news.google.com/rss/search?q=viral+OR+telecinco+OR+tiktok+OR+reality&hl=es&gl=ES&ceid=ES:es"
]

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Lista de modelos de reserva por si uno agota la cuota diaria gratuita
MODELOS_RESERVA = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash"
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
            print(f"⚠️ Error procesando feed {feed_url}: {e}")
    return None

def generar_articulo_miri(tema_viral):
    prompt = f"""
    Eres la redactora principal del proyecto "Miri te lo cuenta", un portal sobre tendencias de internet, vídeos virales, reality shows y cultura pop en redes sociales (TikTok, X, Instagram, YouTube).

    Escribe un artículo ameno, explicativo, cotilla y optimizado para SEO sobre el siguiente tema viral:
    "{tema_viral}"

    REQUISITOS DEL ARTÍCULO:
    1. Tono: Fresco, cercano, directo y explicativo ("Te lo cuento detalladamente").
    2. Estructura SEO obligatoria:
       - Título pegadizo e irresistible para Google / Google Discover.
       - Introducción enganchante.
       - Secciones con etiquetas HTML <h2> y <h3> (¿Qué ha pasado?, ¿Por qué se ha hecho viral?, Reacciones en redes sociales).
       - Una sección final con 2 preguntas frecuentes (FAQ) usando HTML para posicionar en Google.
    3. Responde ÚNICAMENTE con un objeto JSON válido (sin marcas markdown ni código extra).
    
    Formato JSON esperado:
    {{
      "titulo": "Título SEO aquí",
      "contenido_html": "<p>Texto de introducción...</p><h2>¿Qué ha pasado?</h2><p>Contenido...</p>"
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

    # Probamos los modelos uno por uno si el anterior falla o no tiene cuota
    for modelo in MODELOS_RESERVA:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        print(f"🤖 Intentando redactar con el modelo: {modelo}...")

        max_intentos = 2
        for intento in range(1, max_intentos + 1):
            response = requests.post(url, headers=headers, json=payload, timeout=40)
            
            if response.status_code == 200:
                res_data = response.json()
                raw_text = res_data['candidates'][0]['content']['parts'][0]['text'].strip()
                
                if raw_text.startswith("```"):
                    raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                        
                try:
                    articulo = json.loads(raw_text)
                    return articulo["titulo"], articulo["contenido_html"]
                except json.JSONDecodeError as e:
                    raise Exception(f"Error al parsear la respuesta JSON de Gemini: {e}\nTexto recibido:\n{raw_text}")
            
            elif response.status_code == 429:
                print(f"⚠️ Límite de cuota en {modelo} (429). Probando reintento corto...")
                time.sleep(15)
                ultimo_error = response.text
            else:
                print(f"⚠️ El modelo {modelo} devolvió error {response.status_code}. Pasando al modelo de reserva...")
                ultimo_error = response.text
                break

    raise Exception(f"No se pudo generar el artículo con ningún modelo. Última respuesta de Google: {ultimo_error}")

def enviar_por_email_a_wordpress(titulo, contenido_html):
    if not GMAIL_USER or not GMAIL_APP_PASS or not WP_SECRET_EMAIL:
        print("❌ Error: Faltan variables de entorno para el envío de correo (GMAIL_USER, GMAIL_APP_PASS o WP_SECRET_EMAIL).")
        return False

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
        print("✅ Artículo enviado con éxito a WordPress vía Email")
        return True
    except Exception as e:
        print(f"❌ Error enviando email a WordPress: {e}")
        return False

if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()
    if tema:
        print(f"🔥 Tema viral detectado: {tema}")
        titulo, contenido_html = generar_articulo_miri(tema)
        if enviar_por_email_a_wordpress(titulo, contenido_html):
            guardar_en_historial(tema)
    else:
        print("ℹ️ No se encontraron nuevos temas virales sin procesar en este ciclo.")
