#!/usr/bin/env python3
"""AZ Inversión · Alertas Bursátiles

Lee feeds de medios financieros, deja que Gemini (capa gratuita) elija las
noticias que importan y las redacte en formato flash AZ, y las publica en el
canal de Telegram con el bot. Pensado para ejecutarse cada 15 min en GitHub
Actions. Coste 0: si la cuota gratuita se agota, la llamada falla y no se cobra.

Variables de entorno
  TELEGRAM_BOT_TOKEN  token del bot (secreto)
  GEMINI_API_KEY      clave de Google AI Studio SIN facturación (secreto)
  MODE                normal | test | dry
"""
import calendar
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import feedparser
import requests

try:
    import trafilatura
except ImportError:  # el bot funciona igual, solo con los resúmenes del feed
    trafilatura = None

BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE, "config.json"), encoding="utf-8") as fh:
    CFG = json.load(fh)
with open(os.path.join(BASE, "prompt.md"), encoding="utf-8") as fh:
    SYSTEM_PROMPT = fh.read()
STATE_PATH = os.path.join(BASE, "state.json")

TZ_ES = ZoneInfo("Europe/Madrid")
TZ_PT = ZoneInfo("America/Los_Angeles")  # la cuota de Gemini se reinicia a medianoche del Pacífico
UA = {"User-Agent": "Mozilla/5.0 (compatible; AZInversionBot/1.0; +https://t.me/AZ_Inversion)"}

MODE = os.environ.get("MODE", "normal").strip().lower() or "normal"
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or CFG["telegram_chat"]
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "").strip()
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "").strip()

FALLBACK_IMAGE = {  # búsqueda de reserva por categoría si la del modelo no da resultados
    "macro": "economy finance charts", "bancos_centrales": "central bank building",
    "geopolitica": "world map globe", "materias_primas": "oil barrels industry",
    "empresas": "business district skyscrapers", "cripto": "bitcoin coins",
    "mercados": "stock market trading screen",
}

BANNED = [
    "el matiz", "lo importante", "aquí está lo interesante", "aqui esta lo interesante",
    "el mercado parece estar diciendo", "el mercado no parece", "es una señal bastante clara",
    "tiene más fondo del que parece", "y aquí aparece el problema", "ojo,", "ojo ",
]
PHRASE_FIXES = [
    r"\b(y\s+)?el matiz (es que|está en que|está en|es)\s*",
    r"\bojo,?\s*",
    r"\blo importante (no es solo[^,.]*,\s*(sino\s*)?|es que\s*|aquí es que\s*)",
    r"\b(y\s+)?aquí está lo interesante,?\s*",
    r"\bde momento,\s*",
]
EMOJI = (r"(?:[\U0001F1E6-\U0001F1FF]{2}"
         r"|[\U0001F000-\U0001F1E5\U0001F200-\U0001FAFF☀-➿⬀-⯿₿]️?)")
LEAD_EMOJI = r"^\s*(" + EMOJI + r")\s*"


def log(*args):
    print(*args, flush=True)


# ---------------------------------------------------------------- estado

def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            st = json.load(fh)
    except Exception:
        st = {}
    st.setdefault("seen", {})        # clave noticia -> epoch
    st.setdefault("posted", [])      # [{d, t, title}]
    st.setdefault("calls", {})       # fecha PT -> {modelo: n}
    st.setdefault("exhausted", {})   # fecha PT -> [modelos sin cuota]
    st.setdefault("last_call", 0)
    st.setdefault("photos", [])      # ids de Pexels ya usados, para no repetir foto
    return st


def save_state(st):
    now = time.time()
    st["seen"] = {k: v for k, v in st["seen"].items() if now - v < 3 * 86400}
    st["posted"] = st["posted"][-200:]
    st["photos"] = st["photos"][-400:]
    for key in ("calls", "exhausted"):
        st[key] = {d: st[key][d] for d in sorted(st[key])[-3:]}
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- fuentes

