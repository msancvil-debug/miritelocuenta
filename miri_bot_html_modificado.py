import requests
import json
import os
import html
import re
import mimetypes
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageStat

# ============================================================
# MIRI TE LO CUENTA · BOT AUTOMÁTICO
# ============================================================
# FLUJO:
# RSS -> Gemini -> selección visual editorial -> miniatura 1200x630
# -> WordPress -> historial
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
# ============================================================


# ==========================================
# 1. VARIABLES DE ENTORNO
# ==========================================
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
WP_URL = (os.environ.get("WP_URL") or "").strip().rstrip("/")
WP_USER = (os.environ.get("WP_USER") or "").strip()
WP_APP_PASS = (os.environ.get("WP_APP_PASS") or "").strip().replace(" ", "")

HISTORIAL_FILE = "historial_temas.json"

# ÚNICO cambio funcional respecto al bot antiguo que sí publicaba.
# 31 días / 20 publicaciones = 37,2 horas.
INTERVALO_PUBLICACION_HORAS = float(
    os.environ.get(
        "INTERVALO_PUBLICACION_HORAS",
        "37.2"
    )
)

GITHUB_EVENT_NAME = str(
    os.environ.get(
        "GITHUB_EVENT_NAME",
        ""
    )
).strip().lower()

try:
    GITHUB_RUN_ATTEMPT = int(
        os.environ.get(
            "GITHUB_RUN_ATTEMPT",
            "1"
        )
    )
except Exception:
    GITHUB_RUN_ATTEMPT = 1

EJECUCION_MANUAL_GITHUB = (
    GITHUB_EVENT_NAME == "workflow_dispatch"
    or GITHUB_RUN_ATTEMPT > 1
)

# ============================================================
# MOTOR DE TENDENCIAS · MIRI TE LO CUENTA
# ============================================================
# Se mantiene el bot estable de publicación e imágenes.
# SOLO se amplía la forma de elegir el tema.
#
# Fuentes:
# - Google Trends España
# - búsquedas verticales en Google News
# - Gemini + Google Search para conversación social reciente
# - 20minutos como señal secundaria, nunca como fuente dominante

GOOGLE_TRENDS_RSS = "https://trends.google.com/trending/rss?geo=ES"

GOOGLE_NEWS_QUERIES = [
    # Audiencia puente: el público que llegó por Casa/Zona Gemelos.
    "\"Casa de los Gemelos\" OR ZonaGemelos when:3d",
    "\"Casa de los Gemelos 3\" when:3d",
    "\"Zona Gemelos\" participantes influencer polémica when:3d",
    "\"Serena Milan\" OR \"Serena Milán\" when:7d",
    "\"Imantado\" influencer streamer when:7d",
    "\"Johaan\" influencer TikTok when:7d",

    "TikTok España influencer viral polémica when:2d",
    "influencer España creador contenido polémica viral when:2d",
    "youtuber YouTube España polémica viral when:2d",
    "streamer España Twitch Kick polémica viral when:2d",
    "Instagram España influencer viral polémica when:2d",
    "creador de contenido España viral polémica when:2d",
    "meme viral Internet España when:2d",
    "vídeo viral redes sociales España when:2d",
    "música viral TikTok España when:2d",
    "fandom España viral redes sociales when:2d",
    "famoso influencer ruptura polémica redes España when:2d",
    "reality España redes sociales viral when:2d",
    "Telecinco reality redes sociales viral when:2d",
]

FEEDS_GENERALES = [
    "https://20minutos.es/rss/"
]

MAX_CANDIDATOS_TENDENCIA = int(
    os.environ.get(
        "MAX_CANDIDATOS_TENDENCIA",
        "70"
    )
)

COOLDOWN_ENTIDAD_DIAS = int(
    os.environ.get(
        "COOLDOWN_ENTIDAD_DIAS",
        "7"
    )
)

HISTORIAL_REPETICION_DIAS = int(
    os.environ.get(
        "HISTORIAL_REPETICION_DIAS",
        "30"
    )
)

UMBRAL_SIMILITUD_TEMA = float(
    os.environ.get(
        "UMBRAL_SIMILITUD_TEMA",
        "0.72"
    )
)

# Preferencia editorial. NO son cuotas rígidas.
# Sirven para que Telecinco/TV no gane por pura abundancia de noticias.
# "AUDIENCIA_PUENTE" ayuda a no romper de golpe con el público
# que llegó por Casa de los Gemelos / Zona Gemelos y personajes
# de ese ecosistema. Tiene prioridad alta, pero no puede copar
# todas las publicaciones seguidas.
PESO_CATEGORIA_EDITORIAL = {
    "AUDIENCIA_PUENTE": 32,
    "INFLUENCERS": 24,
    "TIKTOK": 22,
    "STREAMERS": 21,
    "YOUTUBE": 20,
    "CREADORES": 20,
    "VIRAL": 19,
    "MEME": 18,
    "INTERNET": 15,
    "MUSICA": 12,
    "FAMOSOS": 10,
    "REALITY": 3,
    "TV": -8,
}

# Nombres/temas que conectan directamente con la audiencia actual.
# Se buscan como señal editorial; NO obligan a publicar si no hay
# una historia real y reciente.
AUDIENCIA_PUENTE_TERMINOS = {
    "casa de los gemelos",
    "casa de los gemelos 3",
    "zona gemelos",
    "zonagemelos",
    "serena milan",
    "serena milán",
    "imantado",
    "johaan",
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
    "tema": 42,
}

OUTPUT_IMAGE = "miniatura_destacada.jpg"

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


def titulo_social_es_valido(texto):
    """
    Valida que el título social no solo mida <=100 caracteres,
    sino que termine como una frase/titular completo.
    """
    texto = re.sub(
        r"\s+",
        " ",
        str(texto or "").strip()
    )

    if not texto:
        return False

    if len(texto) > 100:
        return False

    if len(texto) < 45:
        return False

    # No aceptar comillas, paréntesis o signos abiertos sin cerrar.
    pares = [
        ("“", "”"),
        ("«", "»"),
        ("(", ")"),
        ("[", "]"),
    ]

    for apertura, cierre in pares:
        if texto.count(apertura) != texto.count(cierre):
            return False

    # Tampoco aceptar comillas rectas impares.
    if texto.count('"') % 2 != 0:
        return False

    palabras = texto.rstrip(
        " .!?…:;,—-"
    ).split()

    if not palabras:
        return False

    ultima = normalizar_texto(
        palabras[-1]
    )

    # Finales que suelen indicar que el texto se ha cortado.
    finales_incompletos = {
        "de", "del", "la", "las", "el", "los",
        "un", "una", "unos", "unas",
        "que", "y", "o", "en", "con", "por",
        "para", "sin", "sobre", "al",
        "es", "son", "ha", "han", "he", "hemos",
        "se", "me", "te", "le", "les", "nos",
        "lo", "los", "la", "las",
        "mi", "mis", "tu", "tus", "su", "sus",
        "como", "cuando", "donde", "porque",
        "pero", "aunque", "si", "mientras",
        "desde", "hasta", "entre", "contra",
    }

    if ultima in finales_incompletos:
        return False

    # Si termina en dos puntos o coma, también está incompleto.
    if texto.endswith((",", ":", ";", "—", "-")):
        return False

    return True



