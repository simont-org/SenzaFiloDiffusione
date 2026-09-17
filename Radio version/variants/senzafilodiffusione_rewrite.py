#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SenzaFiloDiffusione - versione riscritta
========================================
Stessa pinout, stesso formato di radio_list.txt, stesse funzionalita'
della versione originale, ma con un'architettura diversa:

  - Una CODA DI EVENTI (queue.Queue): encoder e pulsanti non fanno
    nulla direttamente, si limitano a depositare un evento nella coda.
  - Un UNICO thread "controller" consuma gli eventi, esegue le azioni
    (comandi mpc) e DISEGNA sul display. Essendo l'unico a toccare
    display e mpc, non servono lock e non ci sono race condition.
  - Una MACCHINA A STATI dell'interfaccia (NOW_PLAYING, LIST, VOLUME,
    SETTINGS, MESSAGE) con timeout: qualunque schermata temporanea
    torna da sola alla schermata "in riproduzione".
  - Il polling degli encoder avviene in un thread dedicato con
    sleep(0.001): reattivo ma con consumo CPU trascurabile.

Requisiti esterni: identici alla versione originale
(mpd+mpc, luma.oled, RPi.GPIO). Nessun file intermedio radio_info.txt.
"""

from RPi import GPIO
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1106
from PIL import ImageFont
import subprocess
import threading
import queue
import socket
import time
import re

# ------------------------------------------------------------------
# Configurazione
# ------------------------------------------------------------------
RADIO_LIST_FILE = "/home/pi/WoodStream/radio_list.txt"

# Pin GPIO (numerazione BCM) - identici all'originale
LIST_CLK, LIST_DT, LIST_SW = 27, 22, 17
VOL_CLK,  VOL_DT,  VOL_SW  = 4, 18, 23
CONF_PUSH                  = 12
PRESET_PINS = [21, 20, 16, 13, 19, 26]

VOLUME_STEP        = 5
VOLUME_INIZIALE    = 20
TIMEOUT_LIST       = 8.0    # s: la lista torna a "now playing"
TIMEOUT_VOLUME     = 2.0    # s: l'indicatore volume sparisce
TIMEOUT_MESSAGE    = 3.0    # s: i messaggi (IP, ecc.) spariscono
REFRESH_SONG       = 10.0   # s: aggiornamento titolo canzone
MPC_TIMEOUT        = 10     # s: tempo massimo per un comando mpc

# Stati dell'interfaccia
NOW_PLAYING, LIST, VOLUME, SETTINGS, MESSAGE = range(5)

SETTINGS_OPTIONS = ["<-- Back", "WiFi WPS", "Show IP address",
                    "Reload list", "Standby", "Shutdown"]

# ------------------------------------------------------------------
# Stato globale (letto/scritto SOLO dal thread controller,
# tranne la coda eventi che e' thread-safe per natura)
# ------------------------------------------------------------------
events = queue.Queue()

radios = []          # lista di [nome, url]
presets = [0] * 6    # indice radio associato a ciascun tasto
current_radio = 0    # radio in riproduzione
list_cursor = 0      # posizione del cursore nella lista
settings_cursor = 0
volume = VOLUME_INIZIALE
mute = False
standby = False

ui_state = NOW_PLAYING
state_deadline = 0.0     # quando scade lo stato temporaneo corrente
last_song_refresh = 0.0
message_text = ""

# ------------------------------------------------------------------
# Display
# ------------------------------------------------------------------
serial = i2c(port=1, address=0x3C)
device = sh1106(serial, rotate=0)
FONT = ImageFont.load_default()


def text_width(draw, text):
    """Larghezza in pixel di un testo (con fallback per PIL vecchie)."""
    try:
        return int(draw.textlength(text, font=FONT))
    except AttributeError:
        return 6 * len(text)   # il font di default e' largo ~6 px


def draw_centered(draw, y, text, inverted=False):
    """Riga di testo centrata, eventualmente in negativo."""
    w = text_width(draw, text)
    x = max(0, (128 - w) // 2)
    if inverted:
        draw.rectangle((0, y, 127, y + 10), outline=255, fill=255)
        draw.text((x, y), text, font=FONT, fill="black")
    else:
        draw.text((x, y), text, font=FONT, fill="white")


def render():
    """Disegna la schermata corrispondente allo stato corrente.
    Chiamata SOLO dal thread controller."""
    with canvas(device) as draw:
        if ui_state == NOW_PLAYING:
            if standby:
                draw_centered(draw, 26, "-- Standby --")
                return
            draw_centered(draw, 0, radios[current_radio][0], inverted=True)
            title = get_song_title()
            if not title:
                draw_centered(draw, 28, "......")
            else:
                # spezza il titolo in righe da 19 caratteri (max 4 righe)
                rows = [title[i:i + 19] for i in range(0, min(len(title), 76), 19)]
                for i, row in enumerate(rows):
                    draw_centered(draw, 16 + 10 * i, row.strip())
            if mute:
                draw.text((0, 54), "MUTE", font=FONT, fill="white")

        elif ui_state == LIST:
            # finestra scorrevole calcolata dal cursore: funziona anche
            # dopo salti arbitrari e con liste piu' corte di 6 voci
            start = max(0, min(list_cursor - 2, len(radios) - 6))
            for i in range(min(6, len(radios))):
                idx = start + i
                name = radios[idx][0]
                if idx == list_cursor:
                    draw.rectangle((0, 2 + i * 10, 127, 12 + i * 10),
                                   outline=255, fill=255)
                    draw.text((4, 2 + i * 10), name, font=FONT, fill="black")
                else:
                    draw.text((4, 2 + i * 10), name, font=FONT, fill="white")

        elif ui_state == VOLUME:
            draw_centered(draw, 10, radios[current_radio][0])
            draw_centered(draw, 28, "Volume  %d" % volume)
            # barra grafica
            draw.rectangle((14, 44, 114, 50), outline="white", fill="black")
            draw.rectangle((14, 44, 14 + volume, 50), outline="white",
                           fill="white")

        elif ui_state == SETTINGS:
            for i, opt in enumerate(SETTINGS_OPTIONS):
                if i == settings_cursor:
                    draw.rectangle((0, 2 + i * 10, 127, 12 + i * 10),
                                   outline=255, fill=255)
                    draw.text((4, 2 + i * 10), opt, font=FONT, fill="black")
                else:
                    draw.text((4, 2 + i * 10), opt, font=FONT, fill="white")

        elif ui_state == MESSAGE:
            for i, row in enumerate(message_text.split("\n")):
                draw_centered(draw, 20 + 12 * i, row)


# ------------------------------------------------------------------
# Interazione con mpd (tramite mpc, come nell'originale)
# ------------------------------------------------------------------
def mpc(*args):
    """Esegue un comando mpc con timeout e restituisce lo stdout.
    Il timeout evita che uno stream che non risponde blocchi tutto."""
    try:
        out = subprocess.run(["mpc"] + list(args),
                             capture_output=True, text=True,
                             timeout=MPC_TIMEOUT)
        return out.stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


def get_song_title():
    """Chiede a mpc il brano corrente e ne estrae il titolo.
    Sostituisce il vecchio file intermedio radio_info.txt:
    cosi' il titolo e' sempre aggiornato."""
    line = mpc("current").strip()
    if not line or line.startswith("http"):
        return ""
    pos = line.find(":")
    if pos != -1:
        return line[pos + 1:].strip()
    return "*" + line


def play_radio(index):
    """Cambia stazione."""
    global current_radio, standby
    current_radio = index
    standby = False
    # feedback immediato: l'utente vede subito cosa sta succedendo
    with canvas(device) as draw:
        draw_centered(draw, 0, radios[index][0], inverted=True)
        draw_centered(draw, 28, "connessione...")
    mpc("clear")
    mpc("add", radios[index][1])
    mpc("play")


# ------------------------------------------------------------------
# Lettura lista radio
# ------------------------------------------------------------------
def read_radio_list():
    """Legge nomi e URL dal file di testo.
    Formato riga:  (preset.)Nome radio|URL
    Robusta rispetto a righe vuote o malformate."""
    global radios, presets
    new_radios = []
    new_presets = [0] * 6
    try:
        with open(RADIO_LIST_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or "|" not in line:
                    continue                      # ignora righe non valide
                name, url = line.split("|", 1)
                m = re.match(r"^([1-6])\.(.+)$", name)
                if m:
                    new_presets[int(m.group(1)) - 1] = len(new_radios)
                    name = m.group(2)
                new_radios.append([name.strip(), url.strip()])
    except OSError:
        pass
    if new_radios:                                # mai lasciare lista vuota
        radios, presets = new_radios, new_presets


# ------------------------------------------------------------------
# Utilita' di rete (come nell'originale, con bug corretti)
# ------------------------------------------------------------------
def ip_address():
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("8.8.8.8", 80))
        ip = sk.getsockname()[0]
        sk.close()
        return str(ip)
    except OSError:
        return "no network"


def wps_connect():
    """Cerca una rete con WPS-PBC attivo e si collega.
    NB: nell'originale c'era group(5) su una regex con 3 gruppi
    (crash garantito); l'SSID e' l'ultimo campo della riga."""
    try:
        subprocess.check_output(["wpa_cli", "-i", "wlan0", "scan"])
        time.sleep(1)
        wpa = subprocess.check_output(
            ["wpa_cli", "-i", "wlan0", "scan_results"]).decode("utf-8")
    except (subprocess.CalledProcessError, OSError):
        return None
    for row in wpa.splitlines():
        if "[WPS-PBC]" in row:
            fields = row.split("\t")
            mac, ssid = fields[0], fields[-1]
            try:
                subprocess.check_output(
                    ["wpa_cli", "-i", "wlan0", "wps_pbc", mac])
            except (subprocess.CalledProcessError, OSError):
                return None
            return ssid
    return None


