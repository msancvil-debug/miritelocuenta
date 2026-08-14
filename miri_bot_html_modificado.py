import requests
import json
import os
import html
import re
import mimetypes
import unicodedata
import subprocess
import shutil
import calendar
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageStat

# ============================================================
# MIRI TE LO CUENTA · BOT AUTOMÁTICO
# ============================================================
# FLUJO:
# Google Trends + Google News + Google Search social + RSS
# -> selección editorial anti-repetición -> verificación web estricta
# -> Gemini -> miniatura 1200x630 -> MP4 fijo 5 s
# -> WordPress -> pack Metricool -> historial estructurado
#
# MOTOR VISUAL:
# 1) Identifica el tipo visual:
#    persona / programa / marca / evento / lugar / tema
# 2) Prioriza Wikimedia Commons.
# 3) Si Wikimedia no ofrece una opción suficientemente fiable,
#    prueba Openverse.
# 4) Si la noticia trata de UNA persona:
#    - exige relación fuerte con el nombre
#    - penaliza y descarta fotos de grupo/cast/equipos si no hay
#      evidencia suficiente de que sean una opción adecuada
# 5) Para lugares, busca imágenes reales del lugar.
# 6) Penaliza imágenes casi en blanco y negro si existen alternativas.
# 7) Si ninguna imagen supera el umbral de seguridad/relevancia,
#    usa un fallback gráfico con el branding.
#
# NO REQUIERE NUEVAS CREDENCIALES NI PERMISOS.
# Requisitos Python:
#   requests
#   pillow
#
# Para generar MP4:
#   ffmpeg disponible en el sistema (recomendado).
# Si no existe, el bot no falla: genera igualmente ambas imágenes sociales.
# ============================================================


# ==========================================
# 1. VARIABLES DE ENTORNO
# ==========================================
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
WP_URL = (os.environ.get("WP_URL") or "").strip().rstrip("/")
WP_USER = (os.environ.get("WP_USER") or "").strip()
WP_APP_PASS = (os.environ.get("WP_APP_PASS") or "").strip().replace(" ", "")

HISTORIAL_FILE = "historial_temas.json"

# ============================================================
# TENDENCIAS + FRECUENCIA
# ============================================================
# El bot ya no se queda con el primer titular de un RSS.
# Construye una bolsa amplia de candidatos, los cruza y luego
# Gemini elige la tendencia más útil para "Miri te lo cuenta".
#
# Fuentes:
# - Google Trends España (RSS oficial exportable)
# - varias búsquedas de Google News
# - 20minutos
# - Google Search en tiempo real mediante la MISMA GEMINI_API_KEY
#
# No hace falta ninguna clave nueva.
GOOGLE_TRENDS_RSS = "https://trends.google.com/trending/rss?geo=ES"

GOOGLE_NEWS_QUERIES = [
    'TikTok España viral when:1d',
    'influencer España polémica viral when:1d',
    'youtuber España polémica viral when:1d',
    'streamer España Twitch Kick polémica when:1d',
    'YouTube España viral creador when:1d',
    'Instagram España influencer viral when:1d',
    'reality televisión España redes sociales when:1d',
    'Telecinco Antena 3 reality redes sociales when:1d',
    'meme tendencia Internet España when:1d',
    'famoso ruptura polémica redes sociales España when:1d',
    'música viral TikTok España when:1d',
    'creador de contenido España tendencia when:1d',
    'viral redes sociales España when:1d',
]

FEEDS_GENERALES = [
    "https://20minutos.es/rss/"
]

# Máximo de candidatos que pasan a la selección final.
MAX_CANDIDATOS_TENDENCIA = int(
    os.environ.get("MAX_CANDIDATOS_TENDENCIA", "70")
)

# Ritmo adaptado al plan gratuito de Metricool.
#
# En este proyecto cada artículo consume SOLO 1 publicación
# de Metricool: la publicación con imagen fija en Instagram.
#
# Facebook NO cuenta aquí porque el contenido de Instagram se
# comparte automáticamente en Facebook desde Meta.
#
# El vídeo vertical de 5 segundos se genera igualmente, pero
# queda como archivo para publicación manual y NO interviene
# en el cálculo de frecuencia.
#
# Con 20 publicaciones disponibles:
# - mes de 31 días -> 37,2 h entre artículos
# - mes de 30 días -> 36,0 h
# - mes de 28 días -> 33,6 h
ARTICULOS_MES_OBJETIVO = int(
    os.environ.get("ARTICULOS_MES_OBJETIVO", "20")
)
METRICOOL_PUBLICACIONES_MES = int(
    os.environ.get("METRICOOL_PUBLICACIONES_MES", "20")
)

COOLDOWN_ENTIDAD_DIAS = int(
    os.environ.get("COOLDOWN_ENTIDAD_DIAS", "7")
)
HISTORIAL_REPETICION_DIAS = int(
    os.environ.get("HISTORIAL_REPETICION_DIAS", "30")
)
UMBRAL_SIMILITUD_TEMA = float(
    os.environ.get("UMBRAL_SIMILITUD_TEMA", "0.72")
)

# Seguridad editorial:
# una tendencia descubierta mediante búsqueda social no se publica
# si Google Search no aporta al menos 2 evidencias web reales.
MIN_GROUNDING_TENDENCIA = int(
    os.environ.get("MIN_GROUNDING_TENDENCIA", "2")
)
MIN_GROUNDING_INVESTIGACION = int(
    os.environ.get("MIN_GROUNDING_INVESTIGACION", "2")
)
MIN_CONFIANZA_INVESTIGACION = int(
    os.environ.get("MIN_CONFIANZA_INVESTIGACION", "70")
)
MAX_INTENTOS_VERIFICACION = int(
    os.environ.get("MAX_INTENTOS_VERIFICACION", "6")
)

# PRUEBA MANUAL:
# Si FORCE_RUN=1, el bot ignora SOLO el control de frecuencia y ejecuta
# una publicación completa. Por defecto sigue desactivado.
FORCE_RUN = str(
    os.environ.get("FORCE_RUN", "0")
).strip().lower() in {
    "1",
    "true",
    "yes",
    "si",
    "sí",
}

HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"
}

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"

# Licencias permitidas en Openverse.
# Se excluyen NC/ND para mantener abierta la monetización.
OPENVERSE_LICENSES_PERMITIDAS = {
    "cc0",
    "pdm",
    "by",
    "by-sa",
}

# ==========================================
# 2. CONFIGURACIÓN EDITORIAL / VISUAL
# ==========================================
TIPOS_VISUALES_VALIDOS = {
    "persona",
    "programa",
    "marca",
    "evento",
    "lugar",
    "tema",
}

UMBRAL_SCORE = {
    "persona": 72,
    "programa": 58,
    "marca": 58,
    "evento": 52,
    "lugar": 48,
    # Para temas conceptuales, Wikimedia/Openverse suelen tener
    # metadatos mucho más breves. El umbral baja, pero SOLO se
    # acepta si los metadatos reales contienen conceptos de la
    # búsqueda; la query nunca cuenta por sí sola como evidencia.
    "tema": 28,
}

OUTPUT_IMAGE = "miniatura_destacada.jpg"

# Pack social generado automáticamente por cada artículo.
SOCIAL_OUTPUT_DIR = "social_output"
OUTPUT_SOCIAL_POST = os.path.join(
    SOCIAL_OUTPUT_DIR,
    "social_image_1200x630.jpg"
)
OUTPUT_SOCIAL_VERTICAL = os.path.join(
    SOCIAL_OUTPUT_DIR,
    "social_video_frame_1080x1920.jpg"
)
OUTPUT_SOCIAL_VIDEO = os.path.join(
    SOCIAL_OUTPUT_DIR,
    "social_video_1080x1920.mp4"
)
OUTPUT_SOCIAL_JSON = os.path.join(
    SOCIAL_OUTPUT_DIR,
    "social_pack.json"
)
OUTPUT_SOCIAL_TXT = os.path.join(
    SOCIAL_OUTPUT_DIR,
    "social_copies.txt"
)

# 5 segundos: supera el mínimo de 3 s exigido por TikTok/Metricool
# para la publicación automática de vídeo.
SOCIAL_VIDEO_SECONDS = 5

# Branding Miri te lo cuenta
COLOR_BG = "#E9E1DA"
COLOR_BLACK = "#0E0E10"
COLOR_WHITE = "#FAFAFA"
COLOR_RED = "#F04438"
COLOR_YELLOW = "#FFD84D"
COLOR_GREY = "#6A6663"
COLOR_LIGHT_GREY = "#C9C1BA"


# ==========================================
# 3. UTILIDADES GENERALES
# ==========================================
def limpiar_html_tags(texto):
    return re.sub(r"<[^>]+>", " ", texto or "").strip()


def normalizar_texto(texto):
    texto = texto or ""
    texto = html.unescape(str(texto))
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(
        c for c in texto
        if not unicodedata.combining(c)
    )
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def tokenizar(texto):
    stop = {
        "de", "del", "la", "las", "el", "los", "y", "en",
        "the", "of", "and", "a", "an", "for", "to", "on",
        "con", "por", "para", "un", "una", "at", "in"
    }

    return [
        t for t in normalizar_texto(texto).split()
        if len(t) >= 2 and t not in stop
    ]


def deduplicar_lista(lista):
    vistos = set()
    salida = []

    for item in lista or []:
        item = str(item or "").strip()

        if not item:
            continue

        clave = normalizar_texto(item)

        if clave and clave not in vistos:
            vistos.add(clave)
            salida.append(item)

    return salida


def asegurar_lista(valor):
    if isinstance(valor, list):
        return [
            str(x).strip()
            for x in valor
            if str(x).strip()
        ]

    if isinstance(valor, str) and valor.strip():
        return [valor.strip()]

    return []


def contiene_entidad_exacta(entidad, texto):
    ent = normalizar_texto(entidad)
    txt = normalizar_texto(texto)
    return bool(ent and ent in txt)


def porcentaje_cobertura_entidad(entidad, texto):
    ent_tokens = set(tokenizar(entidad))
    txt_tokens = set(tokenizar(texto))

    if not ent_tokens:
        return 0.0

    return (
        len(ent_tokens.intersection(txt_tokens))
        / max(1, len(ent_tokens))
    )


def limitar_texto(texto, max_len=95):
    texto = re.sub(
        r"\s+",
        " ",
        str(texto or "").strip()
    )

    if len(texto) <= max_len:
        return texto

    return texto[:max_len - 1].rstrip() + "…"


def extraer_json_de_respuesta(raw_text):
    raw_text = (raw_text or "").strip()

    if raw_text.startswith("```"):
        raw_text = (
            raw_text
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )

    try:
        return json.loads(raw_text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", raw_text, re.S)

    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    raise ValueError(
        "No se pudo extraer un JSON válido de la respuesta."
    )


def normalizar_tipo_visual(tipo_visual):
    tipo = normalizar_texto(tipo_visual)

    equivalencias = {
        "person": "persona",
        "persona": "persona",
        "celebridad": "persona",
        "personaje": "persona",

        "programa": "programa",
        "tv": "programa",
        "television": "programa",
        "reality": "programa",
        "show": "programa",
        "serie": "programa",

        "marca": "marca",
        "brand": "marca",
        "empresa": "marca",
        "plataforma": "marca",

        "evento": "evento",
        "event": "evento",

        "lugar": "lugar",
        "place": "lugar",
        "location": "lugar",
        "city": "lugar",
        "town": "lugar",
        "ciudad": "lugar",
        "localizacion": "lugar",
        "destino": "lugar",
        "pais": "lugar",
        "recinto": "lugar",

        "tema": "tema",
        "topic": "tema",
        "concepto": "tema",
    }

    tipo = equivalencias.get(tipo, tipo)

    if tipo not in TIPOS_VISUALES_VALIDOS:
        return "tema"

    return tipo


# ==========================================
# 4. HISTORIAL, FRECUENCIA Y TENDENCIAS
# ==========================================
def _parse_iso_fecha(valor):
    if not valor:
        return None

    if isinstance(valor, datetime):
        dt = valor
    else:
        texto = str(valor).strip()

        try:
            dt = datetime.fromisoformat(
                texto.replace("Z", "+00:00")
            )
        except Exception:
            try:
                dt = parsedate_to_datetime(texto)
            except Exception:
                return None

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        timezone.utc
    )


