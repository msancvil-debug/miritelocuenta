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
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

OPENVERSE_API = "https://api.openverse.org/v1/images/"
WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"

OPENVERSE_LICENSES_PERMITIDAS = {
    "cc0",
    "pdm",
    "by",
    "by-sa",
}

COLOR_NEGRO = "#161616"
COLOR_CORAL = "#F04438"
COLOR_MARFIL = "#FFF7EF"
COLOR_AMARILLO = "#FFD84D"
COLOR_GRIS = "#D8D4CF"

CATEGORIAS_VALIDAS = {
    "ESTÁ PASANDO",
    "SE HA LIADO",
    "TE PONGO EN CONTEXTO",
    "INTERNET ESTÁ HABLANDO",
    "¿QUIÉN ES?",
    "MIRI REACCIONA",
    "REALITY",
    "VIRAL",
    "INTERNET",
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
            res = requests.get(
                feed_url,
                headers=HEADERS_BROWSER,
                timeout=15
            )

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
    url_list = (
        "https://generativelanguage.googleapis.com/v1beta/models"
        f"?key={GEMINI_API_KEY}"
    )

    try:
        res = requests.get(url_list, timeout=10)

        if res.status_code == 200:
            models_data = res.json().get("models", [])

            modelos_validos = [
                m.get("name", "").replace("models/", "")
                for m in models_data
                if "generateContent" in m.get(
                    "supportedGenerationMethods", []
                )
                and "gemini" in m.get("name", "").lower()
            ]

            if modelos_validos:
                return modelos_validos

    except Exception:
        pass

    return ["gemini-1.5-flash", "gemini-1.5-pro"]


def normalizar_categoria(categoria):
    categoria = (categoria or "").strip().upper()

    equivalencias = {
        "SALSEO": "SE HA LIADO",
        "POLÉMICA": "SE HA LIADO",
        "POLEMICA": "SE HA LIADO",
        "TELECINCO": "REALITY",
        "REALITIES": "REALITY",
        "REDES": "INTERNET",
        "REDES SOCIALES": "INTERNET",
        "ACTUALIDAD": "ESTÁ PASANDO",
        "ESTA PASANDO": "ESTÁ PASANDO",
        "CONTEXTO": "TE PONGO EN CONTEXTO",
        "QUIÉN ES": "¿QUIÉN ES?",
        "QUIEN ES": "¿QUIÉN ES?",
        "REACCIÓN": "MIRI REACCIONA",
        "REACCION": "MIRI REACCIONA",
    }

    if "/" in categoria:
        partes = [p.strip() for p in categoria.split("/") if p.strip()]
        for parte in partes:
            normalizada = equivalencias.get(parte, parte)
            if normalizada in CATEGORIAS_VALIDAS:
                return normalizada

    categoria = equivalencias.get(categoria, categoria)

    if categoria not in CATEGORIAS_VALIDAS:
        categoria = "ESTÁ PASANDO"

    return categoria


def generar_articulo_miri(tema_viral):
    modelos = obtener_modelos_disponibles()

    prompt = f'''
Eres la redactora principal del portal de actualidad, entretenimiento
y cultura de Internet "Miri te lo cuenta".

Escribe un artículo ameno, fresco, dinámico y con tono de salseo sobre:
"{tema_viral}"

Responde ÚNICAMENTE con un objeto JSON válido, sin Markdown ni bloques de código.

La estructura debe ser exactamente:

{{
  "titulo": "Titular atractivo y viral para el artículo",
  "contenido_html": "<p>Primer párrafo...</p><p>Segundo párrafo...</p>",
  "titulo_miniatura": "TITULAR MUY CORTO PARA LA IMAGEN",
  "categoria_visual": "UNA CATEGORÍA DE LA LISTA",
  "busqueda_imagen": "persona, programa, plataforma o tema visual principal"
}}

REGLAS PARA titulo_miniatura:
- Debe ser distinto del título SEO si este es largo.
- Entre 3 y 9 palabras.
- Máximo 60 caracteres.
- Debe funcionar visualmente en una miniatura.
- No pongas punto final.

REGLAS PARA categoria_visual:
Escoge SOLO UNA de estas:
- ESTÁ PASANDO
- SE HA LIADO
- TE PONGO EN CONTEXTO
- INTERNET ESTÁ HABLANDO
- ¿QUIÉN ES?
- MIRI REACCIONA
- REALITY
- VIRAL
- INTERNET

REGLAS PARA busqueda_imagen:
- NO escribas una frase genérica como "news party".
- Si hay una persona conocida, usa su nombre exacto.
- Si hay un programa, reality o plataforma, usa su nombre exacto.
- Si no hay persona concreta, usa 2-5 palabras del tema visual principal.
- Puede estar en español o inglés.
'''

    headers = {"Content-Type": "application/json"}

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }

    for modelo in modelos:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{modelo}:generateContent?key={GEMINI_API_KEY}"
        )

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=40
            )

            if response.status_code == 200:
                raw_text = (
                    response.json()["candidates"][0]["content"]["parts"][0]["text"]
                    .strip()
                )

                if raw_text.startswith("```"):
                    raw_text = (
                        raw_text
                        .replace("```json", "")
                        .replace("```", "")
                        .strip()
                    )

                articulo = json.loads(raw_text)

                titulo = html.unescape(
                    articulo["titulo"]
                ).strip().strip('"').strip("'")

                contenido_html = articulo["contenido_html"]

                titulo_miniatura = html.unescape(
                    articulo.get("titulo_miniatura") or titulo
                ).strip().strip('"').strip("'")

                if len(titulo_miniatura) > 72:
                    recortado = titulo_miniatura[:69]
                    if " " in recortado:
                        recortado = recortado.rsplit(" ", 1)[0]
                    titulo_miniatura = recortado + "…"

                categoria_visual = normalizar_categoria(
                    articulo.get("categoria_visual")
                )

                busqueda_imagen = html.unescape(
                    articulo.get("busqueda_imagen") or tema_viral
                ).strip()

                return (
                    titulo,
                    contenido_html,
                    titulo_miniatura,
                    categoria_visual,
                    busqueda_imagen
                )

        except Exception as e:
            print(f"⚠️ Fallo con el modelo {modelo}: {e}")
            continue

    raise Exception(
        "❌ Error crítico: La API de Gemini no pudo generar el artículo."
    )