# ------------------------------------------------------------------
# Produttori di eventi: encoder (polling) e pulsanti (interrupt)
# ------------------------------------------------------------------
class Encoder:
    """Decodifica a tabella di stati, identica alla tua, ma
    incapsulata in una classe: niente piu' funzioni duplicate."""
    TABLE = [0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0]

    def __init__(self, clk, dt):
        self.clk, self.dt = clk, dt
        self.code = 0
        self.store = 0

    def read(self):
        self.code = (self.code << 2) & 0x0F
        if GPIO.input(self.dt):
            self.code |= 0x02
        if GPIO.input(self.clk):
            self.code |= 0x01
        if Encoder.TABLE[self.code]:
            self.store = ((self.store << 4) | self.code) & 0xFFFF
            if (self.store & 0xFF) == 0x2B:
                return -1
            if (self.store & 0xFF) == 0x17:
                return 1
        return 0


def encoder_thread():
    """Unico thread che campiona ENTRAMBI gli encoder.
    Lo sleep(0.001) e' il punto chiave: reattivita' identica,
    consumo CPU che passa dal 100% a meno dell'1%."""
    enc_list = Encoder(LIST_CLK, LIST_DT)
    enc_vol = Encoder(VOL_CLK, VOL_DT)
    while True:
        step = enc_list.read()
        if step:
            events.put(("list_rotate", step))
        step = enc_vol.read()
        if step:
            events.put(("vol_rotate", step))
        time.sleep(0.001)


