"""Genera la web estática de AZ News (GitHub Pages) con lo que el bot ya ha publicado en Telegram.

Fuentes
  web/data/noticias.json          noticias flash (las guarda bot.py al publicar)
  claude_brief/AAAA-MM-DD/        brief de Claude (posts.json + post_N.png)
  web/gemini_brief/AAAA-MM-DD/    brief de respaldo de Gemini (mismo formato)

Salida
  _site/ (o WEB_OUT), que el workflow web.yml despliega en GitHub Pages:
         portada, última hora, noticias, brief diario, glosario (el concepto del día), categorías,
         legales, sitemap.xml, feed.xml, robots.txt, ads.txt

Solo reescribe los ficheros que cambian, así un build sin novedades no genera commit.
Uso: python web/build.py
"""
import datetime as dt
import html
import json
import os
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from PIL import Image

WEB = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(WEB)
OUT = os.path.abspath(os.environ.get("WEB_OUT") or os.path.join(ROOT, "_site"))
TZ = ZoneInfo("Europe/Madrid")

with open(os.path.join(WEB, "site.json"), encoding="utf-8") as fh:
    S = json.load(fh)
if S.get("custom_domain"):
    S["base_url"] = "https://" + S["custom_domain"].strip("/")
BASE_URL = S["base_url"].rstrip("/")
BASE_PATH = urlparse(BASE_URL).path.rstrip("/")

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
MES_CORTO = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"]
CATS = {
    "macro": "Macroeconomía", "bancos_centrales": "Bancos centrales", "geopolitica": "Geopolítica",
    "materias_primas": "Materias primas", "empresas": "Empresas", "cripto": "Cripto", "mercados": "Mercados",
}
CAT_INTRO = {
    "macro": "Inflación, empleo, PIB, déficit y todo lo que mueve la economía real.",
    "bancos_centrales": "Fed, BCE, Banco de Japón y Banco de Inglaterra, tipos de interés y lo que dicen sus gobernadores.",
    "geopolitica": "Ormuz, Irán, Rusia, aranceles y sanciones, contado por cómo afecta a los mercados.",
    "materias_primas": "Petróleo, gas, oro, cobre y el resto de materias primas.",
    "empresas": "Resultados, fusiones y movimientos de las cotizadas que importan.",
    "cripto": "Bitcoin, ether y lo que pasa en el mercado cripto.",
    "mercados": "Índices, bolsas y movimientos fuertes del mercado.",
}

written = set()


# ----------------------------------------------------------------- utilidades

def u(path=""):
    """Ruta absoluta dentro del sitio, sirve igual en github.io/az-alertas-bot que en un dominio propio."""
    return f"{BASE_PATH}/{path.lstrip('/')}"


def absu(path=""):
    return f"{BASE_URL}/{path.lstrip('/')}"


def esc(t):
    return html.escape(t or "", quote=True)


def slugify(t, maxlen=70):
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    if len(t) > maxlen:
        t = t[:maxlen].rsplit("-", 1)[0]
    return t or "noticia"


EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF₿⭐️‍]+")


def no_emoji(t):
    return re.sub(r"\s+", " ", EMOJI.sub("", t or "")).strip()


def fecha_larga(d):
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]} de {d.year}"


def fecha_corta(d):
    return f"{d.day} {MES_CORTO[d.month - 1]} {d.year}"


def plain(htm):
    t = re.sub(r"<br\s*/?>", " ", htm or "")
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def cut(t, n=155):
    t = (t or "").strip()
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(",;.") + "…"


WORD = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+")


def case_map(text):
    """Cómo se escribe cada palabra en el cuerpo del post (Sabadell, BBVA, IA, España, bono...)."""
    t = plain(text)
    seen = {}
    for m in WORD.finditer(t):
        w = m.group(0)
        before = t[:m.start()].rstrip()
        start = not before or before[-1] in ".!?:•—\n"
        low = w.lower()
        if w == low:
            seen[low] = low            # si alguna vez va en minúscula, es palabra normal
        elif not start and seen.get(low) != low:
            seen.setdefault(low, w)
    return seen


# reserva para nombres y siglas que no aparezcan en el cuerpo del post
KNOWN = {w.lower(): w for w in """OPA OPV OPEP BCE FMI IPC PIB PER VIX ETF TAE TIN UE IA EE. UU. EEUU OCDE IBEX Ibex Nasdaq
Brent Texas Fed Wall Street Ormuz Irán Israel China Japón España Europa Rusia Ucrania Trump Powell Lagarde Bitcoin
Dow Jones S&P Euríbor Tesoro Moncloa Hacienda Banco BBVA Santander Sabadell CaixaBank Iberdrola Repsol Inditex
Telefónica Nvidia Apple Microsoft Amazon Tesla Meta Google Alphabet Netflix Anthropic OpenAI""".split()}
KNOWN["euríbor"] = "euríbor"
KNOWN["banco"] = "banco"
KNOWN["ee"], KNOWN["uu"] = "EE", "UU"


def sentence_case(t, ref=""):
    """'UN AÑO DESPUÉS, BBVA VALE UN 40% MÁS' -> 'Un año después, BBVA vale un 40% más'.
    Las mayúsculas de nombres y siglas se copian del cuerpo del post (ref)."""
    cmap = case_map(ref)
    def fix(m):
        w = m.group(0)
        if not w.isupper():
            return w
        return cmap.get(w.lower()) or KNOWN.get(w.lower(), w.lower())
    s = WORD.sub(fix, t or "")
    i = next((k for k, c in enumerate(s) if c.isalpha()), None)
    return s if i is None else s[:i] + s[i].upper() + s[i + 1:]


