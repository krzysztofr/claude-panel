# -*- coding: utf-8 -*-
# /// script
# dependencies = ["pillow", "pyserial"]
# ///
"""
Renderer panelu Claude pod Turing Smart Screen 3.5" (320x480, pion).

Rysowanie PRZYROSTOWE: pelna klatka (300 KB, ~3 s kabla) idzie tylko na
start i po reconnect. Potem wysylane sa wylacznie zmienione prostokaty,
wiec panel reaguje w ulamku sekundy, a kabel jest niemal wolny.

W naglowku zamiast napisu CLAUDE mieszka pikselowy stworek:
  - spi (z "Z"), gdy tokeny nie leca i nic nie pracuje
  - chodzi/biega w tempie zuzycia tokenow
  - poci sie, gdy najgorszy limit >= 85%
  - staje i miga niebieskim dymkiem "!", gdy sesja czeka na Twoja decyzje

  python render.py                 -> jedna klatka do preview.png
  python render.py --loop          -> odswieza preview.png
  python render.py --serial AUTO   -> rysuje na ekranie (auto-wykrycie portu)
"""
import argparse
import json
import os
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone

# ---------------------------------------------------------------- jezyk
LANGS = {
    "pl": {
        "limit5h": "Limit 5h", "weekly": "Tydzien", "sessions": "SESJE",
        "wait1": "CLAUDE CZEKA NA CIEBIE", "waitN": "%d SESJE CZEKAJA",
        "none": "nic nie pracuje", "more": "+%d dalszych", "na": "brak",
        "reset_now": "reset lada moment", "reset_dh": "reset za %dd %dh",
        "reset_hm": "reset za %dh %02dm", "reset_m": "reset za %d min",
    },
    "en": {
        "limit5h": "5h limit", "weekly": "Weekly", "sessions": "SESSIONS",
        "wait1": "CLAUDE IS WAITING FOR YOU", "waitN": "%d SESSIONS WAITING",
        "none": "nothing running", "more": "+%d more", "na": "n/a",
        "reset_now": "resets any moment", "reset_dh": "resets in %dd %dh",
        "reset_hm": "resets in %dh %02dm", "reset_m": "resets in %d min",
    },
}
T = LANGS["pl"]   # ustawiane w main() z --lang / PANEL_LANG

from PIL import Image, ImageChops, ImageDraw, ImageFont

# 127.0.0.1, NIE "localhost": Windows dla localhost probuje najpierw IPv6
# ::1 (serwer slucha tylko IPv4) i kazde zadanie lapalo ~2 s kary, co
# rozciagalo tick petli z 0,15 s do 2,3 s - stworek pelzal 15x wolniej.
API = "http://127.0.0.1:4747/api/state"
W, H = 320, 480          # natywny pion

BG    = (7, 9, 13)
LINE  = (28, 37, 48)
DIM   = (165, 178, 195)  # kiedys ciemniejszy - na ekranie za slabo czytelny
TXT   = (223, 231, 240)
LABEL = (165, 178, 195)  # naglowki limitow
SUB   = (165, 178, 195)  # "reset za..."
OK    = (34, 211, 167)
WARN  = (245, 178, 61)
HOT   = (255, 77, 94)
ACC   = (122, 162, 255)
FABLE = (192, 132, 252)
TRACK = (24, 33, 48)
WAIT  = (56, 189, 248)   # blekit - kropka sesji pracujacej

# Pas naglowka nalezacy WYLACZNIE do stworka. Baza rysuje tu czyste tlo,
# diff go ignoruje - jedynym pisarzem jest animacja.
MASCOT_ZONE = (6, 2, 208, 40)

# Lagodne zatrzymanie: utworzenie tego pliku konczy petle MIEDZY zapisami.
# Twardy kill (Stop-Process) w polowie transmisji bitmapy desynchronizuje
# parser ekranu - kolejny proces wysyla komendy, a ekran zjada je jako
# brakujace piksele i pokazuje smieci.
STOP_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stop.flag")


# Segoe jest tylko na Windows - na macOS bierzemy odpowiednik z Arial
# (Supplemental sa w systemie od zawsze, bez instalowania czegokolwiek).
MAC_FONTS = {
    "segoeuib.ttf": "Arial Bold.ttf",
    "seguisb.ttf": "Arial Bold.ttf",
    "segoeui.ttf": "Arial.ttf",
}


