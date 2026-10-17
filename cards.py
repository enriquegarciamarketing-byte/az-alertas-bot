"""Tarjetas AZ News para el brief diario (1080x1350, PIL + Barlow).

Estructura fija: chip de sector arriba a la izquierda y monograma AZ a la derecha,
titular de 2-3 líneas con la parte final en color de acento, subtítulo, cifra
protagonista en el centro, bloque de dos cifras abajo y pie con AZ NEWS y la fecha.
Todo texto se auto-reduce y se reparte en líneas hasta caber en su caja, y al final
se valida con textbbox que nada se sale del lienzo ni se pisa.
"""
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1350
M = 76                      # margen lateral
CW = W - 2 * M              # ancho útil
FONT_DIR = os.environ.get("AZ_FONTS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"))
FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

ACCENTS = {
    "tipos": (255, 86, 42), "macro": (255, 86, 42), "concepto": (78, 168, 255),
    "energia": (255, 176, 32), "bolsa": (107, 227, 138), "tecnologia": (181, 123, 255),
    "consumo": (255, 62, 108), "empresas": (255, 62, 108), "geopolitica": (255, 120, 60),
    "curioso": (255, 213, 74), "cripto": (255, 176, 32),
}
WHITE = (245, 246, 250)
SOFT = (196, 201, 218)
MUTED = (132, 139, 162)
AZ_RED = (155, 28, 28)


def font(name, size):
    for path in (os.path.join(FONT_DIR, name), FALLBACK):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def text_w(draw, txt, f):
    b = draw.textbbox((0, 0), txt, font=f)
    return b[2] - b[0]


def wrap(draw, txt, f, width):
    words, lines, cur = (txt or "").split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if text_w(draw, test, f) <= width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit(draw, txt, name, sizes, width, max_lines):
    """Mayor tamaño de la lista con el que el texto cabe en width y max_lines (sin cortar palabras)."""
    for s in sizes:
        f = font(name, s)
        lines = wrap(draw, txt, f, width)
        if len(lines) <= max_lines and all(text_w(draw, l, f) <= width for l in lines):
            return f, lines
    f = font(name, sizes[-1])
    return f, wrap(draw, txt, f, width)