def write(rel, content):
    """Escribe solo si cambia."""
    path = os.path.join(OUT, rel)
    written.add(os.path.normpath(path))
    data = content.encode("utf-8") if isinstance(content, str) else content
    try:
        with open(path, "rb") as fh:
            if fh.read() == data:
                return
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def keep(rel):
    written.add(os.path.normpath(os.path.join(OUT, rel)))


def image_variants(src, rel_noext):
    """PNG de la tarjeta -> .webp para la página y .jpg para compartir. Solo se convierte una vez."""
    webp, jpg = rel_noext + ".webp", rel_noext + ".jpg"
    for rel in (webp, jpg):
        keep(rel)
    pw, pj = os.path.join(OUT, webp), os.path.join(OUT, jpg)
    if os.path.exists(pw) and os.path.exists(pj):   # las tarjetas no cambian una vez publicadas (caché del workflow)
        return webp, jpg
    os.makedirs(os.path.dirname(pw), exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGB")
        im.save(pw, "WEBP", quality=80, method=6)
        im.save(pj, "JPEG", quality=82, optimize=True, progressive=True)
    return webp, jpg


# ----------------------------------------------------------------- HTML de Telegram -> HTML seguro

class _Clean(HTMLParser):
    MAP = {"b": "strong", "strong": "strong", "i": "em", "em": "em", "u": None, "s": None, "code": None}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href.startswith(("http://", "https://")):
                self.out.append(f'<a href="{esc(href)}" rel="nofollow noopener" target="_blank">')
            else:
                self.out.append("<a>")
        elif tag == "br":
            self.out.append("<br>")
        elif self.MAP.get(tag):
            self.out.append(f"<{self.MAP[tag]}>")

    def handle_endtag(self, tag):
        if tag == "a":
            self.out.append("</a>")
        elif self.MAP.get(tag):
            self.out.append(f"</{self.MAP[tag]}>")

    def handle_data(self, data):
        self.out.append(esc(data).replace("\n", "<br>"))


def clean_html(fragment):
    p = _Clean()
    p.feed(fragment)
    p.close()
    return "".join(p.out)


def tickers(htm):
    return re.sub(r"(?<![\w>])\$([A-Z][A-Z0-9.]{0,9})\b", r'<span class="tk">$\1</span>', htm)


def paragraphs(fragment_html):
    blocks = [b.strip() for b in re.split(r"\n\s*\n", fragment_html or "") if b.strip()]
    return "".join(f"<p>{tickers(clean_html(b))}</p>" for b in blocks)


# ----------------------------------------------------------------- datos

def load_news():
    try:
        with open(os.path.join(WEB, "data", "noticias.json"), encoding="utf-8") as fh:
            news = json.load(fh)
    except (OSError, ValueError):
        news = []
    used = {}
    for n in news:
        d = dt.datetime.fromtimestamp(n["ts"], TZ)
        n["dt"] = d
        slug = slugify(no_emoji(n["title"]))
        key = f"{d:%Y/%m/%d}/{slug}"
        if key in used:  # mismo titular el mismo día
            slug = f"{slug}-{n['id'][:4]}"
            key = f"{d:%Y/%m/%d}/{slug}"
        used[key] = 1
        n["path"] = f"noticias/{key}/"
        n["cat_label"] = CATS.get(n.get("category"), "Mercados")
        body = html.escape(n.get("body", ""), quote=False)
        body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body, flags=re.S)
        n["body_html"] = "".join(f"<p>{tickers(b.strip()).replace(chr(10), '<br>')}</p>"
                                 for b in re.split(r"\n\s*\n", body) if b.strip())
        n["clean_title"] = no_emoji(n["title"])
        n["desc"] = cut(plain(n["body_html"]))
    news.sort(key=lambda n: -n["ts"])
    return news


