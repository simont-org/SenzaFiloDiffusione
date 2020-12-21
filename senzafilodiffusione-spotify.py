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

# Sliding windows parameters for lists
# ----------------------------------------------------------
listMenuStart = 0
listMenuEnd = 5
songMenuStart = 0
songMenuEnd = 5
counter = 0
currentRadio = 7
currentSong = 0
menuindex = 0
names: list = []
songs: list = []
songMode = False
listMode = True

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

# File for song info
# ----------------------------------------------------------
songFile = open('/home/pi/WoodStream/radio_info.txt', 'r')

# Lock for display competition
# ----------------------------------------------------------
sem =  threading.Lock()

def getWindow(index, maxlen):
    """
    Realign list sliding window
    """
    
    tempindex = index
    if (index + 5) > maxlen:
        tempindex = maxlen - 5
        if tempindex < 0:
                        newindex = 0
    start = tempindex
    end = tempindex + 5

    return [start, end]
    
def readRadioList():
    """
    Reads playlists names from text file
    """
    
    global names
    
    names=[]
    listFile = open('/home/pi/WoodStream/radio_list.txt', 'r')
    line = listFile.readlines()
    for r in range(len(line)):
        names.append(line[r].replace('\n','').split('|'))
    names.sort()
    listFile.close()

def readSongList():
    """
    Reads playlists names from text file
    """
    
    global songs
    
    songs=[]
    listFile = open('/home/pi/WoodStream/playlist.txt', 'r')
    line = listFile.readlines()
    for r in range(len(line)):
        songs.append(line[r].replace('\n','').split('|'))
    listFile.close()
    
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
    # scan networks on interface wlan0, to see some nice networks
    subprocess.check_output(["wpa_cli", "-i", "wlan0", "scan"])       
    sleep(1);
    
    #get and decode results
    wpa = subprocess.check_output(["wpa_cli", "-i", "wlan0", "scan_results"]).decode("UTF-8")
    
    #parse response to get MAC address of router that has WPS-PBC state
    active_spot_reg = re.search("(([\da-f]{2}:){5}[\da-f]{2})(.*?)\[WPS-PBC\]", wpa)
    
    #check if found any
    if not (active_spot_reg is None):
        if active_spot_reg.group(1):
            
            #connect via wps_pbc
            subprocess.check_output(["wpa_cli", "-i", "wlan0", "wps_pbc", active_spot_reg.group(1)])
            SSID = active_spot_reg.group(5)
            
            print(active_spot_reg.group(1) + " " + SSID)
            print(wpa)
    
    return(SSID)


def radioList(dev, draw, index):
    """
    Draws radio list with a sliding window of 6 rows
    """

    global menuindex
    global listMenuStart, listMenuEnd
    global songMenuStart, songMenuEnd
    global names, songs
    global songMode, listMode
    
    font = ImageFont.load_default()

    if listMode:
        #print(listMenuStart, listMenuEnd)
        if index > listMenuEnd:
            listMenuEnd += 1
            listMenuStart += 1
        elif index < listMenuStart:
            listMenuEnd -= 1
            listMenuStart -= 1
    elif songMode:
        #print(songMenuStart, songMenuEnd)
        if index > songMenuEnd:
            songMenuEnd += 1
            songMenuStart += 1
        elif index < songMenuStart:
            songMenuEnd -= 1
            songMenuStart -= 1

    for i in range(6):
        #print (i) 
        if listMode:
            start = listMenuStart
            theLine = names[start + i][0]
        elif songMode:
            start = songMenuStart
            theLine = songs[start + i][0]
            
        if( i == (index-start)):
            menuindex = index
            invert(draw, 4, 4+(index-start)*10, theLine, False)
        else:
            draw.text((4, 4+i*10), theLine, font=font, fill=255)


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


def formatSong(thestring):
    """
    Gets song info from mpc-provided string   
    """

    #if it starts with "http" there isn't any song info, so display just dots
    if thestring[:4] == "http":
        return "......"
    
    #otherwise usually the string is <radio name>:<song info> so strip the radio name
    pos = thestring.find(':')
    if (pos != -1):
        strTemp = thestring[-(len(thestring)-pos-2):]
        return strTemp
    else:
        return ("*" + thestring)


