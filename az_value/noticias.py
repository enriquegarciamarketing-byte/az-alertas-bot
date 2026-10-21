"""AZ Value · noticias corporativas de las empresas que llevas en cartera.

Vive en este repo público para poder pasar cada 15 minutos gratis (en el
privado az-radar los minutos de GitHub Actions no daban). Cada pasada mira Google News y Yahoo Finance para cada empresa de
"posiciones" en portfolio.yaml, deja que Gemini (capa gratuita) agrupe los
titulares por hecho, los puntúe y los redacte, y publica por el bot de
AZ Value solo lo que supera la relevancia mínima. Formato de cada mensaje

  <b>Amper, S.A. (AMP)</b>
  Corporativo · relevancia 98/100

  <b>Amper anuncia la adquisición de ...</b>

  Párrafo de tres o cuatro frases.

  Fuente: medio · Ver gráfico

La relevancia la suma el código a partir de cuatro notas de Gemini
(impacto, magnitud, novedad y fiabilidad de la fuente) para que el modelo
no la infle.

  python az_value/noticias.py              busca, puntúa y envía
  python az_value/noticias.py --dry-run    imprime los mensajes sin enviarlos
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import logging
import os
import pathlib
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import yaml


log = logging.getLogger(__name__)
RAIZ = pathlib.Path(__file__).resolve().parent
CONFIG = RAIZ / "empresas.yaml"
ESTADO = RAIZ / "estado.json"
API = "https://generativelanguage.googleapis.com/v1beta"
MADRID = ZoneInfo("Europe/Madrid")
UA = "AZRadar/1.0 (+https://github.com/enriquegarciamarketing-byte)"

CATEGORIAS = ["Corporativo", "Resultados", "Accionistas", "Financiación", "Contratos",
              "Regulatorio", "Producto", "Directiva", "Analistas"]

PROMPT = """Eres el editor de un canal de Telegram que avisa a un inversor particular de las
noticias corporativas de las empresas que tiene en cartera. Tienes titulares de las
últimas horas agrupados por empresa. Cada titular lleva un id.

Tu trabajo
1. Agrupa los titulares que cuentan el mismo hecho en un único evento.
2. Descarta lo que no es un hecho nuevo de la empresa, por ejemplo artículos que solo
   explican que la acción sube o baja sin causa concreta, listas de "acciones para
   comprar", opiniones genéricas, previas de resultados sin datos, noticias de otra
   empresa con nombre parecido o noticias que ya estaban en "Ya publicadas".
3. Para cada evento que quede, puntúa con estas cuatro notas enteras
   impacto 0-40      cuánto puede cambiar el valor de la acción a medio plazo.
                     Fusiones, adquisiciones, ampliaciones de capital, OPA, impagos,
                     rebaja o subida fuerte de previsiones, contratos que cambian el
                     negocio 30-40. Activismo, entrada o salida de un accionista de
                     referencia, resultados, aprobaciones regulatorias, cambios de
                     consejero delegado 18-30. Contratos menores, alianzas, cambios de
                     recomendación de analistas 8-18. Trámites rutinarios 0-8.
   magnitud 0-30     tamaño del hecho respecto al tamaño de la empresa.
   novedad 0-20      cuánto sorprende frente a lo que el mercado ya sabía.
   fuente 0-10       10 comunicado oficial (CNMV, SEC, nota de la propia empresa),
                     7 agencia o diario financiero serio, 3 blog o agregador.
4. Redacta en español de España con ortografía perfecta (todas las tildes y eñes, por
   ejemplo dólares, compañía, operación, posición), tono informativo y sobrio como una
   agencia. Cifras con formato español (35.000 millones, 4,1 millones, 7,10 euros).
   titulo   una frase de 70 a 110 caracteres con el sujeto y el hecho, sin dos puntos.
   texto    tres frases y entre 45 y 70 palabras. Las dos primeras cuentan el hecho
            usando SOLO los datos de los titulares y resúmenes que tienes (quién, qué,
            cuánto, cuándo). Si no tienes cifras, no las inventes y di que la compañía
            no las ha detallado. La tercera explica en general qué suele implicar este
            tipo de hecho para el accionista. Nada de frases de relleno que repitan lo
            dicho con otras palabras. Sin dos puntos, sin rayas, sin emojis, sin exclamaciones.