def parse_post(caption):
    """Separa un post del brief en cabecera (solo post 1), etiqueta de sección, titular y cuerpo."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", caption or "") if b.strip()]
    info = {"header": "", "thermo": "", "label": "", "headline": "", "body": ""}
    i = 0
    if blocks and "AZ NEWS" in blocks[0] and "<u>" not in blocks[0]:
        info["header"] = blocks[0]
        m = re.search(r"Termómetro:\s*(.+?)</b>", blocks[0])
        if m:
            info["thermo"] = plain(m.group(1))
        i = 1
    if i < len(blocks) and "<u>" in blocks[i]:
        info["label"] = no_emoji(plain(blocks[i]))
        i += 1
    if i < len(blocks):
        b = blocks[i]
        if re.fullmatch(r"<b>[^<]+</b>", b):
            info["headline"] = plain(b)
            i += 1
    info["body"] = "\n\n".join(blocks[i:])
    return info


def load_briefs():
    briefs = {}
    for src, folder in (("gemini", os.path.join(WEB, "gemini_brief")), ("claude", os.path.join(ROOT, "claude_brief"))):
        if not os.path.isdir(folder):
            continue
        for day in sorted(os.listdir(folder)):
            man = os.path.join(folder, day, "posts.json")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or not os.path.exists(man):
                continue
            try:
                with open(man, encoding="utf-8") as fh:
                    posts = sorted(json.load(fh), key=lambda p: int(p.get("n", 99)))
            except (OSError, ValueError):
                continue
            if len(posts) < 4:
                continue
            items = []
            for p in posts:
                info = parse_post(p.get("caption", ""))
                info["n"] = int(p.get("n", len(items) + 1))
                info["png"] = os.path.join(folder, day, p.get("image", ""))
                items.append(info)
            briefs[day] = {"date": day, "source": src, "posts": items}  # Claude pisa a Gemini el mismo día
    out = []
    for day in sorted(briefs, reverse=True):
        b = briefs[day]
        d = dt.date.fromisoformat(day)
        b["d"] = d
        b["path"] = f"brief/{day}/"
        b["thermo"] = next((p["thermo"] for p in b["posts"] if p["thermo"]), "")
        lead = b["posts"][0]
        b["headline"] = lead["headline"] or "Resumen de mercados"
        b["title"] = f"Bolsa y mercados hoy, {fecha_larga(d)}"
        first_para = re.split(r"\n\s*\n", lead["body"] or "")
        b["headline_txt"] = sentence_case(b["headline"], lead["body"])
        b["desc"] = cut(f"{b['headline_txt']}. " + plain(first_para[1] if len(first_para) > 1 else first_para[0]))
        out.append(b)
    return out


def glossary_from(briefs):
    terms, seen = [], set()
    for b in sorted(briefs, key=lambda b: b["date"]):
        for p in b["posts"]:
            if "CONCEPTO" in p["label"].upper() and p["headline"]:
                name = sentence_case(p["headline"], p["body"])
                bare = re.sub(r"^(el|la|los|las)\s+", "", name, flags=re.I)
                slug = slugify(bare, 60)
                if slug in seen:
                    continue
                seen.add(slug)
                terms.append({"name": name, "bare": bare[:1].upper() + bare[1:], "slug": slug,
                              "path": f"glosario/{slug}/", "post": p, "brief": b,
                              "desc": cut(plain(p["body"]), 150)})
    terms.sort(key=lambda t: t["bare"].lower())
    return terms


# ----------------------------------------------------------------- plantilla

PLANE = ('<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M21.5 3.5 2.8 10.7c-.9.4-.9 1.6 0 1.9l4.6 '
         '1.6 1.8 5.6c.3.9 1.4 1.1 2 .4l2.6-2.7 4.7 3.5c.7.5 1.7.1 1.9-.8l3-14.6c.2-1.1-.9-2-1.9-1.5Z" '
         'stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="m7.4 14.2 10-7.2" '
         'stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>')


def tg_btn(text="Unirme gratis en Telegram", cls="btn", src="web"):
    return (f'<a class="{cls}" href="{esc(S["telegram_url"])}" target="_blank" rel="noopener" '
            f'data-cta="{src}">{PLANE}<span>{esc(text)}</span></a>')


def cta_box(title="Recibe estas alertas antes que nadie",
            text="Las noticias que mueven el mercado llegan primero al canal, gratis y sin ruido."):
    return (f'<aside class="cta"><div><h3>{esc(title)}</h3><p>{esc(text)}</p></div>'
            f'{tg_btn(src="cta")}</aside>')


def ad(slot_key="adsense_slot_article"):
    client, slot = S.get("adsense_client"), S.get(slot_key)
    if not client or not slot:
        return ""
    return (f'<div class="ad"><ins class="adsbygoogle" style="display:block" data-ad-client="{esc(client)}" '
            f'data-ad-slot="{esc(slot)}" data-ad-format="auto" data-full-width-responsive="true"></ins>'
            f'<script>(adsbygoogle=window.adsbygoogle||[]).push({{}});</script></div>')


def page(path, title, desc, body, *, nav="", image=None, og_type="website", jsonld=None, noindex=False):
    full_title = title if title.endswith(S["name"]) else f"{title} | {S['name']}"
    img = absu(image or "assets/og.png")
    head_extra = []
    if S.get("adsense_client"):
        head_extra.append(f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client='
                          f'{esc(S["adsense_client"])}" crossorigin="anonymous"></script>')
    if S.get("google_site_verification"):
        head_extra.append(f'<meta name="google-site-verification" content="{esc(S["google_site_verification"])}">')
    if noindex:
        head_extra.append('<meta name="robots" content="noindex">')
    for block in (jsonld or []):
        head_extra.append('<script type="application/ld+json">' + json.dumps(block, ensure_ascii=False) + "</script>")

    def nl(href, label, key):
        cur = ' aria-current="page"' if nav == key else ""
        return f'<a href="{u(href)}"{cur}>{label}</a>'

    doc = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{absu(path)}">
<meta property="og:site_name" content="{esc(S['name'])}">
<meta property="og:locale" content="es_ES">
<meta property="og:type" content="{og_type}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{absu(path)}">
<meta property="og:image" content="{img}">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#F7F7F4" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#111214" media="(prefers-color-scheme: dark)">
<link rel="icon" href="{u('assets/favicon.png')}" type="image/png">
<link rel="apple-touch-icon" href="{u('assets/apple-touch-icon.png')}">
<link rel="alternate" type="application/rss+xml" title="{esc(S['name'])}" href="{u('feed.xml')}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{u('assets/style.css')}">
{chr(10).join(head_extra)}
</head>
<body>
<header class="top"><div class="wrap">
<a class="brand" href="{u()}"><img class="logo-light" src="{u('assets/logo-red.png')}" alt="" width="38" height="29"><img class="logo-dark" src="{u('assets/logo-white.png')}" alt="" width="38" height="29">{esc(S['name'])}</a>
<nav class="nav" aria-label="Secciones">{nl('noticias/', 'Última hora', 'noticias')}{nl('brief/', 'Brief diario', 'brief')}{nl('glosario/', 'Glosario', 'glosario')}</nav>
{tg_btn('Telegram', 'btn btn-sm', 'header')}
</div></header>
<main>
{body}
</main>
<footer class="foot"><div class="wrap">
<div class="cols"><div><strong style="color:var(--ink)">{esc(S['name'])}</strong> · {esc(S['tagline'])}</div>
<nav aria-label="Legal"><a href="{u('noticias/')}">Última hora</a><a href="{u('brief/')}">Brief diario</a><a href="{u('glosario/')}">Glosario</a><a href="{u('sobre/')}">Quiénes somos</a><a href="{u('aviso-legal/')}">Aviso legal</a><a href="{u('privacidad/')}">Privacidad y cookies</a></nav></div>
<p class="disc">Contenido informativo y divulgativo. Nada de lo publicado en {esc(S['name'])} es asesoramiento financiero ni una recomendación de compra o venta de ningún activo. Invertir conlleva riesgo de pérdida.</p>
</div></footer>
<div class="stick">{tg_btn('Alertas de bolsa gratis en Telegram', 'btn btn-lg', 'sticky')}</div>
</body>
</html>
"""
    write(os.path.join(path, "index.html") if not path.endswith(".html") else path, doc)