def clean(text, limit):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]


def news_key(title):
    t = re.sub(r"\s+-\s+[^-]{2,40}$", "", title.lower())  # quita " - Medio" de Google News
    t = re.sub(r"\W+", " ", t).strip()
    return hashlib.sha1(t[:140].encode()).hexdigest()[:16]


def fetch_feed(feed):
    try:
        r = requests.get(feed["url"], headers=UA, timeout=15)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
    except Exception as exc:
        return feed["name"], [], f"error {type(exc).__name__}"
    items = []
    now = time.time()
    for e in parsed.entries[:40]:
        title = clean(e.get("title"), 300)
        if not title:
            continue
        ts = None
        for k in ("published_parsed", "updated_parsed"):
            if e.get(k):
                ts = calendar.timegm(e[k])
                break
        if ts is None or ts > now + 600:
            ts = now  # sin fecha fiable, se trata como recién visto (luego queda deduplicado)
        items.append({
            "source": feed["name"],
            "title": title,
            "summary": clean(e.get("summary") or e.get("description"), 500),
            "link": e.get("link", ""),
            "ts": ts,
            "key": news_key(title),
        })
    return feed["name"], items, "ok"


def article_text(url):
    if not trafilatura or not url or "news.google.com" in url:
        return ""
    try:
        r = requests.get(url, headers=UA, timeout=8)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return ""
        txt = trafilatura.extract(r.text, include_comments=False, include_tables=False) or ""
        return re.sub(r"\s+", " ", txt)[: CFG["article_chars"]]
    except Exception:
        return ""


# ---------------------------------------------------------------- Gemini

def extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else None


def call_gemini(st, user_msg):
    """Prueba los modelos en orden. Devuelve dict o None. Nunca hay coste: sin facturación, 429 = parar."""
    day = dt.datetime.now(TZ_PT).strftime("%Y-%m-%d")
    calls = st["calls"].setdefault(day, {})
    exhausted = st["exhausted"].setdefault(day, [])
    cap = int(CFG.get("max_calls_per_model_per_day") or 0)
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json",
                             "maxOutputTokens": 8192},
    }
    for model in CFG["models"]:
        if model in exhausted or (cap and calls.get(model, 0) >= cap):
            continue
        for attempt in range(2):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                r = requests.post(url, json=body, timeout=150,
                                  headers={"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"})
            except requests.RequestException as exc:
                log(f"  {model}: error de red {type(exc).__name__}")
                break
            calls[model] = calls.get(model, 0) + 1
            st["last_call"] = time.time()
            if r.status_code == 200:
                data = r.json()
                parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                try:
                    parsed = extract_json(text)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    log(f"  modelo usado {model}")
                    return parsed
                log(f"  {model}: respuesta sin JSON válido")
                break
            msg = r.text[:400]
            if r.status_code == 429:
                if "PerDay" in msg or "per day" in msg.lower() or attempt == 1:
                    log(f"  {model}: cuota gratuita del día agotada, paso al siguiente")
                    exhausted.append(model)
                    break
                log(f"  {model}: límite por minuto, espero 40 s")
                time.sleep(40)
                continue
            if r.status_code in (400, 404) and ("not found" in msg.lower() or "not supported" in msg.lower()):
                log(f"  {model}: modelo no disponible, paso al siguiente")
                exhausted.append(model)
                break
            if r.status_code in (401, 403):
                log(f"  ERROR clave de Gemini rechazada ({r.status_code}). Revisa el secreto GEMINI_API_KEY. {msg}")
                return None
            log(f"  {model}: HTTP {r.status_code} {msg}")
            break
    return None


# ---------------------------------------------------------------- estilo AZ

