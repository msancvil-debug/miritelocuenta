import requests
import json
import os
import smtplib
import time
import textwrap
import xml.etree.ElementTree as ET
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from PIL import Image, ImageDraw, ImageFont

# Limpieza estricta de variables de entorno
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
GMAIL_USER = (os.environ.get("GMAIL_USER") or "").strip()
GMAIL_APP_PASS = (os.environ.get("GMAIL_APP_PASS") or "").strip().replace(" ", "")
WP_SECRET_EMAIL = (os.environ.get("WP_SECRET_EMAIL") or "").strip()

print("--- DIAGNÓSTICO DE VARIABLES DE ENTORNO ---")
print(f"GEMINI_API_KEY: {'Detectada' if GEMINI_API_KEY else '❌ FALTA EN GITHUB SECRETS'}")
print(f"GMAIL_USER: {GMAIL_USER if GMAIL_USER else '❌ FALTA EN GITHUB SECRETS'}")
print(f"GMAIL_APP_PASS: {'Detectada' if GMAIL_APP_PASS else '❌ FALTA EN GITHUB SECRETS'}")
print(f"WP_SECRET_EMAIL: {WP_SECRET_EMAIL if WP_SECRET_EMAIL else '❌ FALTA EN GITHUB SECRETS'}")

if not all([GEMINI_API_KEY, GMAIL_USER, GMAIL_APP_PASS, WP_SECRET_EMAIL]):
    raise Exception("ERROR CRÍTICO: Falta una o varias variables en GitHub Secrets.")

HISTORIAL_FILE = "historial_temas.json"
FEEDS_TENDENCIAS = [
    "https://news.google.com/rss/search?q=viral+OR+tiktok+OR+telecinco+OR+reality&hl=es&gl=ES&ceid=ES:es",
    "https://20minutos.es/rss/"
]

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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

def obtener_modelos_disponibles():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    modelos = []
    try:
        res = requests.get(url, timeout=15)
        if res.status_code == 200:
            data = res.json()
            for m in data.get("models", []):
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods:
                    name = m["name"].replace("models/", "")
                    modelos.append(name)
    except Exception as e:
        print(f"⚠️ Excepción al consultar modelos: {e}")

    if not modelos:
        modelos = ["gemini-2.0-flash", "gemini-1.5-flash"]
    return modelos

