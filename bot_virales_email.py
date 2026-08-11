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

# Fuentes RSS estables que no bloquean servidores cloud
FEEDS_TENDENCIAS = [
    "https://news.google.com/rss/search?q=viral+OR+tiktok+OR+telecinco+OR+reality&hl=es&gl=ES&ceid=ES:es",
    "https://20minutos.es/rss/"
]

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

MODELOS_RESERVA = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-exp"
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
            print(f"📡 Leyendo noticias de: {feed_url}...")
            res = requests.get(feed_url, headers=HEADERS_BROWSER, timeout=15)
            if res.status_code == 200:
                root = ET.fromstring(res.content)
                for item in root.findall(".//item"):
                    title_elem = item.find("title")
                    if title_elem is not None and title_elem.text:
                        title = title_elem.text.strip()
                        if title and title not in historial:
                            return title
            else:
                print(f"⚠️ El feed respondió con código: {res.status_code}")
        except Exception as e:
            print(f"⚠️ Error procesando feed {feed_url}: {e}")
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

    for modelo in MODELOS_RESERVA:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        print(f"🤖 Intentando redactar con el modelo: {modelo}...")

        for intento in range(1, 3):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=40)
                
                if response.status_code == 200:
                    res_data = response.json()
                    raw_text = res_data['candidates'][0]['content']['parts'][0]['text'].strip()
                    
                    if raw_text.startswith("```"):
                        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                            
                    articulo = json.loads(raw_text)
                    return articulo["titulo"], articulo["contenido_html"]
                
                elif response.status_code == 429:
                    print(f"⚠️ Límite de cuota en {modelo} (429). Esperando 20s...")
                    time.sleep(20)
                else:
                    break
            except Exception as e:
                print(f"⚠️ Excepción en solicitud: {e}")
                break

    print("ℹ️ La API de Google está temporalmente saturada o sin cuota.")
    return None, None

def enviar_por_email_a_wordpress(titulo, contenido_html):
    if not GMAIL_USER or not GMAIL_APP_PASS or not WP_SECRET_EMAIL:
        print("❌ Faltan variables de entorno para el envío.")
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
        print(f"❌ Error enviando email: {e}")
        return False

if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()
    
    # Si no encuentra ninguna noticia en los RSS, fuerza una de tendencia actual
    if not tema:
        print("⚠️ No se pudieron leer los RSS o se agotaron. Usando tema de tendencia automática...")
        tema = "Polémica y tendencias virales en TikTok de esta semana"

    print(f"🔥 Tema viral detectado: {tema}")
    titulo, contenido_html = generar_articulo_miri(tema)
    if titulo and contenido_html:
        if enviar_por_email_a_wordpress(titulo, contenido_html):
            guardar_en_historial(tema)