# ==========================================
# 4. MOTOR VISUAL INDEPENDIENTE
# ==========================================
def quitar_html(texto):
    if not texto:
        return ""

    texto = re.sub(r"<[^>]+>", "", str(texto))
    texto = html.unescape(texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def licencia_wikimedia_permitida(licencia):
    texto = (licencia or "").lower().strip()

    if not texto:
        return False

    if "noncommercial" in texto or "no derivatives" in texto:
        return False

    if re.search(r"(^|[-\s])nc($|[-\s])", texto):
        return False
    if re.search(r"(^|[-\s])nd($|[-\s])", texto):
        return False

    permitidas = (
        "public domain",
        "cc0",
        "cc by",
        "cc-by",
        "creative commons attribution",
    )

    return any(x in texto for x in permitidas)


def buscar_imagen_openverse(busqueda):
    print(f"🔎 Buscando imagen en Openverse: {busqueda}")

    params = {
        "q": busqueda,
        "page_size": 20,
    }

    try:
        res = requests.get(
            OPENVERSE_API,
            params=params,
            headers=HEADERS_BROWSER,
            timeout=15
        )

        if res.status_code != 200:
            print(
                f"⚠️ Openverse respondió {res.status_code}. "
                "Probando Wikimedia..."
            )
            return None

        resultados = res.json().get("results", [])

        for item in resultados:
            licencia = (item.get("license") or "").lower().strip()

            if licencia not in OPENVERSE_LICENSES_PERMITIDAS:
                continue

            autor = quitar_html(item.get("creator"))

            if licencia in {"by", "by-sa"} and not autor:
                continue

            url_principal = item.get("url")
            url_fallback = item.get("thumbnail")

            if not (url_principal or url_fallback):
                continue

            return {
                "url": url_principal or url_fallback,
                "url_fallback": url_fallback,
                "author": autor or "Dominio público",
                "license": (
                    "Public Domain Mark"
                    if licencia == "pdm"
                    else f"CC {licencia.upper()}"
                ),
                "license_url": item.get("license_url") or "",
                "source": item.get("source") or "Openverse",
                "page_url": (
                    item.get("foreign_landing_url")
                    or item.get("detail_url")
                    or ""
                ),
                "title": item.get("title") or "",
            }

    except Exception as e:
        print(f"⚠️ Error buscando en Openverse: {e}")

    return None


def buscar_imagen_wikimedia(busqueda):
    print(f"🔎 Buscando imagen en Wikimedia Commons: {busqueda}")

    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": busqueda,
        "gsrnamespace": 6,
        "gsrlimit": 20,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 1600,
    }

    try:
        res = requests.get(
            WIKIMEDIA_API,
            params=params,
            headers=HEADERS_BROWSER,
            timeout=15
        )

        if res.status_code != 200:
            print(f"⚠️ Wikimedia respondió {res.status_code}.")
            return None

        pages = res.json().get("query", {}).get("pages", {})

        for page in pages.values():
            infos = page.get("imageinfo") or []

            if not infos:
                continue

            info = infos[0]
            meta = info.get("extmetadata") or {}

            licencia = quitar_html(
                (meta.get("LicenseShortName") or {}).get("value")
            )

            if not licencia_wikimedia_permitida(licencia):
                continue

            autor = quitar_html(
                (meta.get("Artist") or {}).get("value")
            )

            if (
                ("cc by" in licencia.lower() or "cc-by" in licencia.lower())
                and not autor
            ):
                continue

            url_principal = info.get("thumburl") or info.get("url")
            url_fallback = info.get("url")

            if not url_principal:
                continue

            licencia_url = quitar_html(
                (meta.get("LicenseUrl") or {}).get("value")
            )

            return {
                "url": url_principal,
                "url_fallback": url_fallback,
                "author": autor or "Wikimedia Commons",
                "license": licencia or "Licencia abierta",
                "license_url": licencia_url,
                "source": "Wikimedia Commons",
                "page_url": info.get("descriptionurl") or "",
                "title": page.get("title") or "",
            }

    except Exception as e:
        print(f"⚠️ Error buscando en Wikimedia: {e}")

    return None