def read_pressed_preset():
    """Ritorna l'indice (0-5) del tasto preset attualmente premuto
    (i tasti sono autoescludenti: al piu' uno e' a livello basso)."""
    for i, pin in enumerate(PRESET_PINS):
        if GPIO.input(pin) == 0:
            return i
    return None


# I callback GPIO devono essere ISTANTANEI: niente sleep, niente
# display, niente mpc. Depositano l'evento e basta.
def cb_list_push(channel):
    events.put(("list_push", None))


def cb_vol_push(channel):
    events.put(("vol_push", None))


def cb_settings_push(channel):
    events.put(("settings_push", None))


def cb_preset(channel):
    events.put(("preset", None))


# ------------------------------------------------------------------
# Azioni del menu impostazioni
# ------------------------------------------------------------------
def show_message(text, seconds=TIMEOUT_MESSAGE):
    """Passa allo stato MESSAGE per qualche secondo."""
    global ui_state, message_text, state_deadline
    ui_state = MESSAGE
    message_text = text
    state_deadline = time.time() + seconds
    render()


def do_settings_action():
    global ui_state, list_cursor, standby
    n = settings_cursor
    if n == 0:                                   # Back
        ui_state = NOW_PLAYING
        render()
    elif n == 1:                                 # WiFi WPS
        show_message("ricerca WPS...", 60)
        ssid = wps_connect()
        show_message("* " + ssid if ssid else "* No WPS found")
    elif n == 2:                                 # Show IP
        show_message(ip_address())
    elif n == 3:                                 # Reload list
        read_radio_list()
        list_cursor = min(list_cursor, len(radios) - 1)
        show_message("List updated.")
    elif n == 4:                                 # Standby
        mpc("clear")
        standby = True
        ui_state = NOW_PLAYING
        render()
    elif n == 5:                                 # Shutdown
        show_message("Shutdown...")
        time.sleep(2)
        with canvas(device):
            pass                                 # schermo nero
        subprocess.run(["sudo", "shutdown", "0"])


# ------------------------------------------------------------------
# Gestione eventi (macchina a stati)
# ------------------------------------------------------------------
def goto(state, timeout=None):
    global ui_state, state_deadline
    ui_state = state
    state_deadline = time.time() + timeout if timeout else 0.0
    render()