def titulo_social_fallback(titulo_articulo, tema_viral):
    """
    Fallback SIN cortar frases por caracteres.
    Busca una cláusula completa de menos de 100 caracteres.
    Si no existe una opción segura, devuelve cadena vacía.
    """
    fuentes = [
        str(titulo_articulo or "").strip(),
        str(tema_viral or "").strip(),
    ]

    candidatos = []

    for fuente in fuentes:
        fuente = re.sub(
            r"\s+",
            " ",
            fuente
        ).strip()

        if not fuente:
            continue

        if titulo_social_es_valido(
            fuente
        ):
            candidatos.append(
                fuente
            )

        # Separar ganchos iniciales y cláusulas completas.
        trozos = re.split(
            r"(?<=[.!?])\s+|\s+[—–-]\s+|:\s+",
            fuente
        )

        for trozo in trozos:
            trozo = trozo.strip(
                " —–-"
            )

            if titulo_social_es_valido(
                trozo
            ):
                candidatos.append(
                    trozo
                )

        # Muy útil para titulares del tipo:
        # "¡Gancho! Persona hace X..."
        if "!" in fuente:
            resto = fuente.split(
                "!",
                1
            )[1].strip()

            if titulo_social_es_valido(
                resto
            ):
                candidatos.append(
                    resto
                )

    if not candidatos:
        return ""

    # Preferir el más descriptivo sin pasar de 100.
    candidatos = sorted(
        set(
            candidatos
        ),
        key=lambda x: (
            len(x),
            len(
                set(
                    tokenizar(x)
                )
            )
        ),
        reverse=True
    )

    return candidatos[0]