def cargar_historial():
    """
    Compatible con el historial antiguo, que era una lista de strings,
    y con el nuevo historial estructurado.
    """
    if not os.path.exists(
        HISTORIAL_FILE
    ):
        return []

    try:
        with open(
            HISTORIAL_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if not isinstance(
            data,
            list
        ):
            return []

        normalizado = []

        for item in data:
            if isinstance(
                item,
                str
            ):
                normalizado.append({
                    "tema": item,
                    "titulo": item,
                    "entidad": "",
                    "categoria": "",
                    "keywords": [],
                    "fecha": None,
                })

            elif isinstance(
                item,
                dict
            ):
                item = dict(item)

                item.setdefault(
                    "tema",
                    item.get(
                        "titulo",
                        ""
                    )
                )

                item.setdefault(
                    "titulo",
                    item.get(
                        "tema",
                        ""
                    )
                )

                item.setdefault(
                    "entidad",
                    ""
                )

                item.setdefault(
                    "categoria",
                    ""
                )

                item.setdefault(
                    "keywords",
                    []
                )

                item.setdefault(
                    "fecha",
                    None
                )

                normalizado.append(
                    item
                )

        return normalizado

    except Exception as e:
        print(
            f"⚠️ No se pudo leer el historial: {e}"
        )
        return []


def guardar_en_historial(
    tema,
    titulo="",
    entidad="",
    categoria="",
    keywords=None,
    fuente="",
    url="",
    score_tendencia=None
):
    historial = cargar_historial()

    registro = {
        "tema": (
            tema
            or titulo
            or ""
        ).strip(),
        "titulo": (
            titulo
            or tema
            or ""
        ).strip(),
        "entidad": (
            entidad
            or ""
        ).strip(),
        "categoria": (
            categoria
            or ""
        ).strip(),
        "keywords": deduplicar_lista(
            asegurar_lista(
                keywords
            )
        )[:12],
        "fuente": (
            fuente
            or ""
        ).strip(),
        "url": (
            url
            or ""
        ).strip(),
        "score_tendencia": score_tendencia,
        "fecha": datetime.now(
            timezone.utc
        ).isoformat(
            timespec="seconds"
        ),
    }

    historial.append(
        registro
    )

    # Mantiene el fichero razonablemente pequeño.
    historial = historial[
        -250:
    ]

    with open(
        HISTORIAL_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            historial,
            f,
            ensure_ascii=False,
            indent=2
        )


def articulos_mes_efectivos():
    """
    Cada artículo consume una sola publicación de Metricool:
    Instagram con imagen fija.

    Facebook se comparte desde Instagram y el vídeo vertical
    se publica manualmente, por lo que ninguno de los dos
    consume publicaciones adicionales en este cálculo.
    """
    return max(
        1,
        min(
            ARTICULOS_MES_OBJETIVO,
            METRICOOL_PUBLICACIONES_MES
        )
    )


def intervalo_objetivo_horas(
    momento=None
):
    momento = (
        momento
        or datetime.now(
            timezone.utc
        )
    )

    dias = calendar.monthrange(
        momento.year,
        momento.month
    )[1]

    return (
        dias
        * 24.0
        / articulos_mes_efectivos()
    )


def publicaciones_este_mes():
    ahora = datetime.now(
        timezone.utc
    )

    total = 0

    for item in cargar_historial():
        fecha = _parse_iso_fecha(
            item.get(
                "fecha"
            )
        )

        if (
            fecha
            and fecha.year == ahora.year
            and fecha.month == ahora.month
        ):
            total += 1

    return total


def ultima_publicacion():
    fechas = []

    for item in cargar_historial():
        fecha = _parse_iso_fecha(
            item.get(
                "fecha"
            )
        )

        if fecha:
            fechas.append(
                fecha
            )

    return max(
        fechas
    ) if fechas else None


def puede_publicar_ahora():
    objetivo = articulos_mes_efectivos()
    hechas = publicaciones_este_mes()

    if hechas >= objetivo:
        print(
            "⏸️ Cuota mensual alcanzada: "
            f"{hechas}/{objetivo} artículos."
        )
        return False

    ultima = ultima_publicacion()

    if not ultima:
        return True

    intervalo = intervalo_objetivo_horas()
    transcurridas = (
        datetime.now(
            timezone.utc
        )
        - ultima
    ).total_seconds() / 3600.0

    if transcurridas < intervalo:
        faltan = intervalo - transcurridas

        print(
            "⏸️ Todavía no toca publicar. "
            f"Objetivo actual: {objetivo} artículos/mes. "
            f"Intervalo aproximado: {intervalo:.1f} h. "
            f"Faltan ~{faltan:.1f} h."
        )

        return False

    return True


def limpiar_titulo_feed(title):
    title = html.unescape(
        (title or "").strip()
    )

    # Elimina sufijos típicos tipo " - Medio X".
    title = re.sub(
        r"\s+-\s+[^-]{2,60}$",
        "",
        title
    ).strip()

    return title


def _texto_local_tag(
    elem,
    nombre
):
    for child in elem.iter():
        tag = str(
            child.tag
        )

        if tag.split(
            "}"
        )[-1] == nombre:
            return (
                child.text
                or ""
            ).strip()

    return ""


def _fecha_item_rss(item):
    for nombre in [
        "pubDate",
        "published",
        "updated"
    ]:
        texto = _texto_local_tag(
            item,
            nombre
        )

        if texto:
            fecha = _parse_iso_fecha(
                texto
            )

            if fecha:
                return fecha.isoformat()

    return ""


def crear_candidato(
    titulo,
    fuente,
    url="",
    contexto="",
    senal="",
    fecha="",
    trafico="",
    score_base=0
):
    titulo = limpiar_titulo_feed(
        titulo
    )

    if not titulo:
        return None

    return {
        "titulo": titulo,
        "fuente": (
            fuente
            or ""
        ).strip(),
        "url": (
            url
            or ""
        ).strip(),
        "contexto": (
            contexto
            or ""
        ).strip(),
        "senales": [
            senal
        ] if senal else [],
        "fuentes": [
            fuente
        ] if fuente else [],
        "fecha": fecha,
        "trafico": (
            trafico
            or ""
        ).strip(),
        "score_base": int(
            score_base
            or 0
        ),
    }


def similaridad_texto(
    a,
    b
):
    a_norm = normalizar_texto(
        a
    )

    b_norm = normalizar_texto(
        b
    )

    if not a_norm or not b_norm:
        return 0.0

    seq = SequenceMatcher(
        None,
        a_norm,
        b_norm
    ).ratio()

    a_tokens = set(
        tokenizar(
            a_norm
        )
    )

    b_tokens = set(
        tokenizar(
            b_norm
        )
    )

    if (
        a_tokens
        and b_tokens
    ):
        jac = len(
            a_tokens
            & b_tokens
        ) / max(
            1,
            len(
                a_tokens
                | b_tokens
            )
        )
    else:
        jac = 0.0

    return max(
        seq,
        jac
    )


def fusionar_candidatos(
    candidatos
):
    """
    Si una misma historia aparece en varias fuentes, la fusiona.
    Esa coincidencia cuenta como señal de tendencia real.
    """
    fusionados = []

    for candidato in candidatos:
        if not candidato:
            continue

        mejor = None
        mejor_sim = 0.0

        for existente in fusionados:
            sim = similaridad_texto(
                candidato.get(
                    "titulo",
                    ""
                ),
                existente.get(
                    "titulo",
                    ""
                )
            )

            if sim > mejor_sim:
                mejor_sim = sim
                mejor = existente

        if (
            mejor is not None
            and mejor_sim >= 0.82
        ):
            mejor[
                "score_base"
            ] = max(
                mejor.get(
                    "score_base",
                    0
                ),
                candidato.get(
                    "score_base",
                    0
                )
            ) + 8

            mejor[
                "fuentes"
            ] = deduplicar_lista(
                (
                    mejor.get(
                        "fuentes",
                        []
                    )
                    + candidato.get(
                        "fuentes",
                        []
                    )
                )
            )

            mejor[
                "senales"
            ] = deduplicar_lista(
                (
                    mejor.get(
                        "senales",
                        []
                    )
                    + candidato.get(
                        "senales",
                        []
                    )
                )
            )

            if (
                len(
                    candidato.get(
                        "contexto",
                        ""
                    )
                )
                > len(
                    mejor.get(
                        "contexto",
                        ""
                    )
                )
            ):
                mejor[
                    "contexto"
                ] = candidato.get(
                    "contexto",
                    ""
                )

            if (
                not mejor.get(
                    "url"
                )
                and candidato.get(
                    "url"
                )
            ):
                mejor[
                    "url"
                ] = candidato.get(
                    "url"
                )

        else:
            fusionados.append(
                dict(
                    candidato
                )
            )

    return fusionados


PALABRAS_MIRI = {
    "tiktok", "instagram", "youtube", "youtuber",
    "streamer", "twitch", "kick", "influencer",
    "creador", "creadora", "viral", "meme",
    "reality", "telecinco", "antena", "mediaset",
    "famoso", "famosa", "celebridad", "polemica",
    "polémica", "redes", "internet", "directo",
    "streaming", "video", "vídeo", "programa",
    "cantante", "artista", "musica", "música",
    "ruptura", "pareja", "concurso", "gran hermano",
    "supervivientes", "tentaciones", "fandom",
}

PALABRAS_DESCARTE = {
    "bolsa", "ibex", "euribor", "meteorologia",
    "meteorología", "terremoto", "accidente",
    "asesinato", "guerra", "elecciones", "elección",
    "partido político", "congreso", "senado",
}


def score_encaje_miri(
    candidato
):
    texto = normalizar_texto(
        " ".join([
            candidato.get(
                "titulo",
                ""
            ),
            candidato.get(
                "contexto",
                ""
            ),
        ])
    )

    score = int(
        candidato.get(
            "score_base",
            0
        )
    )

    for palabra in PALABRAS_MIRI:
        if normalizar_texto(
            palabra
        ) in texto:
            score += 6

    for palabra in PALABRAS_DESCARTE:
        if normalizar_texto(
            palabra
        ) in texto:
            score -= 20

    # Deporte puro no es el foco salvo que exista ángulo
    # claro de cultura de Internet/celebridad.
    deporte = {
        "futbol", "fútbol", "liga", "champions",
        "tenis", "formula 1", "f1", "baloncesto",
        "athletic", "betis", "madrid", "barça",
    }

    tiene_deporte = any(
        normalizar_texto(
            x
        ) in texto
        for x in deporte
    )

    tiene_angulo_miri = any(
        normalizar_texto(
            x
        ) in texto
        for x in {
            "viral",
            "tiktok",
            "influencer",
            "streamer",
            "redes",
            "meme",
            "famoso",
        }
    )

    if (
        tiene_deporte
        and not tiene_angulo_miri
    ):
        score -= 28

    return score


def penalizacion_historial(
    titulo,
    entidad=""
):
    ahora = datetime.now(
        timezone.utc
    )

    penalizacion = 0
    repeticion_dura = False

    entidad_norm = normalizar_texto(
        entidad
    )

    for item in cargar_historial():
        fecha = _parse_iso_fecha(
            item.get(
                "fecha"
            )
        )

        if fecha:
            edad_dias = (
                ahora - fecha
            ).total_seconds() / 86400.0

            if edad_dias > HISTORIAL_REPETICION_DIAS:
                continue
        else:
            # Historial antiguo: se compara por título, pero no
            # activa cooldown de entidad porque no sabemos la fecha.
            edad_dias = None

        previo = (
            item.get(
                "tema"
            )
            or item.get(
                "titulo"
            )
            or ""
        )

        sim = similaridad_texto(
            titulo,
            previo
        )

        if sim >= UMBRAL_SIMILITUD_TEMA:
            penalizacion += 100
            repeticion_dura = True

        elif sim >= 0.58:
            penalizacion += 35

        entidad_previa = normalizar_texto(
            item.get(
                "entidad",
                ""
            )
        )

        if (
            entidad_norm
            and entidad_previa
            and entidad_norm == entidad_previa
            and edad_dias is not None
            and edad_dias < COOLDOWN_ENTIDAD_DIAS
        ):
            penalizacion += 28

    return penalizacion, repeticion_dura


def recoger_google_trends():
    candidatos = []

    try:
        res = requests.get(
            GOOGLE_TRENDS_RSS,
            headers=HEADERS_BROWSER,
            timeout=20
        )

        if res.status_code != 200:
            print(
                "⚠️ Google Trends RSS no respondió correctamente."
            )
            return candidatos

        root = ET.fromstring(
            res.content
        )

        for item in root.findall(
            ".//item"
        )[:30]:
            titulo = _texto_local_tag(
                item,
                "title"
            )

            trafico = _texto_local_tag(
                item,
                "approx_traffic"
            )

            picture_source = _texto_local_tag(
                item,
                "picture_source"
            )

            news_titles = []

            for child in item.iter():
                local = str(
                    child.tag
                ).split(
                    "}"
                )[-1]

                if (
                    local == "news_item_title"
                    and child.text
                ):
                    news_titles.append(
                        child.text.strip()
                    )

            contexto = " | ".join(
                deduplicar_lista(
                    news_titles
                )[:3]
            )

            if picture_source:
                contexto = (
                    contexto
                    + " | "
                    + picture_source
                ).strip(
                    " |"
                )

            score = 34

            trafico_num = re.sub(
                r"\D",
                "",
                trafico
                or ""
            )

            if trafico_num:
                try:
                    n = int(
                        trafico_num
                    )

                    if n >= 100000:
                        score += 20
                    elif n >= 50000:
                        score += 15
                    elif n >= 10000:
                        score += 10
                    elif n >= 5000:
                        score += 6
                except Exception:
                    pass

            candidatos.append(
                crear_candidato(
                    titulo=titulo,
                    fuente="Google Trends",
                    url=GOOGLE_TRENDS_RSS,
                    contexto=contexto,
                    senal="google_trends",
                    fecha=_fecha_item_rss(
                        item
                    ),
                    trafico=trafico,
                    score_base=score
                )
            )

    except Exception as e:
        print(
            f"⚠️ Error leyendo Google Trends: {e}"
        )

    return [
        x
        for x in candidatos
        if x
    ]


def construir_google_news_feed(
    query
):
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(
            query
        )
        + "&hl=es&gl=ES&ceid=ES:es"
    )


def recoger_google_news():
    candidatos = []

    for query in GOOGLE_NEWS_QUERIES:
        url = construir_google_news_feed(
            query
        )

        try:
            res = requests.get(
                url,
                headers=HEADERS_BROWSER,
                timeout=15
            )

            if res.status_code != 200:
                continue

            root = ET.fromstring(
                res.content
            )

            for item in root.findall(
                ".//item"
            )[:8]:
                titulo = _texto_local_tag(
                    item,
                    "title"
                )

                link = _texto_local_tag(
                    item,
                    "link"
                )

                fuente = _texto_local_tag(
                    item,
                    "source"
                ) or "Google News"

                candidato = crear_candidato(
                    titulo=titulo,
                    fuente=fuente,
                    url=link,
                    contexto=(
                        "Detectado con Google News. "
                        f"Búsqueda temática: {query}"
                    ),
                    senal="google_news",
                    fecha=_fecha_item_rss(
                        item
                    ),
                    score_base=18
                )

                if candidato:
                    candidatos.append(
                        candidato
                    )

        except Exception as e:
            print(
                "⚠️ Error en Google News "
                f"({query}): {e}"
            )

    return candidatos


def recoger_feeds_generales():
    candidatos = []

    for feed_url in FEEDS_GENERALES:
        try:
            res = requests.get(
                feed_url,
                headers=HEADERS_BROWSER,
                timeout=15
            )

            if res.status_code != 200:
                continue

            root = ET.fromstring(
                res.content
            )

            for item in root.findall(
                ".//item"
            )[:25]:
                titulo = _texto_local_tag(
                    item,
                    "title"
                )

                link = _texto_local_tag(
                    item,
                    "link"
                )

                descripcion = limpiar_html_tags(
                    _texto_local_tag(
                        item,
                        "description"
                    )
                )

                candidato = crear_candidato(
                    titulo=titulo,
                    fuente="20minutos",
                    url=link,
                    contexto=limitar_texto(
                        descripcion,
                        300
                    ),
                    senal="medio_general",
                    fecha=_fecha_item_rss(
                        item
                    ),
                    score_base=10
                )

                if candidato:
                    candidatos.append(
                        candidato
                    )

        except Exception as e:
            print(
                f"⚠️ Error leyendo feed {feed_url}: {e}"
            )

    return candidatos


def _modelos_para_google_search():
    disponibles = obtener_modelos_disponibles()

    preferidos = []

    # Google Search está pensado para modelos Gemini recientes.
    for patron in [
        "gemini-3",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
    ]:
        for modelo in disponibles:
            m = modelo.lower()

            if (
                patron in m
                and "image" not in m
                and "tts" not in m
                and "embedding" not in m
                and modelo not in preferidos
            ):
                preferidos.append(
                    modelo
                )

    return (
        preferidos[:6]
        or [
            "gemini-2.5-flash"
        ]
    )


def _extraer_grounding_sources(candidate):
    """
    Extrae las fuentes que Google Search realmente devolvió
    en groundingMetadata. No depende de URLs escritas por Gemini.
    """
    salida = []

    metadata = (
        candidate.get(
            "groundingMetadata",
            {}
        )
        or {}
    )

    chunks = (
        metadata.get(
            "groundingChunks",
            []
        )
        or []
    )

    for chunk in chunks:
        web = (
            chunk.get(
                "web",
                {}
            )
            or {}
        )

        uri = str(
            web.get(
                "uri",
                ""
            )
            or ""
        ).strip()

        title = str(
            web.get(
                "title",
                ""
            )
            or ""
        ).strip()

        if not uri:
            continue

        salida.append({
            "titulo": title,
            "url": uri,
        })

    # Deduplicación por URL.
    vistos = set()
    unicos = []

    for item in salida:
        clave = item.get(
            "url",
            ""
        )

        if (
            clave
            and clave not in vistos
        ):
            vistos.add(
                clave
            )
            unicos.append(
                item
            )

    return unicos


def _gemini_google_search_json(
    prompt,
    timeout=70
):
    """
    Usa la misma GEMINI_API_KEY con Google Search.

    Además del JSON pedido al modelo, devuelve en
    `_grounding_sources` las fuentes REALES que aparecen
    en groundingMetadata. Esas fuentes son la prueba
    editorial; una URL inventada dentro del texto no cuenta.
    """
    if not GEMINI_API_KEY:
        return None

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ],
        "tools": [
            {
                "google_search": {}
            }
        ],
        "generationConfig": {
            "temperature": 0.15
        }
    }

    for modelo in _modelos_para_google_search():
        url = (
            "https://generativelanguage.googleapis.com/"
            "v1beta/models/"
            f"{modelo}:generateContent"
        )

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout
            )

            if response.status_code != 200:
                continue

            data = response.json()

            candidates = data.get(
                "candidates",
                []
            )

            if not candidates:
                continue

            candidate = candidates[0]

            partes = (
                candidate
                .get(
                    "content",
                    {}
                )
                .get(
                    "parts",
                    []
                )
            )

            texto = "\n".join(
                str(
                    p.get(
                        "text",
                        ""
                    )
                )
                for p in partes
                if p.get(
                    "text"
                )
            ).strip()

            if not texto:
                continue

            parsed = extraer_json_de_respuesta(
                texto
            )

            if not isinstance(
                parsed,
                dict
            ):
                continue

            parsed[
                "_grounding_sources"
            ] = _extraer_grounding_sources(
                candidate
            )

            return parsed

        except Exception as e:
            print(
                f"⚠️ Google Search con {modelo}: {e}"
            )

    return None


