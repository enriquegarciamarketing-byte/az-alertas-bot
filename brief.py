#!/usr/bin/env python3
"""AZ News · Brief diario

Cada mañana (7.45 hora de España) recoge las noticias de las últimas 24 horas y
los datos de mercado, deja que Gemini redacte los 8 posts del brief con las reglas
de AZ News, genera una tarjeta AZ para cada uno y los publica en orden en el canal.

MODE  normal (solo publica una vez al día y entre las 7.00 y las 10.59 de España)
      test   (publica ya, aunque hoy ya se haya publicado)
      dry    (no publica, genera textos e imágenes en brief_out/ para revisarlos)
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import bot
import cards

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE, "brief_state.json")
OUT = os.path.join(BASE, "brief_out")
with open(os.path.join(BASE, "brief_prompt.md"), encoding="utf-8") as fh:
    BRIEF_PROMPT = fh.read()

MODE = os.environ.get("MODE", "normal").strip().lower() or "normal"
log = bot.log
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]

MARKETS = [  # símbolo de Yahoo Finance, nombre, decimales, sufijo
    ("^VIX", "VIX", 2, ""), ("^GSPC", "S&P 500", 2, ""), ("^IXIC", "Nasdaq", 2, ""),
    ("^IBEX", "Ibex 35", 2, ""), ("^STOXX50E", "Euro Stoxx 50", 2, ""),
    ("BZ=F", "Brent", 2, " $"), ("GC=F", "Oro", 2, " $"), ("EURUSD=X", "Euro-dólar", 4, ""),
]
BRIEF_FEEDS = [  # búsquedas de 24 h que se suman a las fuentes del bot
    ("Google News Euríbor", "https://news.google.com/rss/search?q=eur%C3%ADbor+hoy+when:2d&hl=es&gl=ES&ceid=ES:es"),
    ("Google News Empresas España", "https://news.google.com/rss/search?q=(OPA+OR+despidos+OR+resultados+OR+Ibex)+when:1d&hl=es&gl=ES&ceid=ES:es"),
    ("Google News IPO y debuts", "https://news.google.com/rss/search?q=(IPO+OR+debut+OR+%22stock+soars%22+OR+%22shares+plunge%22)+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("Google News IA y chips", "https://news.google.com/rss/search?q=(Nvidia+OR+OpenAI+OR+semiconductors+OR+%22data+center%22)+when:1d&hl=en-US&gl=US&ceid=US:en"),
]


def es_num(x, dec=2):
    s = f"{x:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            st = json.load(fh)
    except Exception:
        st = {}
    st.setdefault("briefs", [])   # [{date, historia, protagonista, concepto, contexto, curioso}]
    st.setdefault("calls", {})
    st.setdefault("exhausted", {})
    return st


def save_state(st):
    st["briefs"] = st["briefs"][-40:]
    for key in ("calls", "exhausted"):
        st[key] = {d: st[key][d] for d in sorted(st[key])[-3:]}
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- datos de mercado

def yahoo(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol)}"
    r = requests.get(url, params={"range": "10d", "interval": "1d"}, timeout=15,
                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"})
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    pts = [(t, c) for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c is not None]
    (t1, last), (_, prev) = pts[-1], pts[-2]
    day = dt.datetime.fromtimestamp(t1, bot.TZ_ES).date()
    return {"valor": last, "cambio": (last / prev - 1) * 100, "fecha": day}


def euribor():
    """Euríbor 12 meses diario. Si no se puede leer con seguridad, devuelve None (el brief no lo inventa)."""
    for url in ("https://www.euribor-rates.eu/es/tipos-euribor-actuales/4/euribor-12-meses/",
                "https://www.euribor-rates.eu/en/current-euribor-rates/4/euribor-rate-12-months/"):
        try:
            r = requests.get(url, headers=bot.UA, timeout=15)
            if r.status_code != 200:
                continue
            txt = re.sub(r"<[^>]+>", " ", r.text)
            txt = re.sub(r"\s+", " ", txt)
            pairs = re.findall(r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})\s+(-?\d{1,2}[.,]\d{3})\s*%", txt)
            if len(pairs) >= 2:
                vals = [float(v.replace(",", ".")) for _, v in pairs[:2]]
                if all(-1 < v < 10 for v in vals):
                    a, b2, y = re.split(r"[/-]", pairs[0][0])
                    if int(b2) > 12:          # la página en inglés da mes/día
                        a, b2 = b2, a
                    return {"valor": vals[0], "anterior": vals[1], "fecha": f"{int(a):02d}/{int(b2):02d}/{y}"}
        except Exception:
            continue
    return None


def market_block():
    data, lines = {}, []
    for sym, name, dec, suf in MARKETS:
        try:
            q = yahoo(sym)
            data[name] = q
            lines.append(f"- {name} {es_num(q['valor'], dec)}{suf}, {'+' if q['cambio'] >= 0 else ''}"
                         f"{es_num(q['cambio'], 2)} % en la sesión del {q['fecha']:%d/%m/%Y}")
        except Exception as exc:
            log(f"  mercado {name}: sin dato ({type(exc).__name__})")
    e = euribor()
    if e:
        data["Euríbor 12 meses"] = e
        lines.append(f"- Euríbor 12 meses (dato diario) {es_num(e['valor'], 3)} %, el anterior {es_num(e['anterior'], 3)} %, "
                     f"fecha {e['fecha']}")
    else:
        lines.append("- Euríbor 12 meses, sin dato verificado en el bloque. Úsalo solo si una noticia de hoy da el dato "
                     "diario con fecha; si no, no lo menciones.")
    return data, "\n".join(lines)


def termometro(vix):
    if vix is None:
        return None
    estado = "Calma" if vix < 15 else "Cautela" if vix < 25 else "Riesgo"
    return f"VIX {es_num(vix, 1)} — {estado}", estado


# ---------------------------------------------------------------- texto y publicación

def sanitize(htm):
    """Escapa todo y deja pasar solo <b>, <u> e <i>, que es lo que admite el pie de Telegram."""
    t = html.escape(htm or "", quote=False)
    t = re.sub(r"&lt;(/?)(b|u|i)&gt;", r"<\1\2>", t)
    t = t.replace("**", "")
    return t.strip()


def visible_len(htm):
    return len(html.unescape(re.sub(r"<[^>]+>", "", htm)))


def no_colons(htm):
    """Regla de estilo AZ, sin dos puntos en la redacción. Se mantienen solo al presentar una lista."""
    out = []
    for line in htm.split("\n"):
        plain = re.sub(r"<[^>]+>", "", line).strip()
        if plain.startswith("•") or plain.endswith(":"):
            out.append(line)
            continue
        line = re.sub(r"(\d{1,2}):(\d{2})", r"\1.\2", line)
        line = re.sub(r"\s*:\s+", ", ", line)
        out.append(line)
    return "\n".join(out)


def build_caption(p, header=None):
    parts = []
    if header:
        parts.append(header)
    parts.append(f"<b><u>{sanitize(p.get('seccion', ''))}</u></b>")
    parts.append(f"<b>{no_colons(sanitize(p.get('titular', ''))).upper()}</b>")
    parts.append(no_colons(sanitize(p.get("cuerpo", ""))))
    if (p.get("fuentes") or "").strip():
        parts.append(f"📚 {sanitize(p['fuentes'])}")
    return "\n\n".join(x for x in parts if x)


def tg(method, **kw):
    r = requests.post(f"https://api.telegram.org/bot{bot.TG_TOKEN}/{method}", timeout=60, **kw)
    ok = r.status_code == 200 and r.json().get("ok")
    if not ok:
        try:
            log(f"  ERROR Telegram {method} {r.status_code} {r.json().get('description')}")
        except Exception:
            log(f"  ERROR Telegram {method} {r.status_code}")
    return ok


def publish(caption, png):
    with open(png, "rb") as fh:
        img = fh.read()
    if visible_len(caption) <= 1024:
        for _ in range(3):
            if tg("sendPhoto", data={"chat_id": bot.TG_CHAT, "caption": caption, "parse_mode": "HTML"},
                  files={"photo": ("az.png", img)}):
                return True
            time.sleep(5)
        return False
    # si el texto no cabe en el pie, imagen y texto van seguidos
    log("  pie demasiado largo, imagen y texto por separado")
    return (tg("sendPhoto", data={"chat_id": bot.TG_CHAT}, files={"photo": ("az.png", img)})
            and tg("sendMessage", json={"chat_id": bot.TG_CHAT, "text": caption, "parse_mode": "HTML",
                                        "disable_web_page_preview": True}))


# ---------------------------------------------------------------- principal

def main():
    if MODE not in ("normal", "test", "dry"):
        sys.exit(f"MODE desconocido {MODE}")
    if not bot.GEMINI_KEY:
        sys.exit("Falta el secreto GEMINI_API_KEY")
    if MODE != "dry" and not bot.TG_TOKEN:
        sys.exit("Falta el secreto TELEGRAM_BOT_TOKEN")

    now = dt.datetime.now(bot.TZ_ES)
    today = now.strftime("%Y-%m-%d")
    st = load_state()
    log(f"AZ Brief · modo {MODE} · {now:%d/%m/%Y %H.%M} España")
    if MODE == "normal":
        if not 7 <= now.hour <= 10:
            log("  fuera de la franja de la mañana, no toca")
            return
        if any(b.get("date") == today for b in st["briefs"]):
            log("  el brief de hoy ya está publicado")
            return

    # noticias de 24 h
    feeds = [dict(f, url=f["url"].replace("when:2h", "when:1d")) for f in bot.CFG["feeds"]]
    feeds += [{"name": n, "url": u} for n, u in BRIEF_FEEDS]
    with ThreadPoolExecutor(10) as ex:
        results = list(ex.map(bot.fetch_feed, feeds))
    items, seen = [], set()
    for name, its, status in results:
        log(f"  feed {name}: {status} ({len(its)})")
        for it in its:
            if time.time() - it["ts"] <= 26 * 3600 and it["key"] not in seen:
                seen.add(it["key"])
                items.append(it)
    items.sort(key=lambda x: -x["ts"])
    items = items[:110]
    with ThreadPoolExecutor(10) as ex:
        texts = list(ex.map(lambda c: bot.article_text(c["link"])[:500], items[:60]))
    for c, t in zip(items, texts):
        c["text"] = t
    log(f"  noticias de 24 h {len(items)}")
    if len(items) < 10:
        save_state(st)
        sys.exit("Muy pocas noticias, no publico un brief cojo")

    market, mblock = market_block()
    log("  datos de mercado\n" + mblock)
    vix = market.get("VIX", {}).get("valor")
    termo = termometro(vix)

    recent = st["briefs"][-3:]
    conceptos = [b.get("concepto", "") for b in st["briefs"][-30:] if b.get("concepto")]
    fecha_txt = f"{DIAS[now.weekday()]} {now.day} de {MESES[now.month - 1]} de {now.year}"
    msg = [f"Hoy es {fecha_txt}, son las {now:%H.%M} en España.",
           "\nDATOS DE MERCADO VERIFICADOS (únicas cifras de mercado que puedes usar)", mblock,
           "\nBRIEFS DE LOS ÚLTIMOS 3 DÍAS (no repetir)"]
    msg += [f"- {b['date']} · historia {b.get('historia')} · abre {b.get('protagonista')} · concepto {b.get('concepto')} · "
            f"contexto {', '.join(b.get('contexto', []))} · curioso {b.get('curioso')}" for b in recent] or ["- (ninguno)"]
    msg.append("\nCONCEPTOS USADOS EN 30 DÍAS (no repetir) " + (", ".join(conceptos) or "ninguno"))
    msg.append("\nNOTICIAS DE LAS ÚLTIMAS 24 HORAS")
    for i, c in enumerate(items):
        h = int((time.time() - c["ts"]) / 3600)
        block = f"\n[{i}] {c['source']} · hace {h} h\nTitular {c['title']}"
        if c["summary"] and c["summary"] not in c["title"]:
            block += f"\nResumen {c['summary'][:350]}"
        if c.get("text"):
            block += f"\nTexto {c['text']}"
        msg.append(block)

    result = bot.call_gemini(st, "\n".join(msg), system=BRIEF_PROMPT, max_tokens=24000)
    posts = (result or {}).get("posts") or []
    posts = [p for p in posts if isinstance(p, dict)]
    posts.sort(key=lambda p: int(p.get("n", 99)))
    if len(posts) < 8:
        save_state(st)
        sys.exit(f"Gemini no ha devuelto los 8 posts ({len(posts)})")
    posts = posts[:8]

    # post 7, las filas de la tarjeta salen de los datos de mercado, no del modelo
    filas = []
    for name, dec, suf in (("S&P 500", 2, ""), ("Euríbor 12 meses", 3, " %"), ("Brent", 2, " $"),
                           ("Ibex 35", 2, ""), ("Oro", 2, " $")):
        q = market.get(name)
        if not q or len(filas) >= 3:
            continue
        if name == "Euríbor 12 meses":
            delta = q["valor"] - q["anterior"]
            chg = f"{'+' if delta >= 0 else '−'}{es_num(abs(delta), 3)} puntos"
        else:
            chg = f"{'+' if q['cambio'] >= 0 else '−'}{es_num(abs(q['cambio']), 2)} % sesión del {q['fecha']:%d/%m}"
        filas.append({"label": name, "valor": es_num(q["valor"], dec) + suf, "cambio": chg})
    c7 = posts[6].setdefault("card", {})
    c7["filas"], c7["hero"] = filas, ""
    if vix is not None:
        c7["stat1"], c7["stat1_label"] = es_num(vix, 1), "VIX, termómetro del miedo"
        c7["stat2"], c7["stat2_label"] = termo[1], "estado del mercado"

    header = f"<b>🗞 AZ NEWS · {fecha_txt[0].upper() + fecha_txt[1:]}</b>"
    if termo:
        header += f"\n<b>🌡 Termómetro: {termo[0]}</b>"

    os.makedirs(OUT, exist_ok=True)
    date_label = now.strftime("%d · %m · %Y")
    ready = []
    for i, p in enumerate(posts):
        cap = build_caption(p, header if i == 0 else None)
        png = os.path.join(OUT, f"post_{i + 1}.png")
        card = dict(p.get("card") or {})
        if not card.get("titulo"):
            card["titulo"] = p.get("titular", "")[:22]
        try:
            cards.render(card, date_label, png)
        except AssertionError as exc:   # si algo no cabe, tarjeta mínima antes que nada
            log(f"  tarjeta {i + 1} simplificada ({exc})")
            cards.render({"chip": card.get("chip"), "titulo": card.get("titulo"), "acento": card.get("acento"),
                          "hero": card.get("hero"), "hero_label": card.get("hero_label")}, date_label, png)
        ready.append((cap, png))
        log(f"\n=== POST {i + 1} ({visible_len(cap)} caracteres) ===\n{cap}")
        with open(os.path.join(OUT, f"post_{i + 1}.txt"), "w", encoding="utf-8") as fh:
            fh.write(cap)

    if MODE == "dry":
        log("\nModo prueba, no se publica nada. Las imágenes están en los artefactos de la ejecución.")
        save_state(st)
        return

    for i, (cap, png) in enumerate(ready):
        if not publish(cap, png):
            save_state(st)
            sys.exit(f"Se ha parado en el post {i + 1}, los anteriores sí se publicaron")
        log(f"  post {i + 1} publicado")
        time.sleep(4)

    mem = result.get("memoria") or {}
    mem["date"] = today
    st["briefs"].append(mem)
    save_state(st)
    log("Brief publicado entero, 8 de 8")


if __name__ == "__main__":
    main()