def songInfo():
    """
    Shows current song info (over multiple lines if needed)  
    """
    
    global songFile, currentRadio
    
    lines = songFile.readlines()
    if len(lines) > 0:

        songFile.seek(0)
        title = formatSong(lines[0]).strip()
        
        with canvas(device) as draw:
            invert(draw, 0, 0, names[currentRadio][0][:24], False)
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
    
    global songFile, songMenuStart, songMenuEnd
    global names
    
    streamurl = str(names[selection][0]).strip().replace("'", "'\\''")
    #print ("URL: --" + streamurl + "--")
    subprocess.run(["mpc", "clear"],stdout=subprocess.DEVNULL)
    
    command = "mpc load '" + streamurl + "'"
    p = subprocess.Popen(command, universal_newlines=True, shell=True)
    retcode = p.wait()
    
    command = "mpc playlist '" + streamurl + "' > /home/pi/WoodStream/playlist.txt"
    p = subprocess.Popen(command, universal_newlines=True, shell=True)
    retcode = p.wait()
    
    subprocess.run(["mpc", "play"])
    command = "mpc current > /home/pi/WoodStream/radio_info.txt"
    p = subprocess.Popen(command, universal_newlines=True, shell=True)
    retcode = p.wait()
    
    readSongList()
    songInfo()
    currentSong = 0
    songMenuStart = 0
    songMenuEnd = 5