def recoger_tendencias_sociales_gemini():
    """
    Amplía la detección a conversación social, pero Gemini
    SOLO sirve para DESCUBRIR.

    Una supuesta tendencia no entra en la bolsa si la búsqueda
    de Google no devuelve evidencias web reales suficientes.
    """
    prompts = [
        """
Busca en Google temas de las últimas 24-48 horas que estén
generando conversación digital REAL en España alrededor de
TikTok, Instagram, YouTube, Twitch, Kick, streamers,
influencers y creadores de contenido.

NO conviertas consejos antiguos, artículos evergreen ni una
sola publicación aislada en "tendencia".

Devuelve SOLO JSON:
{
  "tendencias": [
    {
      "titulo": "hecho o conversación concreta",
      "contexto": "qué ha ocurrido y por qué se habla de ello",
      "entidad": "persona/programa/tema principal",
      "categoria": "INFLUENCERS|STREAMERS|TIKTOK|YOUTUBE|VIRAL|OTRO"
    }
  ]
}
Máximo 8 tendencias.
""",
        """
Busca en Google noticias y conversaciones de las últimas
48 horas en España sobre realities, televisión que esté
moviendo redes, famosos, polémicas de influencers, rupturas
públicas, memes o fenómenos virales.

No inventes modas. No llames "viral" a algo si no encuentras
evidencia reciente. Descarta artículos antiguos reciclados.

Devuelve SOLO JSON:
{
  "tendencias": [
    {
      "titulo": "historia concreta",
      "contexto": "qué está pasando ahora",
      "entidad": "entidad principal",
      "categoria": "REALITY|FAMOSOS|POLEMICA|VIRAL|TV"
    }
  ]
}
Máximo 8 tendencias.
""",
        """
Busca en Google tendencias emergentes de las últimas 24-48 h
en España sobre música viral, audios, memes, vídeos, fandoms,
creadores y personajes de Internet.

Una tendencia debe tener evidencia reciente suficiente para
un artículo periodístico. Si solo encuentras una mención
aislada o contenido antiguo, no la incluyas.

Devuelve SOLO JSON:
{
  "tendencias": [
    {
      "titulo": "tema concreto",
      "contexto": "qué está pasando y qué señal reciente existe",
      "entidad": "entidad principal",
      "categoria": "MUSICA|MEME|VIRAL|CREADOR|FANDOM"
    }
  ]
}
Máximo 8 tendencias.
""",
    ]

    candidatos = []

    for prompt in prompts:
        data = _gemini_google_search_json(
            prompt
        )

        if not isinstance(
            data,
            dict
        ):
            continue

        grounding = (
            data.get(
                "_grounding_sources",
                []
            )
            or []
        )

        # Si Google Search no ha devuelto evidencias reales,
        # no usamos lo que haya "sugerido" el modelo.
        if len(
            grounding
        ) < MIN_GROUNDING_TENDENCIA:
            print(
                "⚠️ Búsqueda social descartada: "
                "pocas fuentes reales de Google."
            )
            continue

        tendencias = data.get(
            "tendencias",
            []
        )

        if not isinstance(
            tendencias,
            list
        ):
            continue

        fuente_nombres = deduplicar_lista([
            item.get(
                "titulo",
                ""
            )
            for item in grounding
            if isinstance(
                item,
                dict
            )
        ])

        urls_grounding = deduplicar_lista([
            item.get(
                "url",
                ""
            )
            for item in grounding
            if isinstance(
                item,
                dict
            )
        ])

        for item in tendencias[:8]:
            if not isinstance(
                item,
                dict
            ):
                continue

            titulo = (
                item.get(
                    "titulo",
                    ""
                )
                or ""
            ).strip()

            contexto = (
                item.get(
                    "contexto",
                    ""
                )
                or ""
            ).strip()

            entidad = (
                item.get(
                    "entidad",
                    ""
                )
                or ""
            ).strip()

            categoria = (
                item.get(
                    "categoria",
                    ""
                )
                or ""
            ).strip()

            if not titulo:
                continue

            candidato = crear_candidato(
                titulo=titulo,
                fuente=(
                    ", ".join(
                        fuente_nombres[:3]
                    )
                    or "Gemini + Google Search"
                ),
                url=(
                    urls_grounding[0]
                    if urls_grounding
                    else ""
                ),
                contexto=" | ".join(
                    x
                    for x in [
                        contexto,
                        (
                            f"Entidad: {entidad}"
                            if entidad
                            else ""
                        ),
                        (
                            f"Categoría: {categoria}"
                            if categoria
                            else ""
                        ),
                    ]
                    if x
                ),
                senal="google_search_social",
                fecha=datetime.now(
                    timezone.utc
                ).isoformat(
                    timespec="seconds"
                ),
                score_base=24
            )

            if candidato:
                candidato[
                    "entidad_sugerida"
                ] = entidad

                candidato[
                    "categoria_sugerida"
                ] = categoria

                candidato[
                    "grounding_sources"
                ] = grounding[:8]

                candidatos.append(
                    candidato
                )

    return candidatos


def construir_bolsa_tendencias():
    candidatos = []

    print(
        "📈 Buscando Google Trends España..."
    )
    candidatos.extend(
        recoger_google_trends()
    )

    print(
        "📰 Buscando múltiples verticales en Google News..."
    )
    candidatos.extend(
        recoger_google_news()
    )

    print(
        "🌐 Revisando medios generales..."
    )
    candidatos.extend(
        recoger_feeds_generales()
    )

    print(
        "🔎 Buscando conversación social con Gemini + Google Search..."
    )
    candidatos.extend(
        recoger_tendencias_sociales_gemini()
    )

    fusionados = fusionar_candidatos(
        candidatos
    )

    for c in fusionados:
        c[
            "score_base"
        ] = score_encaje_miri(
            c
        )

        penalizacion, repeticion = penalizacion_historial(
            c.get(
                "titulo",
                ""
            ),
            c.get(
                "entidad_sugerida",
                ""
            )
        )

        c[
            "penalizacion_historial"
        ] = penalizacion

        c[
            "repeticion_dura"
        ] = repeticion

        c[
            "score_preliminar"
        ] = (
            c.get(
                "score_base",
                0
            )
            - penalizacion
            + (
                max(
                    0,
                    len(
                        c.get(
                            "fuentes",
                            []
                        )
                    )
                    - 1
                )
                * 7
            )
        )

    # Descarta repetición evidente antes de gastar otra llamada a Gemini.
    fusionados = [
        c
        for c in fusionados
        if not c.get(
            "repeticion_dura"
        )
        and c.get(
            "score_preliminar",
            -999
        ) > -20
    ]

    fusionados.sort(
        key=lambda x: x.get(
            "score_preliminar",
            0
        ),
        reverse=True
    )

    return fusionados[
        :MAX_CANDIDATOS_TENDENCIA
    ]


def _historial_para_prompt(
    limite=35
):
    historial = cargar_historial()[
        -limite:
    ]

    salida = []

    for item in historial:
        salida.append({
            "tema": item.get(
                "tema",
                ""
            ),
            "entidad": item.get(
                "entidad",
                ""
            ),
            "categoria": item.get(
                "categoria",
                ""
            ),
            "fecha": item.get(
                "fecha"
            ),
        })

    return salida


def seleccionar_mejor_tendencia_gemini(
    candidatos
):
    if not candidatos:
        return None

    candidatos_prompt = []

    for i, c in enumerate(
        candidatos[:55]
    ):
        candidatos_prompt.append({
            "id": i,
            "titulo": c.get(
                "titulo",
                ""
            ),
            "contexto": limitar_texto(
                c.get(
                    "contexto",
                    ""
                ),
                420
            ),
            "senales": c.get(
                "senales",
                []
            ),
            "fuentes": c.get(
                "fuentes",
                []
            ),
            "trafico_google": c.get(
                "trafico",
                ""
            ),
            "score_preliminar": c.get(
                "score_preliminar",
                0
            ),
        })

    prompt = f"""
Eres editora de tendencias de "Miri te lo cuenta", un medio
sobre cultura de Internet, virales, influencers, streamers,
TikTokers, YouTubers, realities, famosos y polémicas que
están generando conversación digital.

Debes elegir UNA historia con potencial REAL para publicar ahora.

CANDIDATOS:
{json.dumps(candidatos_prompt, ensure_ascii=False)}

HISTORIAL RECIENTE:
{json.dumps(_historial_para_prompt(), ensure_ascii=False)}

CRITERIOS, por orden:
1. Tiene que estar ocurriendo o creciendo AHORA.
2. Debe encajar claramente con cultura de Internet / entretenimiento.
3. Mejor si aparece en más de una señal o fuente.
4. Debe tener suficiente información verificable para un artículo.
5. Evita repetir la misma noticia o prácticamente el mismo ángulo.
6. Penaliza repetir una misma persona/programa demasiado seguido.
7. Varía categorías: no llenes el medio solo de Telecinco/reality.
8. Deporte, política, sucesos o economía solo si la conversación
   online es el centro de la historia.
9. No inventes que algo es tendencia si las señales no lo justifican.

Devuelve SOLO JSON:
{{
  "id": 0,
  "score": 0,
  "entidad": "entidad principal",
  "categoria": "TIKTOK|INFLUENCERS|STREAMERS|YOUTUBE|REALITY|TV|VIRAL|MEME|MUSICA|FAMOSOS|INTERNET",
  "keywords": ["keyword1", "keyword2"],
  "por_que_ahora": "explicación breve",
  "angulo_articulo": "enfoque concreto y no repetido"
}}

score debe ser 0-100.
Si ninguno merece publicarse, usa id=-1.
"""

    modelos = obtener_modelos_disponibles()

    headers = {
        "Content-Type": "application/json"
    }

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.25
        }
    }

    for modelo in modelos:
        m = modelo.lower()

        if (
            "image" in m
            or "tts" in m
            or "embedding" in m
        ):
            continue

        url = (
            "https://generativelanguage.googleapis.com/"
            "v1beta/models/"
            f"{modelo}:generateContent"
            f"?key={GEMINI_API_KEY}"
        )

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=60
            )

            if response.status_code != 200:
                continue

            data = response.json()

            raw = (
                data["candidates"][0]
                ["content"]
                ["parts"][0]
                ["text"]
            )

            seleccion = extraer_json_de_respuesta(
                raw
            )

            idx = int(
                seleccion.get(
                    "id",
                    -1
                )
            )

            if (
                idx < 0
                or idx >= len(
                    candidatos[:55]
                )
            ):
                return None

            elegido = dict(
                candidatos[
                    idx
                ]
            )

            elegido[
                "score_tendencia"
            ] = int(
                seleccion.get(
                    "score",
                    elegido.get(
                        "score_preliminar",
                        0
                    )
                )
            )

            elegido[
                "entidad"
            ] = (
                seleccion.get(
                    "entidad",
                    ""
                )
                or elegido.get(
                    "entidad_sugerida",
                    ""
                )
                or ""
            ).strip()

            elegido[
                "categoria"
            ] = (
                seleccion.get(
                    "categoria",
                    ""
                )
                or elegido.get(
                    "categoria_sugerida",
                    ""
                )
                or "INTERNET"
            ).strip()

            elegido[
                "keywords"
            ] = deduplicar_lista(
                asegurar_lista(
                    seleccion.get(
                        "keywords",
                        []
                    )
                )
            )[:10]

            elegido[
                "por_que_ahora"
            ] = (
                seleccion.get(
                    "por_que_ahora",
                    ""
                )
                or ""
            ).strip()

            elegido[
                "angulo_articulo"
            ] = (
                seleccion.get(
                    "angulo_articulo",
                    ""
                )
                or ""
            ).strip()

            # Segunda comprobación de repetición con la entidad ya resuelta.
            penalizacion, repeticion = penalizacion_historial(
                elegido.get(
                    "titulo",
                    ""
                ),
                elegido.get(
                    "entidad",
                    ""
                )
            )

            if repeticion:
                print(
                    "⚠️ Gemini eligió un tema demasiado repetido; "
                    "se descarta."
                )
                return None

            elegido[
                "score_tendencia"
            ] -= min(
                35,
                penalizacion
            )

            return elegido

        except Exception as e:
            print(
                f"⚠️ Selección de tendencia con {modelo}: {e}"
            )

    # Fallback sin Gemini: mejor score preliminar.
    for candidato in candidatos:
        if not candidato.get(
            "repeticion_dura"
        ):
            candidato = dict(
                candidato
            )

            candidato[
                "score_tendencia"
            ] = candidato.get(
                "score_preliminar",
                0
            )

            candidato[
                "entidad"
            ] = candidato.get(
                "entidad_sugerida",
                ""
            )

            candidato[
                "categoria"
            ] = candidato.get(
                "categoria_sugerida",
                "INTERNET"
            )

            candidato[
                "keywords"
            ] = tokenizar(
                candidato.get(
                    "titulo",
                    ""
                )
            )[:8]

            return candidato

    return None


def obtener_nuevo_tema_viral():
    candidatos = construir_bolsa_tendencias()

    print(
        "📊 Candidatos útiles encontrados: "
        f"{len(candidatos)}"
    )

    if not candidatos:
        print(
            "⚠️ No hay candidatos suficientes."
        )
        return None

    restantes = list(
        candidatos
    )

    intentos = min(
        MAX_INTENTOS_VERIFICACION,
        len(
            restantes
        )
    )

    for numero in range(
        1,
        intentos + 1
    ):
        elegido = seleccionar_mejor_tendencia_gemini(
            restantes
        )

        if not elegido:
            break

        print(
            f"🔎 Verificando candidata {numero}/{intentos}: "
            f"{elegido.get('titulo')}"
        )

        investigacion = investigar_tema_actual(
            elegido
        )

        publicable, motivo = investigacion_es_publicable(
            investigacion
        )

        if publicable:
            tema_corregido = (
                investigacion.get(
                    "tema_corregido",
                    ""
                )
                or ""
            ).strip()

            if tema_corregido:
                elegido[
                    "titulo_original_detectado"
                ] = elegido.get(
                    "titulo",
                    ""
                )

                elegido[
                    "titulo"
                ] = tema_corregido

            elegido[
                "_investigacion_verificada"
            ] = investigacion

            print(
                "✅ Tendencia verificada: "
                f"{elegido.get('titulo')}"
            )

            print(
                "   Fuentes reales Google: "
                f"{len(investigacion.get('_grounding_sources', []))}"
            )

            return elegido

        print(
            "🚫 Tendencia descartada tras verificar: "
            f"{motivo}"
        )

        # Elimina la candidata rechazada y vuelve a elegir.
        titulo_rechazado = normalizar_texto(
            elegido.get(
                "titulo_original_detectado",
                ""
            )
            or elegido.get(
                "titulo",
                ""
            )
        )

        nuevos = []

        for c in restantes:
            if normalizar_texto(
                c.get(
                    "titulo",
                    ""
                )
            ) == titulo_rechazado:
                continue

            nuevos.append(
                c
            )

        restantes = nuevos

        if not restantes:
            break

    print(
        "⚠️ Ninguna tendencia sobrevivió la verificación. "
        "No se publicará relleno ni una moda inventada."
    )

    return None


def investigar_tema_actual(
    tendencia
):
    """
    Verificación editorial obligatoria.

    La tendencia puede haber sido descubierta por Gemini, Trends
    o RSS, pero aquí debe sobrevivir una segunda búsqueda.
    """
    if not tendencia:
        return {}

    prompt = f"""
Comprueba en Google si esta historia es REAL, RECIENTE y tiene
información suficiente para publicarse hoy en un medio español
sobre cultura de Internet.

TEMA:
{tendencia.get('titulo', '')}

CONTEXTO DETECTADO:
{tendencia.get('contexto', '')}

POR QUÉ AHORA:
{tendencia.get('por_que_ahora', '')}

ÁNGULO PROPUESTO:
{tendencia.get('angulo_articulo', '')}

REGLAS MUY IMPORTANTES:
- No presupongas que es una tendencia porque el texto lo diga.
- Comprueba expresamente si hay evidencia reciente.
- Una noticia evergreen, un consejo antiguo o una publicación
  aislada NO es suficiente.
- No inventes una moda de TikTok a partir de un artículo
  relacionado de forma vaga.
- Si el titular inicial exagera o deforma los hechos, indícalo.
- Si no se puede verificar, marca verificable=false.
- Prioriza las últimas 48 h; admite hasta 7 días solo si sigue
  siendo claramente conversación actual.

Devuelve SOLO JSON:
{{
  "verificable": true,
  "es_tendencia_real": true,
  "confianza": 0,
  "tema_corregido": "formulación factual y precisa del tema",
  "resumen_verificado": "qué está ocurriendo realmente",
  "hechos_confirmados": [
    "hecho comprobable 1",
    "hecho comprobable 2"
  ],
  "afirmaciones_a_evitar": [
    "dato, exageración o rumor que no debe publicarse"
  ],
  "fuentes": [
    {{
      "nombre": "medio/sitio",
      "url": "https://..."
    }}
  ]
}}

confianza: 0-100.
"""

    data = _gemini_google_search_json(
        prompt,
        timeout=90
    )

    return (
        data
        if isinstance(
            data,
            dict
        )
        else {}
    )


def investigacion_es_publicable(
    investigacion
):
    if not isinstance(
        investigacion,
        dict
    ):
        return False, "sin investigación"

    if investigacion.get(
        "verificable"
    ) is not True:
        return False, "Google no la considera verificable"

    if investigacion.get(
        "es_tendencia_real"
    ) is not True:
        return False, "no se confirmó como tendencia/conversación actual"

    try:
        confianza = int(
            investigacion.get(
                "confianza",
                0
            )
        )
    except Exception:
        confianza = 0

    if confianza < MIN_CONFIANZA_INVESTIGACION:
        return False, f"confianza insuficiente ({confianza})"

    grounding = (
        investigacion.get(
            "_grounding_sources",
            []
        )
        or []
    )

    if len(
        grounding
    ) < MIN_GROUNDING_INVESTIGACION:
        return False, (
            "menos de "
            f"{MIN_GROUNDING_INVESTIGACION} fuentes reales de Google"
        )

    hechos = asegurar_lista(
        investigacion.get(
            "hechos_confirmados",
            []
        )
    )

    if len(
        hechos
    ) < 2:
        return False, "menos de 2 hechos confirmados"

    return True, "ok"