def generar_articulo_miri(tema_viral):
    prompt = f"""
    Eres la redactora principal del proyecto "Miri te lo cuenta", un portal sobre tendencias de internet, vídeos virales, reality shows y cultura pop en redes sociales (TikTok, X, Instagram, YouTube).

    Escribe un artículo ameno, explicativo, cotilla y optimizado para SEO sobre el siguiente tema viral:
    "{tema_viral}"

    REQUISITOS DEL ARTÍCULO Y ESTÉTICA NEO-BRUTALISTA (INLINE STYLES):
    1. Tono: Fresco, cercano, directo y explicativo ("Te lo cuento detalladamente").
    2. Todo el HTML DEBE llevar estilos en línea (inline styles):
       - Paleta de colores: Fondo Marfil (#FFF7EF), Bordes Negros (#161616), Amarillo (#FFD84D), Coral (#F04438).
       - Cajas destacadas: <div style="background-color: #FFD84D; border: 3px solid #161616; border-radius: 12px; padding: 16px; margin: 20px 0; box-shadow: 4px 4px 0px #161616;">
       - Títulos h2: <h2 style="font-size: 22px; font-weight: 800; color: #161616; background-color: #FFF7EF; border-left: 6px solid #F04438; padding: 8px 12px; margin-top: 25px;">
       - Títulos h3: <h3 style="font-size: 18px; font-weight: 700; color: #161616; margin-top: 20px;">
       - Texto normal: <p style="font-size: 16px; line-height: 1.6; color: #161616; margin-bottom: 15px;">
       - Sección FAQ al final: <div style="background-color: #FFF7EF; border: 3px solid #161616; border-radius: 12px; padding: 18px; margin-top: 30px; box-shadow: 4px 4px 0px #161616;">
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
        "generationConfig": {"response_mime_type": "application/json"}
    }

    modelos = obtener_modelos_disponibles()
    ultimo_error = ""

    for modelo in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        print(f"🤖 Generando artículo con modelo: {modelo}...")

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=40)
            if response.status_code == 200:
                res_data = response.json()
                raw_text = res_data['candidates'][0]['content']['parts'][0]['text'].strip()
                if raw_text.startswith("```"):
                    raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                articulo = json.loads(raw_text)
                print(f"✅ Artículo generado con éxito ({modelo}).")
                return articulo["titulo"], articulo["contenido_html"]
        except Exception as e:
            ultimo_error = str(e)

    raise Exception(f"ERROR CRÍTICO: No se pudo generar el artículo. {ultimo_error}")

def crear_imagen_destacada(titulo):
    """
    Descarga una imagen libre de derechos de Unsplash/Picsum,
    y dibuja encima la tarjeta Neo-brutalista con el titular.
    """
    print("🎨 Creando imagen destacada con titular para redes sociales...")
    width, height = 1200, 630  # Medidas estándar para previas de redes (Open Graph)
    
    # 1. Obtener imagen de fondo de stock (libre de derechos)
    try:
        url_stock = "[https://picsum.photos/1200/630](https://picsum.photos/1200/630)"
        res = requests.get(url_stock, headers=HEADERS_BROWSER, timeout=15)
        if res.status_code == 200:
            from io import BytesIO
            bg_img = Image.open(BytesIO(res.content)).convert("RGBA")
        else:
            bg_img = Image.new("RGBA", (width, height), (255, 247, 239, 255))
    except Exception:
        bg_img = Image.new("RGBA", (width, height), (255, 247, 239, 255))

    # 2. Aplicar capa oscura sobre la foto para mejorar lectura
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 120))
    img = Image.alpha_composite(bg_img, overlay)
    draw = ImageDraw.Draw(img)

    # 3. Dibujar caja Neo-brutalista (Amarillo #FFD84D con borde negro)
    margin = 50
    box_x1, box_y1 = margin, 120
    box_x2, box_y2 = width - margin, height - 80

    # Sombra negra de la caja
    draw.rectangle([box_x1 + 8, box_y1 + 8, box_x2 + 8, box_y2 + 8], fill=(22, 22, 22, 255))
    # Caja amarilla principal
    draw.rectangle([box_x1, box_y1, box_x2, box_y2], fill=(255, 216, 77, 255), outline=(22, 22, 22, 255), width=5)

    # 4. Dibujar distintivo superior de Marca: "MIRI TE LO CUENTA | TENDENCIAS"
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

    # 5. Formatear y envolver el Titular para que quepa en la caja
    lineas = textwrap.wrap(titulo, width=38)
    texto_formateado = "\n".join(lineas[:4]) # Máximo 4 líneas

    draw.multiline_text((box_x1 + 30, box_y1 + 35), texto_formateado, fill=(22, 22, 22, 255), font=font_title, spacing=12)

    # 6. Guardar imagen resultante
    img_filename = "miniatura_destacada.jpg"
    img.convert("RGB").save(img_filename, "JPEG", quality=90)
    print("🖼️ Miniatura con titular creada correctamente.")
    return img_filename

def enviar_por_email_a_wordpress(titulo, contenido_html, ruta_imagen):
    print(f"📧 Preparando envío de correo con miniatura adjunta...")
    
    # Para que la imagen también aparezca dentro del cuerpo del artículo
    html_con_imagen = f"""
    <div style="margin-bottom: 25px; text-align: center;">
      <img src="cid:miniatura_header" style="max-width: 100%; height: auto; border: 3px solid #161616; border-radius: 12px; box-shadow: 4px 4px 0px #161616;" alt="{titulo}" />
    </div>
    {contenido_html}
    """

    msg = MIMEMultipart('related')
    msg['From'] = GMAIL_USER
    msg['To'] = WP_SECRET_EMAIL
    msg['Subject'] = titulo

    msg_alt = MIMEMultipart('alternative')
    msg.attach(msg_alt)
    msg_alt.attach(MIMEText(html_con_imagen, 'html'))

    # Adjuntar archivo de imagen
    if os.path.exists(ruta_imagen):
        with open(ruta_imagen, 'rb') as f:
            img_data = f.read()
            img_part = MIMEImage(img_data)
            img_part.add_header('Content-ID', '<miniatura_header>')
            img_part.add_header('Content-Disposition', 'inline', filename=os.path.basename(ruta_imagen))
            msg.attach(img_part)

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASS)
        server.send_message(msg)
        server.quit()
        print("🎉 ÉXITO TOTAL: Artículo e imagen enviados a WordPress.")
        return True
    except Exception as e:
        raise Exception(f"ERROR AL ENVIAR CORREO: {e}")

if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()
    if not tema:
        print("⚠️ No se encontró tema nuevo. Usando tema por defecto...")
        tema = "Polémica y tendencias virales en TikTok de esta semana"

    print(f"🔥 Tema seleccionado: {tema}")
    titulo, contenido_html = generar_articulo_miri(tema)
    ruta_imagen = crear_imagen_destacada(titulo)
    enviar_por_email_a_wordpress(titulo, contenido_html, ruta_imagen)
    guardar_en_historial(tema)