NAMES = {  # códigos que el modelo a veces pone con $ en noticias que no son de acciones
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "SPX": "S&P 500", "SPY": "S&P 500",
    "NDX": "Nasdaq 100", "QQQ": "Nasdaq 100", "DJI": "Dow Jones", "IBEX": "Ibex 35", "DAX": "DAX",
    "OIL": "El petróleo", "WTI": "WTI", "BRENT": "Brent", "GOLD": "El oro", "XAU": "El oro",
    "DXY": "El dólar", "EURUSD": "El euro", "USDJPY": "El yen",
}
RUBRIC_MAX = {"alcance": 3, "sorpresa": 3, "magnitud": 2, "impacto": 2}


def rubric_score(post):
    """Suma la escala de importancia AZ. Cada criterio se limita a su máximo, así el modelo no puede inflarla."""
    pts = post.get("puntos")
    if not isinstance(pts, dict):
        return 0
    total = 0
    for key, top in RUBRIC_MAX.items():
        try:
            total += max(0, min(top, int(float(pts.get(key, 0)))))
        except (TypeError, ValueError):
            pass
    return total


def lint(text, category=""):
    """Aplica las reglas de redacción que no se pueden dejar al azar. Devuelve texto o None."""
    t = (text or "").strip().replace("\r", "")
    if category and category != "empresas":
        # sin ticker si no es una acción, en el titular se quita entero y en el cuerpo se deja el nombre
        t = re.sub(r"\$([A-Z][A-Z0-9.]{0,9})\b", lambda m: NAMES.get(m.group(1), m.group(1)), t)
    t = t.replace("—", ",").replace("–", "-")
    t = re.sub(r"(\d{1,2}):(\d{2})", r"\1.\2", t)          # 15:30 -> 15.30
    t = re.sub(r"\s*:\s*", ", ", t)                         # sin dos puntos
    t = re.sub(r",\s*,", ",", t)
    t = re.sub(r"\*\*\s*,\s*", "** ", t)
    t = re.sub(r"[ \t]+", " ", t)

    parts = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(parts) < 2:
        return None
    title, body = parts[0], " ".join(parts[1:])            # un solo párrafo
    title = title.replace("**", "").strip("* ").strip()
    lead = re.match(LEAD_EMOJI, title)
    lead = lead.group(1) + " " if lead and not title.startswith("$") else ""
    title = re.sub(EMOJI, "", title)
    title = re.sub(r" {2,}", " ", title).strip(" ,.")
    if not title:
        return None
    title = f"**{lead}{title}**"

    # quita coletillas prohibidas; si no se pueden quitar limpiamente, cae la frase entera
    for pat in PHRASE_FIXES:
        body = re.sub(pat, "", body, flags=re.I)
    body = re.sub(r"\s+,", ",", re.sub(r",\s*,", ",", body))
    body = re.sub(r"(^|[.!?]\s+)([a-záéíóúñ])", lambda m: m.group(1) + m.group(2).upper(), body)
    sentences = re.split(r"(?<=[.!?])\s+", body)
    sentences = [s for s in sentences if not any(b in s.lower() for b in BANNED)]
    body = " ".join(sentences).strip()
    if len(body) < 40:
        return None

    # como mucho 2 negritas en el cuerpo
    count = 0

    def keep_two(m):
        nonlocal count
        count += 1
        return m.group(0) if count <= 2 else m.group(1)
    body = re.sub(r"\*\*(.+?)\*\*", keep_two, body)

    # como mucho 1 emoji, y sin punto antes del emoji final
    emojis = list(re.finditer(EMOJI, body))
    if len(emojis) > 1:
        last = emojis[-1].group(0)
        body = re.sub(EMOJI, "", body).rstrip()
        body = re.sub(r"\s+([.,])", r"\1", body) + " " + last
    body = re.sub(r"[.,;]\s*(" + EMOJI + r")\s*$", r" \1", body)
    body = re.sub(r" {2,}", " ", body).strip()
    return f"{title}\n\n{body}"


