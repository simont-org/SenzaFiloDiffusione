from RPi import GPIO
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1106
from PIL import ImageFont 
from time import sleep
import threading
import os
import subprocess
import socket

# Radio list sliding window
listMenuStart = 0
listMenuEnd = 5
counter = 0
currentRadio = 7
menuindex = 0
names: list=[]

# Encoder reading
listPrevNextCode = 0
listStore = 0
volPrevNextCode = 0
volStore = 0
rot_enc_table: list = [0,1,1,0,1,0,0,1,1,0,0,1,0,1,1,0]

volume = 20
mute = False

# Settings menu
settingsMode = False
settingsCount = 0
options: list=["<-- Back", "WiFi WPS", "Show IP address",
         "Reload list", "Standby", "Shutdown"]

#GPIO pins
list_clk = 27
list_dt = 22
list_sw = 17
vol_clk = 4
vol_dt = 18
vol_sw = 23
conf_push = 12
preset_sw: list = [[21,0],[20,0],[16,0],[13,0],[19,0],[26,0]]

# Radio assigned to radio buttons
preset_list: list = [0,1,2,3,4,5]

# Setup OLED display
serial = i2c(port=1, address=0x3C)
device = sh1106(serial, rotate=0)

# File for song info
songFile = open('/home/pi/WoodStream/radio_info.txt', 'r')

# Lock for display competition
sem =  threading.Lock()

# Reads radio URLS and names from text file (format: Radio name|Radio URL)
def readRadioList():
    global names
    names=[]
    listFile = open('/home/pi/WoodStream/radio_list.txt', 'r')
    line = listFile.readlines()
    for r in range(len(line)):
        names.append(line[r].replace('\n','').split('|'))
        print(names[r])
    listFile.close()

def invert(draw,x,y,text, center):
    font = ImageFont.load_default()
    draw.rectangle((x, y, x+120, y+10), outline=255, fill=255)
    if (center):
        x=76-4*len(text)
    draw.text((x, y), text, font=font, outline=0,fill="black")

def menu(dev, draw, index):
    global menuindex
    global listMenuStart, listMenuEnd
    global names

    
    font = ImageFont.load_default()
    draw.rectangle(dev.bounding_box, outline="white", fill="black")
    if index > listMenuEnd:
        listMenuEnd += 1
        listMenuStart += 1
    elif index < listMenuStart:
        listMenuEnd -= 1
        listMenuStart -= 1
    for i in range(6):
        if( i == (index-listMenuStart)):
            menuindex = index
            invert(draw, 2, (index-listMenuStart)*10, names[listMenuStart + i][0], False)
        else:
            draw.text((2, i*10), names[listMenuStart + i][0], font=font, fill=255)


def optionsMenu( draw, index):
    global options
    
    font = ImageFont.load_default()
    
    for i in range(6):
        if( i == index):
            invert(draw, 2,  index*10, options[i], False)
        else:
            draw.text((2, i*10), options[i], font=font, fill=255)

def ip_address():
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sk.connect(("8.8.8.8", 80))
    ip = (sk.getsockname()[0])
    sk.close()
    return str(ip)

def formatSong(thestring):
    if thestring[:4] == "http":
        return "......"
    pos = thestring.find(':')
    if (pos != -1):
        strTemp = thestring[-(len(thestring)-pos-2):]
        return strTemp
    else:
        return ("*" + thestring)

def songInfo():
    global songFile, currentRadio
    lines = songFile.readlines()
    if len(lines) > 0:

        songFile.seek(0)
        title = formatSong(lines[0]).strip()
        
        with canvas(device) as draw:
            invert(draw, 0, 0, names[currentRadio][0], True)
            if len(title)<19:
                draw.text((72-4*(len(title)), 20), title , fill="white")
            else:
                lineNum = len(title)
                if lineNum > 72:
                    lineNum = 72
                thelist = [title[i:i+19] for i in range(0, lineNum, 19)]
                for i in range(len(thelist)):   
                    draw.text((81-4*(len(thelist[i].strip())), 19+10*i), thelist[i] , fill="white")
        

