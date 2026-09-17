# SenzaFiloDiffusione

A 1970s Italian *Filodiffusione* receiver, gutted and rebuilt as a Wi-Fi web radio
player driven by a Raspberry Pi — keeping the original wooden case, aluminium front
panel, speaker and, most importantly, the six mechanical preset buttons.

Built for the [element14 Project14 "Recycle & Retrofit" challenge](https://community.element14.com/challenges-projects/project14/recycleretrofit/b/blog/posts/senzafilodiffusione).
The article covers the build with photos and video; this README is the technical
reference for anyone who wants to reproduce it or read the code.

<!-- TODO: add a photo of the finished unit here, e.g. ![SenzaFiloDiffusione](docs/front.jpg) -->

---

## What "SenzaFiloDiffusione" means

In Italy the national broadcaster (*RAI*) and the phone carrier (then *Telecom*, now
*TIM*) used to distribute six radio channels over the telephone line. The service was
called **Filodiffusione** — literally "wire broadcast" — and ran from the late 1950s
until a few years ago. You could rent a dedicated receiver, and the device itself
ended up being called *a Filodiffusione*.
([Wikipedia, in Italian](https://it.wikipedia.org/wiki/Filodiffusione))

Since the wired broadcast becomes a wireless one, *filo* (wire) becomes
**senza filo** (wireless).

---

## Features

- Six original mechanical buttons reused as radio presets. They are self-excluding
  by a mechanical lever — the actual ancestor of the "radio button" UI metaphor.
- Rotary encoder to browse the station list, with push to select.
- Rotary encoder for volume, with push to mute.
- 1.3" OLED display showing the current station and, when the stream provides it,
  the current track.
- Settings menu on a dedicated rear button: Wi-Fi setup via WPS, show IP address,
  reload station list, standby, shutdown.
- Station list editable over the network from a browser, no SSH required.
- Original internal speaker driven by a class-D amplifier; rear stereo jack and
  HDMI replicated on the back panel.
- Second application variant that plays Spotify playlists through Mopidy.

---

## Hardware

### Bill of materials

| Part | Notes |
|---|---|
| Raspberry Pi 3B+ | Provided by element14 for the challenge. Any Pi with Wi-Fi works. |
| 2 × KY-040 rotary encoder | Buy the version on a breakout PCB: it brings out Dupont-friendly pins and has the pull-ups already fitted. |
| 1.3" OLED 128×64, I²C | Mine uses an **SH1106** controller. The visually identical 0.96" ones are usually **SSD1306** — same wiring, different driver class in code. |
| TPA3118 class-D amplifier board | 30 W stereo / 60 W mono, 4.5–26 V. Also exposes a pair of mute pins. |
| 5 V 3 A USB PSU | For the Pi. |
| 12 V 2 A PSU | For the amplifier. |
| Stripboard + 7 × 10 kΩ resistors | Pull-ups for the six preset switches and the settings button. |
| 2 × 1 kΩ resistors | Passive stereo-to-mono summing for the speaker branch. |
| HDMI male-to-female short extension | Replicates HDMI on the back panel. |
| 3.5 mm female jack, FASTON terminals, shielded cable | Rear audio output and speaker connection. |
| LED + 330 Ω resistor | Activity LED behind the original indicator hole. |

Salvaged from the original device: the wooden case, the aluminium front panel, the
8 Ω speaker and the six-button switch assembly. The original potentiometers, PCB
bracket and 220→12 V AC transformer were not reused.

A small 3D-printed frame masking the aluminium edge around the display is included
as `frame.stl`.

### Wiring

GPIO numbering is **BCM**. The encoders are fed from 5 V; the display and the
pull-up network run from 3.3 V.

| Function | Signal | BCM pin |
|---|---|---|
| List encoder | CLK | 27 |
| List encoder | DT | 22 |
| List encoder | SW (push) | 17 |
| Volume encoder | CLK | 4 |
| Volume encoder | DT | 18 |
| Volume encoder | SW (push) | 23 |
| Settings button (rear) | — | 12 |
| Preset 1 … 6 | — | 21, 20, 16, 13, 19, 26 |
| OLED | SDA | 2 |
| OLED | SCL | 3 |
| Activity LED | — | 25 |

The KY-040 push buttons already have a pull-up on the breakout board. The six preset
switches and the settings button need an external pull-up each — that is what the
piece of stripboard is for. In software every input is also configured with
`GPIO.PUD_UP`, so the external resistors are belt-and-braces for the long,
unshielded runs inside the case.

To move the Pi activity LED to GPIO 25, see
[this forum thread](https://www.raspberrypi.org/forums/viewtopic.php?t=158293).

### Audio chain

The Pi's 3.5 mm analog output feeds a Y cable. One branch goes straight to the rear
panel jack; the other sums left and right through two 1 kΩ resistors into the mono
amplifier input, which drives the original 8 Ω speaker.

The TPA3118 mute pins are wired to a switch: closing it silences the internal
speaker, which is what you want when something is plugged into the rear jack.

---

## Software setup

### 1. Base system

Flash Raspberry Pi OS (formerly Raspbian) with the
[Raspberry Pi Imager](https://www.raspberrypi.org/software/). Enable SSH, VNC and
I²C via `raspi-config`.

### 2. Audio output

This is the step that costs the most time. The Pi defaults to HDMI audio, and this
build has no HDMI sink attached — so out of the box you get silence and no error.
Force the analog jack in `raspi-config` and check the `audio_output` section of
`/etc/mpd.conf`. Reference:
[Raspberry Pi audio configuration](https://www.raspberrypi.org/documentation/configuration/audio-config.md).

### 3. MPD and mpc

```bash
sudo apt install mpd mpc
```

MPD was chosen over VLC and XMMS because it is a real daemon rather than a desktop
player with a CLI bolted on, it handles SHOUTcast/Icecast/mp3/m3u8/pls, and it
exposes stream metadata. Everything in this project drives it through `mpc`:

```bash
mpc add http://sc2.radiocaroline.net:8040/   # queue a stream
mpc play                                     # play
mpc stop                                     # stop
mpc clear                                    # empty the queue
mpc volume 50                                # set volume (relative to ALSA master)
mpc current                                  # current stream / track info
```

Worth knowing: MPD is a **separate daemon**. Stopping the Python application does
not stop the music — the display goes dark but MPD keeps playing whatever it was
last told to play. Use `mpc stop` for silence.

Full documentation: <https://www.musicpd.org/doc/html/user.html>

### 4. Python dependencies

```bash
sudo apt install python3-pil python3-rpi.gpio
sudo pip3 install luma.oled
```

- [`RPi.GPIO`](https://sourceforge.net/p/raspberry-gpio-python/wiki/Home/) — GPIO,
  including interrupt callbacks with hardware-independent debouncing.
- [`luma.oled`](https://github.com/rm-hull/luma.oled) — SH1106/SSD1306 driver built
  on PIL. Setup walkthrough:
  [codelectron tutorial](http://codelectron.com/setup-oled-display-raspberry-pi-python/).

If your display is SSD1306 rather than SH1106, change the import and the device
constructor accordingly.

### 5. Install the application

```bash
mkdir -p /home/pi/senzafilodiffusione
cd /home/pi/senzafilodiffusione
# copy the chosen .py file here as senzafilodiffusione.py, plus radio_list.txt
chmod 664 radio_list.txt
```

The application reads its station list from the path in the `RADIO_LIST_FILE`
constant near the top of the source. If you install somewhere other than
`/home/pi/senzafilodiffusione`, change that one line.

Test it in the foreground before setting up the service:

```bash
python3 -u senzafilodiffusione.py
```

The splash screen should appear, a station should start, and the six front buttons
should jump to their presets. `Ctrl-C` to quit.

---

## Autostart at boot

The device has no keyboard and is meant to behave like an appliance, so the
application has to come up on its own.

Three mechanisms exist on Raspberry Pi OS — `/etc/rc.local`, a `crontab @reboot`
entry, and a systemd unit. **Use systemd.** The others give you no control over
ordering, no restart on failure, and no logs.

Create `/etc/systemd/system/senzafilodiffusione.service`:

```ini
[Unit]
Description=SenzaFiloDiffusione web radio player
Wants=network-online.target
After=network-online.target sound.target mpd.service

[Service]
Type=idle
WorkingDirectory=/home/pi/senzafilodiffusione
ExecStart=/usr/bin/python3 -u /home/pi/senzafilodiffusione/senzafilodiffusione.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable it and watch it start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable senzafilodiffusione.service
sudo systemctl start senzafilodiffusione.service
journalctl -u senzafilodiffusione.service -f
```

Later, to inspect what happened during the current boot:

```bash
journalctl -u senzafilodiffusione.service -b --no-pager
```

Notes on the unit file:

- **`-u`** disables Python's output buffering. Without it, `print()` output sits in
  a buffer and never reaches the journal — the service looks silent even when it is
  saying something.
- **`Type=idle`** makes systemd wait for the boot job queue to drain (up to about
  5 seconds) before running the command. With the `After=` dependencies above it is
  largely redundant, but it is harmless and adds a little margin.
- **`After=mpd.service`** only guarantees that systemd *started* MPD first, not that
  MPD is ready to accept commands. In practice the startup splash covers the gap; if
  a cold boot ever fails on the first `mpc` call, add `ExecStartPre=/bin/sleep 3`.
- **`Restart=on-failure`** recovers from a crash. Combined with the
  `finally: GPIO.cleanup()` block in the code, a restart finds the pins in a sane
  state.
- The service runs as **root** (no `User=` line). Running it as `pi` would be
  tidier — that user is in the `gpio` and `i2c` groups and has passwordless sudo for
  the *Shutdown* menu entry — but it has not been tested on this build.

Put the unit in `/etc/systemd/system/`, not `/lib/systemd/system/`. The latter
belongs to the package manager and its contents can be overwritten by a system
upgrade.

**Nothing else belongs in `rc.local`.** Older setups of this project started a
`watch -n 1 'mpc current > radio_info.txt'` poller there; the current code queries
`mpc current` directly and that file no longer exists. Leaving the poller in place
rewrites a file on the SD card 86,400 times a day for nothing.

---

## Configuration

### Station list

`radio_list.txt`, one station per line:

```
<station name>|<stream URL>
```

A station mapped to one of the six front buttons is prefixed with its preset number
and a dot. The prefix is parsed at load time, used to build the preset map, and
stripped before the name is shown on the display:

```
1.San Marino Classic|https://d18ufyp3q60j7u.cloudfront.net/radio-ch01/radio-ch02/playlist.m3u8
2.Radio Caroline|http://sc2.radiocaroline.net:8040/;
RDS|http://stream.rds.radio/audio/rds.stream_aac/playlist.m3u8
```

Lines without a prefix are reachable from the list encoder only.

Finding stream URLs is tedious — station websites hide them behind players.
[fmstream.org](http://fmstream.org/index.php) is ugly and enormously useful.

### Editing the list over the network

`edit_list.php` is a one-screen textarea served by lighttpd, so the list can be
edited from any browser on the LAN without SSH:

```bash
sudo apt install lighttpd php-cgi
sudo lighttpd-enable-mod fastcgi fastcgi-php
sudo systemctl restart lighttpd
```

Drop `edit_list.php` in the web root and point it at your `radio_list.txt`. The web
server needs write access to that file: give it mode `664` and put the web server
user in the owning group. Do **not** use `777` — it works, but it lets any local
account rewrite the file.

After saving, pick **Reload list** in the settings menu on the device.

> This editor has no authentication and no input validation. It is a LAN-only
> convenience. Do not expose it to the internet.

---

## Using it

**Front panel.** The six buttons jump straight to their preset. Turning the list
encoder shows the station list; the display returns to the now-playing screen on its
own if you stop turning, keeping your position in the list. Pushing the encoder
selects the highlighted station. The volume encoder works the same way, showing a
volume indicator that fades out; pushing it mutes.

**Settings menu**, from the rear button:

| Entry | What it does |
|---|---|
| `<-- Back` | Leave the menu |
| `WiFi WPS` | Join a WPS-enabled router — no keyboard needed. Credentials are written to `/etc/wpa_supplicant/wpa_supplicant.conf` and persist. |
| `Show IP address` | Shows the current address, for SSH/VNC or the list editor |
| `Reload list` | Re-reads `radio_list.txt` after an edit |
| `Standby` | Stops playback, mutes and blanks the display. Turning the list encoder wakes it and restores the previous station and volume. |
| `Shutdown` | Says goodbye and runs `sudo shutdown 0` |

---

## Repository layout

```
Radio version/
  senzafilodiffusione.py              original application
  variants/
    senzafilodiffusione_fixed.py      same structure, bugs fixed and annotated
    senzafilodiffusione_rewrite.py    event queue + state machine  <- running on the device
  radio_list.txt                      station list
  edit_list.php                       browser-based list editor
Spotify version/
  senzafilodiffusione-spotify.py
  edit_playlist.php
frame.stl                             3D-printed bezel for the display opening
```

---

## Code versions

Three generations of the radio application are kept here, deliberately. They
document how the project evolved, and the middle one is annotated well enough to be
worth reading on its own.

**`senzafilodiffusione.py`** — the original, written while learning Python during
the build. It works, and it is the version the element14 article describes. It has
two structural weaknesses: the encoder threads are `while True` loops with no sleep,
so they pin the CPU and, because of the GIL, starve the other threads — which is
what makes the display stutter; and three threads draw on the same display behind a
single lock.

**`variants/senzafilodiffusione_fixed.py`** — the same structure (threads,
callbacks, globals) with the specific defects repaired. Every change is marked
`# >>> FIX n:` and explained in place. This is the one to read if you want to know
what was wrong and why.

**`variants/senzafilodiffusione_rewrite.py`** — same pinout, same `radio_list.txt`
format, same external dependencies, different architecture. Encoders and buttons
only post events to a `queue.Queue`; a single controller thread consumes them, runs
the `mpc` commands and owns the display, so there are no locks and no race
conditions. The UI is an explicit state machine (now playing / list / volume /
settings / message) with timeouts. Encoder polling sleeps 1 ms per iteration.
`radio_info.txt` is gone — `mpc current` is read directly.

**This last one is what currently runs on the device**, installed as
`/home/pi/senzafilodiffusione/senzafilodiffusione.py`.

> Note on the published history: the copy of `senzafilodiffusione.py` in this repo
> between 2020 and 2026 was an intermediate snapshot that predated the finished
> version. It was missing the preset-parsing logic, so the six front-panel buttons
> did nothing, and its settings menu was still in Italian. It has been replaced with
> the version the article actually documents.

---

## Spotify variant

The Spotify version swaps MPD for [Mopidy](https://mopidy.com/), a Python server
that speaks the MPD protocol while pulling audio from streaming services. Because of
that, the display and track-info layer carries over untouched.

> **This variant has not been revised.** It is the December 2020 code, and it has
> received none of the fixes applied to the radio branch. The busy-loop and display
> contention problems described above almost certainly apply to it as well.

Install per the
[Raspbian guide](https://docs.mopidy.com/en/latest/installation/raspberrypi/#how-to-for-raspbian),
then add two extensions:

- [mopidy-spotify](https://mopidy.com/ext/spotify/) — needs a Spotify **Premium**
  account plus a client id/secret pair, configured in the Spotify section of the
  Mopidy config file.
- [mopidy-mpd](https://github.com/mopidy/mopidy-mpd) — exposes Mopidy to MPD
  clients, so `mpc` keeps working.

Useful commands:

```bash
mpc lsplaylists                  # list playlists
mpc load "<full playlist name>"  # load one
mpc play                         # play from the first track
mpc play 3                       # jump to track 3
mpc next / mpc prev
```

The interaction model changes, because the hardware runs out of controls: a playlist
is a second level of navigation, and there is only one free encoder. So two modes
were added — **button 5** enters playlist mode (browse playlists, push to start
one), **button 6** enters track mode (browse the current playlist, push to jump).
The preset buttons have no other meaning here.

Playlists are dumped to a file with `mpc lsplaylists > filename` and edited through
`edit_playlist.php`, same approach as the radio list. Reading them live from Spotify
every time was slow and error-prone.

---

## Known limitations and ideas

- The Spotify variant is unmaintained (see above).
- The service runs as root; moving it to the `pi` user is untested.
- The application is silent in the journal during normal operation. Replacing the
  remaining `print()` calls with the `logging` module would make station changes and
  errors visible with timestamps.
- No proper Wi-Fi setup UI — WPS only. SSID scan and password entry on the OLED
  would be better.
- The station list is a flat text file. A small database and a decent editing
  interface would be an improvement.
- Two extra buttons, below the display or on the side, for previous / next / seek —
  useful in both variants.
- A web remote control calling `mpc` from PHP.
- Switching between the radio and Spotify applications still has to be done by hand;
  it belongs in the settings menu.
- The unit is powered off and on frequently and occasionally loses mains power
  abruptly. Moving frequently-written files to `tmpfs`, or mounting the root
  filesystem read-only, would reduce the risk of SD card corruption.

---

## Credits and references

The wheel should not be reinvented every time. Where a tutorial worked, it is linked
here and its author credited.

- MPD/mpc: <https://www.musicpd.org/doc/html/user.html>
- Configuring audio on the Pi: <https://www.raspberrypi.org/documentation/configuration/audio-config.md>
- OLED display setup: <http://codelectron.com/setup-oled-display-raspberry-pi-python/>
- Luma OLED library: <https://github.com/rm-hull/luma.oled>
- RPi.GPIO library: <https://sourceforge.net/p/raspberry-gpio-python/wiki/Home/>
- Reliable KY-040 decoding (Arduino code, ported to Python here): <https://www.best-microcontroller-projects.com/rotary-encoder.html>
- Moving the Pi activity LED: <https://www.raspberrypi.org/forums/viewtopic.php?t=158293>
- Finding radio stream URLs: <http://fmstream.org/index.php>
- Mopidy: <https://mopidy.com/> · [Raspbian install](https://docs.mopidy.com/en/latest/installation/raspberrypi/#how-to-for-raspbian) · [mopidy-spotify](https://mopidy.com/ext/spotify/) · [mopidy-mpd](https://github.com/mopidy/mopidy-mpd) · [clients](https://docs.mopidy.com/en/latest/clients/)

Build write-up with photos and video:
[SenzaFiloDiffusione on element14](https://community.element14.com/challenges-projects/project14/recycleretrofit/b/blog/posts/senzafilodiffusione)

Raspberry Pi 3B+ supplied by element14 for the Project14 Recycle & Retrofit challenge.