def org():
    return {"@type": "Organization", "name": S["name"], "url": absu(),
            "logo": {"@type": "ImageObject", "url": absu("assets/apple-touch-icon.png")},
            "sameAs": [S["telegram_url"]]}


def crumbs_ld(items):
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": n, "item": absu(p)} for i, (n, p) in enumerate(items)]}


def crumbs_html(items):
    parts = [f'<a href="{u(p)}">{esc(n)}</a>' for n, p in items[:-1]]
    parts.append(f"<span>{esc(items[-1][0])}</span>")
    return '<nav class="crumbs" aria-label="Migas">' + "<span>›</span>".join(parts) + "</nav>"


def feed_items(news, empty_text):
    if not news:
        return f'<p class="empty">{esc(empty_text)}</p>'
    lis = []
    for n in news:
        lis.append(f'<li><a href="{u(n["path"])}"><time datetime="{n["dt"].isoformat()}">{n["dt"]:%H:%M}</time>'
                   f'<span><span class="t">{esc(n["title"])}</span><span class="c">{esc(n["cat_label"])} · '
                   f'{fecha_corta(n["dt"])}</span></span></a></li>')
    return '<ul class="feed">' + "".join(lis) + "</ul>"


# ----------------------------------------------------------------- páginas

def build_assets():
    with open(os.path.join(WEB, "static", "style.css"), encoding="utf-8") as fh:
        write("assets/style.css", fh.read())
    logo = Image.open(os.path.join(ROOT, "az_logo.png")).convert("RGBA")
    logo = logo.crop(logo.getbbox())
    alpha = logo.getchannel("A")

    def tinted(rgb, width):
        im = Image.new("RGBA", logo.size, rgb + (0,))
        im.putalpha(alpha)
        h = round(logo.height * width / logo.width)
        return im.resize((width, h), Image.LANCZOS)

    import io

    def png_bytes(im):
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        return buf.getvalue()

    write("assets/logo-red.png", png_bytes(tinted((155, 28, 28), 152)))
    write("assets/logo-white.png", png_bytes(tinted((237, 237, 232), 152)))
    for name, size in (("favicon.png", 64), ("apple-touch-icon.png", 180)):
        bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        from PIL import ImageDraw
        ImageDraw.Draw(bg).rounded_rectangle([0, 0, size - 1, size - 1], radius=round(size * .24), fill=(155, 28, 28, 255))
        mark = tinted((255, 255, 255), round(size * .66))
        bg.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
        write(f"assets/{name}", png_bytes(bg))
    og = os.path.join(WEB, "static", "og.png")
    if os.path.exists(og):
        with open(og, "rb") as fh:
            write("assets/og.png", fh.read())


def build_news_pages(news):
    for idx, n in enumerate(news):
        related = [m for m in news if m is not n and m["category"] == n["category"]][:5]
        if len(related) < 5:
            related += [m for m in news if m is not n and m not in related][:5 - len(related)]
        items = [("Inicio", ""), ("Última hora", "noticias/"), (n["cat_label"], f"categoria/{slugify(n['category'])}/"),
                 (cut(n["clean_title"], 60), n["path"])]
        body = f"""<div class="wrap narrow">
{crumbs_html(items)}
<article>
<a class="chip" href="{u('categoria/' + slugify(n['category']) + '/')}">{esc(n['cat_label'])}</a>
<h1>{esc(n['title'])}</h1>
<div class="meta"><time datetime="{n['dt'].isoformat()}">{fecha_larga(n['dt']).capitalize()} · {n['dt']:%H:%M}</time><span>·</span><span>Redacción AZ News</span></div>
<div class="body lead">{n['body_html']}</div>
</article>
{cta_box()}
{ad()}
<section class="card"><h2><span class="dot"></span>Más noticias</h2>{feed_items(related, '')}
<a class="more" href="{u('noticias/')}">Ver toda la última hora →</a></section>
</div>"""
        ld = [{"@context": "https://schema.org", "@type": "NewsArticle", "headline": cut(n["clean_title"], 110),
               "description": n["desc"], "datePublished": n["dt"].isoformat(), "dateModified": n["dt"].isoformat(),
               "inLanguage": "es-ES", "mainEntityOfPage": absu(n["path"]), "image": [absu("assets/og.png")],
               "articleSection": n["cat_label"], "author": org(), "publisher": org()}, crumbs_ld(items)]
        page(n["path"], n["clean_title"], n["desc"], body, nav="noticias", og_type="article", jsonld=ld)


