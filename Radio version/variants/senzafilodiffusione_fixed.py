# -*- coding: utf-8 -*-
# ==================================================================
# SenzaFiloDiffusione - versione ORIGINALE con correzioni commentate
# ==================================================================
# La struttura (thread, callback, variabili globali) e' rimasta la
# tua. Ogni modifica e' marcata con  # >>> FIX n:  e spiegata.
#
# Riepilogo dei fix:
#   FIX 1  - sleep(0.001) nei loop degli encoder (causa n.1 della
#            lentezza: due busy-loop al 100% di CPU che, per via del
#            GIL di Python, strangolavano tutti gli altri thread)
#   FIX 2  - sleep spostato FUORI dall'if in showSong (terzo
#            busy-loop, attivo mentre eri nel menu impostazioni)
#   FIX 3  - lock attorno a OGNI scrittura sul display, anche nei
#            callback (causa della grafica corrotta: scritture I2C
#            concorrenti da thread diversi si mescolavano)
#   FIX 4  - via lo sleep(2) da preset_callback: i callback di
#            RPi.GPIO girano tutti su UN SOLO thread, quindi ogni
#            sleep li' dentro bloccava tutti gli altri pulsanti
#   FIX 5  - il titolo canzone viene riletto da mpc ad ogni ciclo:
#            prima il file radio_info.txt era scritto solo al cambio
#            radio, quindi il titolo non si aggiornava mai
#   FIX 6  - finestra scorrevole della lista ricalcolata dal
#            cursore: prima si spostava solo di +-1 e si rompeva sui
#            salti (es. uscendo dal menu impostazioni)
#   FIX 7  - readRadioList robusta (righe vuote/malformate -> prima
#            IndexError) e currentRadio non piu' fissato a 7
#   FIX 8  - wpsConnect: group(5) su una regex con 3 gruppi ->
#            IndexError garantito appena trovava una rete WPS
#   FIX 9  - coerenza del mute quando si ruota il volume
#   FIX 10 - thread daemon + cleanup GPIO all'uscita
# ==================================================================

from RPi import GPIO
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1106
from PIL import ImageFont
from time import sleep
import os
import subprocess
import socket
import threading
import re

# Radio list sliding window
# ----------------------------------------------------------
listMenuStart = 0
listMenuEnd = 5
counter = 0
# >>> FIX 7: era "currentRadio = 7": se la lista avesse avuto meno
# di 8 voci, names[7] avrebbe dato IndexError. Meglio partire da 0.
currentRadio = 0
menuindex = 0
names: list=[]

# Encoder reading
# ----------------------------------------------------------
listPrevNextCode = 0
listStore = 0
volPrevNextCode = 0
volStore = 0
rot_enc_table: list = [0,1,1,0,1,0,0,1,1,0,0,1,0,1,1,0]

# Volume and mute
# ----------------------------------------------------------
volume = 20
mute = False

# Settings menu
# ----------------------------------------------------------
settingsMode = False
settingsCount = 0
options: list=["<-- Back", "WiFi WPS", "Show IP address",
         "Reload list", "Standby", "Shutdown"]

# GPIO pins (BCM numbering scheme)
# ----------------------------------------------------------
list_clk = 27
list_dt = 22
list_sw = 17
vol_clk = 4
vol_dt = 18
vol_sw = 23
conf_push = 12
preset_sw: list = [[21,0],[20,0],[16,0],[13,0],[19,0],[26,0]]

# Preset assigned to radio buttons
# ----------------------------------------------------------
preset_list: list = [0,0,0,0,0,0]

# Setup OLED display
# ----------------------------------------------------------
serial = i2c(port=1, address=0x3C)  #default value
device = sh1106(serial, rotate=0)

# >>> FIX 5: niente piu' file radio_info.txt tenuto aperto per
# tutta la vita del programma. Il brano corrente viene chiesto
# direttamente a mpc quando serve (vedi currentSongLine piu' avanti).

# Lock for display competition
# ----------------------------------------------------------
sem =  threading.Lock()