def buscar_imagen_abierta(busqueda):
    imagen = buscar_imagen_openverse(busqueda)

    if imagen:
        print(
            f"✅ Imagen encontrada en {imagen.get('source')}: "
            f"{imagen.get('title') or 'sin título'}"
        )
        return imagen

    imagen = buscar_imagen_wikimedia(busqueda)

    if imagen:
        print(
            f"✅ Imagen encontrada en Wikimedia Commons: "
            f"{imagen.get('title') or 'sin título'}"
        )
        return imagen

    print(
        "⚠️ No se encontró una imagen abierta adecuada. "
        "Se usará el fondo gráfico de marca."
    )
    return None


def descargar_imagen_bytes(url):
    if not url:
        return None, None

    try:
        res = requests.get(
            url,
            headers=HEADERS_BROWSER,
            timeout=20,
            allow_redirects=True
        )

        if res.status_code != 200:
            return None, None

        content_type = (
            res.headers.get("Content-Type", "")
            .split(";")[0]
            .strip()
            .lower()
        )

        if not content_type.startswith("image/"):
            return None, None

        if len(res.content) > 18 * 1024 * 1024:
            return None, None

        return res.content, content_type

    except Exception as e:
        print(f"⚠️ Error descargando imagen: {e}")
        return None, None


def imagen_a_data_uri(info_imagen):
    if not info_imagen:
        return None

    urls = [
        info_imagen.get("url"),
        info_imagen.get("url_fallback"),
    ]

    urls_probadas = set()

    for url in urls:
        if not url or url in urls_probadas:
            continue

        urls_probadas.add(url)

        data, mime = descargar_imagen_bytes(url)

        if data and mime:
            encoded = base64.b64encode(data).decode("ascii")
            return f"data:{mime};base64,{encoded}"

    return None


def calcular_font_size(titulo):
    longitud = len(titulo or "")

    if longitud <= 28:
        return 62
    if longitud <= 42:
        return 56
    if longitud <= 58:
        return 50
    if longitud <= 72:
        return 44
    return 40