def _search_pixabay(q):
    r = requests.get("https://pixabay.com/api/", timeout=15,
                     params={"key": PIXABAY_KEY, "q": q[:100], "image_type": "photo",
                             "orientation": "horizontal", "safesearch": "true", "per_page": 30})
    if r.status_code != 200:
        log(f"  Pixabay HTTP {r.status_code}")
        return []
    return [("pixabay", h["id"], h["largeImageURL"], h.get("user")) for h in r.json().get("hits", [])]


def _search_pexels(q):
    r = requests.get("https://api.pexels.com/v1/search", timeout=15, headers={"Authorization": PEXELS_KEY},
                     params={"query": q, "per_page": 30, "orientation": "landscape"})
    if r.status_code != 200:
        log(f"  Pexels HTTP {r.status_code}")
        return []
    return [("pexels", p["id"], p["src"].get("large") or p["src"]["original"], p.get("photographer"))
            for p in r.json().get("photos", [])]


def find_photo(st, query, category):
    """Busca una foto libre relacionada con la noticia (Pixabay o Pexels) y la descarga.
    Devuelve los bytes de la imagen o None. Se descarga para no enlazar en caliente."""
    if not CFG.get("images", False):
        return None
    providers = [f for k, f in ((PIXABAY_KEY, _search_pixabay), (PEXELS_KEY, _search_pexels)) if k]
    if not providers:
        return None
    for q in [query, FALLBACK_IMAGE.get(category)]:
        if not q:
            continue
        for search in providers:
            try:
                results = search(q)
            except Exception as exc:
                log(f"  error buscando foto {type(exc).__name__}")
                continue
            for source, pid, url, author in results:
                key = f"{source}:{pid}"
                if key in st["photos"] or pid in st["photos"]:
                    continue
                try:
                    img = requests.get(url, headers=UA, timeout=20)
                except requests.RequestException:
                    continue
                if img.status_code == 200 and img.headers.get("content-type", "").startswith("image/") \
                        and len(img.content) < 9_000_000:
                    st["photos"].append(key)
                    log(f"  foto {source} '{q}' de {author}")
                    return img.content
    return None


def to_telegram_html(text):
    t = html.escape(text, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)


SOURCE_NAMES = {
    "cnbc.com": "CNBC", "marketwatch.com": "MarketWatch", "seekingalpha.com": "Seeking Alpha",
    "asia.nikkei.com": "Nikkei Asia", "coindesk.com": "CoinDesk", "es.investing.com": "Investing.com",
    "expansion.com": "Expansión", "cincodias.elpais.com": "Cinco Días", "elpais.com": "El País",
    "europapress.es": "Europa Press", "reuters.com": "Reuters", "bloomberg.com": "Bloomberg",
    "ft.com": "Financial Times", "wsj.com": "WSJ", "eleconomista.es": "elEconomista",
    "finance.yahoo.com": "Yahoo Finance", "apnews.com": "AP", "bbc.com": "BBC", "elmundo.es": "El Mundo",
}


def source_link(post, cands):
    """Enlace directo a la noticia original (no Google News) y nombre del medio, o (None, None)."""
    from urllib.parse import urlparse
    for i in post.get("ids") or []:
        try:
            c = cands[int(i)]
        except (ValueError, IndexError, TypeError):
            continue
        url = c.get("link") or ""
        if not url.startswith("http") or "news.google.com" in url:
            continue
        host = urlparse(url).netloc.lower().removeprefix("www.")
        name = next((v for k, v in SOURCE_NAMES.items() if host == k or host.endswith("." + k)), None)
        return url, name or host
    return None, None