def readRadioList():
    """
    Reads radio URLS and names from text file
    Format: (preset.)Radio name|Radio URL
    """

    global names, preset_list

    # >>> FIX 7: la vecchia versione faceva names[r][0][1] == "."
    # senza controllare la lunghezza: bastava una riga vuota in
    # fondo al file (facilissima da introdurre con l'editor PHP)
    # o un nome di un solo carattere per avere IndexError.
    # Ora: si saltano le righe vuote o senza "|", e il prefisso
    # preset si riconosce con una regex.
    newNames = []
    newPresets = [0,0,0,0,0,0]
    listFile = open('/home/pi/WoodStream/radio_list.txt', 'r')
    for line in listFile:
        line = line.strip()
        if not line or '|' not in line:
            continue
        name, url = line.split('|', 1)
        m = re.match(r'^([1-6])\.(.+)$', name)
        if m:
            newPresets[int(m.group(1)) - 1] = len(newNames)
            name = m.group(2)
        newNames.append([name.strip(), url.strip()])
    listFile.close()
    if newNames:                # mai lasciare la lista vuota
        names = newNames
        preset_list = newPresets


def invert(draw, x, y, text, center):
    """
    Display utilities. The values used have been tested
        specifically for the sh1106 controller
    """

    font = ImageFont.load_default()
    draw.rectangle((x, y, x+120, y+10), outline=255, fill=255)
    if (center):
        x=74-4*len(text) - 4*(len(text)%2)
    draw.text((x, y), text, font=font, outline=0,fill="black")


def ipAddress():
    """
    Get current IP address
    """

    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sk.connect(("8.8.8.8", 80))
    ip = (sk.getsockname()[0])
    sk.close()
    return str(ip)


def wpsConnect():
    """
    Search for a WPS-enabled wireless lan and connects to it
    """

    SSID = "none"
    subprocess.check_output(["wpa_cli", "-i", "wlan0", "scan"])
    sleep(1);

    wpa = subprocess.check_output(["wpa_cli", "-i", "wlan0", "scan_results"]).decode("UTF-8")

    # (aggiunta la "r" davanti alla stringa: senza, Python 3.12+
    #  segnala "invalid escape sequence" per \d e \[ )
    active_spot_reg = re.search(r"(([\da-f]{2}:){5}[\da-f]{2})(.*?)\[WPS-PBC\]", wpa)

    if not (active_spot_reg is None):
        if active_spot_reg.group(1):

            subprocess.check_output(["wpa_cli", "-i", "wlan0", "wps_pbc", active_spot_reg.group(1)])
            # >>> FIX 8: la regex ha SOLO 3 gruppi:
            #   group(1) = MAC, group(2) = gruppo interno, group(3) = testo
            #   fra il MAC e [WPS-PBC].
            # group(5) non esiste -> IndexError appena trovava una rete.
            # In scan_results i campi sono separati da tab e l'SSID e'
            # l'ultimo: lo estraiamo dalla riga giusta.
            for row in wpa.splitlines():
                if active_spot_reg.group(1) in row:
                    SSID = row.split("\t")[-1]
                    break

            print(active_spot_reg.group(1) + " " + SSID)

    return(SSID)


def radioList(dev, draw, index):
    """
    Draws radio list with a sliding window of 6 rows
    """

    global menuindex
    global listMenuStart, listMenuEnd
    global names

    font = ImageFont.load_default()
    draw.rectangle(dev.bounding_box, outline="white", fill="black")

    # >>> FIX 6: la vecchia logica spostava la finestra solo di +-1
    # per chiamata. Se pero' "index" saltava di piu' di una posizione
    # (per esempio uscendo dal menu impostazioni, dove facevi
    # counter = currentRadio), l'evidenziazione finiva fuori dalla
    # finestra e la lista sembrava "impazzita".
    # La finestra ora si RICALCOLA sempre a partire dall'indice,
    # e funziona anche con liste piu' corte di 6 voci (prima:
    # IndexError su names[listMenuStart + i]).
    rows = min(6, len(names))
    listMenuStart = max(0, min(index - rows + 1, listMenuStart, len(names) - rows))
    if index > listMenuStart + rows - 1:
        listMenuStart = index - rows + 1
    listMenuEnd = listMenuStart + rows - 1

    for i in range(rows):
        if( i == (index-listMenuStart)):
            menuindex = index
            invert(draw, 4, 4 + i*10, names[listMenuStart + i][0], False)
        else:
            draw.text((4, 4 + i*10), names[listMenuStart + i][0], font = font, fill = 255)


def formatSong(thestring):
    """
    Gets song info from mpc-provided string
    """

    if thestring[:4] == "http":
        return "......"

    pos = thestring.find(':')
    if (pos != -1):
        strTemp = thestring[-(len(thestring)-pos-2):]
        return strTemp
    else:
        return ("*" + thestring)