def background(accent):
    img = Image.new("RGB", (W, H))
    top, bot = (9, 12, 28), (28, 11, 18)
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        c = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        for x in range(W):
            px[x, y] = c
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((W - 520, H - 620, W + 260, H + 160), fill=accent + (120,))
    g.ellipse((-300, -300, 420, 380), fill=(60, 110, 255, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(145))
    img = Image.alpha_composite(img.convert("RGBA"), glow)
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    for x in range(0, W, 60):
        gd.line((x, 0, x, H), fill=(255, 255, 255, 9))
    for y in range(0, H, 60):
        gd.line((0, y, W, y), fill=(255, 255, 255, 9))
    return Image.alpha_composite(img, grid)


def render(card, date_label, out_path):
    accent = ACCENTS.get((card.get("acento") or "").lower(), ACCENTS["tipos"])
    img = background(accent)
    d = ImageDraw.Draw(img)
    boxes = []

    def put(xy, txt, f, fill, anchor="la"):
        d.text(xy, txt, font=f, fill=fill, anchor=anchor)
        boxes.append(d.textbbox(xy, txt, font=f, anchor=anchor))

    # chip de sector
    chip = (card.get("chip") or "AZ NEWS").upper()[:28]
    fchip = font("Barlow-SemiBold.ttf", 28)
    cw = text_w(d, chip, fchip) + 44
    d.rounded_rectangle((M, 78, M + cw, 132), radius=27, fill=(20, 24, 40), outline=accent, width=3)
    put((M + 22, 105), chip, fchip, accent, "lm")
    # logo AZ (isotipo blanco); si no está el fichero, monograma de reserva
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "az_logo.png")
    if os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGBA")
        lh_ = 66
        logo = logo.resize((int(logo.width * lh_ / logo.height), lh_), Image.LANCZOS)
        img.alpha_composite(logo, (W - M - logo.width, 105 - lh_ // 2))
        boxes.append((W - M - logo.width, 105 - lh_ // 2, W - M, 105 + lh_ // 2))
        d = ImageDraw.Draw(img)
    else:
        d.ellipse((W - M - 58, 76, W - M, 134), fill=AZ_RED)
        put((W - M - 29, 105), "AZ", font("Barlow-Black.ttf", 26), WHITE, "mm")

    # titular, parte blanca y parte en acento, hasta 3 líneas entre las dos
    t_main, t_acc = (card.get("titulo") or "").strip(), (card.get("titulo_acento") or "").strip()
    y = 186
    for s in (124, 116, 108, 100, 92, 84, 76, 70, 64):
        f = font("BarlowCondensed-Bold.ttf", s)
        l1 = wrap(d, t_main.upper(), f, CW) if t_main else []
        l2 = wrap(d, t_acc.upper(), f, CW) if t_acc else []
        if len(l1) + len(l2) <= 3:
            break
    lh = int(f.size * 1.02)
    for line in l1:
        put((M, y), line, f, WHITE)
        y += lh
    for line in l2:
        put((M, y), line, f, accent)
        y += lh
    y += 22
    d.rectangle((M, y, M + 110, y + 6), fill=accent)
    y += 6 + 40

    # subtítulo
    fsub, lsub = fit(d, card.get("subtitulo") or "", "Barlow-Medium.ttf", [40, 37, 34, 31], CW, 3)
    for line in lsub:
        put((M, y), line, fsub, SOFT)
        y += int(fsub.size * 1.28)
    sub_bottom = y

    # bloque de cifras anclado abajo
    stats_top = 1010
    d.line((M, stats_top, W - M, stats_top), fill=(58, 64, 90), width=2)
    s1, s1l = (card.get("stat1") or "").strip(), (card.get("stat1_label") or "").strip()
    s2, s2l = (card.get("stat2") or "").strip(), (card.get("stat2_label") or "").strip()
    if s1:
        f1, _ = fit(d, s1, "Barlow-Black.ttf", [96, 88, 80, 72, 64, 56], int(CW * 0.52), 1)
        put((M, stats_top + 30), s1, f1, WHITE)
        fl, ll = fit(d, s1l, "Barlow-Medium.ttf", [30, 27, 24, 22], int(CW * 0.50), 2)
        yy = stats_top + 30 + f1.size + 14
        for line in ll:
            put((M, yy), line, fl, MUTED)
            yy += int(fl.size * 1.2)
    if s2:
        f2, _ = fit(d, s2, "Barlow-Bold.ttf", [64, 58, 52, 46, 40], int(CW * 0.40), 1)
        put((W - M, stats_top + 42), s2, f2, accent, "ra")
        fl, ll = fit(d, s2l, "Barlow-Medium.ttf", [28, 25, 23, 21], int(CW * 0.40), 2)
        yy = stats_top + 42 + f2.size + 14
        for line in ll:
            put((W - M, yy), line, fl, MUTED, "ra")
            yy += int(fl.size * 1.2)

    # cifra protagonista centrada entre subtítulo y bloque de cifras
    hero, hero_l = (card.get("hero") or "").strip(), (card.get("hero_label") or "").strip()
    area_top, area_bot = sub_bottom + 40, stats_top - 40
    rows = [r for r in (card.get("filas") or []) if isinstance(r, dict) and r.get("valor")][:4]
    if rows and area_bot - area_top > 120:
        avail = area_bot - area_top
        gap = 22
        rh = min(150, (avail - gap * (len(rows) - 1)) // len(rows))
        block = rh * len(rows) + gap * (len(rows) - 1)
        ry = area_top + (avail - block) // 2
        for r in rows:
            d.rounded_rectangle((M, ry, W - M, ry + rh), radius=22, fill=(20, 24, 40), outline=(58, 64, 90), width=2)
            flab, _ = fit(d, str(r.get("label", "")), "Barlow-SemiBold.ttf", [36, 32, 28, 24], int(CW * 0.48), 1)
            fval, _ = fit(d, str(r["valor"]), "Barlow-Black.ttf", [int(rh * 0.5), int(rh * 0.44), int(rh * 0.38)], int(CW * 0.44), 1)
            chg = str(r.get("cambio") or "")
            up = chg.strip().startswith("+")
            down = chg.strip().startswith(("-", "−"))
            ccol = (107, 227, 138) if up else (255, 86, 86) if down else MUTED
            if chg:
                put((M + 32, ry + rh // 2 - 4), str(r.get("label", "")), flab, SOFT, "ls")
                put((M + 32, ry + rh // 2 + 8), chg, font("Barlow-Bold.ttf", max(22, int(flab.size * 0.85))), ccol, "lt")
            else:
                put((M + 32, ry + rh // 2), str(r.get("label", "")), flab, SOFT, "lm")
            put((W - M - 32, ry + rh // 2), str(r["valor"]), fval, WHITE, "rm")
            ry += rh + gap
    elif hero and area_bot - area_top > 120:
        avail = area_bot - area_top
        fl, ll = fit(d, hero_l, "Barlow-Medium.ttf", [36, 33, 30, 27], CW, 2)
        lab_h = int(fl.size * 1.25) * len(ll) if hero_l else 0
        sizes = [s for s in (240, 220, 200, 180, 160, 140, 120, 100, 84) if s * 1.05 + lab_h + 24 <= avail] or [84]
        fh, _ = fit(d, hero, "Barlow-Black.ttf", sizes, CW, 1)
        block = int(fh.size * 1.05) + (24 + lab_h if hero_l else 0)
        hy = area_top + (avail - block) // 2
        # halo detrás de la cifra
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo)
        hb = d.textbbox((W // 2, hy), hero, font=fh, anchor="ma")
        hd.rounded_rectangle((hb[0] - 30, hb[1] - 10, hb[2] + 30, hb[3] + 10), radius=40, fill=accent + (70,))
        halo = halo.filter(ImageFilter.GaussianBlur(50))
        img.alpha_composite(halo)
        d = ImageDraw.Draw(img)
        put((W // 2, hy), hero, fh, accent, "ma")
        ly = hy + int(fh.size * 1.05) + 24
        for line in ll:
            put((W // 2, ly), line, fl, SOFT, "ma")
            ly += int(fl.size * 1.25)

    # pie
    d.line((M, 1250, W - M, 1250), fill=(58, 64, 90), width=2)
    put((M, 1280), "AZ NEWS", font("Barlow-Black.ttf", 32), WHITE)
    put((W - M, 1284), date_label, font("Barlow-Medium.ttf", 28), MUTED, "ra")

    # validación, nada fuera del lienzo y nada pisado
    for b in boxes:
        assert b[0] >= 0 and b[1] >= 0 and b[2] <= W and b[3] <= H, f"fuera del lienzo {b}"
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            ov_x = min(a[2], b[2]) - max(a[0], b[0])
            ov_y = min(a[3], b[3]) - max(a[1], b[1])
            assert not (ov_x > 2 and ov_y > 2), f"textos solapados {a} {b}"
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path