def colores_categoria(categoria):
    categoria = normalizar_categoria(categoria)

    if categoria in {"SE HA LIADO", "MIRI REACCIONA"}:
        return {
            "pill_bg": COLOR_CORAL,
            "pill_fg": "#FFFFFF",
            "accent": COLOR_AMARILLO,
        }

    if categoria in {"TE PONGO EN CONTEXTO", "¿QUIÉN ES?"}:
        return {
            "pill_bg": COLOR_MARFIL,
            "pill_fg": COLOR_NEGRO,
            "accent": COLOR_CORAL,
        }

    return {
        "pill_bg": COLOR_AMARILLO,
        "pill_fg": COLOR_NEGRO,
        "accent": COLOR_CORAL,
    }


def crear_html_miniatura(
    titulo_miniatura,
    categoria_visual,
    image_data_uri=None
):
    categoria_visual = normalizar_categoria(categoria_visual)
    paleta = colores_categoria(categoria_visual)
    font_size = calcular_font_size(titulo_miniatura)

    titulo_safe = html.escape(titulo_miniatura)
    categoria_safe = html.escape(categoria_visual)

    if image_data_uri:
        visual = f'''
        <img
            class="cover"
            src="{image_data_uri}"
            alt=""
        />
        '''
        clase_extra = ""
    else:
        visual = '''
        <div class="fallback-art">
            <div class="fallback-word">MIRI</div>
            <div class="blob blob-a"></div>
            <div class="blob blob-b"></div>
            <div class="dots"></div>
            <div class="ring"></div>
        </div>
        '''
        clase_extra = "no-photo"

    return f'''<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1200, initial-scale=1">
<style>
    * {{ box-sizing: border-box; }}

    html, body {{
        width: 1200px;
        height: 630px;
        margin: 0;
        padding: 0;
        overflow: hidden;
        background: {COLOR_MARFIL};
        font-family: Inter, Arial, Helvetica, sans-serif;
    }}

    .card {{
        position: relative;
        width: 1200px;
        height: 630px;
        overflow: hidden;
        background: {COLOR_MARFIL};
    }}

    .visual {{
        position: absolute;
        top: 0;
        left: 0;
        width: 1200px;
        height: 410px;
        overflow: hidden;
        background: {COLOR_MARFIL};
    }}

    .cover {{
        display: block;
        width: 100%;
        height: 100%;
        object-fit: cover;
        object-position: center 38%;
        filter: saturate(.96) contrast(1.04);
        transform: scale(1.01);
    }}

    .visual::after {{
        content: "";
        position: absolute;
        inset: 0;
        pointer-events: none;
        background: linear-gradient(
            to bottom,
            rgba(22,22,22,0.01) 10%,
            rgba(22,22,22,0.04) 55%,
            rgba(22,22,22,0.58) 100%
        );
        z-index: 3;
    }}

    .visual::before {{
        content: "";
        position: absolute;
        width: 255px;
        height: 190px;
        right: 20px;
        top: 15px;
        z-index: 4;
        opacity: .34;
        background-image: radial-gradient({COLOR_AMARILLO} 2.5px, transparent 2.5px);
        background-size: 15px 15px;
        transform: rotate(6deg);
        pointer-events: none;
    }}

    .fallback-art {{
        position: absolute;
        inset: 0;
        overflow: hidden;
        background: linear-gradient(120deg, {COLOR_MARFIL} 0%, #F0E4DA 100%);
    }}

    .fallback-word {{
        position: absolute;
        left: 45px;
        top: 65px;
        color: {COLOR_NEGRO};
        font-family: "Arial Black", Inter, Arial, sans-serif;
        font-size: 188px;
        font-weight: 900;
        line-height: .88;
        letter-spacing: -12px;
        opacity: .075;
        transform: rotate(-4deg);
    }}

    .blob {{ position: absolute; border-radius: 999px; }}

    .blob-a {{
        width: 535px;
        height: 112px;
        left: -90px;
        top: 232px;
        background: {COLOR_CORAL};
        transform: rotate(-17deg);
    }}

    .blob-b {{
        width: 345px;
        height: 82px;
        right: -70px;
        top: 90px;
        background: {COLOR_AMARILLO};
        transform: rotate(23deg);
    }}

    .dots {{
        position: absolute;
        width: 300px;
        height: 175px;
        right: 50px;
        bottom: 25px;
        opacity: .55;
        background-image: radial-gradient({COLOR_NEGRO} 3px, transparent 3px);
        background-size: 18px 18px;
        transform: rotate(-6deg);
    }}

    .ring {{
        position: absolute;
        width: 265px;
        height: 145px;
        left: 520px;
        top: 75px;
        border: 11px solid {COLOR_NEGRO};
        border-left-color: transparent;
        border-bottom-color: transparent;
        border-radius: 50%;
        opacity: .68;
        transform: rotate(10deg);
    }}

    .top-brand {{
        position: absolute;
        left: 29px;
        top: 25px;
        z-index: 7;
        padding: 8px 14px;
        border: 2px solid {COLOR_MARFIL};
        border-radius: 999px;
        background: {COLOR_NEGRO};
        color: {COLOR_MARFIL};
        box-shadow: 3px 3px 0 rgba(0,0,0,.35);
        font-size: 13px;
        font-weight: 900;
        letter-spacing: .08em;
        text-transform: uppercase;
    }}

    .top-brand .dot {{ color: {COLOR_AMARILLO}; padding-right: 3px; }}

    .no-photo .top-brand {{
        color: {COLOR_NEGRO};
        background: {COLOR_AMARILLO};
        border-color: {COLOR_NEGRO};
        box-shadow: 4px 4px 0 {COLOR_NEGRO};
    }}

    .no-photo .top-brand .dot {{ color: {COLOR_CORAL}; }}

    .spark {{
        position: absolute;
        top: 315px;
        right: 35px;
        z-index: 8;
        color: {COLOR_AMARILLO};
        font-family: "Arial Black", Arial, sans-serif;
        font-size: 55px;
        line-height: 1;
        transform: rotate(12deg);
        text-shadow: 3px 3px 0 {COLOR_NEGRO};
    }}

    .accent-line {{
        position: absolute;
        left: 0;
        top: 397px;
        z-index: 9;
        width: 1200px;
        height: 15px;
        background: {paleta["accent"]};
    }}

    .footer {{
        position: absolute;
        left: 0;
        bottom: 0;
        z-index: 10;
        width: 1200px;
        height: 233px;
        padding: 31px 46px 27px 46px;
        display: grid;
        grid-template-columns: minmax(0, 1fr) 275px;
        column-gap: 35px;
        align-items: center;
        overflow: hidden;
        background: {COLOR_NEGRO};
        color: #FFFFFF;
    }}

    .footer::before {{
        content: "";
        position: absolute;
        width: 5px;
        height: 92px;
        left: 25px;
        top: 88px;
        background: {COLOR_CORAL};
        transform: rotate(4deg);
        border-radius: 4px;
    }}

    .footer::after {{
        content: "";
        position: absolute;
        width: 235px;
        height: 235px;
        right: -90px;
        bottom: -115px;
        border: 16px solid {COLOR_CORAL};
        border-radius: 50%;
        opacity: .9;
        pointer-events: none;
    }}

    .copy {{
        min-width: 0;
        position: relative;
        z-index: 2;
        padding-left: 7px;
    }}

    .pill {{
        display: inline-flex;
        align-items: center;
        min-height: 34px;
        margin-bottom: 13px;
        padding: 7px 14px 6px;
        border-radius: 999px;
        border: 2px solid {COLOR_NEGRO};
        background: {paleta["pill_bg"]};
        color: {paleta["pill_fg"]};
        box-shadow: 3px 3px 0 #000000;
        font-size: 16px;
        font-weight: 900;
        letter-spacing: .075em;
        line-height: 1;
        text-transform: uppercase;
    }}

    .headline {{
        max-width: 790px;
        overflow: hidden;
        color: #FFFFFF;
        font-family: "Arial Black", Inter, Arial, Helvetica, sans-serif;
        font-size: {font_size}px;
        font-weight: 900;
        line-height: .98;
        letter-spacing: -2px;
        text-wrap: balance;
        display: -webkit-box;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 3;
    }}

    .logo-area {{
        position: relative;
        z-index: 3;
        width: 260px;
        justify-self: end;
        align-self: end;
        padding: 0 0 9px 5px;
    }}

    .logo-miri {{
        margin-left: 8px;
        color: {COLOR_MARFIL};
        font-family: "Arial Black", Inter, Arial, sans-serif;
        font-size: 62px;
        font-weight: 900;
        line-height: .79;
        letter-spacing: -5px;
    }}

    .bubble {{
        position: relative;
        display: inline-block;
        margin-top: 11px;
        padding: 9px 15px 10px;
        border-radius: 9px;
        background: {COLOR_CORAL};
        color: #FFFFFF;
        box-shadow: 4px 4px 0 #000000;
        font-size: 24px;
        font-weight: 900;
        line-height: 1;
        letter-spacing: -1px;
        transform: rotate(-1.5deg);
    }}

    .bubble::after {{
        content: "";
        position: absolute;
        left: 23px;
        bottom: -11px;
        width: 0;
        height: 0;
        border-top: 13px solid {COLOR_CORAL};
        border-right: 14px solid transparent;
    }}

    .handle {{
        margin-top: 19px;
        margin-left: 8px;
        color: {COLOR_GRIS};
        font-size: 13px;
        font-weight: 800;
        letter-spacing: .06em;
    }}
</style>
</head>

<body>
<div class="card {clase_extra}">
    <div class="visual">
        {visual}
    </div>

    <div class="top-brand">
        <span class="dot">●</span> Miri te lo cuenta
    </div>

    <div class="spark">✦</div>
    <div class="accent-line"></div>

    <section class="footer">
        <div class="copy">
            <div class="pill">{categoria_safe}</div>
            <div class="headline">{titulo_safe}</div>
        </div>

        <div class="logo-area">
            <div class="logo-miri">Miri</div>
            <div class="bubble">te lo cuenta</div>
            <div class="handle">@miritelocuenta</div>
        </div>
    </section>
</div>
</body>
</html>'''


