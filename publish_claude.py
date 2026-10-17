#!/usr/bin/env python3
"""Publica en el canal el brief que deja Claude en claude_brief/AAAA-MM-DD/.

La tarea programada de Claude escribe en esa carpeta
  posts.json  [{"n": 1, "caption": "<b>...</b> pie en HTML de Telegram", "image": "post_1.png"}, ...]
  post_1.png ... post_8.png
y hace push. Este script (lanzado por el push) los publica en orden y deja la marca
published.txt para no repetir. MODE=privado lo manda al chat privado con el bot.
"""
import datetime as dt
import json
import os
import sys
import time

import bot
import brief

BASE = os.path.dirname(os.path.abspath(__file__))
MODE = os.environ.get("MODE", "normal").strip().lower() or "normal"


def main():
    if not bot.TG_TOKEN:
        sys.exit("Falta el secreto TELEGRAM_BOT_TOKEN")
    day = os.environ.get("BRIEF_DATE") or dt.datetime.now(bot.TZ_ES).strftime("%Y-%m-%d")
    folder = os.path.join(BASE, "claude_brief", day)
    manifest = os.path.join(folder, "posts.json")
    if not os.path.exists(manifest):
        print(f"No hay brief de Claude para {day}")
        return
    if MODE == "normal" and os.path.exists(os.path.join(folder, "published.txt")):
        print(f"El brief de Claude del {day} ya está publicado")
        return
    if MODE == "privado":
        chat = brief.private_chat()
        if not chat:
            sys.exit("Nadie ha escrito al bot por privado")
        bot.TG_CHAT = chat

    with open(manifest, encoding="utf-8") as fh:
        posts = sorted(json.load(fh), key=lambda p: int(p.get("n", 99)))
    if len(posts) != 8:
        sys.exit(f"El brief de Claude tiene {len(posts)} posts, se esperaban 8")
    for p in posts:
        png = os.path.join(folder, p["image"])
        if not os.path.exists(png):
            sys.exit(f"Falta la imagen {p['image']}")

    for p in posts:
        cap = brief.sanitize(p["caption"])
        if not brief.publish(cap, os.path.join(folder, p["image"])):
            sys.exit(f"Se ha parado en el post {p['n']}, los anteriores sí se publicaron")
        print(f"  post {p['n']} publicado")
        time.sleep(4)

    if MODE == "normal":
        with open(os.path.join(folder, "published.txt"), "w") as fh:
            fh.write(dt.datetime.now(bot.TZ_ES).isoformat())
        # el brief de Gemini de respaldo ya no hace falta hoy
        st = brief.load_state()
        st["briefs"].append({"date": day, "historia": "brief de Claude", "fuente": "claude"})
        brief.save_state(st)
    print("Brief de Claude publicado, 8 de 8")


if __name__ == "__main__":
    main()
