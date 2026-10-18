"""Guarda lo que el bot publica en Telegram para que la web lo convierta en páginas.

- Noticias flash  -> web/data/noticias.json  (una entrada por noticia publicada en el canal)
- Brief de Gemini -> web/gemini_brief/AAAA-MM-DD/posts.json + post_N.png (mismo formato que claude_brief)

Nunca rompe la publicación en Telegram: si algo falla aquí, solo se avisa en el log.
"""
import datetime as dt
import hashlib
import json
import os
import shutil

WEB = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(WEB, "data")
NEWS = os.path.join(DATA, "noticias.json")
GEMINI_BRIEF = os.path.join(WEB, "gemini_brief")
MAX_NEWS = 3000


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def add_flash(text, category, ts, tz):
    """text es el post tal cual salió en Telegram (titular **en negrita** + párrafo)."""
    try:
        lines = [l for l in (text or "").strip().split("\n")]
        title = lines[0].replace("**", "").strip() if lines else ""
        body = "\n".join(lines[1:]).strip()
        if not title or not body:
            return
        news = _load(NEWS, [])
        nid = hashlib.sha1(f"{int(ts)}|{title}".encode()).hexdigest()[:10]
        if any(n.get("id") == nid for n in news):
            return
        news.append({
            "id": nid,
            "ts": int(ts),
            "date": dt.datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d"),
            "category": category or "mercados",
            "title": title,
            "body": body,
        })
        _save(NEWS, news[-MAX_NEWS:])
    except Exception as exc:  # la web nunca puede tumbar el bot
        print(f"  web: no se pudo guardar la noticia ({exc})")


def save_gemini_brief(day, ready):
    """ready es la lista [(caption_html, png_path)] que se acaba de publicar."""
    try:
        folder = os.path.join(GEMINI_BRIEF, day)
        os.makedirs(folder, exist_ok=True)
        posts = []
        for i, (cap, png) in enumerate(ready, start=1):
            name = f"post_{i}.png"
            shutil.copyfile(png, os.path.join(folder, name))
            posts.append({"n": i, "caption": cap, "image": name})
        _save(os.path.join(folder, "posts.json"), posts)
    except Exception as exc:
        print(f"  web: no se pudo guardar el brief ({exc})")