# ==========================================
# 5. GEMINI - GENERACIÓN DEL ARTÍCULO
# ==========================================
def obtener_modelos_disponibles():
    url_list = (
        "https://generativelanguage.googleapis.com/"
        "v1beta/models"
        f"?key={GEMINI_API_KEY}"
    )

    try:
        res = requests.get(
            url_list,
            timeout=10
        )

        if res.status_code == 200:
            models_data = res.json().get(
                "models",
                []
            )

            modelos_validos = [
                m.get("name", "")
                .replace("models/", "")
                for m in models_data
                if "generateContent"
                in m.get(
                    "supportedGenerationMethods",
                    []
                )
                and "gemini"
                in m.get(
                    "name",
                    ""
                ).lower()
            ]

            if modelos_validos:
                return modelos_validos

    except Exception:
        pass

    return [
        "gemini-2.5-flash",
        "gemini-2.5-pro"
    ]


def generar_articulo_miri(tema_viral, investigacion=None, tendencia=None):
    modelos = obtener_modelos_disponibles()

    prompt = f"""
Eres la redactora principal del portal de actualidad,
entretenimiento y cultura de Internet "Miri te lo cuenta".

Escribe un artículo ameno, fresco, dinámico y con tono
de salseo sobre esta tendencia:

"{tema_viral}"

CONTEXTO DE TENDENCIA:
{json.dumps(tendencia or {}, ensure_ascii=False)}

INVESTIGACIÓN WEB PREVIA:
{json.dumps(investigacion or {}, ensure_ascii=False)}

IMPORTANTE:
- Basa TODOS los hechos concretos en la investigación previa.
- Si la investigación dice que algo no está confirmado, no lo presentes como hecho.
- Si falta una fecha o dato oficial, dilo claramente en vez de rellenarlo.
- No inventes hechos concretos que no estén justificados.
- No inventes declaraciones textuales.
- NO llames a algo "moda", "reto", "tendencia" o "viral" salvo que la
  investigación verificada confirme expresamente que lo es.
- No transformes un consejo, un vídeo aislado o un artículo evergreen
  en una supuesta tendencia de TikTok.
- Evita titulares que den asco, ridiculicen o exageren si eso no está
  respaldado por los hechos.
- Tono cercano y con personalidad, pero periodístico: evita muletillas
  repetitivas como "agárrate fuerte", "internet se ha vuelto a superar",
  "mirilovers" o "familia de Miri".
- Separa claramente hecho, contexto y reacción de usuarios.
- El artículo debe tener entre 4 y 7 párrafos cortos.
- Responde ÚNICAMENTE con un objeto JSON válido.
- No uses Markdown.

Devuelve EXACTAMENTE:

{{
  "titulo": "Titular llamativo y claro",
  "contenido_html": "<p>Primer párrafo...</p><p>Segundo párrafo...</p>",
  "titulo_miniatura": "TITULAR CORTO PARA LA MINIATURA",
  "categoria_visual": "ESTÁ PASANDO / SE HA LIADO / TE PONGO EN CONTEXTO / INTERNET ESTÁ HABLANDO / ¿QUIÉN ES? / MIRI REACCIONA / REALITY / VIRAL / INTERNET",
  "tipo_visual": "persona | programa | marca | evento | lugar | tema",
  "entidad_principal": "entidad visual principal",
  "contexto_visual": "contexto breve para evitar resultados erróneos",
  "busquedas_imagen": [
    "búsqueda 1",
    "búsqueda 2",
    "búsqueda 3"
  ]
}}

REGLAS PARA tipo_visual:

- "persona":
  si la noticia trata principalmente sobre UNA persona concreta.

- "programa":
  si el sujeto visual principal es un programa, reality,
  serie o formato televisivo/digital.

- "marca":
  si el sujeto principal es una empresa, plataforma o marca.

- "evento":
  si la noticia gira alrededor de un evento concreto.

- "lugar":
  si visualmente es más útil mostrar una ciudad, localidad,
  playa, recinto, país, edificio o destino real.

- "tema":
  si no existe una entidad concreta y conviene una imagen
  conceptual relacionada.

REGLAS EDITORIALES PARA ELEGIR LA IMAGEN:

1. Si trata de UNA PERSONA:
   - entidad_principal = nombre completo exacto.
   - TODAS las búsquedas deben incluir el nombre.
   - añade profesión/programa/contexto para evitar homónimos.
   - Ejemplo:
     ["Danny León skateboard",
      "Danny León skater Spain",
      "Danny León Red Bull skate"]

2. Si trata de un LUGAR:
   - entidad_principal = nombre exacto del lugar.
   - prioriza el lugar frente a una imagen genérica.
   - Ejemplo para Salou:
     ["Salou beach",
      "Salou promenade Spain",
      "Salou Tarragona skyline"]

3. Si una noticia habla de un traslado, evento o emisión
   desde una ciudad y no hay una persona concreta como
   protagonista, normalmente elige "lugar".

4. Si trata de un programa:
   usa el nombre exacto del programa + contexto.

5. No inventes personajes visuales.

6. No uses búsquedas genéricas como:
   "news", "party", "viral news", "people".

7. Si tipo_visual = "tema":
   - entidad_principal debe ser el CONCEPTO VISUAL concreto,
     nunca "TikTok", "Instagram", "Internet" o "viral".
   - busquedas_imagen deben describir objetos, acciones o escenas
     fotografiables y concretas.
   - escribe preferiblemente las búsquedas en INGLÉS porque
     Wikimedia Commons y Openverse suelen devolver mejores resultados.
   - NO incluyas "TikTok", "viral", "trend", "social media" ni "news"
     salvo que la plataforma sea realmente el objeto que debe verse.
   - Ejemplo:
     tema sobre desodorantes -> ["deodorant spray", "deodorant can bathroom",
     "personal hygiene deodorant"], NO ["TikTok deodorant viral trend"].

8. titulo_miniatura:
   - 3 a 9 palabras.
   - máximo 60 caracteres.
   - visual y claro.
"""

    headers = {
        "Content-Type": "application/json"
    }

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.7
        }
    }

    for modelo in modelos:
        url = (
            "https://generativelanguage.googleapis.com/"
            "v1beta/models/"
            f"{modelo}:generateContent"
            f"?key={GEMINI_API_KEY}"
        )

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=50
            )

            if response.status_code != 200:
                continue

            data = response.json()

            raw_text = (
                data["candidates"][0]
                ["content"]
                ["parts"][0]
                ["text"]
                .strip()
            )

            articulo = extraer_json_de_respuesta(
                raw_text
            )

            titulo = html.unescape(
                articulo.get(
                    "titulo",
                    ""
                )
            ).strip().strip('"').strip("'")

            contenido_html = articulo.get(
                "contenido_html",
                ""
            )

            titulo_miniatura = html.unescape(
                articulo.get(
                    "titulo_miniatura",
                    titulo
                )
            ).strip()

            categoria_visual = (
                articulo.get(
                    "categoria_visual",
                    "ACTUALIDAD"
                )
                or "ACTUALIDAD"
            ).strip()

            tipo_visual = normalizar_tipo_visual(
                articulo.get(
                    "tipo_visual",
                    "tema"
                )
            )

            entidad_principal = html.unescape(
                articulo.get(
                    "entidad_principal",
                    ""
                )
                or ""
            ).strip()

            contexto_visual = html.unescape(
                articulo.get(
                    "contexto_visual",
                    ""
                )
                or ""
            ).strip()

            busquedas_imagen = deduplicar_lista(
                asegurar_lista(
                    articulo.get(
                        "busquedas_imagen"
                    )
                )
            )

            if not busquedas_imagen:
                base = (
                    entidad_principal
                    or contexto_visual
                    or tema_viral
                )
                busquedas_imagen = [base]

            return {
                "titulo": titulo,
                "contenido_html": contenido_html,
                "titulo_miniatura": limitar_texto(
                    titulo_miniatura,
                    72
                ),
                "categoria_visual": categoria_visual,
                "tipo_visual": tipo_visual,
                "entidad_principal": entidad_principal,
                "contexto_visual": contexto_visual,
                "busquedas_imagen": busquedas_imagen,
            }

        except Exception as e:
            print(
                f"⚠️ Fallo con el modelo {modelo}: {e}"
            )
            continue

    raise Exception(
        "❌ Error crítico: Gemini no pudo generar el artículo."
    )



# ==========================================
# 5B. PACK DE COPIES PARA REDES SOCIALES
# ==========================================
def normalizar_hashtags(valor):
    hashtags = []

    for item in asegurar_lista(
        valor
    ):
        item = normalizar_texto(
            item
        ).replace(
            " ",
            ""
        )

        if not item:
            continue

        tag = (
            "#"
            + item.lstrip("#")
        )

        if (
            len(tag) <= 40
            and tag not in hashtags
        ):
            hashtags.append(
                tag
            )

    return hashtags[:6]


def generar_pack_social_gemini(
    tema_viral,
    titulo_articulo,
    contenido_html,
    entidad_principal,
    contexto_visual
):
    """
    Genera copies orientados a descubrimiento/búsqueda social.

    No usa datos de volumen de búsqueda en tiempo real.
    Optimiza por claridad semántica:
    - entidad o tema exacto al principio
    - nombres propios completos
    - lenguaje natural
    - sin keyword stuffing
    - sin inventar hechos no presentes en el artículo
    """
    modelos = obtener_modelos_disponibles()

    texto_articulo = limpiar_html_tags(
        contenido_html
    )

    texto_articulo = limitar_texto(
        texto_articulo,
        3500
    )

    prompt = f"""
Eres estratega SEO/social para "Miri te lo cuenta".

A partir EXCLUSIVAMENTE de este artículo, prepara un pack
para publicar automáticamente en redes.

TEMA ORIGINAL:
{tema_viral}

TÍTULO DEL ARTÍCULO:
{titulo_articulo}

ENTIDAD PRINCIPAL:
{entidad_principal}

CONTEXTO:
{contexto_visual}

TEXTO DEL ARTÍCULO:
{texto_articulo}

OBJETIVO:
Que cada texto sea fácil de encontrar cuando alguien busque
el nombre de la persona, programa, reality, influencer, ciudad,
polémica o tema principal dentro de cada red.

REGLAS:
- No inventes ningún hecho.
- No inventes fechas, cifras ni declaraciones.
- Empieza los titulares por la entidad/tema principal cuando sea natural.
- Usa nombres propios completos.
- No hagas keyword stuffing.
- No uses hashtags genéricos como #fyp, #viral o #parati salvo
  que sean realmente parte del tema.
- Prioriza 3-6 hashtags muy concretos.
- El titular de la imagen debe entenderse sin leer el pie.
- En Instagram y TikTok usa "link en bio" en vez de pegar una URL.
- En Facebook y YouTube deja el marcador {{URL}} para el artículo.
- TikTok no necesita saltos de línea.
- YouTube Shorts: título máximo 100 caracteres.
- titulo_social: máximo 68 caracteres y 4-10 palabras.

Devuelve ÚNICAMENTE JSON válido con esta estructura:

{{
  "titulo_social": "Titular corto, claro y buscable",
  "facebook": "Copy de Facebook con contexto breve y {{URL}}",
  "instagram": "Copy de Instagram con gancho, palabras clave y CTA link en bio",
  "tiktok": "Copy corto de TikTok en una sola línea",
  "youtube_titulo": "Título para YouTube Shorts",
  "youtube_descripcion": "Descripción breve con contexto y {{URL}}",
  "hashtags": ["#tema1", "#tema2", "#tema3"],
  "keywords_busqueda": ["keyword 1", "keyword 2", "keyword 3"]
}}
"""

    headers = {
        "Content-Type": "application/json"
    }

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.45
        }
    }

    for modelo in modelos:
        url = (
            "https://generativelanguage.googleapis.com/"
            "v1beta/models/"
            f"{modelo}:generateContent"
            f"?key={GEMINI_API_KEY}"
        )

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=50
            )

            if response.status_code != 200:
                continue

            data = response.json()

            raw_text = (
                data["candidates"][0]
                ["content"]
                ["parts"][0]
                ["text"]
                .strip()
            )

            pack = extraer_json_de_respuesta(
                raw_text
            )

            titulo_social = html.unescape(
                pack.get(
                    "titulo_social",
                    titulo_articulo
                )
                or titulo_articulo
            ).strip()

            hashtags = normalizar_hashtags(
                pack.get(
                    "hashtags",
                    []
                )
            )

            keywords = deduplicar_lista(
                asegurar_lista(
                    pack.get(
                        "keywords_busqueda",
                        []
                    )
                )
            )[:8]

            return {
                "titulo_social": limitar_texto(
                    titulo_social,
                    68
                ),
                "facebook": (
                    html.unescape(
                        str(
                            pack.get(
                                "facebook",
                                ""
                            )
                            or ""
                        )
                    ).strip()
                ),
                "instagram": (
                    html.unescape(
                        str(
                            pack.get(
                                "instagram",
                                ""
                            )
                            or ""
                        )
                    ).strip()
                ),
                "tiktok": (
                    html.unescape(
                        str(
                            pack.get(
                                "tiktok",
                                ""
                            )
                            or ""
                        )
                    )
                    .replace(
                        "\n",
                        " "
                    )
                    .strip()
                ),
                "youtube_titulo": limitar_texto(
                    html.unescape(
                        str(
                            pack.get(
                                "youtube_titulo",
                                titulo_social
                            )
                            or titulo_social
                        )
                    ).strip(),
                    100
                ),
                "youtube_descripcion": (
                    html.unescape(
                        str(
                            pack.get(
                                "youtube_descripcion",
                                ""
                            )
                            or ""
                        )
                    ).strip()
                ),
                "hashtags": hashtags,
                "keywords_busqueda": keywords,
            }

        except Exception as e:
            print(
                f"⚠️ Pack social: fallo con {modelo}: {e}"
            )

    # Fallback determinista si Gemini falla.
    titulo_fallback = limitar_texto(
        titulo_articulo,
        68
    )

    return {
        "titulo_social": titulo_fallback,
        "facebook": (
            f"{titulo_fallback}\n\n"
            "Te cuento el contexto completo aquí: {URL}"
        ),
        "instagram": (
            f"{titulo_fallback}. "
            "Te cuento el contexto completo en el blog. Link en bio."
        ),
        "tiktok": (
            f"{titulo_fallback}. "
            "Más contexto en el blog, link en bio."
        ),
        "youtube_titulo": limitar_texto(
            titulo_fallback,
            100
        ),
        "youtube_descripcion": (
            f"{titulo_fallback}\n\n"
            "Artículo completo: {URL}"
        ),
        "hashtags": [],
        "keywords_busqueda": [
            entidad_principal
            or tema_viral
        ],
    }


# ==========================================
# 6. BÚSQUEDAS VISUALES
# ==========================================
def enriquecer_busquedas_lugar(
    entidad_principal,
    contexto_visual,
    busquedas_imagen
):
    entidad = (
        entidad_principal
        or ""
    ).strip()

    contexto = (
        contexto_visual
        or ""
    ).strip()

    base = []

    if entidad:
        base.extend([
            entidad,
            f"{entidad} city",
            f"{entidad} skyline",
            f"{entidad} city view",
            f"{entidad} beach",
            f"{entidad} promenade",
            f"{entidad} tourism",
            f"{entidad} landscape",
        ])

    if entidad and contexto:
        base.extend([
            f"{entidad} {contexto}",
            f"{entidad} {contexto} city",
        ])

    return deduplicar_lista(
        (busquedas_imagen or [])
        + base
    )[:8]


def enriquecer_busquedas_genericas(
    tipo_visual,
    entidad_principal,
    contexto_visual,
    busquedas_imagen
):
    if tipo_visual == "lugar":
        return enriquecer_busquedas_lugar(
            entidad_principal,
            contexto_visual,
            busquedas_imagen
        )

    queries = list(
        busquedas_imagen
        or []
    )

    entidad = (
        entidad_principal
        or ""
    ).strip()

    contexto = (
        contexto_visual
        or ""
    ).strip()

    if entidad and contexto:
        queries.append(
            f"{entidad} {contexto}"
        )

    if entidad:
        queries.append(entidad)

    if tipo_visual == "persona" and entidad:
        queries.extend([
            f"{entidad} portrait",
            f"{entidad} official",
            f"{entidad} {contexto}".strip(),
        ])

    elif tipo_visual == "programa" and entidad:
        queries.extend([
            f"{entidad} television",
            f"{entidad} TV show",
        ])

    elif tipo_visual == "marca" and entidad:
        queries.extend([
            f"{entidad} brand",
            f"{entidad} headquarters",
        ])

    elif tipo_visual == "evento" and entidad:
        queries.extend([
            f"{entidad} event",
            f"{entidad} venue",
        ])

    elif tipo_visual == "tema":
        # Para temas conceptuales buscamos el OBJETO/ACCIÓN concreta,
        # no la plataforma ni la palabra "viral".
        conceptuales = []

        for q in list(queries) + [contexto, entidad]:
            q_limpia = limpiar_query_conceptual(
                q
            )

            if not q_limpia:
                continue

            conceptuales.extend([
                q_limpia,
                f"{q_limpia} photo",
            ])

        # Primero las búsquedas limpias y concretas.
        queries = conceptuales + queries

    return deduplicar_lista(
        queries
    )[:10]