def renderizar_html_a_png(html_doc, ruta_salida):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"❌ Playwright no está disponible: {e}")
        return None

    if os.path.exists(ruta_salida):
        try:
            os.remove(ruta_salida)
        except Exception:
            pass

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )

            page = browser.new_page(
                viewport={"width": 1200, "height": 630},
                device_scale_factor=1
            )

            page.set_content(
                html_doc,
                wait_until="load"
            )

            page.wait_for_function(
                """() => Array.from(document.images)
                    .every(img => img.complete)""",
                timeout=5000
            )

            page.wait_for_timeout(250)

            card = page.locator(".card")
            card.screenshot(
                path=ruta_salida,
                type="png",
                animations="disabled"
            )

            browser.close()

        if os.path.exists(ruta_salida) and os.path.getsize(ruta_salida) > 1000:
            print(
                f"✅ Miniatura generada correctamente: "
                f"{ruta_salida} (1200x630)"
            )
            return ruta_salida

        print("❌ La miniatura no se creó correctamente.")

    except Exception as e:
        print(f"❌ Error renderizando miniatura: {e}")

    return None


def generar_miniatura_html(
    titulo_miniatura,
    categoria_visual,
    busqueda_imagen
):
    ruta_local = "miniatura_destacada.png"

    if os.path.exists(ruta_local):
        try:
            os.remove(ruta_local)
        except Exception:
            pass

    print("🎨 Preparando miniatura de Miri te lo cuenta...")
    print(f"🔍 Búsqueda visual: {busqueda_imagen}")

    info_imagen = buscar_imagen_abierta(
        busqueda_imagen
    )

    image_data_uri = imagen_a_data_uri(
        info_imagen
    )

    if info_imagen and image_data_uri:
        print(
            "✅ La imagen de fondo se ha descargado "
            "y embebido correctamente."
        )
    elif info_imagen and not image_data_uri:
        print(
            "⚠️ Se encontró una imagen, pero no se pudo "
            "descargar. Se usará el fondo gráfico de marca."
        )
        info_imagen = None
    else:
        print(
            "ℹ️ Se generará una miniatura gráfica sin foto."
        )

    html_doc = crear_html_miniatura(
        titulo_miniatura=titulo_miniatura,
        categoria_visual=categoria_visual,
        image_data_uri=image_data_uri
    )

    ruta_generada = renderizar_html_a_png(
        html_doc,
        ruta_local
    )

    if not ruta_generada:
        return None, None

    return ruta_generada, info_imagen