def menu_operation(selection):
    global songFile
    global names
    
    streamurl = names[selection][1]

    subprocess.run(["mpc", "clear"],stdout=subprocess.DEVNULL)
    subprocess.run(["mpc", "add", streamurl],stdout=subprocess.DEVNULL)
    subprocess.run(["mpc", "play"])
    os.system("mpc current > /home/pi/WoodStream/radio_info.txt ")
    songInfo()

def vol_rotary():
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
    global counter, currentRadio
    global settingsMode, settingsCount
    
    if  settingsMode == False:
        currentRadio = counter
        menu_operation(counter)
    else:
        print(settingsCount)
        if settingsCount == 0:
            settingsMode = False
            counter = currentRadio
            sleep(1)
        #elif settingsCount == 1:
            #code for wps connection
        elif settingsCount == 2:
            with canvas(device) as draw:
                draw.text((16, 26), ip_address(), fill="white")
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
            subprocess.run(["sudo", "shutdown", "0"],stdout=subprocess.DEVNULL)
            with canvas(device) as draw:
                draw.text((0, 26), " ", fill="white")


def vol_push_callback(channel):  
    global volume, mute
    if mute:
        subprocess.run(["mpc", "volume", str(volume)],stdout=subprocess.DEVNULL)
    else:
        print("mute")
        subprocess.run(["mpc", "volume", "0"],stdout=subprocess.DEVNULL)
    mute = not mute

def preset_callback(channel):
    global currentRadio
    global preset_sw
    
    for i in range(6):
        preset_sw[i][1] = GPIO.input(preset_sw[i][0])
    sleep(2)
    
    for i in range(6):
        if (preset_sw[i][1]) == 0:
            if i != currentRadio:
                menu_operation(i)
                currentRadio = i
                break
    songInfo()
    

def showSettings(channel):
    global settingsMode, settingsCount

    settingsMode = True
    settingsCount = 0
    sleep(.5)
    
    with canvas(device) as draw:
        optionsMenu( draw, settingsCount)
                    

def showSong():
    while True:
        if not settingsMode:
            sem.acquire()
            songInfo()
            sem.release()
            sleep(10) 

def listEncoder():
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
                    menu(device, draw, counter)
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
                    optionsMenu(draw, settingsCount)
                sem.release()       
            
def volEncoder():
    global volume
    
    while True:
        step = vol_rotary()
     
        if (step):
            volume += step * 5
            if volume > 100:
                volume = 100
            if volume < 0:
                volume = 0
            print(str(volume) + " ")

            sem.acquire()
            with canvas(device) as draw:
                draw.text((48, 20), "--" + str(volume) + "--", fill="white")
            sem.release()
            subprocess.run(["mpc", "volume", str(volume)],stdout=subprocess.DEVNULL)

def presetRead():
    global preset_sw    
    while True:
        for i in range(6):
            preset_sw[i][1] = GPIO.input(preset_sw[i][0])
        sleep (1)

#if __name__ == "__main__":

# Setup GPIOs
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

readRadioList()
result = subprocess.run(["mpc", "volume", "20"],stdout=subprocess.DEVNULL)


sem.acquire()
with canvas(device) as drw:
    drw.text((0, 14), " SenzaFiloDiffusione", fill="white")
    drw.text((0, 22), "---------------------", fill="white")
    drw.text((0, 30), "     by Simon T.", fill="white")
sem.release()

preset_callback(0)

GPIO.add_event_detect(list_sw, GPIO.FALLING , callback=list_push_callback, bouncetime=300)
GPIO.add_event_detect(vol_sw, GPIO.FALLING , callback=vol_push_callback, bouncetime=300)  
GPIO.add_event_detect(conf_push, GPIO.FALLING , callback=showSettings, bouncetime=300)  
for s in range(6):
    GPIO.add_event_detect(preset_sw[s][0],GPIO.FALLING, callback=preset_callback, bouncetime=300)

threadSong = threading.Thread(target = showSong) 
threadSong.start()
threadMenu = threading.Thread(target = listEncoder) 
threadMenu.start()
threadVol = threading.Thread(target = volEncoder) 
threadVol.start()
threadPres = threading.Thread(target = presetRead) 
threadPres.start()