# ==========================================
# 7. LICENCIAS
# ==========================================
def licencia_wikimedia_permitida(
    licencia
):
    texto = normalizar_texto(
        licencia
    )

    if not texto:
        return False

    # Rechazo NC / ND
    if (
        "noncommercial" in texto
        or "no derivatives" in texto
        or re.search(
            r"(^|\s)nc($|\s)",
            texto
        )
        or re.search(
            r"(^|\s)nd($|\s)",
            texto
        )
    ):
        return False

    permitidas = [
        "public domain",
        "cc0",
        "cc by",
        "cc by sa",
        "creative commons attribution",
    ]

    return any(
        x in texto
        for x in permitidas
    )


# ==========================================
# 8. WIKIMEDIA COMMONS
# ==========================================
def extraer_extmeta_val(
    extmetadata,
    key
):
    try:
        return limpiar_html_tags(
            html.unescape(
                (
                    extmetadata.get(key)
                    or {}
                ).get(
                    "value",
                    ""
                )
            )
        )
    except Exception:
        return ""


def buscar_imagenes_wikimedia(
    query,
    limit=10
):
    query = (
        query
        or ""
    ).strip()

    if not query:
        return []

    print(
        f"🔎 Wikimedia: {query}"
    )

    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": min(limit, 20),
        "prop": "imageinfo|info",
        "iiprop": "url|mime|size|extmetadata",
        "inprop": "url",
    }

    try:
        r = requests.get(
            WIKIMEDIA_API,
            params=params,
            headers=HEADERS_BROWSER,
            timeout=20
        )

        if r.status_code != 200:
            return []

        pages = (
            r.json()
            .get("query", {})
            .get("pages", {})
        )

        resultados = []

        for page in pages.values():
            imageinfo = (
                page.get("imageinfo")
                or [{}]
            )[0]

            mime = (
                imageinfo.get("mime")
                or ""
            ).lower()

            url = imageinfo.get(
                "url"
            )

            width = int(
                imageinfo.get("width")
                or 0
            )

            height = int(
                imageinfo.get("height")
                or 0
            )

            if not url:
                continue

            if mime not in {
                "image/jpeg",
                "image/png",
                "image/webp",
            }:
                continue

            if (
                width < 500
                or height < 300
            ):
                continue

            extmetadata = (
                imageinfo.get(
                    "extmetadata",
                    {}
                )
                or {}
            )

            title = (
                page.get(
                    "title",
                    ""
                )
                .replace(
                    "File:",
                    ""
                )
                .strip()
            )

            description = extraer_extmeta_val(
                extmetadata,
                "ImageDescription"
            )

            artist = extraer_extmeta_val(
                extmetadata,
                "Artist"
            )

            license_name = extraer_extmeta_val(
                extmetadata,
                "LicenseShortName"
            )

            if not licencia_wikimedia_permitida(
                license_name
            ):
                continue

            credit = extraer_extmeta_val(
                extmetadata,
                "Credit"
            )

            object_name = extraer_extmeta_val(
                extmetadata,
                "ObjectName"
            )

            categories = extraer_extmeta_val(
                extmetadata,
                "Categories"
            )

            license_url = extraer_extmeta_val(
                extmetadata,
                "LicenseUrl"
            )

            meta = " ".join(
                filter(
                    None,
                    [
                        title,
                        description,
                        artist,
                        credit,
                        object_name,
                        categories,
                    ]
                )
            )

            resultados.append({
                "engine": "wikimedia",
                "query": query,
                "title": title,
                "description": description,
                "artist": artist,
                "license": license_name,
                "license_url": license_url,
                "credit": credit,
                "url": url,
                "fallback_url": "",
                "mime": mime,
                "width": width,
                "height": height,
                "source_page": (
                    page.get("fullurl")
                    or imageinfo.get(
                        "descriptionurl",
                        ""
                    )
                ),
                "meta": meta,
            })

        return resultados

    except Exception as e:
        print(
            f"⚠️ Error Wikimedia '{query}': {e}"
        )
        return []


# ==========================================
# 9. OPENVERSE
# ==========================================
def buscar_imagenes_openverse(
    query,
    limit=12
):
    query = (
        query
        or ""
    ).strip()

    if not query:
        return []

    print(
        f"🔎 Openverse: {query}"
    )

    params = {
        "q": query,
        "page_size": min(
            limit,
            20
        ),
    }

    try:
        r = requests.get(
            OPENVERSE_API,
            params=params,
            headers=HEADERS_BROWSER,
            timeout=20
        )

        if r.status_code != 200:
            return []

        resultados = []

        for item in (
            r.json()
            .get(
                "results",
                []
            )
        ):
            licencia = (
                item.get("license")
                or ""
            ).lower().strip()

            if (
                licencia
                not in OPENVERSE_LICENSES_PERMITIDAS
            ):
                continue

            artist = limpiar_html_tags(
                item.get("creator")
                or ""
            )

            if (
                licencia
                in {"by", "by-sa"}
                and not artist
            ):
                continue

            url = (
                item.get("url")
                or item.get("thumbnail")
            )

            fallback_url = (
                item.get("thumbnail")
                or ""
            )

            if not url:
                continue

            width = int(
                item.get("width")
                or 0
            )

            height = int(
                item.get("height")
                or 0
            )

            # Si hay dimensiones y son diminutas, descartamos.
            if (
                width
                and height
                and (
                    width < 500
                    or height < 300
                )
            ):
                continue

            title = limpiar_html_tags(
                item.get("title")
                or ""
            )

            description = limpiar_html_tags(
                item.get("description")
                or ""
            )

            source_page = (
                item.get(
                    "foreign_landing_url"
                )
                or item.get(
                    "detail_url"
                )
                or ""
            )

            license_url = (
                item.get(
                    "license_url"
                )
                or ""
            )

            source = (
                item.get("source")
                or "Openverse"
            )

            meta = " ".join(
                filter(
                    None,
                    [
                        title,
                        description,
                        artist,
                        source,
                        source_page,
                    ]
                )
            )

            license_label = (
                "Public Domain Mark"
                if licencia == "pdm"
                else f"CC {licencia.upper()}"
            )

            resultados.append({
                "engine": "openverse",
                "query": query,
                "title": title,
                "description": description,
                "artist": (
                    artist
                    or "Dominio público"
                ),
                "license": license_label,
                "license_url": license_url,
                "credit": source,
                "url": url,
                "fallback_url": fallback_url,
                "mime": "",
                "width": width,
                "height": height,
                "source_page": source_page,
                "meta": meta,
            })

        return resultados

    except Exception as e:
        print(
            f"⚠️ Error Openverse '{query}': {e}"
        )
        return []


# ==========================================
# 10. DETECCIÓN DE FOTOS DE GRUPO
# ==========================================
def parece_imagen_grupal_por_metadatos(
    candidato
):
    meta = normalizar_texto(
        " ".join([
            candidato.get(
                "title",
                ""
            ),
            candidato.get(
                "description",
                ""
            ),
            candidato.get(
                "meta",
                ""
            ),
        ])
    )

    palabras_grupo = {
        "group",
        "groups",
        "team",
        "teams",
        "cast",
        "family",
        "friends",
        "people",
        "crowd",
        "members",
        "grupo",
        "grupos",
        "equipo",
        "equipos",
        "reparto",
        "familia",
        "amigos",
        "gente",
        "multitud",
        "miembros",
        "crew",
        "squad",
    }

    tokens = set(
        meta.split()
    )

    return any(
        palabra in tokens
        for palabra in palabras_grupo
    )


def contar_personas_aprox_imagen(
    ruta_imagen
):
    """
    Filtro adicional opcional usando OpenCV si ya está instalado.
    NO es obligatorio.

    Devuelve:
    - None: no se pudo analizar
    - 0,1,2...: número aproximado de caras detectadas

    Si cv2 no está disponible, el bot sigue funcionando solo
    con metadatos, sin romper nada.
    """
    try:
        import cv2

        img = cv2.imread(
            ruta_imagen
        )

        if img is None:
            return None

        gray = cv2.cvtColor(
            img,
            cv2.COLOR_BGR2GRAY
        )

        cascade_path = (
            cv2.data.haarcascades
            + "haarcascade_frontalface_default.xml"
        )

        detector = cv2.CascadeClassifier(
            cascade_path
        )

        caras = detector.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(45, 45)
        )

        return len(caras)

    except Exception:
        return None



# Palabras que describen plataforma/viralidad, pero no el objeto
# que debería aparecer en la fotografía.
STOPWORDS_VISUALES = {
    "tiktok", "instagram", "youtube", "facebook", "twitter", "x",
    "viral", "trend", "trending", "tendencia", "moda", "internet",
    "social", "media", "redes", "news", "noticia", "noticias",
    "spain", "espana", "españa", "today", "hoy", "video", "videos",
    "reel", "reels", "short", "shorts", "online",
}


def tokens_visuales_significativos(texto):
    texto = normalizar_texto(
        texto
    )

    tokens = re.findall(
        r"[a-z0-9áéíóúüñ]+",
        texto
    )

    salida = []

    for token in tokens:
        if len(token) < 3:
            continue

        if token in STOPWORDS_VISUALES:
            continue

        if token not in salida:
            salida.append(
                token
            )

    return salida


def limpiar_query_conceptual(query):
    """
    Elimina palabras de plataforma/viralidad para que un tema como
    'TikTok viral deodorant trend' termine buscando el OBJETO real:
    'deodorant', no logos o capturas genéricas de TikTok.
    """
    tokens = tokens_visuales_significativos(
        query
    )

    return " ".join(
        tokens[:8]
    ).strip()


def coincidencia_query_con_metadatos(
    candidato,
    texto_total
):
    """
    Importante: NO suma puntos por haber hecho una búsqueda.

    Solo usa la query para saber qué conceptos comprobar y exige que
    esos conceptos aparezcan DE VERDAD en título/descripción/metadatos
    del candidato.
    """
    query = candidato.get(
        "search_query",
        ""
    )

    query_tokens = set(
        tokens_visuales_significativos(
            query
        )
    )

    meta_tokens = set(
        tokens_visuales_significativos(
            texto_total
        )
    )

    if not query_tokens:
        return 0, []

    matches = sorted(
        query_tokens.intersection(
            meta_tokens
        )
    )

    return len(matches), matches


# ==========================================
# 11. PUNTUACIÓN DE CANDIDATOS
# ==========================================
def puntuar_candidato(
    candidato,
    tipo_visual,
    entidad_principal,
    contexto_visual
):
    score = 0

    title = candidato.get(
        "title",
        ""
    )

    meta = candidato.get(
        "meta",
        ""
    )

    texto_total = (
        f"{title} {meta}"
    )

    meta_norm = normalizar_texto(
        texto_total
    )

    width = int(
        candidato.get(
            "width",
            0
        )
        or 0
    )

    height = int(
        candidato.get(
            "height",
            0
        )
        or 0
    )

    cobertura_entidad = (
        porcentaje_cobertura_entidad(
            entidad_principal,
            texto_total
        )
    )

    cobertura_contexto = (
        porcentaje_cobertura_entidad(
            contexto_visual,
            texto_total
        )
    )

    # Calidad / resolución
    if width >= 1600:
        score += 12
    elif width >= 1200:
        score += 9
    elif width >= 900:
        score += 6
    elif width >= 700:
        score += 3

    if height >= 630:
        score += 4

    # Ratio horizontal útil
    if width > 0 and height > 0:
        ratio = width / height
        ideal = 1200 / 630
        diff = abs(
            ratio - ideal
        )

        if diff < 0.2:
            score += 10
        elif diff < 0.45:
            score += 7
        elif diff < 0.8:
            score += 3

    # Penalizamos recursos no fotográficos.
    palabras_negativas = [
        "logo",
        "icon",
        "map",
        "flag",
        "coat of arms",
        "escudo",
        "bandera",
        "diagram",
        "vector",
        "svg",
        "poster",
        "cartel",
    ]

    if any(
        p in meta_norm
        for p in palabras_negativas
    ):
        score -= 22

    # Wikimedia recibe pequeño bonus al ser fuente prioritaria.
    if (
        candidato.get("engine")
        == "wikimedia"
    ):
        score += 5

    # --------------------
    # PERSONA
    # --------------------
    if tipo_visual == "persona":
        if entidad_principal:
            entidad_exacta = contiene_entidad_exacta(
                entidad_principal,
                texto_total
            )

            if entidad_exacta:
                score += 48
            else:
                score += int(
                    cobertura_entidad
                    * 28
                )

                # Regla estricta:
                # si buscamos una persona y los metadatos no
                # contienen suficientemente el nombre, se descarta.
                if cobertura_entidad < 0.75:
                    return -999

        if contexto_visual:
            score += int(
                cobertura_contexto
                * 12
            )

        # FOTO DE GRUPO:
        # no la aceptamos alegremente cuando el artículo trata
        # exclusivamente sobre una persona.
        if parece_imagen_grupal_por_metadatos(
            candidato
        ):
            return -999

    # --------------------
    # PROGRAMA / MARCA / EVENTO
    # --------------------
    elif tipo_visual in {
        "programa",
        "marca",
        "evento",
    }:
        if entidad_principal:
            if contiene_entidad_exacta(
                entidad_principal,
                texto_total
            ):
                score += 28
            else:
                score += int(
                    cobertura_entidad
                    * 22
                )

                if cobertura_entidad < 0.50:
                    score -= 25

        if contexto_visual:
            score += int(
                cobertura_contexto
                * 10
            )

    # --------------------
    # LUGAR
    # --------------------
    elif tipo_visual == "lugar":
        if entidad_principal:
            if contiene_entidad_exacta(
                entidad_principal,
                texto_total
            ):
                score += 34
            else:
                score += int(
                    cobertura_entidad
                    * 22
                )

                if cobertura_entidad < 0.50:
                    score -= 18

        if contexto_visual:
            score += int(
                cobertura_contexto
                * 10
            )

        señales_lugar = [
            "city",
            "town",
            "beach",
            "coast",
            "promenade",
            "skyline",
            "harbour",
            "harbor",
            "landscape",
            "street",
            "architecture",
            "ciudad",
            "playa",
            "paseo",
            "costa",
            "vista",
            "turismo",
            "puerto",
            "paisaje",
            "calle",
            "arquitectura",
        ]

        coincidencias = sum(
            1
            for palabra
            in señales_lugar
            if palabra
            in meta_norm
        )

        score += min(
            coincidencias * 4,
            20
        )

        # Evitamos retratos cuando el foco debe ser el lugar.
        palabras_retrato = [
            "portrait",
            "person",
            "people",
            "man",
            "woman",
            "retrato",
            "persona",
            "gente",
            "hombre",
            "mujer",
        ]

        if any(
            p in meta_norm
            for p in palabras_retrato
        ):
            score -= 18

    # --------------------
    # TEMA
    # --------------------
    elif tipo_visual == "tema":
        if entidad_principal:
            score += int(
                cobertura_entidad
                * 10
            )

        if contexto_visual:
            score += int(
                cobertura_contexto
                * 8
            )

        n_matches, matches = coincidencia_query_con_metadatos(
            candidato,
            texto_total
        )

        # La imagen conceptual necesita AL MENOS una coincidencia real.
        if n_matches == 0:
            score -= 18
        else:
            score += min(
                8 * n_matches,
                28
            )

            candidato[
                "conceptos_coincidentes"
            ] = matches

        # Bonus si el propio título del archivo contiene un concepto.
        title_tokens = set(
            tokens_visuales_significativos(
                title
            )
        )

        query_tokens = set(
            tokens_visuales_significativos(
                candidato.get(
                    "search_query",
                    ""
                )
            )
        )

        if title_tokens.intersection(
            query_tokens
        ):
            score += 7

    return score


# ==========================================
# 12. DESCARGA Y ANÁLISIS VISUAL
# ==========================================
def descargar_archivo(
    url,
    ruta_local
):
    try:
        r = requests.get(
            url,
            headers=HEADERS_BROWSER,
            timeout=30,
            allow_redirects=True
        )

        if (
            r.status_code == 200
            and r.content
            and len(r.content)
            <= 20 * 1024 * 1024
        ):
            with open(
                ruta_local,
                "wb"
            ) as f:
                f.write(
                    r.content
                )

            return True

    except Exception:
        pass

    return False


def descargar_candidato(
    candidato,
    indice
):
    urls = deduplicar_lista([
        candidato.get("url"),
        candidato.get(
            "fallback_url"
        ),
    ])

    for intento, url in enumerate(
        urls,
        start=1
    ):
        ext = ".jpg"

        lower_url = (
            url
            or ""
        ).lower()

        if ".png" in lower_url:
            ext = ".png"
        elif ".webp" in lower_url:
            ext = ".webp"

        ruta = (
            f"tmp_visual_"
            f"{indice}_"
            f"{intento}"
            f"{ext}"
        )

        if descargar_archivo(
            url,
            ruta
        ):
            try:
                # Verifica que realmente se puede abrir como imagen.
                with Image.open(
                    ruta
                ) as im:
                    im.verify()

                return ruta

            except Exception:
                try:
                    os.remove(ruta)
                except Exception:
                    pass

    return None