def currentSongLine():
    """
    >>> FIX 5: chiede il brano corrente direttamente a mpc.
    Prima: chooseRadio scriveva radio_info.txt UNA volta sola (e per
    giunta subito dopo "mpc play", quando lo stream non era ancora
    partito e "mpc current" era quasi sempre vuoto). showSong rileggeva
    quel file ogni 10 secondi, ma il contenuto non cambiava mai:
    risultato, il titolo restava congelato per tutta la durata
    dell'ascolto. Il timeout evita blocchi se mpd non risponde.
    """
    try:
        out = subprocess.run(["mpc", "current"], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def songInfo():
    """
    Shows current song info (over multiple lines if needed)
    NOTA: chi chiama questa funzione deve gia' possedere il lock
    (vedi FIX 3).
    """

    global currentRadio

    line = currentSongLine()

    with canvas(device) as draw:
        invert(draw, 0, 0, names[currentRadio][0], True)
        if not line:
            return
        title = formatSong(line).strip()
        if len(title)<19:
            draw.text((72-4*(len(title)), 20), title , fill="white")
        else:
            lineNum = len(title)
            if lineNum > 72:
                lineNum = 72
            thelist = [title[i:i+19] for i in range(0, lineNum, 19)]
            for i in range(len(thelist)):
                draw.text((81-4*(len(thelist[i].strip())), 19+10*i), thelist[i] , fill="white")


def chooseRadio(selection):
    """
    Change current radio
    """

    global names

    streamurl = names[selection][1]

    subprocess.run(["mpc", "clear"],stdout=subprocess.DEVNULL)
    subprocess.run(["mpc", "add", streamurl],stdout=subprocess.DEVNULL)
    subprocess.run(["mpc", "play"],stdout=subprocess.DEVNULL)
    # >>> FIX 5: eliminato os.system("mpc current > radio_info.txt"):
    # il titolo ora lo legge showSong/songInfo direttamente da mpc.
    # >>> FIX 3: songInfo tocca il display, quindi serve il lock.
    with sem:
        songInfo()


def vol_rotary():
    """
    Volume rotary encoder reading
    """

    global volPrevNextCode
    global volStore
    global rot_enc_table
    global vol_dt, vol_clk

    volPrevNextCode <<= 2;
    if (GPIO.input(vol_dt)):
        volPrevNextCode |= 0x02
    if (GPIO.input(vol_clk)):
        volPrevNextCode |= 0x01
    volPrevNextCode &= 0x0f

    if  (rot_enc_table[volPrevNextCode] ):
        volStore <<= 4
        volStore |= volPrevNextCode

        if ((volStore & 0xff) == 0x2b):
            return -1
        if ((volStore & 0xff) == 0x17):
            return 1

    return 0


def list_rotary():
    """
    Radio list rotary encoder reading
    """

    global listPrevNextCode
    global listStore
    global rot_enc_table
    global list_dt, list_clk

    listPrevNextCode <<= 2;
    if (GPIO.input(list_dt)):
        listPrevNextCode |= 0x02
    if (GPIO.input(list_clk)):
        listPrevNextCode |= 0x01
    listPrevNextCode &= 0x0f

    if  (rot_enc_table[listPrevNextCode] ):
        listStore <<= 4
        listStore |= listPrevNextCode

        if ((listStore & 0xff) == 0x2b):
            return -1
        if ((listStore & 0xff) == 0x17):
            return 1

    return 0


def list_push_callback(channel):
    """
    Radio list push button callback
    """

    global counter, currentRadio
    global settingsMode, settingsCount

    if  settingsMode == False:
        currentRadio = counter
        chooseRadio(counter)
    else:
        if settingsCount == 0:
            settingsMode = False
            counter = currentRadio
            # >>> FIX 4: tolto sleep(1). Ogni sleep dentro un callback
            # blocca TUTTI i callback GPIO (girano su un thread solo).
        elif settingsCount == 1:
            # >>> FIX 3: lock su ogni accesso al display
            with sem:
                with canvas(device) as draw:
                    draw.text((0, 26), "ricerca WPS...", fill="white")
            SSID = wpsConnect()
            with sem:
                with canvas(device) as draw:
                    if SSID != "none":
                        draw.text((0, 26), "* " + SSID, fill="white")
                    else:
                        draw.text((0, 26), "* No WPS found", fill="white")
        elif settingsCount == 2:
            with sem:
                with canvas(device) as draw:
                    draw.text((16, 26), ipAddress(), fill="white")
        elif settingsCount == 3:
            readRadioList()
            with sem:
                with canvas(device) as draw:
                    draw.text((0, 26), "    List updated.", fill="white")
        elif settingsCount == 4:
            with sem:
                with canvas(device) as draw:
                    draw.text((0, 26), "------ Standby.------", fill="white")
            subprocess.run(["mpc", "clear"],stdout=subprocess.DEVNULL)
        elif settingsCount == 5:
            with sem:
                with canvas(device) as draw:
                    draw.text((0, 26), "     Shutdown...", fill="white")
            sleep(2)
            with sem:
                with canvas(device) as draw:
                    draw.text((0, 26), " ", fill="white")
            subprocess.run(["sudo", "shutdown", "0"],stdout=subprocess.DEVNULL)


def vol_push_callback(channel):
    """
    Volume push button interrupt callback - Mute on/off
    """

    global volume, mute

    if mute:
        subprocess.run(["mpc", "volume", str(volume)],stdout=subprocess.DEVNULL)
    else:
        subprocess.run(["mpc", "volume", "0"],stdout=subprocess.DEVNULL)
    mute = not mute


def preset_callback(channel):
    """
    Radio button switch interrupt callback - Select preset
    """

    global currentRadio
    global preset_sw, preset_list

    # >>> FIX 4: qui c'era sleep(2)! I deviatori meccanici
    # autoescludenti fanno rimbalzare PIU' pin per una sola pressione
    # (quello premuto scende, quello rilasciato risale), quindi questo
    # callback veniva accodato piu' volte e ognuna dormiva 2 secondi:
    # ecco perche' i tasti rispondevano con secondi di ritardo.
    # Bastano due letture concordi a 80 ms di distanza per avere lo
    # stato assestato dei contatti.
    sleep(0.08)
    first = [GPIO.input(p[0]) for p in preset_sw]
    sleep(0.08)
    second = [GPIO.input(p[0]) for p in preset_sw]
    if first != second:
        return                       # contatti ancora in movimento

    for i in range(6):
        if first[i] == 0:
            if preset_list[i] != currentRadio:
                currentRadio = preset_list[i]
                chooseRadio(currentRadio)
            break


def settings_push_callback(channel):
    """
    Settings button interrupt callback - Start settings mode
    """

    global settingsMode, settingsCount

    settingsMode = True
    settingsCount = 0
    # >>> FIX 4: tolto sleep(.5) - il debouncing lo fa gia' il
    # parametro bouncetime di add_event_detect.

    # >>> FIX 3: lock anche qui
    with sem:
        with canvas(device) as draw:
            settingsMenu( draw, settingsCount)


def settingsMenu( draw, index):
    """
    Draws settings menu
    """

    global options

    font = ImageFont.load_default()

    for i in range(6):
        if( i == index):
            invert(draw, 4,  4 + index * 10, options[i], False)
        else:
            draw.text((4, 4 + i * 10), options[i], font = font, fill = 255)


def showSong():
    """
    Song information thread. It is the prevalent one.
    """

    while True:
        if not settingsMode:
            sem.acquire()
            songInfo()
            sem.release()
        # >>> FIX 2: nell'originale lo sleep(10) era DENTRO l'if.
        # Risultato: appena entravi nel menu impostazioni
        # (settingsMode = True) questo loop girava a vuoto milioni di
        # volte al secondo, saturando la CPU proprio mentre usavi il
        # menu. Lo sleep deve esserci a OGNI iterazione.
        sleep(10) if not settingsMode else sleep(0.2)


def listEncoder():
    """
    Radio list display/selection thread.
    """

    global counter
    global names, options
    global settingsMode, settingsCount
    global sem

    opLen = len(options)

    while True:
        if settingsMode == False:

            step = list_rotary()
            if (step):
                counter += step
                # NB: len(names) va riletto qui e non fissato prima
                # del while, altrimenti dopo un "Reload list" il
                # limite resterebbe quello vecchio.
                if counter > len(names) - 1:
                    counter = len(names) - 1
                if counter < 0:
                    counter = 0

                sem.acquire()
                with canvas(device) as draw:
                    radioList(device, draw, counter)
                sem.release()
        else:
            step = list_rotary()
            if (step):
                settingsCount += step
                if settingsCount > opLen - 1:
                    settingsCount = opLen - 1
                if settingsCount < 0:
                    settingsCount = 0

                sem.acquire()
                with canvas(device) as draw:
                    settingsMenu(draw, settingsCount)
                sem.release()

        # >>> FIX 1 (il piu' importante): questo while girava SENZA
        # nessuna pausa, consumando il 100% di un core. Insieme al
        # gemello volEncoder faceva due busy-loop che, per via del GIL
        # di Python (un solo thread alla volta esegue bytecode),
        # rubavano il tempo a callback, display e chiamate mpc: da qui
        # la lentezza generale. Un encoder ruotato a mano genera
        # transizioni dell'ordine dei millisecondi, quindi campionare
        # ogni 1 ms non perde nessuno scatto.
        sleep(0.001)


def volEncoder():
    """
    Volume variation thread
    """

    global volume, mute

    while True:
        step = vol_rotary()

        if (step):
            volume += step * 5
            if volume > 100:
                volume = 100
            if volume < 0:
                volume = 0

            # >>> FIX 9: se giri il volume mentre sei in mute, il
            # comando sotto riattiva l'audio ma il flag "mute"
            # restava True: alla pressione successiva del pulsante
            # avresti "smutato" qualcosa che gia' suonava.
            if mute:
                mute = False

            sem.acquire()
            with canvas(device) as draw:
                draw.text((48, 20), "--" + str(volume) + "--", fill="white")
            sem.release()
            subprocess.run(["mpc", "volume", str(volume)],stdout=subprocess.DEVNULL)

        # >>> FIX 1: stessa cosa dell'altro encoder.
        sleep(0.001)


# Setup GPIOs
# ----------------------------------------------------------
GPIO.setmode(GPIO.BCM)
GPIO.setup(list_clk, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(list_dt, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(list_sw, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(vol_clk, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(vol_dt, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(vol_sw, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(conf_push, GPIO.IN, pull_up_down=GPIO.PUD_UP)
for s in range(6):
    GPIO.setup(preset_sw[s][0], GPIO.IN, pull_up_down=GPIO.PUD_UP)

# Initial tasks. Set volume low, just in case
# ----------------------------------------------------------
readRadioList()
result = subprocess.run(["mpc", "volume", "20"],stdout=subprocess.DEVNULL)

# Startup screen
# ----------------------------------------------------------
sem.acquire()
with canvas(device) as drw:
    drw.text((0, 14), " SenzaFiloDiffusione", fill="white")
    drw.text((0, 22), "---------------------", fill="white")
    drw.text((0, 30), "     by Simon T.", fill="white")
sem.release()

# Read radio buttons at startup
# ----------------------------------------------------------
preset_callback(0)
counter = currentRadio        # >>> FIX 7: cursore allineato alla radio

# Enable GPIO interrupts for push and radio buttons
# ----------------------------------------------------------
GPIO.add_event_detect(list_sw, GPIO.FALLING, callback=list_push_callback, bouncetime=300)
GPIO.add_event_detect(vol_sw, GPIO.FALLING, callback=vol_push_callback, bouncetime=300)
GPIO.add_event_detect(conf_push, GPIO.FALLING, callback=settings_push_callback, bouncetime=300)
for s in range(6):
    GPIO.add_event_detect(preset_sw[s][0],GPIO.FALLING, callback=preset_callback, bouncetime=400)

# Start threads
# ----------------------------------------------------------
# >>> FIX 10: daemon=True fa morire i thread insieme al programma
# (prima un Ctrl-C lasciava il processo appeso), e il blocco
# try/finally rilascia i GPIO in uscita.
threadSong = threading.Thread(target = showSong, daemon = True)
threadSong.start()
threadMenu = threading.Thread(target = listEncoder, daemon = True)
threadMenu.start()
threadVol = threading.Thread(target = volEncoder, daemon = True)
threadVol.start()
# >>> nota: il thread "presetRead" originale e' stato eliminato:
# leggeva i 6 pin ogni secondo in preset_sw[i][1], ma quei valori
# venivano comunque riletti e sovrascritti da preset_callback prima
# dell'uso. Era un thread che non serviva a nulla.

try:
    while True:
        sleep(1)
except KeyboardInterrupt:
    pass
finally:
    GPIO.cleanup()