def build_news_lists(news, briefs):
    days = {}
    for n in news:
        days.setdefault(n["dt"].date(), []).append(n)
    brief_days = {b["d"]: b for b in briefs}
    day_links = "".join(f'<li><a class="chip chip-n" href="{u(f"noticias/{d:%Y/%m/%d}/")}">{fecha_corta(d)}</a></li>'
                        for d in sorted(days, reverse=True)[:60])
    body = f"""<div class="wrap narrow">
<header class="page-head"><h1>Última hora de los mercados</h1>
<p>Las noticias de bolsa, macro y geopolítica que de verdad mueven el mercado, filtradas por importancia y contadas en un párrafo.</p></header>
<div class="cta" style="margin-top:20px"><div><h3>Llegan antes al canal</h3><p>Cada alerta sale primero en Telegram, en cuanto pasa.</p></div>{tg_btn(src='list')}</div>
<section class="card live"><h2><span class="dot"></span>Lo último</h2>
{feed_items(news[:S['latest_on_list']], 'Las alertas aparecen aquí en cuanto salen en el canal de Telegram.')}</section>
{ad('adsense_slot_list')}
{'<section style="margin-top:28px"><h2 style="font-size:18px">Por días</h2><ul class="days">' + day_links + '</ul></section>' if day_links else ''}
</div>"""
    page("noticias/", "Última hora de bolsa y mercados",
         "Alertas de bolsa, macroeconomía, bancos centrales y geopolítica en cuanto pasan, contadas en 30 segundos.",
         body, nav="noticias")

    for d, items in days.items():
        b = brief_days.get(d)
        extra = (f'<a class="card term-card" style="margin-bottom:20px" href="{u(b["path"])}"><span class="chip">Brief del día</span>'
                 f'<h3 style="margin-top:12px">{esc(b["headline_txt"])}</h3><p>El resumen completo de la mañana en 8 claves.</p></a>') if b else ""
        title = f"Noticias de bolsa del {d.day} de {MESES[d.month - 1]} de {d.year}"
        body = f"""<div class="wrap narrow">
<header class="page-head"><h1>{esc(title)}</h1><p>Todo lo que publicamos el {fecha_larga(d)}.</p></header>
{extra}<section class="card">{feed_items(sorted(items, key=lambda n: n['ts']), '')}</section>
{cta_box()}
</div>"""
        page(f"noticias/{d:%Y/%m/%d}/", title, f"Las noticias de mercados del {fecha_larga(d)}: " +
             cut("; ".join(n["clean_title"] for n in items[:3]), 120), body, nav="noticias")

    for cat, label in CATS.items():
        items = [n for n in news if n["category"] == cat]
        if not items:
            continue
        body = f"""<div class="wrap narrow">
<header class="page-head"><span class="chip">{esc(label)}</span><h1 style="margin-top:12px">Noticias de {esc(label.lower())}</h1><p>{esc(CAT_INTRO[cat])}</p></header>
<section class="card">{feed_items(items[:100], '')}</section>
{cta_box()}
</div>"""
        page(f"categoria/{slugify(cat)}/", f"Noticias de {label.lower()}", CAT_INTRO[cat], body, nav="noticias")


def short_label(label):
    """'EN CONTEXTO 2/4 · Inteligencia artificial' -> 'Inteligencia artificial', 'LA HISTORIA DEL DÍA · ...' -> 'La historia'."""
    parts = [x.strip() for x in (label or "").split("·")]
    first = parts[0].upper()
    if first.startswith("EN CONTEXTO") and len(parts) > 1:
        return cut(parts[1], 26)
    fixed = {"LA HISTORIA DEL DÍA": "La historia del día", "EL CONCEPTO DEL DÍA": "El concepto",
             "CÓMO TE AFECTA": "Cómo te afecta", "DATO CURIOSO": "Dato curioso"}
    return fixed.get(first, sentence_case(parts[0]) if parts[0] else "")


def build_briefs(briefs, terms):
    term_by_brief = {t["brief"]["date"]: t for t in terms}
    for i, b in enumerate(briefs):
        d = b["d"]
        secs, jump = [], []
        img_first = None
        for k, p in enumerate(b["posts"]):
            sid = f"s{p['n']}"
            label = p["label"] or ("La historia del día" if k == 0 else "")
            head = sentence_case(p["headline"], p["body"]) if p["headline"] else label
            fig = ""
            if p["png"] and os.path.exists(p["png"]):
                webp, jpg = image_variants(p["png"], f"brief/{b['date']}/post_{p['n']}")
                img_first = img_first or jpg
                lazy = "" if k == 0 else ' loading="lazy" decoding="async"'
                fig = (f'<figure><img src="{u(webp)}" width="1080" height="1350" alt="{esc(label + ". " + head)}"{lazy}></figure>')
            term = term_by_brief.get(b["date"])
            more = (f'<p><a class="more" href="{u(term["path"])}">Ver «{esc(term["bare"])}» en el glosario →</a></p>'
                    if term and term["post"] is p else "")
            secs.append(f'<section class="sec" id="{sid}">'
                        f'{"<span class=chip>" + esc(label.capitalize() if label.isupper() else label) + "</span>" if label else ""}'
                        f'<h2>{esc(head)}</h2>{fig}<div class="body">{paragraphs(p["body"])}</div>{more}</section>')
            if k == 1:
                secs.append(cta_box("¿Te ha servido? Cada mañana en tu móvil",
                                    "El brief sale a primera hora en el canal de Telegram, con las tarjetas listas para guardar."))
            if k in (1, 4):
                secs.append(ad())
            jump.append(f'<li><a href="#{sid}">{esc(short_label(label) or cut(head, 24))}</a></li>')
        newer = briefs[i - 1] if i > 0 else None
        older = briefs[i + 1] if i + 1 < len(briefs) else None
        pager = '<nav class="pager">' + (
            f'<a class="btn btn-ghost" href="{u(older["path"])}">← {fecha_corta(older["d"])}</a>' if older else "<span></span>") + (
            f'<a class="btn btn-ghost" href="{u(newer["path"])}">{fecha_corta(newer["d"])} →</a>' if newer else "<span></span>") + "</nav>"
        items = [("Inicio", ""), ("Brief diario", "brief/"), (fecha_corta(d), b["path"])]
        thermo = f'<span class="thermo">🌡 Termómetro · {esc(b["thermo"])}</span>' if b["thermo"] else ""
        body = f"""<div class="wrap narrow">
{crumbs_html(items)}
<article>
<span class="chip">Brief diario · {fecha_corta(d)}</span>
<h1>{esc(b['title'])}</h1>
<div class="meta"><span>Las 8 claves para empezar el día</span>{thermo}</div>
<ul class="jump">{''.join(jump)}</ul>
{''.join(secs)}
</article>
{cta_box()}
{pager}
</div>"""
        ld = [{"@context": "https://schema.org", "@type": "NewsArticle", "headline": cut(b["title"], 110),
               "description": b["desc"], "datePublished": f"{b['date']}T08:00:00+02:00", "inLanguage": "es-ES",
               "mainEntityOfPage": absu(b["path"]), "image": [absu(img_first)] if img_first else [absu("assets/og.png")],
               "author": org(), "publisher": org()}, crumbs_ld(items)]
        page(b["path"], b["title"], b["desc"], body, nav="brief", image=img_first, og_type="article", jsonld=ld)

    rows = "".join(f'<li><a href="{u(b["path"])}"><span>{fecha_larga(b["d"]).capitalize()}</span>'
                   f'<span class="c">{esc(b["headline_txt"])}</span></a></li>' for b in briefs)
    body = f"""<div class="wrap narrow">
<header class="page-head"><h1>Brief diario de mercados</h1>
<p>Cada mañana, la historia del día, un concepto explicado en 30 segundos, el contexto que importa y los números que hay que vigilar.</p></header>
<section class="card">{'<ul class="feed brief-list">' + rows.replace('<a ', '<a style="grid-template-columns:1fr" ') + '</ul>' if rows else '<p class="empty">El primer brief aparecerá aquí mañana a primera hora.</p>'}</section>
{cta_box('Recíbelo cada mañana', 'El brief sale a primera hora en el canal de Telegram, gratis.')}
</div>"""
    page("brief/", "Brief diario de bolsa y mercados",
         "El resumen de mercados de cada mañana en 8 claves: la historia del día, un concepto, el contexto y los niveles a vigilar.",
         body, nav="brief")