def analizar_imagen_real(
    ruta_imagen
):
    resultado = {
        "width": 0,
        "height": 0,
        "casi_monocroma": False,
    }

    try:
        with Image.open(
            ruta_imagen
        ) as im:
            rgb = im.convert(
                "RGB"
            )

            resultado[
                "width"
            ], resultado[
                "height"
            ] = rgb.size

            muestra = rgb.copy()
            muestra.thumbnail(
                (260, 260)
            )

            stat = ImageStat.Stat(
                muestra
            )

            r, g, b = stat.mean[:3]

            diferencia_medias = (
                max(r, g, b)
                - min(r, g, b)
            )

            # HSV: canal S (saturación)
            hsv = muestra.convert(
                "HSV"
            )

            sat_stat = ImageStat.Stat(
                hsv
            )

            saturacion_media = (
                sat_stat.mean[1]
            )

            resultado[
                "casi_monocroma"
            ] = (
                diferencia_medias < 13
                and saturacion_media < 22
            )

    except Exception:
        pass

    return resultado


# ==========================================
# 13. SELECCIÓN POR FUENTE
# ==========================================
def evaluar_y_descargar_candidatos(
    candidatos,
    tipo_visual,
    entidad_principal,
    contexto_visual
):
    puntuados = []

    for candidato in candidatos:
        score = puntuar_candidato(
            candidato,
            tipo_visual,
            entidad_principal,
            contexto_visual
        )

        if score <= -900:
            continue

        candidato = dict(candidato)
        candidato["score_metadata"] = score
        puntuados.append(candidato)

    puntuados.sort(
        key=lambda x: x.get(
            "score_metadata",
            0
        ),
        reverse=True
    )

    umbral = UMBRAL_SCORE.get(
        tipo_visual,
        42
    )

    mejor_ruta = None
    mejor_candidato = None
    mejor_score = -99999

    # En temas conceptuales damos más oportunidades porque los
    # metadatos de bancos abiertos suelen ser escuetos.
    max_candidatos = (
        16
        if tipo_visual == "tema"
        else 10
    )

    # Ya no se devuelve el primero que pasa: se elige el mejor.
    for indice, candidato in enumerate(
        puntuados[:max_candidatos],
        start=1
    ):
        ruta = descargar_candidato(
            candidato,
            indice
        )

        if not ruta:
            continue

        conservar_ruta = False

        try:
            analisis = analizar_imagen_real(
                ruta
            )

            score = candidato.get(
                "score_metadata",
                0
            )

            ancho = analisis.get(
                "width",
                0
            )

            alto = analisis.get(
                "height",
                0
            )

            if (
                ancho >= 1200
                and alto >= 630
            ):
                score += 8

            elif (
                ancho
                and alto
                and (
                    ancho < 500
                    or alto < 300
                )
            ):
                score -= 25

            # Preferencia por horizontal para casi todos los usos.
            if ancho > 0 and alto > 0:
                ratio = ancho / alto

                if tipo_visual in {
                    "lugar",
                    "programa",
                    "marca",
                    "evento",
                    "tema",
                }:
                    if ratio >= 1.20:
                        score += 8
                    elif ratio < 0.90:
                        score -= 14

                elif tipo_visual == "persona":
                    # Una foto vertical de persona puede ser perfectamente útil.
                    if ratio >= 0.72:
                        score += 3

            # Blanco y negro / casi monocroma.
            if analisis.get(
                "casi_monocroma"
            ):
                score -= 24
            else:
                score += 5

            # Segunda capa para noticias sobre una única persona:
            # si OpenCV está disponible y detecta más de una cara,
            # descartamos la imagen.
            if tipo_visual == "persona":
                caras = contar_personas_aprox_imagen(
                    ruta
                )

                if (
                    caras is not None
                    and caras >= 2
                ):
                    print(
                        "🚫 Foto descartada: "
                        f"parece contener {caras} personas "
                        "y la noticia es sobre una sola persona."
                    )
                    continue

            print(
                "🧪 "
                f"{candidato.get('engine')} | "
                f"score={score} | "
                f"{candidato.get('title') or 'sin título'} | "
                f"monocroma={analisis.get('casi_monocroma')}"
            )

            if score < umbral:
                continue

            candidato[
                "score_final"
            ] = score

            candidato[
                "width_real"
            ] = ancho

            candidato[
                "height_real"
            ] = alto

            if score > mejor_score:
                if (
                    mejor_ruta
                    and os.path.exists(
                        mejor_ruta
                    )
                ):
                    try:
                        os.remove(
                            mejor_ruta
                        )
                    except Exception:
                        pass

                mejor_score = score
                mejor_ruta = ruta
                mejor_candidato = candidato
                conservar_ruta = True

                print(
                    "⭐ Mejor candidata provisional: "
                    f"{candidato.get('title') or candidato.get('source_page')} "
                    f"(score {score})"
                )

        finally:
            if (
                not conservar_ruta
                and ruta
                and os.path.exists(
                    ruta
                )
            ):
                try:
                    os.remove(
                        ruta
                    )
                except Exception:
                    pass

    if mejor_ruta and mejor_candidato:
        print(
            "✅ Imagen final aprobada: "
            f"{mejor_candidato.get('title') or mejor_candidato.get('source_page')} "
            f"(score {mejor_score})"
        )

        return (
            mejor_ruta,
            mejor_candidato
        )

    return None, None


def recolectar_candidatos_fuente(
    fuente,
    queries
):
    todos = []
    urls_vistas = set()

    for query in queries:
        if fuente == "wikimedia":
            resultados = buscar_imagenes_wikimedia(
                query,
                limit=10
            )
        else:
            resultados = buscar_imagenes_openverse(
                query,
                limit=12
            )

        for cand in resultados:
            cand = dict(
                cand
            )

            # Se conserva aparte. NO se añade a "meta".
            cand[
                "search_query"
            ] = query

            clave = (
                cand.get(
                    "source_page"
                )
                or cand.get(
                    "url"
                )
                or cand.get(
                    "title"
                )
            )

            if (
                not clave
                or clave in urls_vistas
            ):
                continue

            urls_vistas.add(
                clave
            )

            todos.append(
                cand
            )

    return todos


# ==========================================
# 14. SELECCIÓN FINAL:
#     WIKIMEDIA -> OPENVERSE -> FALLBACK
# ==========================================
def seleccionar_mejor_imagen_real(
    tipo_visual,
    entidad_principal,
    contexto_visual,
    busquedas_imagen
):
    queries = enriquecer_busquedas_genericas(
        tipo_visual,
        entidad_principal,
        contexto_visual,
        busquedas_imagen
    )

    print(
        f"🧩 Tipo visual: {tipo_visual}"
    )
    print(
        f"🎯 Entidad: {entidad_principal}"
    )
    print(
        f"🧭 Contexto: {contexto_visual}"
    )
    print(
        f"🔍 Búsquedas: {queries}"
    )

    # --------------------------------------
    # PASO 1: WIKIMEDIA COMMONS
    # --------------------------------------
    print(
        "🥇 Buscando primero en Wikimedia Commons..."
    )

    candidatos_wiki = recolectar_candidatos_fuente(
        "wikimedia",
        queries
    )

    ruta, info = evaluar_y_descargar_candidatos(
        candidatos_wiki,
        tipo_visual,
        entidad_principal,
        contexto_visual
    )

    if ruta and info:
        return ruta, info

    # --------------------------------------
    # PASO 2: OPENVERSE
    # --------------------------------------
    print(
        "🥈 Wikimedia no dio una opción suficientemente fiable. "
        "Probando Openverse..."
    )

    candidatos_openverse = recolectar_candidatos_fuente(
        "openverse",
        queries
    )

    ruta, info = evaluar_y_descargar_candidatos(
        candidatos_openverse,
        tipo_visual,
        entidad_principal,
        contexto_visual
    )

    if ruta and info:
        return ruta, info

    # --------------------------------------
    # PASO 3: FALLBACK DE MARCA
    # --------------------------------------
    print(
        "⚠️ No hay una imagen suficientemente fiable. "
        "Se usará el fallback gráfico de Miri te lo cuenta."
    )

    return None, None


# ==========================================
# 15. TIPOGRAFÍA / BRANDING
# ==========================================
def cargar_fuente(
    size=32,
    bold=False
):
    if bold:
        posibles = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
    else:
        posibles = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]

    for path in posibles:
        if os.path.exists(path):
            return ImageFont.truetype(
                path,
                size=size
            )

    return ImageFont.load_default()


def ajustar_cobertura(
    imagen,
    target_w,
    target_h
):
    img = imagen.copy().convert(
        "RGB"
    )

    w, h = img.size

    if w <= 0 or h <= 0:
        return Image.new(
            "RGB",
            (target_w, target_h),
            COLOR_BG
        )

    scale = max(
        target_w / w,
        target_h / h
    )

    new_w = max(
        1,
        int(w * scale)
    )

    new_h = max(
        1,
        int(h * scale)
    )

    img = img.resize(
        (new_w, new_h),
        Image.LANCZOS
    )

    left = max(
        0,
        (new_w - target_w) // 2
    )

    top = max(
        0,
        (new_h - target_h) // 2
    )

    return img.crop(
        (
            left,
            top,
            left + target_w,
            top + target_h,
        )
    )


def envolver_texto(
    draw,
    texto,
    font,
    max_width
):
    palabras = (
        texto
        or ""
    ).split()

    lineas = []
    actual = ""

    for palabra in palabras:
        prueba = (
            palabra
            if not actual
            else actual
            + " "
            + palabra
        )

        bbox = draw.textbbox(
            (0, 0),
            prueba,
            font=font
        )

        ancho = (
            bbox[2]
            - bbox[0]
        )

        if ancho <= max_width:
            actual = prueba

        else:
            if actual:
                lineas.append(
                    actual
                )

            actual = palabra

    if actual:
        lineas.append(
            actual
        )

    return lineas


def draw_pill(
    draw,
    x,
    y,
    text,
    font,
    fill,
    text_fill,
    outline=None,
    padding_x=18,
    padding_y=8
):
    bbox = draw.textbbox(
        (0, 0),
        text,
        font=font
    )

    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    w = tw + padding_x * 2
    h = th + padding_y * 2

    r = h // 2

    draw.rounded_rectangle(
        (
            x,
            y,
            x + w,
            y + h
        ),
        radius=r,
        fill=fill,
        outline=outline,
        width=2 if outline else 0
    )

    draw.text(
        (
            x + padding_x,
            y + padding_y - 1
        ),
        text,
        font=font,
        fill=text_fill
    )

    return (
        x + w,
        y + h
    )


# ==========================================
# 16. FALLBACK GRÁFICO
# ==========================================
def crear_fondo_fallback(
    size=(1200, 630)
):
    canvas = Image.new(
        "RGB",
        size,
        COLOR_BG
    )

    draw = ImageDraw.Draw(
        canvas
    )

    # Forma coral
    draw.rounded_rectangle(
        (
            -120,
            180,
            430,
            360
        ),
        radius=60,
        fill=COLOR_RED
    )

    # Forma amarilla
    draw.rounded_rectangle(
        (
            935,
            38,
            1265,
            130
        ),
        radius=46,
        fill=COLOR_YELLOW
    )

    # Arco gris
    draw.arc(
        (
            470,
            42,
            800,
            245
        ),
        start=215,
        end=30,
        fill=COLOR_GREY,
        width=8
    )

    # Puntitos grises
    for row in range(11):
        for col in range(17):
            x = 850 + col * 18
            y = 140 + row * 18
            r = 2

            draw.ellipse(
                (
                    x-r,
                    y-r,
                    x+r,
                    y+r
                ),
                fill="#7E7A77"
            )

    # Puntitos amarillos
    for row in range(10):
        for col in range(12):
            x = 935 + col * 16
            y = 12 + row * 16

            draw.ellipse(
                (
                    x-1,
                    y-1,
                    x+1,
                    y+1
                ),
                fill="#E8CF70"
            )

    # MIRI fantasma
    ghost_font = cargar_fuente(
        118,
        bold=True
    )

    draw.text(
        (48, 64),
        "MIRI",
        font=ghost_font,
        fill="#D3CBC4"
    )

    return canvas


# ==========================================
# 17. MINIATURA CORPORATIVA 1200x630
# ==========================================
def crear_miniatura_brandeada(
    titulo_miniatura,
    categoria_visual,
    ruta_salida,
    ruta_imagen_real=None
):
    W = 1200
    H = 630
    BOTTOM_H = 233
    TOP_H = H - BOTTOM_H

    # Fondo
    if (
        ruta_imagen_real
        and os.path.exists(
            ruta_imagen_real
        )
    ):
        try:
            with Image.open(
                ruta_imagen_real
            ) as base_img:
                fondo = ajustar_cobertura(
                    base_img,
                    W,
                    H
                )
        except Exception:
            fondo = crear_fondo_fallback(
                (W, H)
            )
    else:
        fondo = crear_fondo_fallback(
            (W, H)
        )

    fondo = fondo.convert(
        "RGBA"
    )

    # Suave capa cálida solo en zona visual.
    tint = Image.new(
        "RGBA",
        (W, TOP_H),
        (255, 247, 239, 25)
    )

    fondo.alpha_composite(
        tint,
        (0, 0)
    )

    draw = ImageDraw.Draw(
        fondo
    )

    # Gradiente oscuro suave al final de la foto.
    grad = Image.new(
        "RGBA",
        (W, TOP_H),
        (0, 0, 0, 0)
    )

    gd = ImageDraw.Draw(
        grad
    )

    for y in range(TOP_H):
        inicio = int(
            TOP_H * 0.55
        )

        if y < inicio:
            alpha = 0
        else:
            progreso = (
                y - inicio
            ) / max(
                1,
                TOP_H - inicio
            )

            alpha = int(
                105
                * progreso
            )

        gd.line(
            (
                0,
                y,
                W,
                y
            ),
            fill=(
                14,
                14,
                16,
                alpha
            )
        )

    fondo.alpha_composite(
        grad,
        (0, 0)
    )

    draw = ImageDraw.Draw(
        fondo
    )

    # Faldón negro.
    draw.rectangle(
        (
            0,
            H - BOTTOM_H,
            W,
            H
        ),
        fill=COLOR_BLACK
    )

    # Línea coral superior del faldón.
    draw.rectangle(
        (
            0,
            H - BOTTOM_H,
            W,
            H - BOTTOM_H + 12
        ),
        fill=COLOR_RED
    )

    # Fuentes
    font_top = cargar_fuente(
        19,
        bold=True
    )

    font_cat = cargar_fuente(
        17,
        bold=True
    )

    # Ajuste adaptativo del titular
    title_len = len(
        titulo_miniatura
        or ""
    )

    if title_len <= 34:
        title_size = 52
    elif title_len <= 52:
        title_size = 46
    elif title_len <= 72:
        title_size = 40
    else:
        title_size = 36

    font_title = cargar_fuente(
        title_size,
        bold=True
    )

    font_brand_big = cargar_fuente(
        58,
        bold=True
    )

    font_brand_mid = cargar_fuente(
        20,
        bold=True
    )

    font_brand_small = cargar_fuente(
        15,
        bold=True
    )

    # Etiqueta superior izquierda.
    draw_pill(
        draw,
        28,
        22,
        "• MIRI TE LO CUENTA",
        font_top,
        fill=COLOR_YELLOW,
        text_fill=COLOR_BLACK,
        outline=COLOR_BLACK,
        padding_x=18,
        padding_y=7
    )

    # Decoración de puntos arriba derecha.
    for row in range(8):
        for col in range(14):
            x = 945 + col * 16
            y = 10 + row * 16

            draw.ellipse(
                (
                    x-1,
                    y-1,
                    x+1,
                    y+1
                ),
                fill="#F1DB82"
            )

    # Categoría.
    categoria_visual = (
        categoria_visual
        or "ACTUALIDAD"
    ).upper().strip()

    draw_pill(
        draw,
        55,
        H - BOTTOM_H + 42,
        categoria_visual,
        font_cat,
        fill=COLOR_YELLOW,
        text_fill=COLOR_BLACK,
        outline=None,
        padding_x=15,
        padding_y=7
    )

    # Línea coral vertical.
    draw.rounded_rectangle(
        (
            24,
            H - BOTTOM_H + 91,
            31,
            H - 48
        ),
        radius=3,
        fill=COLOR_RED
    )

    # Titular.
    titulo_miniatura = limitar_texto(
        titulo_miniatura
        or "Noticia destacada",
        95
    )

    titulo_x = 55
    titulo_y = (
        H
        - BOTTOM_H
        + 94
    )

    max_title_width = 720

    title_lines = envolver_texto(
        draw,
        titulo_miniatura,
        font_title,
        max_title_width
    )

    if len(title_lines) > 3:
        title_lines = title_lines[:3]

        ultima = (
            title_lines[-1]
        )

        while ultima:
            prueba = ultima + "…"

            bbox = draw.textbbox(
                (0, 0),
                prueba,
                font=font_title
            )

            if (
                bbox[2]
                - bbox[0]
                <= max_title_width
            ):
                break

            ultima = ultima[:-1].rstrip()

        title_lines[-1] = (
            ultima + "…"
        )

    line_height = int(
        title_size * 1.08
    )

    for i, line in enumerate(
        title_lines
    ):
        draw.text(
            (
                titulo_x,
                titulo_y
                + i * line_height
            ),
            line,
            font=font_title,
            fill=COLOR_WHITE
        )

    # Marca a la derecha.
    brand_x = 900
    brand_y = (
        H
        - BOTTOM_H
        + 58
    )

    draw.text(
        (
            brand_x,
            brand_y
        ),
        "Miri",
        font=font_brand_big,
        fill=COLOR_WHITE
    )

    # Bocadillo coral.
    bubble_x = brand_x
    bubble_y = brand_y + 72
    bubble_w = 158
    bubble_h = 55

    draw.rounded_rectangle(
        (
            bubble_x,
            bubble_y,
            bubble_x + bubble_w,
            bubble_y + bubble_h
        ),
        radius=13,
        fill=COLOR_RED
    )

    draw.polygon(
        [
            (
                bubble_x + 24,
                bubble_y + bubble_h
            ),
            (
                bubble_x + 42,
                bubble_y + bubble_h
            ),
            (
                bubble_x + 27,
                bubble_y + bubble_h + 15
            )
        ],
        fill=COLOR_RED
    )

    draw.text(
        (
            bubble_x + 16,
            bubble_y + 13
        ),
        "te lo cuenta",
        font=font_brand_mid,
        fill=COLOR_WHITE
    )

    draw.text(
        (
            brand_x,
            bubble_y + 73
        ),
        "@miritelocuenta",
        font=font_brand_small,
        fill=COLOR_LIGHT_GREY
    )

    # Arco coral abajo derecha.
    draw.arc(
        (
            1035,
            472,
            1325,
            760
        ),
        start=180,
        end=310,
        fill=COLOR_RED,
        width=12
    )

    final_rgb = fondo.convert(
        "RGB"
    )

    final_rgb.save(
        ruta_salida,
        format="JPEG",
        quality=92,
        optimize=True
    )

    return ruta_salida


