# -*- coding: utf-8 -*-
"""
Sterownik ekranu Turing Smart Screen 3.5" (rewizja A) po porcie szeregowym.
Protokol wg mathoudebine/turing-smart-screen-python (MIT).

  python turing.py --port COM5 --hello        # sam handshake, nic nie rysuje
  python turing.py --port COM5 --test         # kolorowy wzorzec testowy
  python turing.py --port COM5 --image plik.png
"""
import argparse
import sys
import time

import serial
from PIL import Image

# --- komendy protokolu ---
CMD_RESET = 101
CMD_CLEAR = 102
CMD_TO_BLACK = 103
CMD_SCREEN_OFF = 108
CMD_SCREEN_ON = 109
CMD_SET_BRIGHTNESS = 110
CMD_SET_ORIENTATION = 121
CMD_DISPLAY_BITMAP = 197
CMD_HELLO = 69

PORTRAIT, REVERSE_PORTRAIT, LANDSCAPE, REVERSE_LANDSCAPE = 0, 1, 2, 3

# natywna rozdzielczosc 3.5" (pion)
NATIVE_W, NATIVE_H = 320, 480


def auto_detect():
    """Znajduje port ekranu po sygnaturze sprzetowej. Numer COM potrafi sie
    zmienic po przelogowaniu USB, wiec nie polegamy na wpisanym na sztywno."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    for p in list_ports.comports():
        hw = ((p.hwid or "") + " " + (p.description or "")).upper()
        if "USB35INCHIPSV2" in hw or ("VID_1A86" in hw and "PID_5722" in hw):
            return p.device
    return None


class Turing:
    def __init__(self, port, baud=115200):
        # write_timeout jest OBOWIAZKOWY: bez niego zawieszony ekran (rtscts!)
        # blokuje write() na zawsze - proces zyje, nadzorca nie reaguje,
        # panel martwy do restartu. Z timeoutem write rzuca wyjatek,
        # ScreenLink go lapie i robi reconnect.
        self.ser = serial.Serial(port, baud, timeout=1, write_timeout=5, rtscts=True)

    def resync(self):
        """Wyprowadza parser ekranu z ewentualnej niedokonczonej bitmapy.

        Po twardym killu w polowie DISPLAY_BITMAP urzadzenie czeka na
        brakujace piksele i zjada kolejne komendy jako dane. Wysylamy
        pelna klatke zer: najgorszy zalegly licznik to 307199 bajtow,
        wiec po tym strumieniu parser NA PEWNO jest w trybie komend,
        a dowolne 6 kolejnych zer to nieszkodliwa komenda 0 - dziala
        niezaleznie od przesuniecia ramki."""
        self.ser.write(b"\x00" * (NATIVE_W * NATIVE_H * 2))
        self.ser.flush()
        time.sleep(0.2)
        self.ser.reset_input_buffer()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    # -- podstawa: kazda komenda to 6 bajtow z upakowanymi wspolrzednymi --
    def _cmd(self, cmd, x=0, y=0, ex=0, ey=0, extra=b""):
        buf = bytearray(6)
        buf[0] = x >> 2
        buf[1] = ((x & 3) << 6) + (y >> 4)
        buf[2] = ((y & 15) << 4) + (ex >> 6)
        buf[3] = ((ex & 63) << 2) + (ey >> 8)
        buf[4] = ey & 255
        buf[5] = cmd
        self.ser.write(bytes(buf) + extra)

    def hello(self):
        """Handshake - tylko odczyt, nic nie rysuje."""
        self.ser.reset_input_buffer()
        self.ser.write(bytearray([CMD_HELLO] * 6))
        time.sleep(0.3)
        resp = self.ser.read(6)
        return resp

    def screen_on(self):
        self._cmd(CMD_SCREEN_ON)

    def screen_off(self):
        self._cmd(CMD_SCREEN_OFF)

    def clear(self):
        self._cmd(CMD_CLEAR)

    def set_brightness(self, percent):
        # w tym protokole 0 = najjasniej, 255 = najciemniej
        level = int(255 - (max(0, min(100, percent)) / 100 * 255))
        self._cmd(CMD_SET_BRIGHTNESS, x=level)

    def set_orientation(self, orientation, width, height):
        buf = bytearray(16)
        buf[5] = CMD_SET_ORIENTATION
        buf[6] = orientation + 100
        buf[7] = width >> 8
        buf[8] = width & 255
        buf[9] = height >> 8
        buf[10] = height & 255
        self.ser.write(bytes(buf))

    def display(self, img, x=0, y=0):
        """Wysyla obraz PIL jako RGB565 little-endian."""
        img = img.convert("RGB")
        w, h = img.size
        if w < 1 or h < 1:
            return  # pusty prostokat = ujemne ex/ey w naglowku protokolu
        self._cmd(CMD_DISPLAY_BITMAP, x, y, x + w - 1, y + h - 1)

        rgb565 = bytearray()
        for (r, g, b) in img.getdata():
            v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            rgb565 += bytes([v & 0xFF, v >> 8])   # little-endian

        # Duze porcje: kazdy osobny write na USB ma staly narzut (CH34x),
        # wiec porcja "szerokosc*8" z referencyjnej biblioteki zabijala male
        # latki (36 px szerokosci = 288-bajtowe strzepki, 10 zapisow na
        # latke stworka). 64 KB = 1 zapis na latke, 5 na pelna klatke.
        chunk = 65536
        for i in range(0, len(rgb565), chunk):
            self.ser.write(bytes(rgb565[i:i + chunk]))
        self.ser.flush()


def test_pattern(w, h):
    img = Image.new("RGB", (w, h))
    px = img.load()
    bands = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
    for yy in range(h):
        col = bands[int(yy / h * len(bands))]
        for xx in range(w):
            px[xx, yy] = col
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--hello", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--image")
    ap.add_argument("--orientation", type=int, default=None,
                    help="0=pion 1=pion-odwr 2=poziom 3=poziom-odwr")
    ap.add_argument("--brightness", type=int, default=None)
    args = ap.parse_args()

    try:
        t = Turing(args.port)
    except Exception as e:
        print("NIE MOGE OTWORZYC PORTU:", e)
        sys.exit(1)
    print("port %s otwarty" % args.port)

    try:
        if args.hello:
            r = t.hello()
            if not r:
                print("HELLO: brak odpowiedzi  -> oryginalny Turing 3.5\" (to normalne)")
            else:
                print("HELLO: %s  (hex: %s)" % (list(r), r.hex()))
                if r == bytearray([1] * 6):
                    print("  -> klon USBMonitor 3.5\", 320x480")
                elif r == bytearray([2] * 6):
                    print("  -> 5\", 480x800")
                elif r == bytearray([3] * 6):
                    print("  -> 7\", 600x1024")
                else:
                    print("  -> nieznana sygnatura")
            return

        if args.brightness is not None:
            t.set_brightness(args.brightness)
            print("jasnosc -> %d%%" % args.brightness)

        w, h = NATIVE_W, NATIVE_H
        if args.orientation is not None:
            if args.orientation in (LANDSCAPE, REVERSE_LANDSCAPE):
                w, h = NATIVE_H, NATIVE_W
            t.set_orientation(args.orientation, w, h)
            time.sleep(0.2)
            print("orientacja -> %d  (plotno %dx%d)" % (args.orientation, w, h))

        t.screen_on()

        if args.test:
            t.display(test_pattern(w, h))
            print("wyslano wzorzec %dx%d" % (w, h))
        elif args.image:
            img = Image.open(args.image)
            if img.size != (w, h):
                print("skaluje %s -> %dx%d" % (str(img.size), w, h))
                img = img.resize((w, h))
            t.display(img)
            print("wyslano obraz")
    finally:
        t.close()


if __name__ == "__main__":
    main()