def build_glossary(terms):
    for t in terms:
        p, b = t["post"], t["brief"]
        items = [("Inicio", ""), ("Glosario", "glosario/"), (t["bare"], t["path"])]
        fig = ""
        if p["png"] and os.path.exists(p["png"]):
            webp, jpg = image_variants(p["png"], f"brief/{b['date']}/post_{p['n']}")
            fig = f'<figure style="margin:0 0 22px"><img src="{u(webp)}" width="1080" height="1350" alt="{esc(t["name"])} explicado" style="border-radius:22px;max-width:420px;width:100%"></figure>'
        else:
            jpg = None
        title = f"Qué es {t['name'][:1].lower() + t['name'][1:]}"
        others = [o for o in terms if o is not t][:8]
        other_html = "".join(f'<li><a href="{u(o["path"])}">{esc(o["bare"])}</a></li>' for o in others)
        body = f"""<div class="wrap narrow">
{crumbs_html(items)}
<article>
<span class="chip">Glosario · explicado en 30 segundos</span>
<h1>{esc(title)}</h1>
<div class="meta"><span>Publicado en el brief del {fecha_larga(b['d'])}</span></div>
{fig}
<div class="body lead">{paragraphs(p['body'])}</div>
<p><a class="more" href="{u(b['path'])}">Lee el brief completo de ese día →</a></p>
</article>
{cta_box('Un concepto nuevo cada mañana', 'Aprende a leer el mercado en 30 segundos al día, gratis en Telegram.')}
{ad()}
{'<section style="margin-top:20px"><h2 style="font-size:18px">Más conceptos</h2><ul class="terms">' + other_html + '</ul></section>' if other_html else ''}
</div>"""
        ld = [{"@context": "https://schema.org", "@type": "Article", "headline": title, "description": t["desc"],
               "datePublished": f"{b['date']}T08:00:00+02:00", "inLanguage": "es-ES", "mainEntityOfPage": absu(t["path"]),
               "image": [absu(jpg)] if jpg else [absu("assets/og.png")], "author": org(), "publisher": org(),
               "about": {"@type": "DefinedTerm", "name": t["bare"], "description": t["desc"]}}, crumbs_ld(items)]
        page(t["path"], f"{title}: explicado fácil", t["desc"], body, nav="glosario", image=jpg, og_type="article", jsonld=ld)

    lis = "".join(f'<li><a href="{u(t["path"])}">{esc(t["bare"])}<span>{esc(cut(t["desc"], 70))}</span></a></li>' for t in terms)
    body = f"""<div class="wrap">
<header class="page-head"><h1>Glosario de inversión</h1>
<p>Los conceptos que salen en las noticias de bolsa, explicados con ejemplos de la vida real. Uno nuevo cada mañana.</p></header>
<div style="margin-top:22px">{'<ul class="terms">' + lis + '</ul>' if lis else '<p class="empty">El primer concepto aparecerá con el próximo brief.</p>'}</div>
<div class="narrow" style="margin:0 auto">{cta_box('Un concepto nuevo cada mañana', 'Aprende a leer el mercado en 30 segundos al día, gratis en Telegram.')}</div>
</div>"""
    page("glosario/", "Glosario de inversión y bolsa explicado fácil",
         "Qué es un bono basura, una OPA, el PER o el VIX: conceptos de inversión explicados en 30 segundos con ejemplos reales.",
         body, nav="glosario")