def construir_titulo_social(titulo_articulo, tema_viral):
    """
    Compatibilidad con el flujo anterior.
    Ya NO recorta a 100 caracteres: devuelve una frase completa
    o cadena vacía.
    """
    return titulo_social_fallback(
        titulo_articulo,
        tema_viral
    )



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
# 3B. FRECUENCIA REAL DESDE WORDPRESS
# ==========================================
def _parse_wp_date_gmt(valor):
    valor = str(
        valor
        or ""
    ).strip()

    if not valor:
        return None

    try:
        # WordPress devuelve normalmente YYYY-MM-DDTHH:MM:SS
        dt = datetime.fromisoformat(
            valor.replace(
                "Z",
                "+00:00"
            )
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except Exception:
        return None


def obtener_ultima_publicacion_wordpress():
    """
    Consulta la última entrada REAL publicada en WordPress.

    Esto evita depender de historial_temas.json, que en GitHub Actions
    puede no persistir entre ejecuciones.
    """
    if not WP_URL:
        return None

    url = (
        f"{WP_URL}/wp-json/wp/v2/posts"
    )

    params = {
        "per_page": 1,
        "orderby": "date",
        "order": "desc",
        "status": "publish",
        "_fields": "id,date_gmt,link",
    }

    try:
        respuesta = requests.get(
            url,
            params=params,
            auth=(
                WP_USER,
                WP_APP_PASS
            ) if (
                WP_USER
                and WP_APP_PASS
            ) else None,
            timeout=25
        )

        if respuesta.status_code != 200:
            print(
                "⚠️ No pude comprobar la última publicación "
                f"en WordPress ({respuesta.status_code}). "
                "No bloquearé el bot por un fallo de consulta."
            )
            return None

        datos = respuesta.json()

        if not isinstance(
            datos,
            list
        ) or not datos:
            return None

        ultima = datos[0]

        fecha = _parse_wp_date_gmt(
            ultima.get(
                "date_gmt"
            )
        )

        if fecha:
            print(
                "🕒 Última publicación real en WordPress: "
                f"{fecha.isoformat()}"
            )

        return fecha

    except Exception as exc:
        print(
            "⚠️ No pude consultar WordPress para la frecuencia: "
            f"{exc}. No bloquearé el bot por ese fallo."
        )
        return None


def puede_publicar_por_frecuencia():
    ultima = obtener_ultima_publicacion_wordpress()

    if not ultima:
        print(
            "✅ Sin bloqueo de frecuencia: no se encontró "
            "una publicación anterior verificable."
        )
        return True

    ahora = datetime.now(
        timezone.utc
    )

    transcurridas = (
        ahora
        - ultima
    ).total_seconds() / 3600.0

    if transcurridas < INTERVALO_PUBLICACION_HORAS:
        faltan = (
            INTERVALO_PUBLICACION_HORAS
            - transcurridas
        )

        print(
            "⏸️ No toca publicar todavía. "
            f"Han pasado {transcurridas:.1f} h; "
            f"el intervalo es {INTERVALO_PUBLICACION_HORAS:.1f} h. "
            f"Faltan ~{faltan:.1f} h."
        )

        return False

    print(
        "✅ Frecuencia permitida: "
        f"han pasado {transcurridas:.1f} h "
        f"(mínimo {INTERVALO_PUBLICACION_HORAS:.1f} h)."
    )

    return True


# ==========================================
# 4. HISTORIAL Y TENDENCIAS
# ==========================================
def cargar_historial():
    """
    Compatible con el historial antiguo (lista de strings)
    y con futuros registros estructurados.
    """
    if not os.path.exists(HISTORIAL_FILE):
        return []

    try:
        with open(
            HISTORIAL_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        return data if isinstance(data, list) else []

    except Exception:
        return []


def guardar_en_historial(tema):
    """
    Se mantiene compatible con el flujo estable.
    """
    historial = cargar_historial()

    if tema not in historial:
        historial.append(tema)

    historial = historial[-250:]

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


def _parse_fecha_tendencia(valor):
    if not valor:
        return None

    if isinstance(valor, datetime):
        dt = valor

    else:
        texto = str(valor).strip()

        try:
            dt = datetime.fromisoformat(
                texto.replace(
                    "Z",
                    "+00:00"
                )
            )
        except Exception:
            try:
                dt = parsedate_to_datetime(
                    texto
                )
            except Exception:
                return None

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        timezone.utc
    )


def _texto_local_tag(elem, nombre):
    for child in elem.iter():
        tag = str(
            child.tag
        ).split(
            "}"
        )[-1]

        if tag == nombre:
            return (
                child.text
                or ""
            ).strip()

    return ""


def _fecha_item_rss(item):
    for nombre in [
        "pubDate",
        "published",
        "updated",
    ]:
        texto = _texto_local_tag(
            item,
            nombre
        )

        fecha = _parse_fecha_tendencia(
            texto
        )

        if fecha:
            return fecha.isoformat()

    return ""


def similaridad_tendencia(a, b):
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

    if a_tokens and b_tokens:
        jac = len(
            a_tokens & b_tokens
        ) / max(
            1,
            len(
                a_tokens | b_tokens
            )
        )
    else:
        jac = 0.0

    return max(
        seq,
        jac
    )


def crear_candidato_tendencia(
    titulo,
    fuente,
    url="",
    contexto="",
    senal="",
    fecha="",
    trafico="",
    score_base=0,
    categoria="",
    entidad=""
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
        "categoria_sugerida": (
            categoria
            or ""
        ).strip().upper(),
        "entidad_sugerida": (
            entidad
            or ""
        ).strip(),
    }


def fusionar_candidatos_tendencia(candidatos):
    """
    Une la misma historia cuando aparece en varias fuentes/señales.
    """
    fusionados = []

    for candidato in candidatos:
        if not candidato:
            continue

        mejor = None
        mejor_sim = 0.0

        for existente in fusionados:
            sim = similaridad_tendencia(
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
                mejor.get(
                    "fuentes",
                    []
                )
                + candidato.get(
                    "fuentes",
                    []
                )
            )

            mejor[
                "senales"
            ] = deduplicar_lista(
                mejor.get(
                    "senales",
                    []
                )
                + candidato.get(
                    "senales",
                    []
                )
            )

            if len(
                candidato.get(
                    "contexto",
                    ""
                )
            ) > len(
                mejor.get(
                    "contexto",
                    ""
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

            if (
                not mejor.get(
                    "categoria_sugerida"
                )
                and candidato.get(
                    "categoria_sugerida"
                )
            ):
                mejor[
                    "categoria_sugerida"
                ] = candidato.get(
                    "categoria_sugerida"
                )

            if (
                not mejor.get(
                    "entidad_sugerida"
                )
                and candidato.get(
                    "entidad_sugerida"
                )
            ):
                mejor[
                    "entidad_sugerida"
                ] = candidato.get(
                    "entidad_sugerida"
                )

        else:
            fusionados.append(
                dict(
                    candidato
                )
            )

    return fusionados


def es_tema_audiencia_puente(texto):
    texto_norm = normalizar_texto(
        str(texto or "")
    )

    return any(
        normalizar_texto(
            termino
        ) in texto_norm
        for termino in AUDIENCIA_PUENTE_TERMINOS
    )


PALABRAS_PRIORIDAD_MIRI = {
    "tiktok", "instagram", "youtube", "youtuber",
    "streamer", "twitch", "kick", "influencer",
    "influencers", "creador", "creadora", "creadores",
    "viral", "meme", "fandom", "podcast",
    "redes", "internet", "directo", "streaming",
    "video", "vídeo", "polémica", "polemica",
    "challenge", "trend", "tendencia",
    "cantante", "artista", "música", "musica",
    "ruptura", "pareja",
}

PALABRAS_BAJA_PRIORIDAD = {
    "bolsa", "ibex", "euribor", "meteorologia",
    "meteorología", "terremoto", "accidente",
    "asesinato", "guerra", "elecciones", "elección",
    "partido político", "congreso", "senado",
}

PALABRAS_TV = {
    "telecinco",
    "mediaset",
    "antena 3",
    "la 1",
    "programa de televisión",
    "programa de television",
}

PALABRAS_REALITY = {
    "gran hermano",
    "supervivientes",
    "la isla de las tentaciones",
    "reality",
}


def inferir_categoria_editorial(candidato):
    categoria = (
        candidato.get(
            "categoria_sugerida",
            ""
        )
        or ""
    ).strip().upper()

    if categoria in PESO_CATEGORIA_EDITORIAL:
        return categoria

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

    if es_tema_audiencia_puente(
        texto
    ):
        return "AUDIENCIA_PUENTE"

    reglas = [
        ("TIKTOK", ["tiktok"]),
        ("STREAMERS", ["streamer", "twitch", "kick", "streaming"]),
        ("YOUTUBE", ["youtube", "youtuber"]),
        ("INFLUENCERS", ["influencer"]),
        ("CREADORES", ["creador", "creadora", "creadores"]),
        ("MEME", ["meme"]),
        ("MUSICA", ["musica", "música", "cancion", "canción", "artista", "cantante"]),
        ("REALITY", ["reality", "gran hermano", "supervivientes", "tentaciones"]),
        ("TV", ["telecinco", "mediaset", "antena 3", "television", "televisión"]),
        ("VIRAL", ["viral", "video", "vídeo", "redes"]),
    ]

    for cat, palabras in reglas:
        if any(
            normalizar_texto(
                p
            ) in texto
            for p in palabras
        ):
            return cat

    return "INTERNET"


def score_encaje_miri(candidato):
    texto_total = normalizar_texto(
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

    titulo_norm = normalizar_texto(
        candidato.get(
            "titulo",
            ""
        )
    )

    score = int(
        candidato.get(
            "score_base",
            0
        )
    )

    # Puente de transición: si hay una noticia real del universo
    # que ya conoce la audiencia, gana prioridad.
    if es_tema_audiencia_puente(
        texto_total
    ):
        score += 24
        candidato[
            "es_audiencia_puente"
        ] = True
    else:
        candidato[
            "es_audiencia_puente"
        ] = False

    for palabra in PALABRAS_PRIORIDAD_MIRI:
        if normalizar_texto(
            palabra
        ) in texto_total:
            score += 5

    for palabra in PALABRAS_BAJA_PRIORIDAD:
        if normalizar_texto(
            palabra
        ) in texto_total:
            score -= 22

    categoria = inferir_categoria_editorial(
        candidato
    )

    score += PESO_CATEGORIA_EDITORIAL.get(
        categoria,
        0
    )

    # TV tradicional no debe ganar solo porque haya muchísimas
    # noticias de Telecinco.
    es_tv_titulo = any(
        normalizar_texto(
            p
        ) in titulo_norm
        for p in PALABRAS_TV
    )

    angulo_social_titulo = any(
        normalizar_texto(
            p
        ) in titulo_norm
        for p in {
            "tiktok",
            "instagram",
            "youtube",
            "viral",
            "influencer",
            "streamer",
            "redes",
            "meme",
        }
    )

    if (
        es_tv_titulo
        and not angulo_social_titulo
    ):
        score -= 20

    # Reality puede entrar, pero con menos prioridad que
    # influencers/creadores/virales.
    es_reality = any(
        normalizar_texto(
            p
        ) in texto_total
        for p in PALABRAS_REALITY
    )

    if (
        es_reality
        and categoria == "REALITY"
    ):
        score -= 5

    # Deporte puro fuera salvo conversación de Internet.
    deporte = {
        "futbol", "fútbol", "liga", "champions",
        "tenis", "formula 1", "f1", "baloncesto",
    }

    tiene_deporte = any(
        normalizar_texto(
            x
        ) in texto_total
        for x in deporte
    )

    tiene_angulo_miri = any(
        normalizar_texto(
            x
        ) in texto_total
        for x in {
            "viral",
            "tiktok",
            "influencer",
            "streamer",
            "redes",
            "meme",
        }
    )

    if (
        tiene_deporte
        and not tiene_angulo_miri
    ):
        score -= 30

    candidato[
        "categoria_editorial"
    ] = categoria

    return score


def construir_google_news_feed(query):
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(
            query
        )
        + "&hl=es&gl=ES&ceid=ES:es"
    )


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

            score = 30

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
                        score += 18
                    elif n >= 50000:
                        score += 14
                    elif n >= 10000:
                        score += 9
                    elif n >= 5000:
                        score += 5

                except Exception:
                    pass

            candidato = crear_candidato_tendencia(
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

            if candidato:
                candidatos.append(
                    candidato
                )

    except Exception as exc:
        print(
            f"⚠️ Error leyendo Google Trends: {exc}"
        )

    return candidatos


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

                candidato = crear_candidato_tendencia(
                    titulo=titulo,
                    fuente=fuente,
                    url=link,
                    contexto=(
                        "Google News · búsqueda vertical: "
                        f"{query}"
                    ),
                    senal="google_news",
                    fecha=_fecha_item_rss(
                        item
                    ),
                    score_base=16
                )

                if candidato:
                    candidatos.append(
                        candidato
                    )

        except Exception as exc:
            print(
                f"⚠️ Error Google News ({query}): {exc}"
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
            )[:20]:
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

                candidato = crear_candidato_tendencia(
                    titulo=titulo,
                    fuente="20minutos",
                    url=link,
                    contexto=limitar_texto(
                        descripcion,
                        260
                    ),
                    senal="medio_general",
                    fecha=_fecha_item_rss(
                        item
                    ),
                    score_base=5
                )

                if candidato:
                    candidatos.append(
                        candidato
                    )

        except Exception as exc:
            print(
                f"⚠️ Error leyendo feed general: {exc}"
            )

    return candidatos


def _modelos_para_google_search():
    modelos = obtener_modelos_disponibles()

    preferidos = []

    for patron in [
        "gemini-3",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
    ]:
        for modelo in modelos:
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
        preferidos[:5]
        or ["gemini-2.5-flash"]
    )


def _gemini_google_search_json(prompt, timeout=70):
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

            parts = (
                data.get(
                    "candidates",
                    [{}]
                )[0]
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
                for p in parts
                if p.get(
                    "text"
                )
            ).strip()

            if not texto:
                continue

            return extraer_json_de_respuesta(
                texto
            )

        except Exception as exc:
            print(
                f"⚠️ Google Search con {modelo}: {exc}"
            )

    return None


def recoger_tendencias_sociales_gemini():
    """
    Este es el bloque que faltaba en el bot estable:
    busca conversación social reciente, no solo titulares de prensa.
    """
    prompts = [
        """
AUDIENCIA PUENTE DE "MIRI TE LO CUENTA".

Busca en la web noticias, salseos y conversaciones REALES y RECIENTES
(últimas 72 horas; hasta 7 días solo si sigue habiendo novedad)
relacionadas con:

- La Casa de los Gemelos
- La Casa de los Gemelos 3
- Zona Gemelos / ZonaGemelos
- participantes, exparticipantes y personas de su entorno
- Serena Milan / Serena Milán
- Imantado
- Johaan
- otros personajes de Internet claramente conectados con ese mismo
  ecosistema y audiencia

Prioriza:
- discusiones, respuestas, rupturas, reconciliaciones, indirectas
- nuevos vídeos o directos que estén generando conversación
- polémicas entre participantes o creadores
- novedades de los formatos de Zona Gemelos
- noticias personales o profesionales de esos personajes
  cuando estén siendo comentadas en redes

MUY IMPORTANTE:
- NO inventes una noticia porque uno de esos nombres aparezca en la lista.
- Si no hay novedad real y reciente de una persona, no la incluyas.
- No uses contenido antiguo como si acabara de ocurrir.
- Una noticia de este bloque puede competir con tendencias generales,
  pero no debe ganar solo por pertenecer a Casa/Zona Gemelos.

Devuelve SOLO JSON:
{
  "tendencias": [
    {
      "titulo": "hecho concreto y actual",
      "contexto": "qué ha pasado y por qué interesa a esa audiencia",
      "entidad": "persona/programa principal",
      "categoria": "AUDIENCIA_PUENTE",
      "fuente_referencia": "fuente o URL"
    }
  ]
}
Máximo 10.
""",
        """
Busca en la web temas que estén generando conversación REAL en España
durante las últimas 24-48 horas sobre influencers, creadores de
contenido, TikTokers e Instagramers.

Prioriza:
- polémicas o respuestas entre creadores
- vídeos que se estén viralizando
- rupturas, anuncios o cambios que estén moviendo comentarios
- nuevos personajes de Internet de los que la gente esté hablando
- tendencias de TikTok con suficiente contexto para un artículo

NO priorices televisión tradicional salvo que el centro de la historia
sea la conversación en redes.

Devuelve SOLO JSON:
{
  "tendencias": [
    {
      "titulo": "tema concreto y actual",
      "contexto": "qué está pasando y por qué se habla de ello",
      "entidad": "persona o tema principal",
      "categoria": "INFLUENCERS|TIKTOK|CREADORES|VIRAL",
      "fuente_referencia": "fuente o URL"
    }
  ]
}
Máximo 10.
""",
        """
Busca en la web temas que estén generando conversación REAL en España
en las últimas 24-48 horas sobre YouTube, youtubers, Twitch, Kick,
streamers, directos, podcasts y creadores digitales.

Quiero historias actuales con nombres concretos y suficiente contexto
para un artículo. Evita noticias genéricas de tecnología.

Devuelve SOLO JSON:
{
  "tendencias": [
    {
      "titulo": "tema concreto",
      "contexto": "qué ha ocurrido y por qué se está comentando",
      "entidad": "creador/persona/canal principal",
      "categoria": "YOUTUBE|STREAMERS|CREADORES|VIRAL",
      "fuente_referencia": "fuente o URL"
    }
  ]
}
Máximo 10.
""",
        """
Busca fenómenos virales de las últimas 24-48 horas en España:
memes, audios, canciones, vídeos, fandoms, challenges, expresiones,
personajes virales y temas de cultura de Internet.

Elige solo fenómenos con contexto suficiente para explicar qué son,
de dónde salen o por qué se han viralizado.

Devuelve SOLO JSON:
{
  "tendencias": [
    {
      "titulo": "fenómeno concreto",
      "contexto": "qué es y qué señal actual existe",
      "entidad": "tema/persona/artista principal",
      "categoria": "MEME|MUSICA|VIRAL|INTERNET",
      "fuente_referencia": "fuente o URL"
    }
  ]
}
Máximo 10.
""",
        """
Busca realities, famosos o televisión de España SOLO cuando estén
generando una conversación digital especialmente fuerte en las últimas
24-48 horas.

No rellenes la lista con Telecinco por defecto. Si un tema de TV no
está moviendo TikTok, X, Instagram, YouTube o conversación online,
no lo incluyas.

Devuelve SOLO JSON:
{
  "tendencias": [
    {
      "titulo": "tema concreto",
      "contexto": "qué conversación digital está generando",
      "entidad": "persona/programa principal",
      "categoria": "REALITY|FAMOSOS|TV|VIRAL",
      "fuente_referencia": "fuente o URL"
    }
  ]
}
Máximo 6.
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

        tendencias = data.get(
            "tendencias",
            []
        )

        if not isinstance(
            tendencias,
            list
        ):
            continue

        for item in tendencias[:10]:
            if not isinstance(
                item,
                dict
            ):
                continue

            titulo = item.get(
                "titulo",
                ""
            )

            contexto = item.get(
                "contexto",
                ""
            )

            entidad = item.get(
                "entidad",
                ""
            )

            categoria = item.get(
                "categoria",
                ""
            )

            fuente_ref = item.get(
                "fuente_referencia",
                ""
            )

            candidato = crear_candidato_tendencia(
                titulo=titulo,
                fuente=(
                    fuente_ref
                    or "Gemini + Google Search"
                ),
                url=(
                    fuente_ref
                    if str(
                        fuente_ref
                    ).startswith(
                        "http"
                    )
                    else ""
                ),
                contexto=contexto,
                senal="google_search_social",
                fecha=datetime.now(
                    timezone.utc
                ).isoformat(
                    timespec="seconds"
                ),
                score_base=28,
                categoria=categoria,
                entidad=entidad
            )

            if candidato:
                candidatos.append(
                    candidato
                )

    return candidatos


def obtener_posts_recientes_wordpress_tendencias(
    dias=HISTORIAL_REPETICION_DIAS,
    limite=40
):
    """
    Anti-repetición robusto: consulta WordPress REAL.
    Así no depende de que historial_temas.json persista en GitHub.
    """
    if not WP_URL:
        return []

    url = (
        f"{WP_URL}/wp-json/wp/v2/posts"
    )

    params = {
        "per_page": min(
            100,
            limite
        ),
        "orderby": "date",
        "order": "desc",
        "status": "publish",
        "_fields": "id,date_gmt,title,link",
    }

    try:
        respuesta = requests.get(
            url,
            params=params,
            auth=(
                WP_USER,
                WP_APP_PASS
            ) if (
                WP_USER
                and WP_APP_PASS
            ) else None,
            timeout=25
        )

        if respuesta.status_code != 200:
            return []

        ahora = datetime.now(
            timezone.utc
        )

        salida = []

        for item in respuesta.json():
            fecha = _parse_fecha_tendencia(
                item.get(
                    "date_gmt"
                )
            )

            if fecha:
                edad = (
                    ahora - fecha
                ).total_seconds() / 86400.0

                if edad > dias:
                    continue
            else:
                edad = None

            title_obj = item.get(
                "title",
                {}
            )

            if isinstance(
                title_obj,
                dict
            ):
                titulo = html.unescape(
                    title_obj.get(
                        "rendered",
                        ""
                    )
                )
            else:
                titulo = html.unescape(
                    str(
                        title_obj
                        or ""
                    )
                )

            salida.append({
                "titulo": limpiar_html_tags(
                    titulo
                ),
                "fecha": fecha,
                "edad_dias": edad,
            })

        return salida

    except Exception as exc:
        print(
            "⚠️ No pude leer posts recientes para antirepetición: "
            f"{exc}"
        )
        return []


def penalizacion_repeticion_tendencia(
    candidato,
    posts_recientes
):
    titulo = candidato.get(
        "titulo",
        ""
    )

    entidad = (
        candidato.get(
            "entidad_sugerida",
            ""
        )
        or ""
    ).strip()

    penalizacion = 0
    repeticion_dura = False

    # La audiencia puente debe aparecer con cierta frecuencia,
    # pero no convertir el canal otra vez en un monográfico.
    if es_tema_audiencia_puente(
        titulo
        + " "
        + candidato.get(
            "contexto",
            ""
        )
    ):
        ultimos_3 = posts_recientes[:3]
        ultimos_6 = posts_recientes[:6]

        puente_3 = sum(
            1
            for previo in ultimos_3
            if es_tema_audiencia_puente(
                previo.get(
                    "titulo",
                    ""
                )
            )
        )

        puente_6 = sum(
            1
            for previo in ultimos_6
            if es_tema_audiencia_puente(
                previo.get(
                    "titulo",
                    ""
                )
            )
        )

        if puente_3 >= 1:
            penalizacion += 14

        if puente_3 >= 2:
            penalizacion += 28

        if puente_6 >= 3:
            penalizacion += 22

    for previo in posts_recientes:
        titulo_previo = previo.get(
            "titulo",
            ""
        )

        sim = similaridad_tendencia(
            titulo,
            titulo_previo
        )

        if sim >= UMBRAL_SIMILITUD_TEMA:
            penalizacion += 100
            repeticion_dura = True

        elif sim >= 0.58:
            penalizacion += 34

        # Si conocemos la entidad y aparece en un post de los últimos
        # 7 días, la castigamos para no encadenar a la misma persona.
        if entidad:
            edad = previo.get(
                "edad_dias"
            )

            if (
                edad is not None
                and edad < COOLDOWN_ENTIDAD_DIAS
                and normalizar_texto(
                    entidad
                ) in normalizar_texto(
                    titulo_previo
                )
            ):
                penalizacion += 30

    # Compatibilidad con historial local antiguo.
    for item in cargar_historial():
        if isinstance(
            item,
            str
        ):
            sim = similaridad_tendencia(
                titulo,
                item
            )

            if sim >= UMBRAL_SIMILITUD_TEMA:
                penalizacion += 80
                repeticion_dura = True

    return penalizacion, repeticion_dura


def construir_bolsa_tendencias():
    candidatos = []

    print(
        "📈 Google Trends España..."
    )
    candidatos.extend(
        recoger_google_trends()
    )

    print(
        "📰 Google News: influencers, TikTok, YouTube, "
        "streamers, virales, memes y reality..."
    )
    candidatos.extend(
        recoger_google_news()
    )

    print(
        "🔎 Google Search: conversación social reciente..."
    )
    candidatos.extend(
        recoger_tendencias_sociales_gemini()
    )

    print(
        "🌐 Medio general como señal secundaria..."
    )
    candidatos.extend(
        recoger_feeds_generales()
    )

    fusionados = fusionar_candidatos_tendencia(
        candidatos
    )

    posts_recientes = (
        obtener_posts_recientes_wordpress_tendencias()
    )

    for candidato in fusionados:
        score = score_encaje_miri(
            candidato
        )

        penalizacion, repeticion = (
            penalizacion_repeticion_tendencia(
                candidato,
                posts_recientes
            )
        )

        candidato[
            "penalizacion_historial"
        ] = penalizacion

        candidato[
            "repeticion_dura"
        ] = repeticion

        candidato[
            "score_preliminar"
        ] = (
            score
            - penalizacion
            + (
                max(
                    0,
                    len(
                        candidato.get(
                            "fuentes",
                            []
                        )
                    )
                    - 1
                )
                * 7
            )
            + (
                max(
                    0,
                    len(
                        candidato.get(
                            "senales",
                            []
                        )
                    )
                    - 1
                )
                * 5
            )
        )

    fusionados = [
        c
        for c in fusionados
        if not c.get(
            "repeticion_dura"
        )
        and c.get(
            "score_preliminar",
            -999
        ) > 0
    ]

    fusionados.sort(
        key=lambda x: x.get(
            "score_preliminar",
            0
        ),
        reverse=True
    )

    # Evitar que la preselección sea casi toda TV/Telecinco.
    seleccion = []
    tv_count = 0

    for candidato in fusionados:
        categoria = candidato.get(
            "categoria_editorial",
            ""
        )

        if categoria in {
            "TV",
            "REALITY",
        }:
            # Máximo aproximado 20% de la bolsa final.
            limite_tv = max(
                3,
                int(
                    MAX_CANDIDATOS_TENDENCIA
                    * 0.20
                )
            )

            if tv_count >= limite_tv:
                continue

            tv_count += 1

        seleccion.append(
            candidato
        )

        if len(
            seleccion
        ) >= MAX_CANDIDATOS_TENDENCIA:
            break

    return seleccion


def seleccionar_mejor_tendencia_gemini(candidatos):
    if not candidatos:
        return None

    candidatos_prompt = []

    for i, candidato in enumerate(
        candidatos[:55]
    ):
        candidatos_prompt.append({
            "id": i,
            "titulo": candidato.get(
                "titulo",
                ""
            ),
            "contexto": limitar_texto(
                candidato.get(
                    "contexto",
                    ""
                ),
                350
            ),
            "categoria": candidato.get(
                "categoria_editorial",
                ""
            ),
            "senales": candidato.get(
                "senales",
                []
            ),
            "fuentes": candidato.get(
                "fuentes",
                []
            ),
            "score_preliminar": candidato.get(
                "score_preliminar",
                0
            ),
        })

    prompt = f"""
Eres editora de tendencias de "Miri te lo cuenta".

El medio trata PRINCIPALMENTE de:
- influencers y creadores
- TikTok y virales
- YouTubers
- streamers / Twitch / Kick
- memes, fandoms y cultura de Internet
- polémicas y personajes que están generando conversación online

Reality y televisión pueden entrar, pero NO deben dominar el medio.
Telecinco no tiene prioridad por ser Telecinco.

Selecciona y ORDENA hasta 5 historias que merezcan artículo AHORA.

CANDIDATOS:
{json.dumps(candidatos_prompt, ensure_ascii=False)}

CRITERIOS:
1. Actualidad real: está pasando o creciendo ahora.
2. Encaje con cultura de Internet y el canal.
3. Durante esta etapa de transición, da una VENTAJA MODERADA a noticias
   reales y recientes de Casa de los Gemelos, Zona Gemelos, sus
   participantes y personajes conocidos por esa audiencia.
4. Esa ventaja es un puente, NO una obligación: si no hay novedad real,
   elige otra tendencia mejor.
5. Prioriza influencers, creadores, TikTok, YouTube, streamers,
   virales y memes frente a TV tradicional.
6. Da valor a temas detectados por varias fuentes/señales.
7. No repitas prácticamente el mismo tema.
8. Evita encadenar a la misma persona/programa.
9. Reality/TV solo si hay conversación digital clara.
10. No elijas política, sucesos, economía o deporte puro.
11. No inventes que algo es viral.

Devuelve SOLO JSON:
{{
  "ranking": [
    {{
      "id": 0,
      "score": 0,
      "entidad": "entidad principal",
      "categoria": "AUDIENCIA_PUENTE|INFLUENCERS|TIKTOK|STREAMERS|YOUTUBE|CREADORES|VIRAL|MEME|MUSICA|FAMOSOS|REALITY|TV|INTERNET",
      "por_que_ahora": "motivo breve"
    }}
  ]
}}

Si nada sirve, devuelve {{"ranking":[]}}.
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
            "temperature": 0.2
        }
    }

    for modelo in obtener_modelos_disponibles():
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

            ranking = seleccion.get(
                "ranking",
                []
            )

            if not isinstance(
                ranking,
                list
            ):
                ranking = []

            for item in ranking[:5]:
                if not isinstance(
                    item,
                    dict
                ):
                    continue

                try:
                    idx = int(
                        item.get(
                            "id",
                            -1
                        )
                    )
                except Exception:
                    continue

                if (
                    idx < 0
                    or idx >= len(
                        candidatos[:55]
                    )
                ):
                    continue

                elegido = dict(
                    candidatos[
                        idx
                    ]
                )

                entidad = (
                    item.get(
                        "entidad",
                        ""
                    )
                    or elegido.get(
                        "entidad_sugerida",
                        ""
                    )
                    or ""
                ).strip()

                categoria = (
                    item.get(
                        "categoria",
                        ""
                    )
                    or elegido.get(
                        "categoria_editorial",
                        ""
                    )
                    or "INTERNET"
                ).strip().upper()

                elegido[
                    "entidad_final"
                ] = entidad

                elegido[
                    "categoria_final"
                ] = categoria

                elegido[
                    "score_tendencia"
                ] = int(
                    item.get(
                        "score",
                        elegido.get(
                            "score_preliminar",
                            0
                        )
                    )
                    or 0
                )

                elegido[
                    "por_que_ahora"
                ] = (
                    item.get(
                        "por_que_ahora",
                        ""
                    )
                    or ""
                ).strip()

                # Comprobación final de entidad con WordPress real.
                repetida_entidad = False

                if entidad:
                    for previo in obtener_posts_recientes_wordpress_tendencias(
                        dias=COOLDOWN_ENTIDAD_DIAS,
                        limite=25
                    ):
                        if normalizar_texto(
                            entidad
                        ) in normalizar_texto(
                            previo.get(
                                "titulo",
                                ""
                            )
                        ):
                            repetida_entidad = True
                            break

                if repetida_entidad:
                    print(
                        "↪️ Se salta una candidata por repetir "
                        f"entidad reciente: {entidad}"
                    )
                    continue

                return elegido

        except Exception as exc:
            print(
                f"⚠️ Selección editorial con {modelo}: {exc}"
            )

    # Fallback determinista: mejor score, con la misma bolsa ya filtrada.
    if candidatos:
        elegido = dict(
            candidatos[0]
        )

        elegido[
            "entidad_final"
        ] = elegido.get(
            "entidad_sugerida",
            ""
        )

        elegido[
            "categoria_final"
        ] = elegido.get(
            "categoria_editorial",
            "INTERNET"
        )

        elegido[
            "score_tendencia"
        ] = elegido.get(
            "score_preliminar",
            0
        )

        return elegido

    return None


def obtener_nuevo_tema_viral():
    candidatos = construir_bolsa_tendencias()

    print(
        "📊 Candidatos útiles encontrados: "
        f"{len(candidatos)}"
    )

    # Muestra una foto rápida del mix editorial.
    reparto = {}

    for c in candidatos:
        cat = c.get(
            "categoria_editorial",
            "INTERNET"
        )

        reparto[
            cat
        ] = reparto.get(
            cat,
            0
        ) + 1

    if reparto:
        print(
            "🧭 Reparto de candidatos: "
            + ", ".join(
                f"{k}={v}"
                for k, v in sorted(
                    reparto.items(),
                    key=lambda x: (
                        -x[1],
                        x[0]
                    )
                )
            )
        )

    elegido = seleccionar_mejor_tendencia_gemini(
        candidatos
    )

    if not elegido:
        print(
            "⚠️ No hay un tema suficientemente bueno. "
            "No se publicará contenido de relleno."
        )
        return None

    print(
        "🔥 Tendencia seleccionada: "
        f"{elegido.get('titulo')}"
    )
    print(
        "   Categoría: "
        f"{elegido.get('categoria_final') or elegido.get('categoria_editorial')}"
    )
    print(
        "   Entidad: "
        f"{elegido.get('entidad_final') or elegido.get('entidad_sugerida') or 'n/d'}"
    )
    print(
        "   Señales: "
        f"{', '.join(elegido.get('senales', [])) or 'n/d'}"
    )
    print(
        "   Fuentes: "
        f"{', '.join(elegido.get('fuentes', [])) or 'n/d'}"
    )

    if elegido.get(
        "por_que_ahora"
    ):
        print(
            "   Por qué ahora: "
            f"{elegido.get('por_que_ahora')}"
        )

    return elegido.get(
        "titulo",
        ""
    )


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
        "gemini-1.5-flash",
        "gemini-1.5-pro"
    ]


def generar_articulo_miri(tema_viral):
    modelos = obtener_modelos_disponibles()

    prompt = f"""
Eres la redactora principal del portal de actualidad,
entretenimiento y cultura de Internet "Miri te lo cuenta".

Escribe un artículo ameno, fresco, dinámico y con tono
de salseo sobre esta tendencia:

"{tema_viral}"

IMPORTANTE:
- No inventes hechos concretos que no estén justificados.
- No inventes declaraciones textuales.
- El contenido debe sonar natural y periodístico.
- Escribe en español correcto y natural de España.
- Revisa ORTOGRAFÍA, tildes, puntuación y concordancia antes de responder.
- No inventes palabras ni deformes expresiones conocidas.
- NO dejes ninguna frase a medias ni ningún párrafo cortado.
- Todos los párrafos deben terminar con una frase completa.
- El titular también debe estar ortográfica y gramaticalmente correcto.
- El artículo debe tener entre 4 y 7 párrafos cortos.
- Responde ÚNICAMENTE con un objeto JSON válido.
- No uses Markdown.

Devuelve EXACTAMENTE:

{{
  "titulo": "Titular llamativo y claro",
  "contenido_html": "<p>Primer párrafo...</p><p>Segundo párrafo...</p>",
  "titulo_miniatura": "TITULAR CORTO PARA LA MINIATURA",
  "categoria_visual": "VIRAL / REDES / TELECINCO / REALITY / ACTUALIDAD",
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

7. titulo_miniatura:
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
# 5B. REVISIÓN EDITORIAL DEL TEXTO
# ==========================================
def _modelo_editorial_rapido():
    """
    Elige UN solo modelo Flash para la corrección.
    Así la revisión no puede quedarse probando modelos durante minutos.
    """
    try:
        modelos = obtener_modelos_disponibles()
    except Exception:
        modelos = []

    validos = [
        m
        for m in modelos
        if "image" not in m.lower()
        and "tts" not in m.lower()
        and "embedding" not in m.lower()
    ]

    preferencias = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "flash",
    ]

    for preferencia in preferencias:
        for modelo in validos:
            if preferencia in modelo.lower():
                return modelo

    return validos[0] if validos else "gemini-2.5-flash"


def revisar_articulo_y_crear_titulo_social(
    titulo,
    contenido_html,
    tema_viral
):
    """
    Revisión editorial RÁPIDA:
    - UN solo modelo
    - UN solo intento
    - timeout corto
    - si falla, el bot continúa y publica

    No toca ningún dato visual.
    """
    modelo = _modelo_editorial_rapido()

    print(
        "✍️ Revisión ortográfica rápida..."
    )

    prompt = f"""
Corrige como editora profesional de español de España este artículo.

TEMA:
{tema_viral}

TÍTULO:
{titulo}

CUERPO HTML:
{contenido_html}

Haz únicamente esto:
- corrige ortografía, tildes, puntuación y concordancia;
- corrige palabras mal escritas;
- completa frases que hayan quedado gramaticalmente rotas;
- no inventes información ni elimines datos;
- conserva los mismos párrafos <p>...</p>;
- corrige también el título;
- crea un titulo_social de 55 a 100 caracteres;
- titulo_social debe ser una frase COMPLETA, nunca cortada;
- sin hashtags ni puntos suspensivos.

Devuelve SOLO JSON:
{{
  "titulo_corregido": "título corregido",
  "contenido_html_corregido": "<p>...</p><p>...</p>",
  "titulo_social": "título completo para redes"
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
            "temperature": 0.05
        }
    }

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
            timeout=18
        )

        if response.status_code != 200:
            print(
                "⚠️ La revisión editorial no respondió bien. "
                "Se continúa sin bloquear la publicación."
            )
            return {
                "titulo": titulo,
                "contenido_html": contenido_html,
                "titulo_social": titulo_social_fallback(
                    titulo,
                    tema_viral
                ),
            }

        data = response.json()

        raw = (
            data["candidates"][0]
            ["content"]
            ["parts"][0]
            ["text"]
        )

        revisado = extraer_json_de_respuesta(
            raw
        )

        if not isinstance(
            revisado,
            dict
        ):
            raise ValueError(
                "La revisión no devolvió JSON válido."
            )

        titulo_corregido = html.unescape(
            str(
                revisado.get(
                    "titulo_corregido",
                    ""
                )
                or ""
            )
        ).strip()

        html_corregido = str(
            revisado.get(
                "contenido_html_corregido",
                ""
            )
            or ""
        ).strip()

        titulo_social = html.unescape(
            str(
                revisado.get(
                    "titulo_social",
                    ""
                )
                or ""
            )
        ).strip()

        titulo_social = re.sub(
            r"\s+",
            " ",
            titulo_social
        ).strip()

        if not titulo_corregido:
            titulo_corregido = titulo

        if (
            not html_corregido
            or "<p" not in html_corregido.lower()
        ):
            html_corregido = contenido_html

        if not titulo_social_es_valido(
            titulo_social
        ):
            titulo_social = titulo_social_fallback(
                titulo_corregido,
                tema_viral
            )

        print(
            "✅ Revisión editorial terminada."
        )

        return {
            "titulo": titulo_corregido,
            "contenido_html": html_corregido,
            "titulo_social": titulo_social,
        }

    except Exception as exc:
        print(
            "⚠️ La revisión editorial se omite para no bloquear "
            f"la publicación: {exc}"
        )

        return {
            "titulo": titulo,
            "contenido_html": contenido_html,
            "titulo_social": titulo_social_fallback(
                titulo,
                tema_viral
            ),
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
            f"{entidad} Spain",
        ])

    if entidad and contexto:
        base.extend([
            f"{entidad} {contexto}",
            f"{entidad} {contexto} Spain",
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

    elif tipo_visual == "tema" and contexto:
        queries.append(contexto)

    return deduplicar_lista(
        queries
    )[:8]


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
                        query,
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
                        query,
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
            score -= 40

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
                * 12
            )

        if contexto_visual:
            score += int(
                cobertura_contexto
                * 12
            )

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

        candidato["score_metadata"] = score
        puntuados.append(
            candidato
        )

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

    # Probamos máximo 10 para no eternizar la ejecución.
    for indice, candidato in enumerate(
        puntuados[:10],
        start=1
    ):
        ruta = descargar_candidato(
            candidato,
            indice
        )

        if not ruta:
            continue

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

        # Blanco y negro / casi monocroma
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

                try:
                    os.remove(
                        ruta
                    )
                except Exception:
                    pass

                continue

        print(
            "🧪 "
            f"{candidato.get('engine')} | "
            f"score={score} | "
            f"{candidato.get('title') or 'sin título'} | "
            f"monocroma={analisis.get('casi_monocroma')}"
        )

        if score >= umbral:
            candidato[
                "score_final"
            ] = score

            candidato[
                "width_real"
            ] = ancho

            candidato[
                "height_real"
            ] = alto

            print(
                "✅ Imagen aprobada: "
                f"{candidato.get('title') or candidato.get('source_page')}"
            )

            return ruta, candidato

        try:
            os.remove(
                ruta
            )
        except Exception:
            pass

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

        return True

    raise Exception(
        "❌ Error crítico al publicar entrada "
        f"({r_post.status_code}): "
        f"{r_post.text}"
    )


# ==========================================
# 21. EJECUCIÓN PRINCIPAL
# ==========================================
if __name__ == "__main__":
    print(
        "🗓️ Bot estable: sistema antiguo + "
        f"intervalo de {INTERVALO_PUBLICACION_HORAS:.1f} h."
    )

    if EJECUCION_MANUAL_GITHUB:
        print(
            "🧪 Ejecución de prueba detectada "
            "(Run workflow o Re-run jobs): "
            "se permite publicar sin esperar 37,2 h."
        )
        print(
            "ℹ️ Las ejecuciones automáticas siguen respetando "
            f"{INTERVALO_PUBLICACION_HORAS:.1f} h."
        )
    elif not puede_publicar_por_frecuencia():
        raise SystemExit(0)

    tema = obtener_nuevo_tema_viral()

    if not tema:
        print(
            "⚠️ No hay una tendencia adecuada en esta ejecución. "
            "No se publicará relleno."
        )
        raise SystemExit(0)

    print(
        f"🔥 Tema seleccionado: {tema}"
    )

    articulo = generar_articulo_miri(
        tema
    )

    titulo = articulo[
        "titulo"
    ]

    contenido_html = articulo[
        "contenido_html"
    ]

    revision = revisar_articulo_y_crear_titulo_social(
        titulo=titulo,
        contenido_html=contenido_html,
        tema_viral=tema
    )

    titulo = revision.get(
        "titulo",
        titulo
    )

    contenido_html = revision.get(
        "contenido_html",
        contenido_html
    )

    titulo_social = revision.get(
        "titulo_social",
        ""
    )

    # Defensa final: aunque Gemini devuelva algo de menos de 100 caracteres,
    # si tiene comillas abiertas o acaba en "me", "de", "que", etc.,
    # NO se publica. Se sustituye por una cláusula completa del titular.
    if not titulo_social_es_valido(
        titulo_social
    ):
        titulo_social = titulo_social_fallback(
            titulo,
            tema
        )

    # Solo se inserta si es una frase COMPLETA y <=100 caracteres.
    if titulo_social_es_valido(
        titulo_social
    ):
        texto_visible = re.sub(
            r"<[^>]+>",
            " ",
            contenido_html
        )
        texto_visible = html.unescape(
            texto_visible
        )
        texto_visible = re.sub(
            r"\s+",
            " ",
            texto_visible
        ).strip()

        if not texto_visible.startswith(
            titulo_social
        ):
            contenido_html = (
                f"<p>{html.escape(titulo_social)}</p>"
                + contenido_html
            )
    else:
        print(
            "⚠️ No se insertará un título social incompleto."
        )

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
        "🔎 Título social completo "
        f"({len(titulo_social or '')}/100): "
        f"{titulo_social or 'no generado'}"
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
    print(
        f"🧭 Contexto visual: {contexto_visual}"
    )
    print(
        f"🔎 Búsquedas imagen: {busquedas_imagen}"
    )

    ruta_imagen, info_imagen = generar_miniatura(
        titulo_miniatura=titulo_miniatura,
        categoria_visual=categoria_visual,
        tipo_visual=tipo_visual,
        entidad_principal=entidad_principal,
        contexto_visual=contexto_visual,
        busquedas_imagen=busquedas_imagen
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
            guardar_en_historial(
                tema
            )