def font(name, size):
    for p in ("C:/Windows/Fonts/" + name,
              "/System/Library/Fonts/Supplemental/" + MAC_FONTS.get(name, name)):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


f_brand = font("segoeuib.ttf", 23)
f_pct   = font("segoeuib.ttf", 36)
f_label = font("seguisb.ttf", 19)
f_mid   = font("seguisb.ttf", 17)
f_small = font("segoeui.ttf", 15)
f_tiny  = font("segoeui.ttf", 13)
f_sess  = font("seguisb.ttf", 17)   # nazwy sesji - najczesciej czytana rzecz
f_sesst = font("segoeui.ttf", 14)   # czas przy sesji
f_axis  = font("segoeui.ttf", 11)   # os godzinowa wykresu

# Biale logo (RGBA) w prawym rogu naglowka; brak pliku = po prostu brak logo.
try:
    LOGO = Image.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "logo.png")).convert("RGBA")
    LOGO.thumbnail((30, 30), Image.LANCZOS)
except OSError:
    LOGO = None


def fetch():
    with urllib.request.urlopen(API, timeout=3) as r:
        return json.load(r)


def color_for(p):
    return HOT if p >= 85 else WARN if p >= 60 else OK


def until(iso):
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    diff = (d - datetime.now(timezone.utc)).total_seconds()
    if diff <= 0:
        return T["reset_now"]
    h, m = int(diff // 3600), int(diff % 3600 // 60)
    if h >= 24:
        return T["reset_dh"] % (h // 24, h % 24)
    if h:
        return T["reset_hm"] % (h, m)
    return T["reset_m"] % m


def bar(d, x, y, w, h, pct, col):
    r = h // 2
    d.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=TRACK)
    fw = int(w * max(0, min(100, pct)) / 100)
    if fw >= h:
        d.rounded_rectangle([x, y, x + fw, y + h], radius=r, fill=col)
    elif fw > 0:
        d.ellipse([x, y, x + h, y + h], fill=col)


def block(d, y, h, label, lim, col_override=None):
    """Jeden limit: etykieta i reset po lewej, duzy procent po prawej,
    pasek u dolu."""
    if not lim or lim.get("percent") is None:
        d.text((14, y + 6), label, font=f_label, fill=DIM)
        d.text((W - 14, y + 6), T["na"], font=f_small, fill=DIM, anchor="ra")
        return
    pct = lim["percent"]
    col = col_override or color_for(pct)
    d.text((14, y + 2), label, font=f_label, fill=LABEL)
    d.text((W - 14, y - 4), "%d%%" % pct, font=f_pct, fill=col, anchor="ra")
    t = until(lim.get("resetsAt"))
    if t:
        d.text((14, y + 24), t, font=f_small, fill=SUB)
    bar(d, 14, y + h - 14, W - 28, 11, pct, col)


SESS_ROW_H = 27
CHART_BARS_H = 46
CHART_H = 8 + CHART_BARS_H + 16   # odstep + slupki + os godzinowa
DOT_BOX = 16          # kwadrat odswiezany przy miganiu

# Pozycje kropek z ostatniej klatki: [(x, y, kolor, czy_miga)].
LAST_DOTS = []


def dot_image(color):
    im = Image.new("RGB", (DOT_BOX, DOT_BOX), BG)
    if color:
        ImageDraw.Draw(im).ellipse([3, 3, DOT_BOX - 4, DOT_BOX - 4], fill=color)
    return im


def state_style(state):
    """Kolor kropki i czy ma migac. Czerwien jest zarezerwowana dla paska
    limitu krytycznego."""
    if state == "czeka":
        return WARN, True
    if state == "pracuje":
        return WAIT, True
    return OK, False


def chart(d, y, hourly):
    """Slupki tokenow out za ostatnie 24 h (ostatni = biezaca godzina,
    na zielono). Czerwona pionowa linia = polnoc. Os: co 4. godzina."""
    x0, x1 = 14, W - 14
    d.line([(x0, y), (x1, y)], fill=LINE, width=1)
    top = y + 8
    base = top + CHART_BARS_H
    if not hourly:
        d.text((x0, top + 4), T["na"], font=f_small, fill=DIM)
        return
    mx = max((h.get("out") or 0) for h in hourly) or 1
    bw = (x1 - x0) / len(hourly)
    last = len(hourly) - 1
    for i, h in enumerate(hourly):
        bx = x0 + i * bw
        dt = datetime.fromtimestamp(h["t"] / 1000)
        hour = dt.hour
        bh = round(CHART_BARS_H * (h.get("out") or 0) / mx)
        if bh:
            d.rectangle([bx + 1, base - bh, bx + bw - 2, base],
                        fill=OK if i == last else ACC)
        if hour == 0:
            d.line([(bx, top), (bx, base)], fill=HOT, width=1)
        # godziny pracy (9 i 17) tylko w dni robocze
        if hour in (9, 17) and dt.weekday() < 5:
            d.line([(bx, top), (bx, base)], fill=OK, width=1)
        if hour % 4 == 0:
            d.text((bx + bw / 2, base + 3), str(hour), font=f_axis,
                   fill=DIM, anchor="ma")


def draw(s, phase=True):
    """Rysuje pelna klatke. `phase` = aktualna faza migania kropek - dzieki
    temu baza jest zgodna z tym, co wisi na ekranie, i diff nie zapala
    kropek na sile w polowie mrugniecia."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    lim = s.get("limits") or {}
    # JEDNO zrodlo prawdy o "czeka": stany sesji. Wczesniej baner czytal
    # alerts, a stworek i kropki sessions - przy rozjezdzie ekran pokazywal
    # sprzecznosc (baner swieci, nic nie miga).
    waiting = [x for x in (s.get("sessions") or []) if x.get("state") == "czeka"]

    # Wiek danych limitowych - w naglowku obok zegara.
    age = lim.get("ageMs")
    if age is None:
        age_txt, age_col = "?", WARN
    else:
        sec = age / 1000
        age_txt = ("%ds" % sec if sec < 90 else
                   "%dmin" % (sec / 60) if sec < 5400 else "%.0fh" % (sec / 3600))
        # Gdy API odmawia, dopisujemy kod HTTP - sam kolor nie mowil DLACZEGO
        # dane sa stare (np. "8h 429" = blokada rate-limit po stronie API).
        err = lim.get("liveError")
        if err:
            age_txt += " " + (err.split()[1] if err.startswith("HTTP ") else "!")
        age_col = WARN if (err or sec > 5400) else DIM

    # ---- naglowek: strefa stworka + wiek danych + logo ----
    rx = W - 14
    if LOGO:
        img.paste(LOGO, (rx - LOGO.width, 6), LOGO)
        rx -= LOGO.width + 10
    d.text((rx, 14), age_txt, font=f_label, fill=age_col, anchor="ra")
    d.line([(14, 42), (W - 14, 42)], fill=LINE, width=1)

    y = 52

    # ---- alert: Claude czeka na odpowiedz ----
    if waiting:
        d.rounded_rectangle([10, y, W - 10, y + 38], radius=8, fill=(48, 34, 10), outline=WARN)
        msg = T["waitN"] % len(waiting) if len(waiting) > 1 else T["wait1"]
        d.text((W // 2, y + 19), msg, font=f_mid, fill=(253, 226, 176), anchor="mm")
        y += 46

    # ---- pasek krytyczny (najgorszy limit >=85%) ----
    sc = lim.get("scoped")
    sc_name = (sc or {}).get("name") or "model"
    worst = None
    for name, l in ((T["limit5h"], lim.get("session")),
                    (T["weekly"], lim.get("weekly")),
                    (sc_name, sc)):
        if l and l.get("percent") is not None:
            if l.get("severity") == "critical" or l["percent"] >= 85:
                if worst is None or l["percent"] > worst[1]:
                    worst = (name, l["percent"])
    if worst:
        d.rounded_rectangle([10, y, W - 10, y + 34], radius=8, fill=(48, 14, 20), outline=HOT)
        d.text((W // 2, y + 17), "! %s  %d%%" % (worst[0], worst[1]),
               font=f_mid, fill=(255, 217, 221), anchor="mm")
        y += 42

    # ---- stopka (wykres 24h) zakotwiczona na DOLE ----
    foot_y = H - CHART_H

    # ---- trzy limity ----
    need_sess = 26 + 2 * SESS_ROW_H
    blk = max(50, min(58, (foot_y - y - need_sess) // 3))

    block(d, y, blk, T["limit5h"], lim.get("session"));    y += blk
    block(d, y, blk, T["weekly"],  lim.get("weekly"), ACC); y += blk
    block(d, y, blk, sc_name,      sc, FABLE);             y += blk

    # ---- sesje: zabieraja wszystko az do stopki ----
    ses = s.get("sessions") or []
    d.line([(14, y + 2), (W - 14, y + 2)], fill=LINE, width=1)
    d.text((14, y + 8), T["sessions"], font=f_tiny, fill=DIM)
    d.text((W - 14, y + 8), "%d / %d" % (len(ses), s.get("processes", 0)),
           font=f_tiny, fill=DIM, anchor="ra")
    y += 26

    fits = max(0, int((foot_y - y - 2) // SESS_ROW_H))
    shown = ses[:fits]
    if len(ses) > fits and fits > 0:
        shown = ses[:fits - 1]

    dots = []
    for row in shown:
        col, blinks = state_style(row.get("state"))
        cy = y + (SESS_ROW_H - DOT_BOX) // 2
        dots.append((14, cy, col, blinks))
        if not blinks or phase:
            d.ellipse([14 + 3, cy + 3, 14 + DOT_BOX - 4, cy + DOT_BOX - 4], fill=col)

        full = row.get("title") or row.get("id") or "?"
        name = full
        while f_sess.getlength(name) > W - 98 and len(name) > 4:
            name = name[:-2]
        if name != full:
            name = name.rstrip() + "…"
        d.text((38, y + 3), name, font=f_sess,
               fill=WARN if row.get("state") == "czeka" else TXT)

        idle = row.get("idleMs", 0) / 1000
        idle_txt = "%ds" % idle if idle < 60 else "%dm" % (idle / 60)
        d.text((W - 14, y + 5), idle_txt, font=f_sesst, fill=DIM, anchor="ra")
        y += SESS_ROW_H

    if not ses:
        d.text((14, y + 4), T["none"], font=f_small, fill=DIM)
    elif len(ses) > len(shown):
        d.text((38, y + 4), T["more"] % (len(ses) - len(shown)),
               font=f_tiny, fill=DIM)

    # ---- stopka: wykres tokenow out / 24h ----
    chart(d, foot_y, s.get("hourly") or [])

    global LAST_DOTS
    LAST_DOTS = dots
    return img


# ---------------------------------------------------------------- diff
def dirty_rects(old, new):
    """Prostokaty, w ktorych nowa klatka rozni sie od tego, co wisi na
    ekranie. Strefa stworka jest wylaczona - tam pisze tylko animacja."""
    diff = ImageChops.difference(old, new)
    dd = ImageDraw.Draw(diff)
    dd.rectangle(MASCOT_ZONE, fill=(0, 0, 0))

    rects = []
    STRIP = 24
    for y0 in range(0, H, STRIP):
        y1 = min(y0 + STRIP, H)
        bbox = diff.crop((0, y0, W, y1)).getbbox()
        if bbox:
            rects.append([max(0, bbox[0] - 1), y0 + bbox[1],
                          min(W, bbox[2] + 1), y0 + bbox[3]])

    # sklejamy sasiednie paski o nachodzacych zakresach X
    merged = []
    for r in rects:
        if merged:
            m = merged[-1]
            if r[1] <= m[3] + 4 and r[0] < m[2] + 12 and r[2] > m[0] - 12:
                m[0] = min(m[0], r[0])
                m[2] = max(m[2], r[2])
                m[3] = r[3]
                continue
        merged.append(r)
    return merged


# ---------------------------------------------------------------- stworek
class Mascot:
    """Pikselowy stworek w naglowku. Rysowany na siatce 2x2 px (Minecraft-vibe).

    Stany: spi / stoi / chodzi (tempo od tokenow) / czeka (dymek "!").
    Kazdy tick zwraca maly prostokat do wyslania - nigdy pelna klatke.
    """
    CELL = 2
    SPR_W, SPR_H = 32, 28            # cialo w boxie 16x14 komorek
    TOP = 10                          # y gornej krawedzi sprite'a
    BODY = OK
    BELLY = (26, 160, 126)
    DK = (8, 24, 19)

    def __init__(self):
        self.x = MASCOT_ZONE[0] + 20
        self.dir = 1
        self.frame = 0
        self.tick_n = 0
        self.state = "stoi"
        self.speed = 0
        self.hot = False
        self.prev_box = None
        self.force = True   # pierwszy tick i kazda zmiana stanu = natychmiastowy redraw

    # ---- decyzje ----
    def set_mood(self, rate, waiting, working, hot):
        if hot != self.hot:
            self.force = True
        self.hot = hot
        if waiting:
            new = "czeka"
        elif rate < 1 and not working:
            new = "spi"
        elif rate < 1:
            new, self.speed = "stoi", 0
        else:
            self.speed = 1 if rate < 1500 else 2 if rate < 4000 else 3 if rate < 8000 else 4
            new = "chodzi"
        if new != self.state:
            self.state = new
            self.frame = 0
            self.force = True   # bez tego stara poza wisi do najblizszego przelomu animacji

    def _advance(self):
        """Tick ~0,15 s: male kroki czesto zamiast duzych rzadko - plynny
        marsz zamiast teleportacji. Kadencje przeliczone pod ten rytm."""
        n = self.tick_n
        if self.state == "chodzi":
            # nogi: wolny marsz przeklada nogi co 3 ticki, bieg co 2
            if n % (2 if self.speed >= 3 else 3) == 0:
                self.frame ^= 1
            # KAZDY bieg robi krok co tick (7 krokow/s) - dopiero to daje
            # widoczna plynnosc; tempo tokenow roznicuje dlugosc kroku.
            # (Poprzednio bieg 1 = 1 px co DRUGI tick, czyli identycznie
            # ze stara wersja - stad "chodzi tak samo".)
            self.x += self.dir * (0, 1, 1, 2, 3)[self.speed]
            lo, hi = MASCOT_ZONE[0], MASCOT_ZONE[2] - self.SPR_W - 24
            if self.x <= lo:
                self.x, self.dir = lo, 1
            elif self.x >= hi:
                self.x, self.dir = hi, -1
            return True
        if self.state == "czeka":
            if n % 4 == 0:
                self.frame ^= 1
                return True
            return False
        if self.state == "spi":
            if n % 10 == 0:
                self.frame ^= 1
                return True
            return False
        # stoi: mrugniecie co ~3 s
        if n % 20 == 0 or (n - 1) % 20 == 0:
            self.frame = 1 if n % 20 == 0 else 0
            return True
        return False

    # ---- rysowanie ----
    def _cur_box(self):
        x0, x1 = self.x, self.x + self.SPR_W
        if self.state == "czeka":
            if x1 + 24 <= MASCOT_ZONE[2]:
                x1 += 24
            else:
                x0 -= 24
        return [max(MASCOT_ZONE[0], x0 - 2), MASCOT_ZONE[1],
                min(MASCOT_ZONE[2], x1 + 2), MASCOT_ZONE[3]]

    def _render(self, d, ox):
        """Rysuje stworka; ox = absolutny x lewego brzegu sprite'a."""
        C = self.CELL
        # delikatne bujanie tulowia w rytm krokow
        oy = self.TOP + (1 if self.state == "chodzi" and self.frame else 0)

        def px(cx, cy, col):
            d.rectangle([ox + cx * C, oy + cy * C,
                         ox + cx * C + C - 1, oy + cy * C + C - 1], fill=col)

        if self.state == "spi":
            # cialo lezace
            for cy in range(8, 13):
                for cx in range(1, 15):
                    px(cx, cy, self.BODY if cy < 11 else self.BELLY)
            # zamkniete oczy
            for cx in (4, 5, 9, 10):
                px(cx, 9, self.DK)
            # "Z" na przemian male/duze
            zx, zy = (10, 0) if self.frame == 0 else (8, 2)
            for cx in range(zx, zx + 4):
                px(cx, zy, DIM)
                px(cx, zy + 3, DIM)
            px(zx + 2, zy + 1, DIM)
            px(zx + 1, zy + 2, DIM)
            return

        # cialo stojace
        for cy in range(1, 9):
            for cx in range(2, 14):
                if (cx, cy) in ((2, 1), (13, 1), (2, 8), (13, 8)):
                    continue
                px(cx, cy, self.BODY if cy < 7 else self.BELLY)

        # oczy - przesuniete w strone marszu
        e = 1 if self.dir > 0 else -1
        blink = (self.state == "stoi" and self.frame == 1)
        for bx in (4 + e, 9 + e):
            if blink:
                px(bx, 4, self.DK)
                px(bx + 1, 4, self.DK)
            else:
                for cy in (3, 4):
                    px(bx, cy, self.DK)
                    px(bx + 1, cy, self.DK)

        # nogi: dwie pary, na przemian
        legs = [(3, 4), (11, 12)]
        for i, (la, lb) in enumerate(legs):
            raised = (self.state == "chodzi" and self.frame == i)
            depth = 11 if raised else 12
            for cy in range(9, depth + 1):
                px(la, cy, self.BODY)
                px(lb, cy, self.BODY)

        # kurz przy biegu
        if self.state == "chodzi" and self.speed >= 3 and self.frame == 0:
            dx = 0 if self.dir > 0 else 15
            px(dx, 11, DIM)
            px(dx, 12, DIM)

        # pot przy wysokim limicie
        if self.hot:
            sy = 0 if self.frame == 0 else 1
            px(1, sy, WARN)
            px(0, sy + 2, WARN)

        # dymek "!" gdy czeka
        if self.state == "czeka" and self.frame == 0:
            right = ox + self.SPR_W + 24 <= MASCOT_ZONE[2]
            bx = ox + self.SPR_W + 2 if right else ox - 24
            d.rounded_rectangle([bx, oy + 2, bx + 20, oy + 22],
                                radius=4, fill=(48, 34, 10), outline=WARN)
            d.rectangle([bx + 9, oy + 6, bx + 11, oy + 14], fill=WARN)
            d.rectangle([bx + 9, oy + 17, bx + 11, oy + 19], fill=WARN)

    def paint(self, d):
        """Nanosi stworka na podglad (wspolrzedne absolutne)."""
        self._render(d, self.x)

    def step(self):
        """Jeden tick animacji. Zwraca (obrazek, x, y) do wyslania albo None."""
        self.tick_n += 1
        changed = self._advance()
        if self.force:
            changed = True
            self.force = False
        if not changed:
            return None
        box = self._cur_box()
        if self.prev_box:
            box = [min(box[0], self.prev_box[0]), min(box[1], self.prev_box[1]),
                   max(box[2], self.prev_box[2]), max(box[3], self.prev_box[3])]
        self.prev_box = self._cur_box()
        im = Image.new("RGB", (box[2] - box[0], box[3] - box[1]), BG)
        d = ImageDraw.Draw(im)
        # rysujemy w ukladzie wycinka: przesuwamy caly uklad w lewo/gore
        shifted = _ShiftedDraw(d, -box[0], -box[1])
        self._render(shifted, self.x)
        return im, box[0], box[1]


class _ShiftedDraw:
    """Cienka nakladka na ImageDraw przesuwajaca wspolrzedne o staly wektor -
    dzieki temu stworek rysuje sie tym samym kodem na pelnym podgladzie
    i na malym wycinku."""

    def __init__(self, d, dx, dy):
        self._d, self._dx, self._dy = d, dx, dy

    def _sh(self, xy):
        out = []
        for i, v in enumerate(xy):
            out.append(v + (self._dx if i % 2 == 0 else self._dy))
        return out

    def rectangle(self, xy, **kw):
        self._d.rectangle(self._sh(xy), **kw)

    def rounded_rectangle(self, xy, **kw):
        self._d.rounded_rectangle(self._sh(xy), **kw)


# ---------------------------------------------------------------- ekran
class ScreenLink:
    """Polaczenie z ekranem odporne na wypiecie kabla.

    Wypiecie USB uniewaznia uchwyt portu - kazdy kolejny zapis konczy sie
    bledem i sam z siebie NIGDY sie nie naprawi. Przy bledzie zrywamy
    polaczenie i odtwarzamy je od zera, razem z orientacja i jasnoscia,
    ktore ekran gubi po odcieciu zasilania.
    """

    RETRY_COOLDOWN = 3.0

    def __init__(self, port, brightness):
        self.want = port
        self.port = None
        self.brightness = brightness
        self.dev = None
        self.last_try = 0.0
        self.needs_full = True
        self.was_down = False
        self.fail_n = 0

    def _resolve(self):
        """Nigdy nie rzuca - blad wykrywania portu nie moze ubic petli
        (nadzorca by ja wskrzesil prosto w ten sam crash: crash-loop)."""
        try:
            from turing import auto_detect
            explicit = self.want and self.want.upper() != "AUTO"
            # Po serii porazek jawnego portu probujemy auto-wykrycia:
            # Windows po przelogowaniu USB potrafi nadac urzadzeniu inny COM.
            if explicit and self.fail_n < 5:
                return self.want
            return auto_detect() or (self.want if explicit else None)
        except Exception as e:
            print("blad wykrywania portu:", e)
            return None

    def connect(self):
        if time.time() - self.last_try < self.RETRY_COOLDOWN:
            return False
        self.last_try = time.time()
        port = self._resolve()
        if not port:
            self.fail_n += 1
            return False
        try:
            from turing import Turing, PORTRAIT
            dev = Turing(port)
            # ~3 s: gwarantuje wyjscie parsera z niedokonczonej bitmapy po
            # ewentualnym twardym killu poprzedniego procesu. Bez tego ekran
            # zjadalby nasze komendy jako piksele i pokazywal smieci,
            # a model klamalby, ze wyslana klatka wisi na ekranie.
            print("resynchronizacja parsera ekranu (~3 s)...")
            dev.resync()
            dev.set_orientation(PORTRAIT, W, H)
            time.sleep(0.2)
            dev.set_brightness(self.brightness)
            dev.screen_on()
        except Exception as e:
            print("nie moge otworzyc %s: %s" % (port, e))
            self.fail_n += 1
            return False
        self.dev = dev
        self.port = port
        self.needs_full = True
        self.fail_n = 0
        if self.was_down:
            print("ekran wrocil na %s" % port)
            self.was_down = False
        return True

    def _drop(self, err):
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:
                pass
        self.dev = None
        self.needs_full = True
        if not self.was_down:
            print("utracono ekran (%s) - probuje wznowic" % err)
            self.was_down = True

    def send(self, img, x=0, y=0):
        if self.dev is None and not self.connect():
            return False
        try:
            self.dev.display(img, x, y)
            return True
        except Exception as e:
            self._drop(e)
            return False

    def close(self):
        self._drop("koniec")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="co ile odswiezenie danych i diff (s)")
    ap.add_argument("--blink", type=float, default=0.6, help="polokres migania kropek")
    ap.add_argument("--tick", type=float, default=0.15, help="krok animacji stworka")
    ap.add_argument("--brightness", type=int, default=80)
    ap.add_argument("--lang", choices=sorted(LANGS),
                    default=os.environ.get("PANEL_LANG", "pl"),
                    help="jezyk napisow na ekranie / screen language")
    args = ap.parse_args()

    global T
    T = LANGS.get(args.lang, LANGS["pl"])

    screen = None
    if args.serial:
        screen = ScreenLink(args.serial, args.brightness)
        if screen.connect():
            print("ekran %s gotowy (%dx%d, pion, przyrostowo)" % (screen.port, W, H))
        else:
            print("ekran niedostepny - bede probowal w tle")

    mascot = Mascot()
    model = None            # co realnie wisi na ekranie
    base = None             # ostatnia narysowana baza
    rate_hist = deque()     # (czas, tokeny out w oknie 5h)
    rate = 0.0

    loop = args.loop or (args.serial and not args.once)
    last_refresh = 0.0
    blink_acc = 0.0
    phase = True

    def full_to_screen(img):
        nonlocal model
        if screen and screen.send(img):
            model = img.copy()
            screen.needs_full = False
            # pelna klatka wymazala stworka (baza ma pusta strefe) -
            # wymuszamy natychmiastowe odrysowanie w nowym ticku
            mascot.prev_box = None
            mascot.force = True
            return True
        return False

    fail_streak = 0
    marker_on = False
    diag_n = 0
    diag_t0 = time.time()

    try:
        while True:
            now = time.time()

            # diagnostyka tempa: co 100 tikow raportujemy realny rytm petli
            diag_n += 1
            if diag_n >= 100:
                span = now - diag_t0
                print("100 tikow w %.1f s (sredni tick %.0f ms) | stan=%s speed=%d rate=%.0f/min x=%d"
                      % (span, span * 10, mascot.state, mascot.speed, rate, mascot.x))
                diag_n = 0
                diag_t0 = now

            # ---- odswiezenie danych + diff ----
            # Sam warunek czasowy: "or base is None" robilo fetch-sztorm przy
            # martwym API na starcie (proba co tick zamiast co interval).
            if now - last_refresh >= args.interval:
                last_refresh = now
                try:
                    s = fetch()
                    new_base = draw(s, phase)
                    fail_streak = 0
                    marker_on = False
                except Exception as e:
                    print("blad klatki:", e)
                    s, new_base = None, None
                    fail_streak += 1

                if new_base is not None:
                    # tempo tokenow (out/min) z okna 5h; spadki (przesuwanie
                    # sie okna, reset) obcinamy do zera
                    out5 = ((s.get("window5h") or {}).get("out")) or 0
                    rate_hist.append((now, out5))
                    while rate_hist and now - rate_hist[0][0] > 90:
                        rate_hist.popleft()
                    if len(rate_hist) >= 2:
                        dt = now - rate_hist[0][0]
                        d_out = out5 - rate_hist[0][1]
                        rate = max(0.0, d_out / dt * 60) if dt > 0 else 0.0

                    lim = s.get("limits") or {}
                    worst = 0
                    for l in (lim.get("session"), lim.get("weekly"), lim.get("fable")):
                        if l and l.get("percent") is not None:
                            worst = max(worst, l["percent"])
                    ses = s.get("sessions") or []
                    mascot.set_mood(
                        rate,
                        any(x.get("state") == "czeka" for x in ses),
                        any(x.get("state") == "pracuje" for x in ses),
                        worst >= 85,
                    )

                    base = new_base

                    # podglad ze stworkiem; zablokowany plik nie moze
                    # ubic petli
                    try:
                        prev = base.copy()
                        mascot.paint(ImageDraw.Draw(prev))
                        prev.save("preview.png")
                    except Exception as e:
                        print("blad zapisu podgladu:", e)

                    if screen:
                        if model is None or screen.needs_full:
                            full_to_screen(base)
                        else:
                            rects = dirty_rects(model, base)
                            area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in rects)
                            # prog wysoko: pelna klatka (307 KB, ~3 s blokady)
                            # oplaca sie dopiero, gdy prostokaty kosztuja
                            # niemal tyle samo
                            if area > 0.85 * W * H:
                                full_to_screen(base)
                            else:
                                for r in rects:
                                    patch = base.crop(tuple(r))
                                    if screen.send(patch, r[0], r[1]):
                                        model.paste(patch, (r[0], r[1]))
                                        # diff mogl wjechac w strefe stworka
                                        # (sklejanie paskow) - odrysuj go
                                        if (r[0] < MASCOT_ZONE[2] and r[2] > MASCOT_ZONE[0]
                                                and r[1] < MASCOT_ZONE[3] and r[3] > MASCOT_ZONE[1]):
                                            mascot.force = True
                                            mascot.prev_box = None
                                    else:
                                        break

                # Serwer lezy, a ekran po reconnect czeka na pelna klatke:
                # wysylamy OSTATNIA znana baze. Stara, ale spojna - bez tego
                # stworek i kropki malowalyby po smieciach po wlaczeniu
                # zasilania ekranu.
                if screen and screen.dev is not None and screen.needs_full and base is not None:
                    full_to_screen(base)

                # Po >=5 nieudanych odswiezeniach (>=10 s) stawiamy czerwony
                # znacznik w rogu - ekran z zamrozonymi danymi nie moze
                # wygladac na zywy.
                if screen and screen.dev is not None and not screen.needs_full \
                        and fail_streak >= 5 and not marker_on and model is not None:
                    mark = Image.new("RGB", (10, 10), HOT)
                    if screen.send(mark, W - 12, 2):
                        model.paste(mark, (W - 12, 2))
                        marker_on = True

            # ---- animacja stworka ----
            # needs_full = ekran w nieznanym stanie (swiezy reconnect) -
            # nie malujemy po nim latek, dopoki nie pojdzie pelna klatka.
            if screen and screen.dev is not None and model is not None and not screen.needs_full:
                out = mascot.step()
                if out:
                    im, mx, my = out
                    if screen.send(im, mx, my):
                        model.paste(im, (mx, my))
            elif not screen:
                mascot.step()   # podglad tez ma zyc

            # ---- miganie kropek ----
            blink_acc += args.tick
            if blink_acc >= args.blink:
                blink_acc = 0.0
                phase = not phase
                if screen and screen.dev is not None and model is not None and not screen.needs_full:
                    for (dx, dy, col, blinks) in LAST_DOTS:
                        if not blinks:
                            continue
                        im = dot_image(col if phase else None)
                        if screen.send(im, dx, dy):
                            model.paste(im, (dx, dy))
                        else:
                            break

            if not loop:
                break
            if os.path.exists(STOP_FLAG):
                try:
                    os.remove(STOP_FLAG)
                except OSError:
                    pass
                print("stop.flag - koncze lagodnie")
                break
            if screen and screen.dev is None:
                screen.connect()
            time.sleep(args.tick)
    except KeyboardInterrupt:
        print("\nkoniec")
    finally:
        if screen:
            screen.close()


if __name__ == "__main__":
    main()