def send_telegram(text, photo=None, link=None):
    base = f"https://api.telegram.org/bot{TG_TOKEN}"
    if link and link[0]:
        # foto de la propia noticia vía previsualización del enlace, con la fuente al pie
        url, name = link
        body = to_telegram_html(text) + f'\n\n🔗 <a href="{html.escape(url, quote=True)}">Fuente {html.escape(name)}</a>'
        r = requests.post(f"{base}/sendMessage", timeout=20, json={
            "chat_id": TG_CHAT, "text": body, "parse_mode": "HTML",
            "link_preview_options": {"url": url, "prefer_large_media": True, "show_above_text": True}})
        if r.status_code == 200 and r.json().get("ok"):
            return True
        log(f"  el enlace con vista previa falló ({r.status_code}), publico sin él")
    # el pie de foto de Telegram admite 1024 caracteres; si no cabe, va como texto
    if photo and len(text.replace("**", "")) <= 1000:
        r = requests.post(f"{base}/sendPhoto", timeout=60,
                          data={"chat_id": TG_CHAT, "caption": to_telegram_html(text), "parse_mode": "HTML"},
                          files={"photo": ("foto.jpg", photo)})
        if r.status_code == 200 and r.json().get("ok"):
            return True
        log(f"  la foto no se pudo enviar ({r.status_code}), publico solo el texto")
    r = requests.post(
        f"{base}/sendMessage",
        json={"chat_id": TG_CHAT, "text": to_telegram_html(text), "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20,
    )
    ok = r.status_code == 200 and r.json().get("ok")
    if not ok:
        try:
            desc = r.json().get("description")
        except Exception:
            desc = r.text[:200]
        log(f"  ERROR Telegram {r.status_code} {desc}")
    return ok


# ---------------------------------------------------------------- principal

def build_message(cands, st, min_score, max_posts, now_es):
    recent = [f"[{p.get('c', '?')}] {p['title']}" for p in st["posted"][-30:]]
    lines = [
        f"Fecha y hora actual en España {now_es.strftime('%A %d/%m/%Y %H.%M')}.",
        f"Puntuación mínima en la escala de importancia AZ para publicar {min_score} sobre 10. Máximo {max_posts} noticias.",
    ]
    if MODE == "test":
        lines.append("Esto es una prueba del canal, devuelve SIEMPRE la mejor noticia disponible (1 sola) aunque no llegue a la puntuación, siempre que sea real y relevante.")
    lines.append("\nYa publicado en el canal (no repitas estos temas)")
    lines += [f"- {t}" for t in recent] or ["- (nada todavía)"]
    lines.append("\nNoticias candidatas")
    for i, c in enumerate(cands):
        mins = int((time.time() - c["ts"]) / 60)
        block = f"\n[{i}] {c['source']} · hace {mins} min\nTitular {c['title']}"
        if c["summary"] and c["summary"] not in c["title"]:
            block += f"\nResumen {c['summary']}"
        if c.get("text"):
            block += f"\nTexto {c['text']}"
        lines.append(block)
    return "\n".join(lines)


def main():
    if MODE not in ("normal", "test", "dry"):
        sys.exit(f"MODE desconocido {MODE}")
    if not GEMINI_KEY:
        sys.exit("Falta el secreto GEMINI_API_KEY")
    if MODE != "dry" and not TG_TOKEN:
        sys.exit("Falta el secreto TELEGRAM_BOT_TOKEN")

    st = load_state()
    now = time.time()
    now_es = dt.datetime.now(TZ_ES)
    log(f"AZ Alertas · modo {MODE} · {now_es:%d/%m/%Y %H.%M} España")

    with ThreadPoolExecutor(10) as ex:
        results = list(ex.map(fetch_feed, CFG["feeds"]))
    items = []
    for name, its, status in results:
        log(f"  feed {name}: {status} ({len(its)})")
        items += its

    max_age = CFG["max_age_minutes"] * 60 * (2 if MODE == "test" else 1)
    cands, keys = [], set()
    for it in sorted(items, key=lambda x: -x["ts"]):
        if now - it["ts"] > max_age or it["key"] in keys:
            continue
        if it["key"] in st["seen"] and MODE != "test":
            continue
        keys.add(it["key"])
        cands.append(it)
    cands = cands[: CFG["max_candidates"]]
    log(f"  candidatas nuevas {len(cands)}")
    if not cands:
        save_state(st)
        if MODE == "test":
            sys.exit("La prueba no ha encontrado noticias en los feeds")
        return

    today = now_es.strftime("%Y-%m-%d")
    posted_today = sum(1 for p in st["posted"] if p.get("d") == today)
    night = CFG["night_hours"][0] <= now_es.hour < CFG["night_hours"][1]
    min_score = CFG["min_score_night"] if night else CFG["min_score"]
    max_posts = min(CFG["max_posts_per_run"], CFG["daily_post_cap"] - posted_today)
    if MODE == "test":
        min_score, max_posts = CFG["test_min_score"], 1

    def mark_seen():
        for c in cands:
            st["seen"][c["key"]] = now

    if max_posts <= 0:
        log("  tope diario de publicaciones alcanzado")
        mark_seen()
        save_state(st)
        return

    # agrupa llamadas para no gastar la cuota gratuita en 1 noticia suelta
    mins_since = (now - st.get("last_call", 0)) / 60
    if MODE == "normal" and len(cands) < CFG["call_if_new_at_least"] and mins_since < CFG["call_every_minutes"]:
        log(f"  espero a juntar más noticias ({len(cands)} nuevas, última llamada hace {int(mins_since)} min)")
        save_state(st)
        return

    with ThreadPoolExecutor(10) as ex:
        texts = list(ex.map(lambda c: article_text(c["link"]), cands))
    for c, t in zip(cands, texts):
        c["text"] = t

    result = call_gemini(st, build_message(cands, st, min_score, max_posts, now_es))
    if result is None:
        log("  sin respuesta del modelo, lo reintento en la siguiente ejecución")
        save_state(st)
        if MODE == "test":
            sys.exit("La prueba no ha obtenido respuesta de Gemini")
        return

    posts = [p for p in result.get("posts", []) if isinstance(p, dict) and p.get("text")]
    for p in posts:
        p["score"] = rubric_score(p)
        log(f"  propuesta [{p['score']}/10 · {p.get('category', '?')}] {p.get('puntos')} {p.get('motivo', '')}")
    posts.sort(key=lambda p: -p["score"])
    last_post = st["posted"][-1]["t"] if st["posted"] else 0
    gap_ok = (now - last_post) / 60 >= CFG["min_minutes_between_posts"]
    published = 0
    for p in posts:
        if published >= max_posts:
            break
        score = float(p.get("score", 0) or 0)
        if MODE != "test" and score < min_score:
            continue
        if MODE == "normal" and not gap_ok and score < CFG["breaking_score"]:
            log(f"  [{score}] se queda fuera para no saturar (última publicación hace {int((now - last_post) / 60)} min)")
            continue
        if MODE == "normal" and published >= 1 and score < CFG["breaking_score"]:
            continue  # en una misma pasada solo se suma una segunda si es de última hora
        text = lint(p["text"], p.get("category", ""))
        if not text:
            log("  descartada por formato")
            continue
        log(f"\n--- [{p.get('score')}] ---\n{text}\n")
        link = source_link(p, cands)
        photo = None
        if not link[0] and CFG.get("stock_photo_fallback"):
            photo = find_photo(st, p.get("imagen", ""), p.get("category", "")) if CFG.get("images") else None
        if MODE == "dry":
            log(f"  fuente {link[1]} {link[0]} · foto de banco {'sí' if photo else 'no'}")
            published += 1
            continue
        if send_telegram(text, photo, link):
            published += 1
            st["posted"].append({"d": today, "t": int(time.time()), "c": p.get("category", "?"),
                                "title": text.split("\n")[0].strip("*")})
            time.sleep(CFG["seconds_between_posts"])
        elif MODE == "test":
            save_state(st)
            sys.exit("La prueba no ha podido publicar en Telegram")

    log(f"  publicadas {published}")
    mark_seen()
    save_state(st)
    if MODE == "test" and published == 0:
        sys.exit("La prueba no ha generado ninguna noticia publicable")


if __name__ == "__main__":
    main()