def vol_rotary(code, store):
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

    # If valid then store as 16 bit data.
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
    Function is duplicated from the volume one because
        code and store variables must be kept separated for each rotary
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

    # If valid then store as 16 bit data.
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
    List encoder push button callback
    If in list mode, select playlist and starts playing using chooseRadio()
    If in song mode, play selected song.
    If in settings list, activates the various options 
    """
 
    global counter, currentRadio
    global settingsMode, settingsCount
    
    if  settingsMode == False:
        if listMode:
            currentRadio = counter
            chooseRadio(currentRadio)
        elif songMode:
            currentSong = counter + 1
            subprocess.run(["mpc", "play", str(currentSong)],stdout=subprocess.DEVNULL)
    else:
        print(settingsCount)
        if settingsCount == 0:
            settingsMode = False
            counter = currentRadio
            sleep(1)
        elif settingsCount == 1:
            SSID = wpsConnect()
            if SSID != "none":   # No WPS access detected
                with canvas(device) as draw:
                    draw.text((0, 26), "* " + SSID, fill="white")
            else:                # WPS access detected
                with canvas(device) as draw:
                    draw.text((0, 26), "* No WPS found", fill="white")
        elif settingsCount == 2:
            with canvas(device) as draw:
                draw.text((16, 26), ipAddress(), fill="white")
        elif settingsCount == 3:
            readRadioList()
            with canvas(device) as draw:
                draw.text((0, 26), "    List updated.", fill="white")
        elif settingsCount == 4:
            with canvas(device) as draw:
                draw.text((0, 26), "------ Standby.------", fill="white")
            subprocess.run(["mpc", "clear"],stdout=subprocess.DEVNULL)
            sleep(3)
            with canvas(device) as draw:
                draw.text((0, 26), " ", fill="white")
        elif settingsCount == 5:
            with canvas(device) as draw:
                draw.text((0, 26), "     Shutdown...", fill="white")
            sleep(2)
            with canvas(device) as draw:
                draw.text((0, 26), " ", fill="white")
            subprocess.run(["sudo", "shutdown", "0"],stdout=subprocess.DEVNULL)


def vol_push_callback(channel):
    """
    Volume push button interrupt callback
    Play on/off.
    """
    
    global volume, mute
    if mute:
        subprocess.run(["mpc", "play"],stdout=subprocess.DEVNULL)
    else:
        subprocess.run(["mpc", "pause"],stdout=subprocess.DEVNULL)
    mute = not mute


def preset_callback(channel):
    """
    Radio button switch interrupt callback
    Select preset
    """
    
    global currentRadio, currentSong, counter
    global preset_sw, preset_list
    global listMenuStart, listMenuEnd
    global songMenuStart, songMenuEnd
    global songMode, listMode
    
    for i in range(4,6):
        preset_sw[i][1] = GPIO.input(preset_sw[i][0])
    sleep(1)
    
    if preset_sw[4][1] == 0:    # playlists mode switch
        listMode = True
        songMode = False
        winList = getWindow(currentRadio, len(names))
        counter = currentRadio
        listMenuStart = winList[0]
        listMenuEnd = winList[1]
        
        sem.acquire()   
        with canvas(device) as draw:
            menu(device, draw, counter)
        sem.release()
        
    if preset_sw[5][1] == 0:    # songs mode switch
        songMode = True
        listMode = False
        winList = getWindow(currentSong, len(songs))
        songMenuStart = winList[0]
        songMenuEnd = winList[1]
        counter = songMenuStart - 1
        
        sem.acquire()   
        with canvas(device) as draw:
            menu(device, draw, counter)
        sem.release()
        
        command = "mpc playlist '" + names[currentRadio][0].strip().replace("'", "'\\''") + "' > /home/pi/WoodStream/playlist.txt"
        p = subprocess.Popen(command, universal_newlines=True, shell=True)
        retcode = p.wait()
        
        readSongList()
 
 
def settings_push_callback(channel):
    """
    Radio button switch interrupt callback
    Start settings mode
    """
    
    global settingsMode, settingsCount

    settingsMode = True
    settingsCount = 0
    sleep(.5) # A little time for debouncing
    
    with canvas(device) as draw:
        settingsMenu( draw, settingsCount)
  

def showSong():
    """
    Song information thread. It is the prevalent one.
    """
    
    while True:
        if not settingsMode:
            command = "mpc current > /home/pi/WoodStream/radio_info.txt"
            p = subprocess.Popen(command, universal_newlines=True, shell=True)
            retcode = p.wait()
            sem.acquire()
            songInfo()
            sem.release()
            sleep(10) 


def listEncoder():
    """
    Radio list display/selection thread. It shows the list
    only when the encoder is moved
    """

    global counter
    global names, options
    global settingsMode, settingsCount
    global sem
    
    nameLen = len(names)
    opLen = len(options)
    
    while True:
        if settingsMode == False:
         
            step = list_rotary()
            if (step):
                counter += step
                if counter > nameLen - 1:
                    counter = nameLen - 1
                if counter < 0:
                    counter = 0
                print(str(counter) + " ")
            
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


def volEncoder():
    """
    Volume variation thread
    """

    global volume
    
    while True:
        step = vol_rotary()
     
        if (step):
            volume += step * 5
            if volume > 100:
                volume = 100
            if volume < 0:
                volume = 0

            sem.acquire()
            with canvas(device) as draw:
                draw.text((48, 20), "--" + str(volume) + "--", fill="white")
            sem.release()
            subprocess.run(["mpc", "volume", str(volume)],stdout=subprocess.DEVNULL)


def presetRead():
    """
    Preset switch thread. Polls the 6 switch current situation
    """

    global preset_sw
    global songMode, listMode
    
    while True:
        for i in range(4,6):
            preset_sw[i][1] = GPIO.input(preset_sw[i][0])
        sleep (1)
        if preset_sw[4][1] == 0:
            listMode = True
            songMode = False
        if preset_sw[5][1] == 0:
            songMode = True
            listMode = False


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
for s in range(4,6):
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
    drw.text((0, 22), "----(spotified)-----", fill="white")
    drw.text((0, 30), "     by Simon T.", fill="white")
sem.release()

# Enable GPIO interrupts for push and radio buttons
# ----------------------------------------------------------
GPIO.add_event_detect(list_sw, GPIO.FALLING, callback=list_push_callback, bouncetime=300)
GPIO.add_event_detect(vol_sw, GPIO.FALLING, callback=vol_push_callback, bouncetime=300)  
GPIO.add_event_detect(conf_push, GPIO.FALLING, callback=settings_push_callback, bouncetime=300)  
for s in range(4,6):
    GPIO.add_event_detect(preset_sw[s][0],GPIO.FALLING, callback=preset_callback, bouncetime=300)

# Start threads
# ----------------------------------------------------------
threadSong = threading.Thread(target = showSong) 
threadSong.start()
threadMenu = threading.Thread(target = listEncoder) 
threadMenu.start()
threadVol = threading.Thread(target = volEncoder) 
threadVol.start()
threadPres = threading.Thread(target = presetRead) 
threadPres.start()