Devuelve JSON con esta forma exacta
{{"eventos": [{{"ticker": "TICKER tal cual aparece", "ids": ["id", "..."],
  "categoria": una de {categorias},
  "impacto": n, "magnitud": n, "novedad": n, "fuente": n,
  "fuente_original": "CNMV" o "SEC" o "Nota de prensa" o el nombre del medio,
  "tema": "etiqueta corta del hecho en minúsculas, por ejemplo adquisicion advanced radar",
  "titulo": "...", "texto": "..."}}]}}
Si no hay nada que merezca la pena devuelve {{"eventos": []}}.

Ya publicadas en los últimos días (no las repitas salvo que haya un giro importante)
{publicadas}

Titulares
{bloques}
"""


# ───────────────────────── cartera y configuración

def empresas_en_cartera() -> list[dict]:
    """Empresas que llevas en cartera (az_value/empresas.yaml)."""
    cfg = (yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}).get("empresas") or {}
    out = []
    for t, c in cfg.items():
        c = c or {}
        corto = t.split(".")[0]
        nombre = c.get("nombre") or corto
        out.append({
            "ticker": t,
            "nombre": nombre,
            "cabecera": c.get("cabecera") or f"{nombre} ({corto})",
            "buscar": c.get("buscar") or [nombre],
            "ingles": bool(c.get("ingles", not t.endswith(".MC"))),
            "grafico": c.get("grafico") or corto,
        })
    return out


def ajustes() -> dict:
    base = {"relevancia_minima": 65, "horas_maximas": 30, "max_por_pasada": 4,
            "silencio_desde": 1, "silencio_hasta": 7}
    base.update((yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}).get("ajustes") or {})
    return base


def _modelos_flash(key: str) -> list[str]:
    """Modelos Flash disponibles para esta clave, del más nuevo al más antiguo (lite al final)."""
    try:
        with urllib.request.urlopen(f"{API}/models?key={key}&pageSize=200", timeout=30) as r:
            modelos = json.loads(r.read()).get("models", [])
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo listar modelos de Gemini (%s)", e)
        return ["gemini-flash-latest", "gemini-2.5-flash"]
    nombres = [m["name"].split("/", 1)[-1] for m in modelos
               if "generateContent" in m.get("supportedGenerationMethods", [])
               and "flash" in m["name"] and not any(x in m["name"] for x in
                                                    ("image", "tts", "audio", "live"))]

    def clave(n: str):
        v = re.search(r"(\d+(?:\.\d+)?)", n)
        return ("lite" in n, "preview" in n or "exp" in n, -(float(v.group(1)) if v else 0))

    return sorted(nombres, key=clave) or ["gemini-flash-latest"]


CHAT_CACHE = RAIZ / ".chat"   # no se sube al repo (público); lo guarda la caché de Actions


def chat_destino() -> str | None:
    """Tu chat privado con el bot de AZ Value: secreto, caché o el último que le escribió."""
    if os.environ.get("TELEGRAM_VALUE_CHAT_ID"):
        return os.environ["TELEGRAM_VALUE_CHAT_ID"].strip()
    if CHAT_CACHE.exists() and CHAT_CACHE.read_text().strip():
        return CHAT_CACHE.read_text().strip()
    token = os.environ.get("TELEGRAM_VALUE_BOT_TOKEN")
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20) as r:
            upd = json.loads(r.read()).get("result", [])
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo preguntar al bot por tu chat (%s)", e)
        return None
    for u in reversed(upd):
        chat = (u.get("message") or u.get("edited_message") or {}).get("chat") or {}
        if chat.get("type") == "private":
            CHAT_CACHE.write_text(str(chat["id"]))
            return str(chat["id"])
    return None


def enviar_telegram(texto: str) -> None:
    token = os.environ["TELEGRAM_VALUE_BOT_TOKEN"]
    chat = chat_destino()
    if not chat:
        raise RuntimeError("No encuentro tu chat: escribe cualquier cosa al bot de AZ Value en Telegram")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for intento in (texto, re.sub(r"<[^>]+>", "", texto)):   # si el HTML falla, en texto plano
        body = {"chat_id": chat, "text": intento, "disable_web_page_preview": True}
        if intento is texto:
            body["parse_mode"] = "HTML"
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if json.loads(r.read()).get("ok"):
                    return
        except urllib.error.HTTPError as e:
            log.warning("Telegram rechazó el mensaje (%s)", e.read()[:200])
    raise RuntimeError("Telegram no aceptó el mensaje")


# ───────────────────────── fuentes

def _get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _id(texto: str) -> str:
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:10]


def google_news(termino: str, ingles: bool) -> list[dict]:
    q = urllib.parse.quote(f'"{termino}" when:2d')
    region = "hl=en-US&gl=US&ceid=US:en" if ingles else "hl=es&gl=ES&ceid=ES:es"
    try:
        root = ET.fromstring(_get(f"https://news.google.com/rss/search?q={q}&{region}"))
    except Exception as e:  # noqa: BLE001
        log.info("Google News sin respuesta para %s (%s)", termino, e)
        return []
    out = []
    for item in root.iter("item"):
        titulo = (item.findtext("title") or "").strip()
        src = item.find("source")
        medio = (src.text or "").strip() if src is not None else ""
        if medio and titulo.endswith(f" - {medio}"):
            titulo = titulo[: -len(medio) - 3]
        try:
            fecha = email.utils.parsedate_to_datetime(item.findtext("pubDate") or "")
        except Exception:  # noqa: BLE001
            continue
        out.append({"titulo": titulo, "medio": medio, "url": item.findtext("link") or "",
                    "fecha": fecha.astimezone(dt.timezone.utc), "resumen": ""})
    return out


def yahoo_news(ticker: str) -> list[dict]:
    try:
        import yfinance as yf
        items = yf.Ticker(ticker).news or []
    except Exception as e:  # noqa: BLE001
        log.info("Yahoo sin noticias para %s (%s)", ticker, e)
        return []
    out = []
    for n in items[:15]:
        c = n.get("content", n)
        titulo = (c.get("title") or "").strip()
        if not titulo:
            continue
        prov = c.get("provider") or {}
        url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
               or c.get("link") or "")
        fecha = c.get("pubDate") or c.get("displayTime")
        try:
            f = dt.datetime.fromisoformat(fecha.replace("Z", "+00:00")) if isinstance(fecha, str) \
                else dt.datetime.fromtimestamp(c.get("providerPublishTime", 0), dt.timezone.utc)
        except Exception:  # noqa: BLE001
            continue
        out.append({"titulo": titulo, "medio": prov.get("displayName", "Yahoo Finance") if isinstance(prov, dict)
                    else c.get("publisher", ""), "url": url, "fecha": f.astimezone(dt.timezone.utc),
                    "resumen": (c.get("summary") or "")[:400]})
    return out


def recoger(empresas: list[dict], estado: dict, horas: int, ahora: dt.datetime) -> dict[str, list[dict]]:
    """Titulares nuevos (no vistos y recientes) por ticker."""
    limite = ahora - dt.timedelta(hours=horas)
    vistos = estado.setdefault("vistos", {})
    por_ticker: dict[str, list[dict]] = {}
    from concurrent.futures import ThreadPoolExecutor
    tareas = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for e in empresas:
            fut = [pool.submit(google_news, termino, e["ingles"]) for termino in e["buscar"]]
            fut.append(pool.submit(yahoo_news, e["ticker"]))
            tareas[e["ticker"]] = fut
    for e in empresas:
        items = []
        for f in tareas[e["ticker"]]:
            try:
                items += f.result()
            except Exception:  # noqa: BLE001
                pass
        nuevos, titulos = [], set()
        for it in items:
            clave = _normal(it["titulo"])
            if not clave or clave in titulos or it["fecha"] < limite:
                continue
            titulos.add(clave)
            it["id"] = _id(e["ticker"] + clave)
            if it["id"] in vistos:
                continue
            nuevos.append(it)
        nuevos.sort(key=lambda x: x["fecha"], reverse=True)
        if nuevos:
            por_ticker[e["ticker"]] = nuevos[:12]
    return por_ticker


# ───────────────────────── Gemini

def gemini(prompt: str) -> dict | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        log.warning("Sin GEMINI_API_KEY: no se puede puntuar ni redactar")
        return None
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    candidatos = [os.environ["GEMINI_MODEL"]] if os.environ.get("GEMINI_MODEL") else _modelos_flash(key)
    # la capa gratuita da 503 cuando va saturada: dos rondas por los modelos con pausa
    lite = [m for m in candidatos if "lite" in m][:1]
    candidatos = list(dict.fromkeys(candidatos[:3] + ["gemini-flash-latest"] + lite))
    for ronda in range(2):
        for model in candidatos:
            try:
                req = urllib.request.Request(f"{API}/models/{model}:generateContent?key={key}",
                                             data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.loads(r.read())
                out = json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
                log.info("Noticias puntuadas con %s", model)
                return out
            except urllib.error.HTTPError as e:
                cuerpo = e.read()[:300].decode("utf-8", "replace")
                log.warning("Gemini %s falló (%s %s)", model, e.code, cuerpo.replace("\n", " "))
            except Exception as e:  # noqa: BLE001
                log.warning("Gemini %s falló (%s)", model, e)
        if ronda == 0:
            time.sleep(20)
    return None


def construir_prompt(por_ticker: dict[str, list[dict]], empresas: list[dict], estado: dict) -> str:
    nombres = {e["ticker"]: e["nombre"] for e in empresas}
    bloques = []
    for t, items in por_ticker.items():
        lineas = []
        for it in items:
            extra = f" | resumen {it['resumen']}" if it.get("resumen") else ""
            lineas.append(f"- id {it['id']} | {it['fecha'].strftime('%d/%m %H:%M')} UTC | "
                          f"{it['medio']} | {it['titulo']}{extra}")
        bloques.append(f"[{t}] {nombres.get(t, t)}\n" + "\n".join(lineas))
    pub = [f"- [{p['ticker']}] {p['tema']} ({p['titulo']})" for p in estado.get("publicadas", [])[-40:]]
    return PROMPT.format(categorias=json.dumps(CATEGORIAS, ensure_ascii=False),
                         publicadas="\n".join(pub) or "ninguna",
                         bloques="\n\n".join(bloques))


# ───────────────────────── selección y formato

def relevancia(ev: dict) -> int:
    def nota(k: str, tope: int) -> int:
        try:
            return max(0, min(tope, int(round(float(ev.get(k, 0))))))
        except (TypeError, ValueError):
            return 0
    return nota("impacto", 40) + nota("magnitud", 30) + nota("novedad", 20) + nota("fuente", 10)


_VACIAS = set("""a al ante con de del desde el en entre la las lo los para por que se sin su sus un una y o e
the a an of to in on for and or with by from at as its is are be has have after over new""".split())


def _normal(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", t).strip()


def _palabras(texto: str) -> set[str]:
    return {w for w in _normal(texto).split() if len(w) > 2 and w not in _VACIAS}


def repetida(ev: dict, publicadas: list[dict]) -> bool:
    """Filtro duro en código: mismo tema o titular muy parecido de la misma empresa."""
    tema = _normal(ev.get("tema", ""))
    pal = _palabras(ev.get("titulo", ""))
    for p in publicadas:
        if p["ticker"] != ev["ticker"]:
            continue
        if tema and tema == _normal(p.get("tema", "")):
            return True
        otras = set(p.get("palabras") or _palabras(p["titulo"]))
        if pal and otras and len(pal & otras) / min(len(pal), len(otras)) >= 0.6:
            return True
    return False


def limpiar(texto: str) -> str:
    t = re.sub(r"\s*[—–]\s*", ", ", texto or "")
    t = re.sub(r"(?<!\d):(?!\d)", ",", t)          # sin dos puntos (respeta horas 10:30)
    t = re.sub(r"[!¡]", "", t)
    t = re.sub(r",\s*,", ",", t)
    return re.sub(r"\s+", " ", t).strip(" ,")


def formatear(ev: dict, empresa: dict, item: dict | None) -> str:
    e = html.escape
    lineas = [f"<b>{e(empresa['cabecera'])}</b>",
              f"{e(ev['categoria'])} · relevancia {ev['relevancia']}/100",
              "",
              f"<b>{e(limpiar(ev['titulo']))}</b>",
              "",
              e(limpiar(ev["texto"])),
              ""]
    fuente = limpiar(ev.get("fuente_original") or (item or {}).get("medio") or "Prensa")
    enlaces = []
    if item and item.get("url"):
        enlaces.append(f'<a href="{e(item["url"], quote=True)}">Fuente: {e(fuente)}</a>')
    else:
        enlaces.append(f"Fuente: {e(fuente)}")
    graf = f"https://www.tradingview.com/symbols/{empresa['grafico'].replace(':', '-')}/"
    enlaces.append(f'<a href="{e(graf, quote=True)}">Ver gráfico</a>')
    lineas.append(" · ".join(enlaces))
    return "\n".join(lineas)


def seleccionar(respuesta: dict, por_ticker: dict[str, list[dict]], empresas: list[dict],
                estado: dict, minimo: int, tope: int) -> list[tuple[dict, dict, dict | None]]:
    por_t = {e["ticker"]: e for e in empresas}
    items = {it["id"]: it for lista in por_ticker.values() for it in lista}
    elegidos = []
    for ev in (respuesta or {}).get("eventos") or []:
        t = str(ev.get("ticker", "")).strip()
        if t not in por_t:
            t = next((k for k in por_t if k.split(".")[0] == t.split(".")[0]), "")
        if not t or not ev.get("titulo") or not ev.get("texto"):
            continue
        ev["ticker"] = t
        ev["relevancia"] = relevancia(ev)
        if ev.get("categoria") not in CATEGORIAS:
            ev["categoria"] = "Corporativo"
        ids = [i for i in ev.get("ids") or [] if i in items]
        if ev["relevancia"] < minimo:
            log.info("Descartada (%s/100) %s · %s", ev["relevancia"], t, ev["titulo"])
            continue
        if repetida(ev, estado.get("publicadas", []) + [x[0] for x in elegidos]):
            log.info("Repetida %s · %s", t, ev["titulo"])
            continue
        # el enlace de la fuente: el titular más fiable del grupo (Yahoo trae URL directa)
        cand = [items[i] for i in ids]
        item = next((c for c in cand if "news.google.com" not in c["url"]), cand[0] if cand else None)
        elegidos.append((ev, por_t[t], item))
    elegidos.sort(key=lambda x: x[0]["relevancia"], reverse=True)
    return elegidos[:tope]


# ───────────────────────── estado

def cargar_estado() -> dict:
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"vistos": {}, "publicadas": []}


def guardar_estado(estado: dict, ahora: dt.datetime) -> None:
    corte = (ahora - dt.timedelta(days=7)).isoformat()
    estado["vistos"] = {k: v for k, v in estado.get("vistos", {}).items() if v >= corte}
    estado["publicadas"] = [p for p in estado.get("publicadas", []) if p["fecha"] >= corte][-200:]
    estado["ultima_pasada"] = ahora.isoformat()
    ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


# ───────────────────────── principal

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--minimo", type=int, help="relevancia mínima (por defecto la de noticias.yaml)")
    ap.add_argument("--horas", type=int, help="antigüedad máxima de los titulares")
    ap.add_argument("--tope", type=int, help="máximo de mensajes en esta pasada")
    ap.add_argument("--excluir", default="", help="tickers a saltar en esta pasada, separados por comas")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.dry_run:
        if not os.environ.get("TELEGRAM_VALUE_BOT_TOKEN"):
            log.warning("Falta el secreto TELEGRAM_VALUE_BOT_TOKEN: no se publica nada")
            return 0
        if not chat_destino():
            log.warning("No encuentro tu chat privado con el bot de AZ Value: escríbele cualquier cosa en Telegram")
            return 0

    ahora = dt.datetime.now(dt.timezone.utc)
    aj = ajustes()
    hora = ahora.astimezone(MADRID).hour
    if not args.dry_run and aj["silencio_desde"] <= hora < aj["silencio_hasta"]:
        log.info("Horario de silencio (%s:00 en Madrid): no se publica", hora)
        return 0

    empresas = empresas_en_cartera()
    fuera = {t.strip().upper().split(".")[0] for t in args.excluir.split(",") if t.strip()}
    empresas = [e for e in empresas if e["ticker"].split(".")[0] not in fuera]
    log.info("Empresas en cartera: %s", ", ".join(e["ticker"] for e in empresas))
    estado = cargar_estado()
    primera = not estado.get("ultima_pasada")
    horas = args.horas or (12 if primera else aj["horas_maximas"])
    por_ticker = recoger(empresas, estado, horas, ahora)
    total = sum(len(v) for v in por_ticker.values())
    log.info("Titulares nuevos: %s", total)
    if not total:
        if not args.dry_run:
            guardar_estado(estado, ahora)
        return 0

    respuesta = gemini(construir_prompt(por_ticker, empresas, estado))
    if respuesta is None:
        log.warning("Sin respuesta de Gemini: lo reintento en la próxima pasada")
        return 0

    elegidos = seleccionar(respuesta, por_ticker, empresas, estado,
                           aj["relevancia_minima"] if args.minimo is None else args.minimo,
                           args.tope or (aj["max_por_pasada"] if args.minimo is None else 10))
    log.info("Para publicar: %s", len(elegidos))

    if args.dry_run:
        for ev in (respuesta or {}).get("eventos") or []:
            print(f"· {relevancia(ev):>3}/100 {ev.get('ticker')} · {ev.get('titulo')}")
        print()
        for ev, emp, item in elegidos:
            print(formatear(ev, emp, item), end="\n\n────────\n\n")
        return 0

    for ev, emp, item in elegidos:
        enviar_telegram(formatear(ev, emp, item))
        estado.setdefault("publicadas", []).append({
            "ticker": ev["ticker"], "tema": ev.get("tema", ""), "titulo": ev["titulo"],
            "relevancia": ev["relevancia"], "fecha": ahora.isoformat(),
            "palabras": sorted(_palabras(ev["titulo"]))})
        log.info("Publicada (%s/100) %s · %s", ev["relevancia"], ev["ticker"], ev["titulo"])
    # todo lo leído queda como visto aunque no se publique, para no repasarlo
    for lista in por_ticker.values():
        for it in lista:
            estado["vistos"][it["id"]] = ahora.isoformat()
    guardar_estado(estado, ahora)
    return 0


if __name__ == "__main__":
    sys.exit(main())