def construir_credito_html(info_imagen):
    if not info_imagen:
        return ""

    author = html.escape(
        info_imagen.get("author") or "Autor no indicado"
    )
    source = html.escape(
        info_imagen.get("source") or "Fuente pública"
    )
    license_name = html.escape(
        info_imagen.get("license") or "Licencia abierta"
    )

    page_url = info_imagen.get("page_url") or ""
    license_url = info_imagen.get("license_url") or ""

    partes = [f"Imagen destacada: {author}"]

    if page_url:
        partes.append(
            f'<a href="{html.escape(page_url, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'{source}</a>'
        )
    else:
        partes.append(source)

    if license_url:
        partes.append(
            f'<a href="{html.escape(license_url, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'{license_name}</a>'
        )
    else:
        partes.append(license_name)

    return (
        '<p style="font-size:11px; color:#888; '
        'margin-top:18px; line-height:1.45;">'
        + " · ".join(partes)
        + "</p>"
    )


# ==========================================
# 5. PUBLICACIÓN EN WORDPRESS
# ==========================================
def publicar_en_wordpress(
    titulo,
    contenido_html,
    ruta_imagen
):
    if not (WP_URL and WP_USER and WP_APP_PASS):
        raise Exception(
            "❌ Error crítico: Faltan credenciales "
            "de WordPress en Secrets."
        )

    media_id = None

    if ruta_imagen and os.path.exists(ruta_imagen):
        print(
            f"🚀 Subiendo imagen destacada a {WP_URL}..."
        )

        url_media = f"{WP_URL}/wp-json/wp/v2/media"

        with open(ruta_imagen, "rb") as f:
            media_bytes = f.read()

        content_type = (
            mimetypes.guess_type(ruta_imagen)[0]
            or "image/png"
        )

        headers_media = {
            "Content-Disposition": (
                "attachment; "
                f"filename={os.path.basename(ruta_imagen)}"
            ),
            "Content-Type": content_type
        }

        r_media = requests.post(
            url_media,
            data=media_bytes,
            headers=headers_media,
            auth=(WP_USER, WP_APP_PASS),
            timeout=30
        )

        if r_media.status_code in [200, 201]:
            media_json = r_media.json()
            media_id = media_json.get("id")

            print(
                "✅ Imagen subida correctamente "
                f"(Media ID: {media_id})"
            )

        else:
            print(
                "⚠️ Aviso al subir imagen a WordPress: "
                f"{r_media.text}"
            )

    else:
        print(
            "⚠️ No hay miniatura válida. "
            "El artículo se publicará sin imagen destacada."
        )

    print(
        "🚀 Publicando artículo completo con su miniatura "
        "en WordPress..."
    )

    url_posts = f"{WP_URL}/wp-json/wp/v2/posts"

    payload = {
        "title": titulo,
        "content": contenido_html,
        "status": "publish"
    }

    if media_id:
        payload["featured_media"] = media_id

    r_post = requests.post(
        url_posts,
        json=payload,
        headers={"Content-Type": "application/json"},
        auth=(WP_USER, WP_APP_PASS),
        timeout=30
    )

    if r_post.status_code in [200, 201]:
        post_data = r_post.json()

        print(
            "🎉 ¡ÉXITO TOTAL! Entrada publicada en "
            f"WordPress. Link: {post_data.get('link')}"
        )

        return True

    raise Exception(
        "❌ Error crítico al publicar entrada "
        f"({r_post.status_code}): {r_post.text}"
    )


# ==========================================
# 6. EJECUCIÓN PRINCIPAL
# ==========================================
if __name__ == "__main__":
    tema = obtener_nuevo_tema_viral()

    if not tema:
        print(
            "⚠️ Sin temas nuevos. Usando tema de prueba..."
        )
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
    print(f"🏷️ Categoría visual: {categoria_visual}")
    print(f"🔎 Búsqueda imagen: {busqueda_imagen}")

    ruta_imagen, info_imagen = generar_miniatura_html(
        titulo_miniatura,
        categoria_visual,
        busqueda_imagen
    )

    if info_imagen:
        credito = construir_credito_html(
            info_imagen
        )

        if credito:
            contenido_html += credito

    if titulo and contenido_html:
        publicado = publicar_en_wordpress(
            titulo,
            contenido_html,
            ruta_imagen
        )

        if publicado:
            guardar_en_historial(tema)