# ==========================================
# 18. CRÉDITO DE LA IMAGEN
# ==========================================
def construir_credito_html(
    info_imagen
):
    if not info_imagen:
        return ""

    title = html.escape(
        info_imagen.get(
            "title",
            "Imagen"
        )
    )

    author = html.escape(
        info_imagen.get(
            "artist",
            "Autor no indicado"
        )
        or "Autor no indicado"
    )

    license_name = html.escape(
        info_imagen.get(
            "license",
            "Licencia no indicada"
        )
        or "Licencia no indicada"
    )

    source_url = (
        info_imagen.get(
            "source_page",
            ""
        )
        or ""
    ).strip()

    source_name = (
        "Wikimedia Commons"
        if info_imagen.get("engine")
        == "wikimedia"
        else "Openverse"
    )

    source_html = (
        html.escape(
            source_name
        )
    )

    if source_url:
        source_html = (
            f'<a href="{html.escape(source_url, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'{html.escape(source_name)}</a>'
        )

    license_url = (
        info_imagen.get(
            "license_url",
            ""
        )
        or ""
    )

    license_html = license_name

    if license_url:
        license_html = (
            f'<a href="{html.escape(license_url, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'{license_name}</a>'
        )

    return (
        '<p style="font-size:11px;color:#888;'
        'margin-top:12px;line-height:1.45;">'
        f'Imagen destacada: {title}. '
        f'Autor: {author}. '
        f'Fuente: {source_html}. '
        f'Licencia: {license_html}.'
        '</p>'
    )


# ==========================================
# 19. GENERACIÓN DE MINIATURA
# ==========================================
def generar_miniatura(
    titulo_miniatura,
    categoria_visual,
    tipo_visual,
    entidad_principal,
    contexto_visual,
    busquedas_imagen
):
    ruta_local = OUTPUT_IMAGE

    # Evita reutilizar una miniatura previa.
    if os.path.exists(
        ruta_local
    ):
        try:
            os.remove(
                ruta_local
            )
        except Exception:
            pass

    ruta_imagen_real, info_imagen = (
        seleccionar_mejor_imagen_real(
            tipo_visual=tipo_visual,
            entidad_principal=entidad_principal,
            contexto_visual=contexto_visual,
            busquedas_imagen=busquedas_imagen
        )
    )

    if ruta_imagen_real and info_imagen:
        print(
            "🖼️ FOTO REAL seleccionada para la miniatura: "
            f"{info_imagen.get('title') or info_imagen.get('source_page')}"
        )
    else:
        print(
            "🎨 FALLBACK gráfico: no se encontró una foto con "
            "metadatos suficientemente relacionados."
        )

    crear_miniatura_brandeada(
        titulo_miniatura=titulo_miniatura,
        categoria_visual=categoria_visual,
        ruta_salida=ruta_local,
        ruta_imagen_real=ruta_imagen_real
    )

    # Limpieza de temporal.
    if (
        ruta_imagen_real
        and os.path.exists(
            ruta_imagen_real
        )
    ):
        try:
            os.remove(
                ruta_imagen_real
            )
        except Exception:
            pass

    print(
        "✅ Miniatura visual generada correctamente."
    )

    return (
        ruta_local,
        info_imagen
    )



# ==========================================
# 19B. ACTIVOS SOCIALES
# ==========================================
def asegurar_directorio_social():
    os.makedirs(
        SOCIAL_OUTPUT_DIR,
        exist_ok=True
    )


def extraer_zona_foto_miniatura(
    ruta_thumbnail
):
    """
    La miniatura 1200x630 del bot tiene una zona fotográfica
    superior de aproximadamente 397 px. Reutilizamos esa zona
    para crear formatos verticales sin duplicar el titular
    que ya existe en el faldón inferior.
    """
    with Image.open(
        ruta_thumbnail
    ) as im:
        im = im.convert(
            "RGB"
        )

        corte = int(
            im.height * 0.63
        )

        return im.crop(
            (
                0,
                0,
                im.width,
                max(
                    1,
                    corte
                )
            )
        )


def crear_social_post_1080x1350(
    ruta_thumbnail,
    titulo_social,
    categoria_visual,
    ruta_salida=OUTPUT_SOCIAL_POST
):
    asegurar_directorio_social()

    W = 1080
    H = 1350
    PHOTO_H = 650

    canvas = Image.new(
        "RGB",
        (W, H),
        COLOR_BG
    )

    try:
        foto = extraer_zona_foto_miniatura(
            ruta_thumbnail
        )

        foto = ajustar_cobertura(
            foto,
            W,
            PHOTO_H
        ).convert(
            "RGB"
        )

        canvas.paste(
            foto,
            (0, 0)
        )

    except Exception:
        fallback = crear_fondo_fallback(
            (
                W,
                PHOTO_H
            )
        ).convert(
            "RGB"
        )

        canvas.paste(
            fallback,
            (0, 0)
        )

    draw = ImageDraw.Draw(
        canvas
    )

    # Zona editorial.
    draw.rectangle(
        (
            0,
            PHOTO_H,
            W,
            H
        ),
        fill=COLOR_BLACK
    )

    draw.rectangle(
        (
            0,
            PHOTO_H,
            W,
            PHOTO_H + 12
        ),
        fill=COLOR_RED
    )

    font_cat = cargar_fuente(
        19,
        bold=True
    )

    longitud = len(
        titulo_social
        or ""
    )

    if longitud <= 34:
        title_size = 66
    elif longitud <= 50:
        title_size = 58
    else:
        title_size = 51

    font_title = cargar_fuente(
        title_size,
        bold=True
    )

    font_brand = cargar_fuente(
        42,
        bold=True
    )

    font_small = cargar_fuente(
        17,
        bold=True
    )

    categoria = (
        categoria_visual
        or "ACTUALIDAD"
    ).upper().strip()

    draw_pill(
        draw,
        64,
        PHOTO_H + 48,
        categoria,
        font_cat,
        fill=COLOR_YELLOW,
        text_fill=COLOR_BLACK,
        outline=None,
        padding_x=16,
        padding_y=8
    )

    titulo_social = limitar_texto(
        titulo_social
        or "Lo último de Internet",
        78
    )

    lineas = envolver_texto(
        draw,
        titulo_social,
        font_title,
        930
    )[:4]

    y = PHOTO_H + 112
    line_h = int(
        title_size * 1.08
    )

    for i, linea in enumerate(
        lineas
    ):
        draw.text(
            (
                64,
                y + i * line_h
            ),
            linea,
            font=font_title,
            fill=COLOR_WHITE
        )

    # Firma.
    firma_y = H - 128

    draw.text(
        (
            64,
            firma_y
        ),
        "Miri",
        font=font_brand,
        fill=COLOR_WHITE
    )

    draw.rounded_rectangle(
        (
            180,
            firma_y + 4,
            364,
            firma_y + 48
        ),
        radius=12,
        fill=COLOR_RED
    )

    draw.text(
        (
            194,
            firma_y + 15
        ),
        "te lo cuenta",
        font=font_small,
        fill=COLOR_WHITE
    )

    draw.text(
        (
            W - 252,
            firma_y + 16
        ),
        "@miritelocuenta",
        font=font_small,
        fill=COLOR_LIGHT_GREY
    )

    canvas.save(
        ruta_salida,
        format="JPEG",
        quality=92,
        optimize=True
    )

    return ruta_salida


def crear_social_vertical_1080x1920(
    ruta_thumbnail,
    titulo_social,
    categoria_visual,
    ruta_salida=OUTPUT_SOCIAL_VERTICAL
):
    asegurar_directorio_social()

    W = 1080
    H = 1920
    PHOTO_H = 1000

    canvas = Image.new(
        "RGB",
        (W, H),
        COLOR_BG
    )

    try:
        foto = extraer_zona_foto_miniatura(
            ruta_thumbnail
        )

        foto = ajustar_cobertura(
            foto,
            W,
            PHOTO_H
        ).convert(
            "RGB"
        )

        canvas.paste(
            foto,
            (0, 0)
        )

    except Exception:
        fallback = crear_fondo_fallback(
            (
                W,
                PHOTO_H
            )
        ).convert(
            "RGB"
        )

        canvas.paste(
            fallback,
            (0, 0)
        )

    draw = ImageDraw.Draw(
        canvas
    )

    draw.rectangle(
        (
            0,
            PHOTO_H,
            W,
            H
        ),
        fill=COLOR_BLACK
    )

    draw.rectangle(
        (
            0,
            PHOTO_H,
            W,
            PHOTO_H + 14
        ),
        fill=COLOR_RED
    )

    font_cat = cargar_fuente(
        21,
        bold=True
    )

    longitud = len(
        titulo_social
        or ""
    )

    if longitud <= 34:
        title_size = 76
    elif longitud <= 50:
        title_size = 68
    else:
        title_size = 59

    font_title = cargar_fuente(
        title_size,
        bold=True
    )

    font_brand = cargar_fuente(
        48,
        bold=True
    )

    font_small = cargar_fuente(
        19,
        bold=True
    )

    categoria = (
        categoria_visual
        or "ACTUALIDAD"
    ).upper().strip()

    draw_pill(
        draw,
        68,
        PHOTO_H + 58,
        categoria,
        font_cat,
        fill=COLOR_YELLOW,
        text_fill=COLOR_BLACK,
        outline=None,
        padding_x=18,
        padding_y=8
    )

    titulo_social = limitar_texto(
        titulo_social
        or "Lo último de Internet",
        78
    )

    lineas = envolver_texto(
        draw,
        titulo_social,
        font_title,
        920
    )[:4]

    y = PHOTO_H + 136
    line_h = int(
        title_size * 1.08
    )

    for i, linea in enumerate(
        lineas
    ):
        draw.text(
            (
                68,
                y + i * line_h
            ),
            linea,
            font=font_title,
            fill=COLOR_WHITE
        )

    # CTA muy discreta, lejos de la UI inferior de TikTok/Reels.
    cta_y = H - 255

    draw.rounded_rectangle(
        (
            68,
            cta_y,
            454,
            cta_y + 58
        ),
        radius=29,
        fill=COLOR_YELLOW
    )

    draw.text(
        (
            91,
            cta_y + 18
        ),
        "MÁS CONTEXTO EN EL BLOG",
        font=font_small,
        fill=COLOR_BLACK
    )

    firma_y = H - 155

    draw.text(
        (
            68,
            firma_y
        ),
        "Miri",
        font=font_brand,
        fill=COLOR_WHITE
    )

    draw.rounded_rectangle(
        (
            195,
            firma_y + 7,
            401,
            firma_y + 56
        ),
        radius=12,
        fill=COLOR_RED
    )

    draw.text(
        (
            211,
            firma_y + 21
        ),
        "te lo cuenta",
        font=font_small,
        fill=COLOR_WHITE
    )

    draw.text(
        (
            W - 260,
            firma_y + 22
        ),
        "@miritelocuenta",
        font=font_small,
        fill=COLOR_LIGHT_GREY
    )

    canvas.save(
        ruta_salida,
        format="JPEG",
        quality=92,
        optimize=True
    )

    return ruta_salida


def crear_video_estatico_social(
    ruta_imagen_vertical,
    ruta_salida=OUTPUT_SOCIAL_VIDEO,
    segundos=SOCIAL_VIDEO_SECONDS
):
    """
    Genera un MP4 vertical H.264 sin audio a partir de la imagen.
    Primero intenta FFmpeg. Si no existe, no rompe el bot:
    deja la imagen vertical lista y avisa.
    """
    asegurar_directorio_social()

    ffmpeg = shutil.which(
        "ffmpeg"
    )

    if not ffmpeg:
        print(
            "⚠️ No se encontró ffmpeg. "
            "Se genera la imagen vertical, pero no el MP4."
        )
        return None

    comando = [
        ffmpeg,
        "-y",
        "-loop",
        "1",
        "-i",
        ruta_imagen_vertical,
        "-t",
        str(segundos),
        "-r",
        "30",
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
        "format=yuv420p",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        ruta_salida,
    ]

    try:
        proc = subprocess.run(
            comando,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90
        )

        if (
            proc.returncode == 0
            and os.path.exists(
                ruta_salida
            )
            and os.path.getsize(
                ruta_salida
            ) > 0
        ):
            print(
                "✅ Vídeo social 1080x1920 generado."
            )
            return ruta_salida

        print(
            "⚠️ FFmpeg no pudo crear el vídeo social."
        )

    except Exception as e:
        print(
            f"⚠️ Error creando vídeo social: {e}"
        )

    return None


def construir_credito_texto_social(
    info_imagen
):
    if not info_imagen:
        return ""

    title = (
        info_imagen.get(
            "title",
            "Imagen"
        )
        or "Imagen"
    ).strip()

    artist = (
        info_imagen.get(
            "artist",
            ""
        )
        or ""
    ).strip()

    license_name = (
        info_imagen.get(
            "license",
            ""
        )
        or ""
    ).strip()

    source_name = (
        "Wikimedia Commons"
        if info_imagen.get(
            "engine"
        ) == "wikimedia"
        else "Openverse"
    )

    piezas = [
        f"Imagen: {title}"
    ]

    if artist:
        piezas.append(
            f"Autor: {artist}"
        )

    if source_name:
        piezas.append(
            source_name
        )

    if license_name:
        piezas.append(
            license_name
        )

    return " · ".join(
        piezas
    )


def completar_copy_con_url(
    texto,
    url_articulo
):
    texto = (
        texto
        or ""
    ).strip()

    url_articulo = (
        url_articulo
        or ""
    ).strip()

    if "{URL}" in texto:
        return texto.replace(
            "{URL}",
            url_articulo
        )

    if url_articulo:
        return (
            texto
            + "\n\n"
            + url_articulo
        ).strip()

    return texto