def build_home(news, briefs, terms):
    b = briefs[0] if briefs else None
    if b:
        img = ""
        p0 = b["posts"][0]
        if p0["png"] and os.path.exists(p0["png"]):
            webp, _ = image_variants(p0["png"], f"brief/{b['date']}/post_{p0['n']}")
            img = f'<img src="{u(webp)}" width="1080" height="1350" alt="">'
        toc = "".join(f'<li><b>{k + 1}</b>{esc(sentence_case(p["headline"], p["body"]) if p["headline"] else p["label"])}</li>'
                      for k, p in enumerate(b["posts"][:8]))
        brief_html = (f'<section class="card"><h2><span class="dot"></span>Brief · {fecha_larga(b["d"])}</h2>'
                      f'<a class="brief-card" href="{u(b["path"])}">{img}<div><h3>{esc(b["headline_txt"])}</h3>'
                      f'<ul class="toc">{toc}</ul></div></a><a class="more" href="{u(b["path"])}">Leer el brief completo →</a></section>')
    else:
        brief_html = ('<section class="card"><h2><span class="dot"></span>Brief diario</h2>'
                      '<p class="empty">El resumen de la mañana aparecerá aquí con el próximo brief.</p></section>')
    t = terms[-1] if terms else None
    if terms:
        latest_term = max(terms, key=lambda x: x["brief"]["date"])
        term_html = (f'<section class="card"><h2><span class="dot"></span>Concepto del día</h2>'
                     f'<a class="term-card" href="{u(latest_term["path"])}"><h3>{esc(latest_term["bare"])}</h3>'
                     f'<p>{esc(latest_term["desc"])}</p></a><a class="more" href="{u("glosario/")}">Ver el glosario →</a></section>')
    else:
        term_html = ""
    body = f"""<div class="wrap">
<section class="hero">
<h1>Lo que mueve tu dinero, <em>en 30 segundos</em></h1>
<p>Alertas de bolsa, macro y geopolítica en cuanto pasan, y un brief cada mañana con lo que hay que saber. Filtrado por importancia, sin ruido.</p>
<div class="row">{tg_btn('Unirme gratis en Telegram', 'btn btn-lg', 'hero')}<a class="btn btn-ghost btn-lg" href="{u('noticias/')}">Ver la última hora</a></div>
</section>
<div class="grid">
<div>{brief_html}</div>
<div><section class="card live"><h2><span class="dot"></span>Última hora</h2>
{feed_items(news[:S['latest_on_home']], 'Las alertas aparecen aquí en cuanto salen en el canal de Telegram.')}
<a class="more" href="{u('noticias/')}">Toda la última hora →</a></section></div>
</div>
{ad('adsense_slot_list')}
<div class="grid">
<div>{term_html}</div>
<div>{cta_box('Las alertas, en tu móvil al momento', 'Únete a ' + S['telegram_handle'] + ' y recibe cada noticia importante en cuanto pasa.')}</div>
</div>
</div>"""
    ld = [{"@context": "https://schema.org", "@type": "WebSite", "name": S["name"], "url": absu(),
           "inLanguage": "es-ES", "publisher": org()}]
    page("", "AZ News · Noticias de bolsa, inversión y finanzas al momento",
         "Alertas de bolsa, macroeconomía y geopolítica en cuanto pasan y un brief de mercados cada mañana. Explicado fácil y gratis en Telegram.",
         body, jsonld=ld)


def build_legal():
    contacto = (f'escribiendo a <a href="mailto:{esc(S["email"])}">{esc(S["email"])}</a>' if S.get("email")
                else f'a través del canal de Telegram <a href="{esc(S["telegram_url"])}">{esc(S["telegram_handle"])}</a>')
    nif = f", con NIF {esc(S['nif'])}" if S.get("nif") else ""
    pages = {
        "sobre/": ("Quiénes somos", "Qué es AZ News, cómo seleccionamos las noticias y quién está detrás.", f"""
<p class="lead">{esc(S['name'])} cuenta lo que mueve los mercados de forma clara y rápida, para que cualquiera pueda entender qué pasa con su dinero sin leer diez periódicos.</p>
<h2>Cómo elegimos las noticias</h2>
<p>Revisamos de forma continua las principales fuentes de información financiera y puntuamos cada noticia según su alcance, lo inesperada que es, su magnitud y su impacto inmediato en el mercado. Solo publicamos las que superan un listón alto, por eso hay días tranquilos con pocas alertas.</p>
<h2>El brief diario</h2>
<p>Cada mañana preparamos un resumen en 8 claves con la historia del día, un concepto de inversión explicado con ejemplos, el contexto que conviene conocer y los niveles de mercado a vigilar.</p>
<h2>Quién está detrás</h2>
<p>{esc(S['name'])} es un proyecto de {esc(S['titular'])}. Puedes seguirnos y escribirnos {contacto}.</p>
<h2>Aviso importante</h2>
<p>Todo el contenido es informativo y divulgativo. No es asesoramiento financiero ni una recomendación personalizada de inversión.</p>"""),
        "aviso-legal/": ("Aviso legal", "Información legal del sitio web AZ News.", f"""
<p>En cumplimiento de la Ley 34/2002, de servicios de la sociedad de la información y de comercio electrónico (LSSI-CE), se informa de que este sitio web es titularidad de {esc(S['titular'])}{nif}. Contacto {contacto}.</p>
<h2>Objeto</h2>
<p>Este sitio ofrece noticias y contenidos divulgativos sobre economía, bolsa y finanzas. La información se publica con fines exclusivamente informativos.</p>
<h2>Sin asesoramiento financiero</h2>
<p>Nada de lo publicado constituye asesoramiento en materia de inversión, recomendación personalizada ni oferta o solicitud de compra o venta de instrumentos financieros. Las decisiones de inversión son responsabilidad de quien las toma. Las rentabilidades pasadas no garantizan rentabilidades futuras.</p>
<h2>Exactitud de la información</h2>
<p>Ponemos el máximo cuidado en contrastar los datos, pero pueden contener errores o quedar desactualizados. No nos hacemos responsables de las decisiones tomadas a partir de ellos.</p>
<h2>Propiedad intelectual</h2>
<p>Los textos y las tarjetas gráficas de {esc(S['name'])} son de elaboración propia. Las marcas y nombres de empresas citados pertenecen a sus titulares.</p>
<h2>Enlaces</h2>
<p>Este sitio puede enlazar a páginas de terceros, sobre cuyos contenidos no tenemos control.</p>"""),
        "privacidad/": ("Privacidad y cookies", "Cómo trata AZ News tus datos y qué cookies se usan.", f"""
<p>Responsable del tratamiento {esc(S['titular'])}{nif}. Contacto {contacto}.</p>
<h2>Qué datos tratamos</h2>
<p>No pedimos registro ni recogemos formularios. El alojamiento (GitHub Pages) registra datos técnicos de las visitas, como la dirección IP, por motivos de seguridad.</p>
<h2>Cookies y publicidad</h2>
<p>Este sitio puede mostrar anuncios de Google AdSense. Google y sus socios usan cookies para mostrar anuncios y medir su rendimiento, y solo las usan para anuncios personalizados si das tu consentimiento en el aviso de cookies. Puedes cambiar tu elección en cualquier momento desde ese aviso o en la <a href="https://adssettings.google.com" rel="noopener" target="_blank">configuración de anuncios de Google</a>. Más información en <a href="https://policies.google.com/technologies/partner-sites" rel="noopener" target="_blank">cómo usa Google los datos de los sitios asociados</a>.</p>
<p>Las tipografías se cargan desde Google Fonts.</p>
<h2>Telegram</h2>
<p>Al unirte al canal de Telegram, el tratamiento de tus datos se rige por la política de privacidad de Telegram.</p>
<h2>Tus derechos</h2>
<p>Puedes ejercer tus derechos de acceso, rectificación, supresión, oposición, limitación y portabilidad {contacto}, y reclamar ante la Agencia Española de Protección de Datos (aepd.es).</p>"""),
    }
    for path, (title, desc, html_body) in pages.items():
        body = f'<div class="wrap narrow"><header class="page-head"><h1>{esc(title)}</h1></header><div class="legal body">{html_body}</div></div>'
        page(path, title, desc, body)
    body = (f'<div class="wrap narrow"><header class="page-head"><h1>Esta página no existe</h1>'
            f'<p>Puede que la noticia se haya movido. Vuelve a la <a href="{u()}">portada</a> o mira la <a href="{u("noticias/")}">última hora</a>.</p></header></div>')
    page("404.html", "Página no encontrada", "Esta página no existe.", body, noindex=True)