def handle_event(name, value):
    global list_cursor, settings_cursor, volume, mute

    if name == "list_rotate":
        if ui_state == SETTINGS:
            settings_cursor = max(0, min(len(SETTINGS_OPTIONS) - 1,
                                         settings_cursor + value))
            goto(SETTINGS)
        else:
            # da qualunque altra schermata, ruotare apre la lista
            list_cursor = max(0, min(len(radios) - 1, list_cursor + value))
            goto(LIST, TIMEOUT_LIST)

    elif name == "list_push":
        if ui_state == SETTINGS:
            do_settings_action()
        elif ui_state == LIST:
            play_radio(list_cursor)
            goto(NOW_PLAYING)
        # se premuto in altre schermate: torna semplicemente al brano
        else:
            goto(NOW_PLAYING)

    elif name == "vol_rotate":
        # coalescing: se l'utente gira veloce, accorpa i passi in coda
        # in un'unica chiamata mpc invece di una per scatto
        while True:
            try:
                n2, v2 = events.get_nowait()
            except queue.Empty:
                break
            if n2 == "vol_rotate":
                value += v2
            else:
                events.put((n2, v2))
                break
        volume = max(0, min(100, volume + value * VOLUME_STEP))
        if mute:
            mute = False       # girare il volume disattiva il mute
        goto(VOLUME, TIMEOUT_VOLUME)
        mpc("volume", str(volume))

    elif name == "vol_push":
        mute = not mute
        mpc("volume", "0" if mute else str(volume))
        goto(NOW_PLAYING)

    elif name == "settings_push":
        settings_cursor = 0
        goto(SETTINGS)

    elif name == "preset":
        # debounce: i deviatori meccanici autoescludenti generano
        # rimbalzi su piu' pin; due letture concordi a 80 ms di
        # distanza bastano per avere lo stato assestato
        time.sleep(0.08)
        first = read_pressed_preset()
        time.sleep(0.08)
        second = read_pressed_preset()
        if first is not None and first == second:
            idx = presets[first]
            if idx != current_radio or standby:
                play_radio(idx)
            goto(NOW_PLAYING)


def controller():
    """Loop principale: consuma eventi, gestisce i timeout degli
    stati temporanei e il refresh periodico del titolo."""
    global ui_state, last_song_refresh
    while True:
        try:
            name, value = events.get(timeout=0.25)
            handle_event(name, value)
        except queue.Empty:
            pass

        now = time.time()

        # scadenza degli stati temporanei -> torna a NOW_PLAYING
        if ui_state in (LIST, VOLUME, MESSAGE) and state_deadline \
                and now > state_deadline:
            goto(NOW_PLAYING)

        # refresh del titolo canzone (solo in NOW_PLAYING)
        if ui_state == NOW_PLAYING and not standby \
                and now - last_song_refresh > REFRESH_SONG:
            last_song_refresh = now
            render()


# ------------------------------------------------------------------
# Avvio
# ------------------------------------------------------------------
def main():
    global list_cursor

    GPIO.setmode(GPIO.BCM)
    for pin in (LIST_CLK, LIST_DT, LIST_SW, VOL_CLK, VOL_DT, VOL_SW,
                CONF_PUSH, *PRESET_PINS):
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    read_radio_list()
    if not radios:
        with canvas(device) as draw:
            draw_centered(draw, 26, "radio_list.txt vuota!")
        return
    mpc("volume", str(VOLUME_INIZIALE))

    # schermata di avvio
    with canvas(device) as draw:
        draw_centered(draw, 14, "SenzaFiloDiffusione")
        draw_centered(draw, 24, "-" * 21)
        draw_centered(draw, 34, "by Simon T.")
    time.sleep(1.5)

    # avvia la radio corrispondente al tasto premuto (o la prima)
    pressed = read_pressed_preset()
    start_index = presets[pressed] if pressed is not None else 0
    play_radio(start_index)
    list_cursor = start_index
    goto(NOW_PLAYING)

    # interrupt sui pulsanti (i callback depositano solo eventi)
    GPIO.add_event_detect(LIST_SW, GPIO.FALLING, callback=cb_list_push,
                          bouncetime=300)
    GPIO.add_event_detect(VOL_SW, GPIO.FALLING, callback=cb_vol_push,
                          bouncetime=300)
    GPIO.add_event_detect(CONF_PUSH, GPIO.FALLING,
                          callback=cb_settings_push, bouncetime=300)
    for pin in PRESET_PINS:
        GPIO.add_event_detect(pin, GPIO.FALLING, callback=cb_preset,
                              bouncetime=400)

    # thread encoder (daemon: muore col programma)
    threading.Thread(target=encoder_thread, daemon=True).start()

    try:
        controller()          # loop principale nel thread main
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    main()