def preparar_pack_social_final(
    pack,
    url_articulo,
    info_imagen
):
    pack = dict(
        pack
        or {}
    )

    hashtags = normalizar_hashtags(
        pack.get(
            "hashtags",
            []
        )
    )

    hashtag_line = " ".join(
        hashtags
    )

    credito = construir_credito_texto_social(
        info_imagen
    )

    facebook = completar_copy_con_url(
        pack.get(
            "facebook",
            ""
        ),
        url_articulo
    )

    instagram = (
        pack.get(
            "instagram",
            ""
        )
        or ""
    ).strip()

    tiktok = (
        pack.get(
            "tiktok",
            ""
        )
        or ""
    ).replace(
        "\n",
        " "
    ).strip()

    youtube_desc = completar_copy_con_url(
        pack.get(
            "youtube_descripcion",
            ""
        ),
        url_articulo
    )

    # Hashtags concretos al final.
    if hashtag_line:
        facebook = (
            facebook
            + "\n\n"
            + hashtag_line
        ).strip()

        instagram = (
            instagram
            + "\n\n"
            + hashtag_line
        ).strip()

        tiktok = (
            tiktok
            + " "
            + hashtag_line
        ).strip()

        youtube_desc = (
            youtube_desc
            + "\n\n"
            + hashtag_line
        ).strip()

    # Créditos en el copy para cumplir licencias BY/BY-SA.
    if credito:
        facebook = (
            facebook
            + "\n\n📷 "
            + credito
        ).strip()

        instagram = (
            instagram
            + "\n\n📷 "
            + credito
        ).strip()

        # TikTok: crédito compacto, en una sola línea.
        tiktok = (
            tiktok
            + " 📷 "
            + credito
        ).strip()

        youtube_desc = (
            youtube_desc
            + "\n\n📷 "
            + credito
        ).strip()

    pack[
        "facebook"
    ] = facebook

    pack[
        "instagram"
    ] = instagram

    pack[
        "tiktok"
    ] = tiktok

    pack[
        "youtube_descripcion"
    ] = youtube_desc

    pack[
        "url_articulo"
    ] = url_articulo

    pack[
        "credito_imagen"
    ] = credito

    return pack


def subir_activo_social_wordpress(
    ruta_local
):
    """
    Sube un activo social a la Biblioteca de Medios para que
    tenga URL pública. Si WordPress rechaza un MP4 por el plan
    o por límites de subida, el bot sigue funcionando.
    """
    if not (
        ruta_local
        and os.path.exists(
            ruta_local
        )
        and WP_URL
        and WP_USER
        and WP_APP_PASS
    ):
        return None

    mime = (
        mimetypes.guess_type(
            ruta_local
        )[0]
        or "application/octet-stream"
    )

    url_media = (
        f"{WP_URL}/wp-json/wp/v2/media"
    )

    try:
        with open(
            ruta_local,
            "rb"
        ) as f:
            data = f.read()

        headers = {
            "Content-Disposition": (
                f'attachment; filename="'
                f'{os.path.basename(ruta_local)}"'
            ),
            "Content-Type": mime,
        }

        r = requests.post(
            url_media,
            data=data,
            headers=headers,
            auth=(
                WP_USER,
                WP_APP_PASS
            ),
            timeout=60
        )

        if r.status_code in {
            200,
            201
        }:
            data = r.json()

            return {
                "id": data.get(
                    "id"
                ),
                "url": data.get(
                    "source_url"
                ),
                "mime": mime,
            }

        print(
            "⚠️ WordPress no pudo subir activo social "
            f"{os.path.basename(ruta_local)}: "
            f"{r.status_code}"
        )

    except Exception as e:
        print(
            f"⚠️ Error subiendo activo social: {e}"
        )

    return None


def guardar_pack_social(
    pack,
    rutas,
    urls_media=None
):
    asegurar_directorio_social()

    datos = dict(
        pack
        or {}
    )

    datos[
        "archivos"
    ] = {
        "image_1200x630": (
            rutas.get(
                "post"
            )
        ),
        "video_frame_1080x1920": (
            rutas.get(
                "vertical"
            )
        ),
        "video_1080x1920": (
            rutas.get(
                "video"
            )
        ),
    }

    datos[
        "media_wordpress"
    ] = (
        urls_media
        or {}
    )

    datos[
        "generado_en"
    ] = datetime.now().isoformat(
        timespec="seconds"
    )

    with open(
        OUTPUT_SOCIAL_JSON,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            datos,
            f,
            ensure_ascii=False,
            indent=2
        )

    texto = (
        "=== MIRI TE LO CUENTA · PACK SOCIAL ===\n\n"
        f"TÍTULO SOCIAL:\n{datos.get('titulo_social', '')}\n\n"
        f"FACEBOOK:\n{datos.get('facebook', '')}\n\n"
        f"INSTAGRAM:\n{datos.get('instagram', '')}\n\n"
        f"TIKTOK:\n{datos.get('tiktok', '')}\n\n"
        f"YOUTUBE SHORTS · TÍTULO:\n{datos.get('youtube_titulo', '')}\n\n"
        f"YOUTUBE SHORTS · DESCRIPCIÓN:\n"
        f"{datos.get('youtube_descripcion', '')}\n\n"
        "KEYWORDS:\n"
        + ", ".join(
            datos.get(
                "keywords_busqueda",
                []
            )
        )
        + "\n"
    )

    with open(
        OUTPUT_SOCIAL_TXT,
        "w",
        encoding="utf-8"
    ) as f:
        f.write(
            texto
        )

    print(
        f"✅ Pack social guardado en {SOCIAL_OUTPUT_DIR}/"
    )

    return datos


def generar_activos_sociales(
    ruta_thumbnail,
    titulo_social,
    categoria_visual
):
    asegurar_directorio_social()

    ruta_post = crear_social_post_1080x1350(
        ruta_thumbnail,
        titulo_social,
        categoria_visual,
        OUTPUT_SOCIAL_POST
    )

    ruta_vertical = crear_social_vertical_1080x1920(
        ruta_thumbnail,
        titulo_social,
        categoria_visual,
        OUTPUT_SOCIAL_VERTICAL
    )

    ruta_video = crear_video_estatico_social(
        ruta_vertical,
        OUTPUT_SOCIAL_VIDEO,
        SOCIAL_VIDEO_SECONDS
    )

    return {
        "post": ruta_post,
        "vertical": ruta_vertical,
        "video": ruta_video,
    }



# ==========================================
# 19C. PIEZAS SIMPLES PARA METRICOOL
# ==========================================
def crear_frame_vertical_desde_miniatura(
    ruta_thumbnail,
    ruta_salida=OUTPUT_SOCIAL_VERTICAL
):
    """
    Mantiene EXACTAMENTE la miniatura del artículo como pieza visual.
    No rediseña el titular. Solo la coloca completa dentro de un lienzo
    vertical 1080x1920 para poder convertirla en Reel/TikTok/Short.
    """
    asegurar_directorio_social()

    W = 1080
    H = 1920

    canvas = Image.new(
        "RGB",
        (W, H),
        COLOR_BG
    )

    with Image.open(
        ruta_thumbnail
    ) as im:
        im = im.convert(
            "RGB"
        )

        # Deja márgenes para que la imagen completa no se recorte.
        max_w = 1000
        max_h = 900

        escala = min(
            max_w / im.width,
            max_h / im.height
        )

        nuevo_w = max(
            1,
            int(
                im.width
                * escala
            )
        )

        nuevo_h = max(
            1,
            int(
                im.height
                * escala
            )
        )

        im = im.resize(
            (
                nuevo_w,
                nuevo_h
            ),
            Image.LANCZOS
        )

        x = (
            W - nuevo_w
        ) // 2

        y = (
            H - nuevo_h
        ) // 2

        # Sombra simple.
        sombra = Image.new(
            "RGBA",
            (W, H),
            (0, 0, 0, 0)
        )

        draw_sombra = ImageDraw.Draw(
            sombra
        )

        draw_sombra.rounded_rectangle(
            (
                x + 10,
                y + 10,
                x + nuevo_w + 10,
                y + nuevo_h + 10
            ),
            radius=18,
            fill=(14, 14, 16, 100)
        )

        canvas = Image.alpha_composite(
            canvas.convert(
                "RGBA"
            ),
            sombra
        ).convert(
            "RGB"
        )

        canvas.paste(
            im,
            (x, y)
        )

    canvas.save(
        ruta_salida,
        format="JPEG",
        quality=93,
        optimize=True
    )

    return ruta_salida


def generar_piezas_metricool(
    ruta_thumbnail
):
    """
    Salidas:
    - social_image_1200x630.jpg -> copia exacta de la miniatura
    - social_video_frame_1080x1920.jpg -> misma miniatura, sin recorte
    - social_video_1080x1920.mp4 -> 5 s, imagen fija
    """
    asegurar_directorio_social()

    shutil.copyfile(
        ruta_thumbnail,
        OUTPUT_SOCIAL_POST
    )

    vertical = crear_frame_vertical_desde_miniatura(
        ruta_thumbnail,
        OUTPUT_SOCIAL_VERTICAL
    )

    video = crear_video_estatico_social(
        vertical,
        OUTPUT_SOCIAL_VIDEO,
        SOCIAL_VIDEO_SECONDS
    )

    return {
        "post": OUTPUT_SOCIAL_POST,
        "vertical": vertical,
        "video": video,
    }


# ==========================================
# 20. WORDPRESS
# ==========================================
def publicar_en_wordpress(
    titulo,
    contenido_html,
    ruta_imagen
):
    if not (
        WP_URL
        and WP_USER
        and WP_APP_PASS
    ):
        raise Exception(
            "❌ Error crítico: faltan credenciales "
            "de WordPress en Secrets."
        )

    media_id = None

    if (
        ruta_imagen
        and os.path.exists(
            ruta_imagen
        )
    ):
        print(
            f"🚀 Subiendo imagen destacada a {WP_URL}..."
        )

        url_media = (
            f"{WP_URL}/wp-json/wp/v2/media"
        )

        with open(
            ruta_imagen,
            "rb"
        ) as f:
            media_bytes = f.read()

        guessed_mime = (
            mimetypes.guess_type(
                ruta_imagen
            )[0]
            or "image/jpeg"
        )

        headers_media = {
            "Content-Disposition": (
                f'attachment; filename="'
                f'{os.path.basename(ruta_imagen)}"'
            ),
            "Content-Type": guessed_mime
        }

        r_media = requests.post(
            url_media,
            data=media_bytes,
            headers=headers_media,
            auth=(
                WP_USER,
                WP_APP_PASS
            ),
            timeout=40
        )

        if r_media.status_code in [
            200,
            201
        ]:
            media_json = r_media.json()

            media_id = media_json.get(
                "id"
            )

            print(
                "✅ Imagen subida correctamente "
                f"(Media ID: {media_id})"
            )

        else:
            print(
                "⚠️ Aviso al subir imagen a WordPress: "
                f"{r_media.status_code} - "
                f"{r_media.text}"
            )

    print(
        "🚀 Publicando artículo completo con su miniatura..."
    )

    url_posts = (
        f"{WP_URL}/wp-json/wp/v2/posts"
    )

    payload = {
        "title": titulo,
        "content": contenido_html,
        "status": "publish"
    }

    if media_id:
        payload[
            "featured_media"
        ] = media_id

    r_post = requests.post(
        url_posts,
        json=payload,
        headers={
            "Content-Type": "application/json"
        },
        auth=(
            WP_USER,
            WP_APP_PASS
        ),
        timeout=40
    )

    if r_post.status_code in [
        200,
        201
    ]:
        post_data = r_post.json()

        print(
            "🎉 ¡ÉXITO TOTAL! Entrada publicada. "
            f"Link: {post_data.get('link')}"
        )

        return post_data

    raise Exception(
        "❌ Error crítico al publicar entrada "
        f"({r_post.status_code}): "
        f"{r_post.text}"
    )


# ==========================================
# 21. EJECUCIÓN PRINCIPAL
# ==========================================
if __name__ == "__main__":
    objetivo = articulos_mes_efectivos()

    print(
        "🗓️ Configuración editorial:"
    )
    print(
        f"   - Objetivo: {objetivo} artículos/mes"
    )
    print(
        "   - Intervalo medio este mes: "
        f"{intervalo_objetivo_horas():.1f} h"
    )
    print(
        "   - Metricool: "
        f"{METRICOOL_PUBLICACIONES_MES} publicaciones/mes; "
        "1 publicación por artículo (Instagram)"
    )
    print(
        "   - Facebook: compartido automáticamente desde Instagram "
        "(no cuenta en Metricool)"
    )
    print(
        "   - Vídeo vertical: generado para publicación manual "
        "(no cuenta en la frecuencia)"
    )
    print(
        "   - Modo prueba manual FORCE_RUN: "
        + ("ACTIVO" if FORCE_RUN else "desactivado")
    )

    if FORCE_RUN:
        print(
            "🧪 FORCE_RUN activo: se ignora SOLO el control de frecuencia "
            "para esta ejecución manual."
        )
    elif not puede_publicar_ahora():
        raise SystemExit(0)

    tendencia = obtener_nuevo_tema_viral()

    if not tendencia:
        # Ya no existe tema de prueba: si no hay tendencia buena,
        # no se publica por publicar.
        raise SystemExit(0)

    tema = tendencia.get(
        "titulo",
        ""
    )

    investigacion = tendencia.get(
        "_investigacion_verificada",
        {}
    )

    publicable, motivo = investigacion_es_publicable(
        investigacion
    )

    if not publicable:
        print(
            "🚫 Seguridad editorial: no se publicará el artículo. "
            f"Motivo: {motivo}"
        )
        raise SystemExit(0)

    articulo = generar_articulo_miri(
        tema,
        investigacion=investigacion,
        tendencia=tendencia
    )

    titulo = articulo[
        "titulo"
    ]

    contenido_html = articulo[
        "contenido_html"
    ]

    titulo_miniatura = articulo[
        "titulo_miniatura"
    ]

    categoria_visual = articulo[
        "categoria_visual"
    ]

    tipo_visual = articulo[
        "tipo_visual"
    ]

    entidad_principal = articulo[
        "entidad_principal"
    ]

    contexto_visual = articulo[
        "contexto_visual"
    ]

    busquedas_imagen = articulo[
        "busquedas_imagen"
    ]

    print(
        f"📰 Título artículo: {titulo}"
    )
    print(
        f"🖼️ Título miniatura: {titulo_miniatura}"
    )
    print(
        f"🧩 Tipo visual: {tipo_visual}"
    )
    print(
        f"🎯 Entidad principal: {entidad_principal}"
    )

    # Copy social se mantiene porque resulta útil al cargar
    # manualmente el vídeo en Metricool.
    pack_social = generar_pack_social_gemini(
        tema_viral=tema,
        titulo_articulo=titulo,
        contenido_html=contenido_html,
        entidad_principal=entidad_principal,
        contexto_visual=contexto_visual
    )

    # 1) Miniatura del artículo: MISMO sistema visual que antes.
    ruta_imagen, info_imagen = generar_miniatura(
        titulo_miniatura=titulo_miniatura,
        categoria_visual=categoria_visual,
        tipo_visual=tipo_visual,
        entidad_principal=entidad_principal,
        contexto_visual=contexto_visual,
        busquedas_imagen=busquedas_imagen
    )

    # 2) Imagen exacta + vídeo fijo de 5 s para Metricool.
    rutas_sociales = generar_piezas_metricool(
        ruta_imagen
    )

    # Crédito/licencia dentro del artículo.
    if info_imagen:
        credito = construir_credito_html(
            info_imagen
        )

        if credito:
            contenido_html += credito

    if titulo and contenido_html:
        post_data = publicar_en_wordpress(
            titulo,
            contenido_html,
            ruta_imagen
        )

        if post_data:
            url_articulo = (
                post_data.get(
                    "link"
                )
                or ""
            )

            pack_social_final = preparar_pack_social_final(
                pack=pack_social,
                url_articulo=url_articulo,
                info_imagen=info_imagen
            )

            # Sube las piezas a WordPress Media para tener URL pública.
            # Metricool RSS seguirá usando og:image del artículo.
            # El MP4 queda además listo para usar como Reel/TikTok/Short.
            media_urls = {}

            for clave, ruta in rutas_sociales.items():
                if not ruta:
                    continue

                subido = subir_activo_social_wordpress(
                    ruta
                )

                if subido:
                    media_urls[
                        clave
                    ] = subido

            guardar_pack_social(
                pack=pack_social_final,
                rutas=rutas_sociales,
                urls_media=media_urls
            )

            print(
                "📦 Piezas Metricool creadas:"
            )
            print(
                f"   - Imagen: {OUTPUT_SOCIAL_POST}"
            )
            print(
                f"   - Frame vertical: {OUTPUT_SOCIAL_VERTICAL}"
            )

            if rutas_sociales.get(
                "video"
            ):
                print(
                    f"   - Vídeo 5 s: {OUTPUT_SOCIAL_VIDEO}"
                )

            guardar_en_historial(
                tema=tema,
                titulo=titulo,
                entidad=(
                    tendencia.get(
                        "entidad"
                    )
                    or entidad_principal
                ),
                categoria=(
                    tendencia.get(
                        "categoria"
                    )
                    or categoria_visual
                ),
                keywords=tendencia.get(
                    "keywords",
                    []
                ),
                fuente=", ".join(
                    tendencia.get(
                        "fuentes",
                        []
                    )
                ),
                url=url_articulo,
                score_tendencia=tendencia.get(
                    "score_tendencia"
                )
            )