def build_feeds(news, briefs, terms):
    urls = [("", None), ("noticias/", None), ("brief/", None), ("glosario/", None), ("sobre/", None)]
    urls += [(n["path"], n["dt"].date().isoformat()) for n in news]
    urls += [(b["path"], b["date"]) for b in briefs]
    urls += [(t["path"], t["brief"]["date"]) for t in terms]
    days = sorted({n["dt"].date() for n in news}, reverse=True)
    urls += [(f"noticias/{d:%Y/%m/%d}/", d.isoformat()) for d in days]
    urls += [(f"categoria/{slugify(c)}/", None) for c in CATS if any(n["category"] == c for n in news)]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, lastmod in urls:
        sm.append(f"<url><loc>{esc(absu(path))}</loc>" + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "") + "</url>")
    sm.append("</urlset>")
    write("sitemap.xml", "\n".join(sm) + "\n")

    items = [(n["dt"], n["clean_title"], n["path"], n["desc"]) for n in news[:40]]
    items += [(dt.datetime.combine(b["d"], dt.time(8), TZ), b["title"], b["path"], b["desc"]) for b in briefs[:10]]
    items.sort(key=lambda x: x[0], reverse=True)
    rss = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0"><channel>',
           f"<title>{esc(S['name'])}</title><link>{absu()}</link><description>{esc(S['tagline'])}</description><language>es-es</language>"]
    for d, title, path, desc in items[:50]:
        rss.append(f"<item><title>{esc(title)}</title><link>{absu(path)}</link><guid>{absu(path)}</guid>"
                   f"<pubDate>{d.strftime('%a, %d %b %Y %H:%M:%S %z')}</pubDate><description>{esc(desc)}</description></item>")
    rss.append("</channel></rss>")
    write("feed.xml", "\n".join(rss) + "\n")
    write("robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {absu('sitemap.xml')}\n")
    if S.get("adsense_client"):
        pub = S["adsense_client"].replace("ca-", "")
        write("ads.txt", f"google.com, {pub}, DIRECT, f08c47fec0942fa0\n")
    write(".nojekyll", "")
    if S.get("custom_domain"):
        write("CNAME", S["custom_domain"].strip("/") + "\n")


def prune():
    """Borra de la salida lo que ya no genera ninguna fuente."""
    for dirpath, _, files in os.walk(OUT, topdown=False):
        for f in files:
            p = os.path.normpath(os.path.join(dirpath, f))
            if p not in written:
                os.remove(p)
        if dirpath != OUT and not os.listdir(dirpath):
            os.rmdir(dirpath)


def main():
    os.makedirs(OUT, exist_ok=True)
    news = load_news()
    briefs = load_briefs()
    terms = glossary_from(briefs)
    build_assets()
    build_home(news, briefs, terms)
    build_news_pages(news)
    build_news_lists(news, briefs)
    build_briefs(briefs, terms)
    build_glossary(terms)
    build_legal()
    build_feeds(news, briefs, terms)
    prune()
    print(f"web: {len(news)} noticias, {len(briefs)} briefs, {len(terms)} conceptos, {len(written)} ficheros")


if __name__ == "__main__":
    main()
