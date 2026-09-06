Absolutely. Since you want this as a **reinstallation/recovery manual**, I’ll document the process from a clean Ubuntu installation rather than merely summarising what we did.

I also want to correct one detail from my previous answer: because your OBS is the **Flatpak version**, its persistent OBS configuration is under:

```text
~/.var/app/com.obsproject.Studio/config/obs-studio/
```

—not the ordinary `~/.config/obs-studio/` location. OBS documents different plugin/config locations depending on the installation method, and Flatpak has its own sandboxed configuration area. ([OBS Studio][1])

# Complete Recovery Manual

## Ubuntu + OBS + Samsung Tab S9 FE+ + scrcpy + Global OBS Scene Switching

### What the finished system does

You will have:

```text
Ctrl + Alt + 1  →  OBS scene: Laptop
Ctrl + Alt + 2  →  OBS scene: Tablet
```

So while recording:

```text
Laptop
   ↓
Ctrl + Alt + 2
   ↓
Samsung Tablet / Samsung Notes
   ↓
Ctrl + Alt + 1
   ↓
Laptop
```

The important point is that this works **globally under Wayland**, without switching to Xorg.

---

# PART 1 — Know the architecture

The final system consists of these components:

```text
Ubuntu 24.04
     │
     ├── GNOME / Wayland
     │
     ├── OBS Studio Flatpak
     │      │
     │      ├── Laptop scene
     │      └── Tablet scene
     │
     ├── scrcpy
     │      └── Samsung Galaxy Tab S9 FE+
     │
     ├── OBS WebSocket
     │      └── port 4455 + password
     │
     ├── Python virtual environment
     │      └── obsws-python
     │
     ├── ~/.local/bin/obs-scene
     │      └── tells OBS which scene to display
     │
     ├── ~/.config/obs-hotkeys/password
     │      └── stores WebSocket password
     │
     └── GNOME Custom Shortcuts
            ├── Ctrl+Alt+1 → obs-scene Laptop
            └── Ctrl+Alt+2 → obs-scene Tablet
```

This is the architecture I recommend preserving.

---

# PART 2 — What you need after reinstalling Ubuntu

Start with a fresh Ubuntu installation.

Your basic environment should be:

- Ubuntu 24.04.x
- GNOME
- Wayland
- Internet connection
- Samsung Galaxy Tab S9 FE+
- USB cable
- Samsung Notes
- OBS Studio
- scrcpy
- adb
- Python 3
- Python `venv`
- `obsws-python`

You **do not need** the Wayland Hotkeys OBS plugin.

That was an intermediate experiment which we abandoned.

---

# PART 3 — Verify that you are using Wayland

Run:

```bash
echo $XDG_SESSION_TYPE
```

You want:

```text
wayland
```

Do **not** switch to Xorg merely to make the OBS shortcuts work.

Our final solution uses GNOME's own global keyboard-shortcut mechanism and OBS WebSocket.

---

# PART 4 — Install Flatpak

Check whether Flatpak is installed:

```bash
flatpak --version
```

If it isn't:

```bash
sudo apt update
sudo apt install flatpak
```

Add Flathub if necessary:

```bash
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
```

Then install OBS:

```bash
flatpak install flathub com.obsproject.Studio
```

Launch it:

```bash
flatpak run com.obsproject.Studio
```

OBS's Linux installation documentation lists Flatpak as an available installation route; OBS WebSocket is built into OBS 28 and newer, so **do not install a separate obs-websocket plugin** for this setup. ([OBS Studio][2])

---

# PART 5 — Configure OBS

Open OBS.

## Video

Go to:

**Settings → Video**

Use:

```text
Base (Canvas) Resolution:     1920×1080
Output (Scaled) Resolution:  1920×1080
Common FPS Values:            30
```

## Recording

Go to:

**Settings → Output → Recording**

Our working configuration was:

```text
Recording Format:     MKV
Encoder:              x264
Rate Control:         CRF
CRF:                  18
CPU Usage Preset:     veryfast
Profile:              High
Keyframe Interval:    0
Audio Encoder:        libfdk AAC
Audio Track:          1
```

Recording path:

```text
/home/navdeep
```

You can change the recording directory later if you want.

---

# PART 6 — Create the OBS scenes

Create exactly two scenes:

```text
Laptop
Tablet
```

## Scene 1 — Laptop

Create:

```text
Laptop
```

Add a **Screen Capture (PipeWire)** source capturing the laptop display.

This scene represents your normal teaching environment:

- VS Code
- browser
- terminal
- PDFs
- demonstrations
- etc.

---

# PART 7 — Install adb

Install Android Debug Bridge:

```bash
sudo apt update
sudo apt install adb
```

Verify:

```bash
adb version
```

You should get an ADB version rather than `command not found`.

---

# PART 8 — Prepare the Samsung tablet

On the Galaxy Tab:

### Enable Developer Options

Go to:

**Settings → About tablet → Software information**

Tap:

**Build number**

seven times.

Then enable:

**Developer options → USB debugging**

Connect the tablet to Ubuntu with USB.

Run:

```bash
adb devices
```

The first time, the tablet should ask you to authorize USB debugging.

Accept it.

Run again:

```bash
adb devices
```

You want something like:

```text
List of devices attached
XXXXXXXX    device
```

If it says:

```text
unauthorized
```

look at the tablet and accept the authorization dialog.

---

# PART 9 — Install scrcpy

This is an important point.

We deliberately **did not use Ubuntu's old apt scrcpy package**, because the version available there was too old for the Android version on your tablet.

The official scrcpy documentation currently identifies the Linux static release as an installation option and specifically notes that the Ubuntu package can be obsolete. ([GitHub][3])

For our setup, we used:

```text
scrcpy-linux-x86_64-v4.1
```

The official scrcpy repository is the correct source; it explicitly warns users not to download scrcpy from random third-party websites. ([GitHub][4])

Download the appropriate official Linux x86_64 release and extract it.

We kept it here:

```text
~/scrcpy-linux-x86_64-v4.1/
```

Test it:

```bash
cd ~/scrcpy-linux-x86_64-v4.1
./scrcpy
```

You should see the tablet screen in a desktop window.

---

# PART 10 — Give the scrcpy window a predictable title

This is important because OBS will capture this particular window.

Launch:

```bash
cd ~/scrcpy-linux-x86_64-v4.1
./scrcpy --window-title="Samsung Tablet"
```

The scrcpy window should be titled:

```text
Samsung Tablet
```

The official scrcpy project supports Linux and Android-device mirroring/control through USB or TCP/IP. ([GitHub][4])

---

# PART 11 — Create the Tablet OBS scene

In OBS create:

```text
Tablet
```

Add:

**Screen Capture (PipeWire)**

Choose the **application window**:

```text
Samsung Tablet
```

This is important.

Do **not** capture your entire laptop screen for this scene.

The purpose of this scene is:

```text
OBS
 ↓
Tablet scene
 ↓
Only scrcpy window
 ↓
Samsung tablet
```

---

# PART 12 — Test OBS manually

Before doing anything with shortcuts:

Click:

```text
Laptop
```

OBS should show your laptop.

Then click:

```text
Tablet
```

OBS should show the Samsung tablet.

If this does not work, **stop here**. Don't proceed to WebSocket or shortcuts until these two scenes work manually.

---

# PART 13 — Enable OBS WebSocket

This is the key part of our final solution.

Open:

**OBS → Tools → WebSocket Server Settings**

Enable:

```text
Enable WebSocket server
```

Port:

```text
4455
```

Enable:

```text
Enable Authentication
```

Keep authentication enabled.

OBS recommends protecting WebSocket access with a password, and WebSocket functionality has been included in OBS since version 28. ([OBS Studio][5])

OBS generates/provides a password.

You need that password for the next steps.

### IMPORTANT

Never put your actual password into this recovery document.

Your actual password belongs only in:

```text
~/.config/obs-hotkeys/password
```

---

# PART 14 — Create the secure password directory

Run:

```bash
mkdir -p ~/.config/obs-hotkeys
chmod 700 ~/.config/obs-hotkeys
```

Now securely enter the OBS password:

```bash
read -rsp "Enter OBS WebSocket password: " OBS_PASSWORD; echo; printf '%s' "$OBS_PASSWORD" > ~/.config/obs-hotkeys/password; unset OBS_PASSWORD
```

Then:

```bash
chmod 600 ~/.config/obs-hotkeys/password
```

Check:

```bash
ls -l ~/.config/obs-hotkeys/password
```

It should look like:

```text
-rw------- 1 navdeep navdeep ... password
```

The important part is:

```text
-rw-------
```

This means only your account can read it.

---

# PART 15 — Create the Python virtual environment

We initially tried installing `obsws-python` directly with pip, but Ubuntu's externally-managed Python environment prevented that.

Therefore we use a virtual environment.

Run:

```bash
python3 -m venv ~/.obs-hotkey-venv
```

Then install the Python OBS WebSocket client:

```bash
~/.obs-hotkey-venv/bin/pip install obsws-python
```

Test it:

```bash
~/.obs-hotkey-venv/bin/python -c "import obsws_python; print('obsws-python is installed')"
```

Expected:

```text
obsws-python is installed
```

---

# PART 16 — Create the scene-switching script

Create the directory:

```bash
mkdir -p ~/.local/bin
```

Create the script:

```bash
nano ~/.local/bin/obs-scene
```

Paste:

```python
#!/home/navdeep/.obs-hotkey-venv/bin/python3

import sys
import obsws_python as obs

if len(sys.argv) != 2:
    sys.exit(1)

scene = sys.argv[1]

with open("/home/navdeep/.config/obs-hotkeys/password", "r") as f:
    password = f.read().strip()

client = obs.ReqClient(
    host="localhost",
    port=4455,
    password=password,
    timeout=2
)

client.set_current_program_scene(scene)
client.disconnect()
```

Save:

```text
Ctrl + O
Enter
Ctrl + X
```

Then:

```bash
chmod +x ~/.local/bin/obs-scene
```

### Why this works

The script:

```text
obs-scene Laptop
```

connects to OBS WebSocket and requests:

```text
Laptop
```

The script:

```text
obs-scene Tablet
```

requests:

```text
Tablet
```

The underlying OBS WebSocket API provides `SetCurrentProgramScene`, which accepts a scene name or UUID. ([GitHub][6])

---

# PART 17 — Test the script before creating shortcuts

Make sure OBS is running and WebSocket is enabled.

Test:

```bash
~/.local/bin/obs-scene Laptop
```

OBS should change to:

```text
Laptop
```

Then:

```bash
~/.local/bin/obs-scene Tablet
```

OBS should change to:

```text
Tablet
```

Then:

```bash
~/.local/bin/obs-scene Laptop
```

Again:

```text
Laptop
```

**Do not proceed until all three work.**

---

# PART 18 — Create the global keyboard shortcuts

This is the part that makes everything global.

Open:

**Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts**

Create:

### Shortcut 1

Name:

```text
OBS — Laptop
```

Command:

```text
/home/navdeep/.local/bin/obs-scene Laptop
```

Shortcut:

```text
Ctrl + Alt + 1
```

### Shortcut 2

Name:

```text
OBS — Tablet
```

Command:

```text
/home/navdeep/.local/bin/obs-scene Tablet
```

Shortcut:

```text
Ctrl + Alt + 2
```

GNOME/Ubuntu provides system-level keyboard shortcut functionality; these commands are then executed independently of which application currently has focus. ([Ubuntu Help][7])

---

# PART 19 — Final test

Open something other than OBS.

For example:

```text
VS Code
```

Press:

```text
Ctrl + Alt + 2
```

OBS should switch to:

```text
Tablet
```

Then press:

```text
Ctrl + Alt + 1
```

OBS should switch to:

```text
Laptop
```

This proves that the shortcuts are genuinely global.

---

# PART 20 — What we tried but DO NOT need

This is important for future recovery.

We initially tried an OBS plugin called:

```text
obs-wayland-hotkeys
```

We compiled it because ordinary OBS hotkeys don't globally intercept keys under Wayland.

However, two problems occurred:

1. The prebuilt plugin had a Qt compatibility problem with our OBS Flatpak.
2. After compiling it ourselves, GNOME 46 did not provide the required Global Shortcuts portal.

Therefore:

### ❌ Do NOT reinstall

```text
obs-wayland-hotkeys
```

### ❌ Do NOT compile that plugin again

### ❌ Do NOT install the Flathub Wayland Hotkeys plugin

### ❌ Do NOT switch to Xorg just for this

The final working solution is much simpler:

```text
GNOME global shortcut
        ↓
obs-scene script
        ↓
obsws-python
        ↓
OBS WebSocket
        ↓
OBS scene
```

---

# PART 21 — Folders/files you must preserve

This is the most important section for future recovery.

## 🔴 1. OBS configuration

Because you use the Flatpak version:

```text
~/.var/app/com.obsproject.Studio/config/obs-studio/
```

### BACK THIS UP.

It contains your OBS configuration, including your scenes and sources.

In particular, don't casually delete:

```text
~/.var/app/com.obsproject.Studio/config/obs-studio/
```

Your `Laptop` and `Tablet` scenes live in the OBS configuration.

OBS's documentation confirms that Flatpak has its own plugin/configuration location rather than the ordinary Linux OBS installation path. ([OBS Studio][1])

---

## 🔴 2. WebSocket password

```text
~/.config/obs-hotkeys/password
```

Preserve this if you want the existing script to continue working.

Permissions should be:

```text
600
```

---

## 🔴 3. Scene-switching script

```text
~/.local/bin/obs-scene
```

Preserve it exactly.

The GNOME shortcuts point to:

```text
/home/navdeep/.local/bin/obs-scene
```

If you move it, the shortcuts will stop working.

---

## 🟡 4. Python virtual environment

```text
~/.obs-hotkey-venv/
```

You **can recreate this**.

It isn't as important as your OBS configuration.

If you lose it:

```bash
python3 -m venv ~/.obs-hotkey-venv
~/.obs-hotkey-venv/bin/pip install obsws-python
```

and you're back in business.

So:

```text
KEEP → recommended
BACKUP → optional
RECREATE → easy
```

---

## 🔴 5. scrcpy

Keep:

```text
~/scrcpy-linux-x86_64-v4.1/
```

You can technically download it again, but you need scrcpy for the Tablet scene.

If you use a newer release after reinstalling, test it carefully because the OBS source is capturing the scrcpy window.

The official project currently lists v4.1 as the Linux x86_64 static release in its Linux documentation. ([GitHub][3])

---

# PART 22 — GNOME keyboard shortcuts

There isn't a normal file in your home directory that we manually created for these.

GNOME stores these settings in its configuration database, **dconf**.

Therefore, I strongly recommend backing them up.

Run:

```bash
dconf dump /org/gnome/settings-daemon/plugins/media-keys/ > ~/obs-gnome-shortcuts.dconf
```

Keep:

```text
~/obs-gnome-shortcuts.dconf
```

Then after a future reinstall you can either recreate the two shortcuts manually, which takes about a minute, or restore the dconf settings.

I recommend **manual recreation** unless you're comfortable with dconf, because it is less error-prone when the GNOME installation changes.

---

# PART 23 — Make a complete backup now

I strongly recommend creating one dedicated backup folder.

Run:

```bash
mkdir -p ~/OBS-Recording-Backup
```

Then copy the important pieces:

```bash
cp -a ~/.local/bin/obs-scene ~/OBS-Recording-Backup/
```

```bash
cp -a ~/.config/obs-hotkeys ~/OBS-Recording-Backup/
```

And back up your OBS Flatpak configuration:

```bash
cp -a ~/.var/app/com.obsproject.Studio/config/obs-studio ~/OBS-Recording-Backup/
```

And your GNOME shortcuts:

```bash
dconf dump /org/gnome/settings-daemon/plugins/media-keys/ > ~/OBS-Recording-Backup/obs-gnome-shortcuts.dconf
```

Your backup should therefore contain approximately:

```text
OBS-Recording-Backup/
│
├── obs-scene
│
├── obs-hotkeys/
│   └── password
│
├── obs-studio/
│   ├── basic/
│   ├── global.ini
│   ├── user.ini
│   └── ...
│
└── obs-gnome-shortcuts.dconf
```

### VERY IMPORTANT

That backup contains your **OBS WebSocket password**.

Treat the backup as sensitive.

Don't upload it publicly or put it in a public GitHub repository.

---

# PART 24 — What you DON'T need to back up

You don't need to preserve:

```text
~/obs-wayland-hotkeys/
```

That was our abandoned experiment.

You also don't need to preserve:

```text
build/
```

from that project.

You don't need to preserve the Python virtual environment if you don't want to.

You can simply recreate it.

---

# PART 25 — Recommended backup hierarchy

If I were setting this up on my own machine, I'd preserve these:

### Absolutely preserve

```text
~/.var/app/com.obsproject.Studio/config/obs-studio/
~/.config/obs-hotkeys/password
~/.local/bin/obs-scene
```

### Good to preserve

```text
~/scrcpy-linux-x86_64-v4.1/
~/OBS-Recording-Backup/obs-gnome-shortcuts.dconf
```

### Easy to recreate

```text
~/.obs-hotkey-venv/
```

### Don't bother preserving

```text
~/obs-wayland-hotkeys/
```

---

# PART 26 — Complete reinstall checklist

If Ubuntu dies tomorrow, this is the short version you can follow.

### Stage 1 — Ubuntu

```text
Ubuntu
↓
Wayland
↓
GNOME
```

### Stage 2 — Basic packages

```bash
sudo apt update
sudo apt install flatpak adb python3-venv
```

### Stage 3 — Flatpak

```bash
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install flathub com.obsproject.Studio
```

### Stage 4 — scrcpy

Install the official x86_64 static release and extract:

```text
~/scrcpy-linux-x86_64-v4.1/
```

Test:

```bash
cd ~/scrcpy-linux-x86_64-v4.1
./scrcpy --window-title="Samsung Tablet"
```

### Stage 5 — OBS

Create:

```text
Laptop
Tablet
```

Configure:

```text
1920×1080
30 FPS
```

Add:

```text
Laptop → laptop display
Tablet → Samsung Tablet scrcpy window
```

### Stage 6 — WebSocket

OBS:

```text
Tools
→ WebSocket Server Settings
→ Enable
→ Port 4455
→ Authentication ON
```

### Stage 7 — Password

```bash
mkdir -p ~/.config/obs-hotkeys
chmod 700 ~/.config/obs-hotkeys
```

Store password:

```bash
read -rsp "Enter OBS WebSocket password: " OBS_PASSWORD; echo; printf '%s' "$OBS_PASSWORD" > ~/.config/obs-hotkeys/password; unset OBS_PASSWORD
```

Then:

```bash
chmod 600 ~/.config/obs-hotkeys/password
```

### Stage 8 — Python

```bash
python3 -m venv ~/.obs-hotkey-venv
~/.obs-hotkey-venv/bin/pip install obsws-python
```

### Stage 9 — Script

Create:

```text
~/.local/bin/obs-scene
```

with the exact Python code above.

Then:

```bash
chmod +x ~/.local/bin/obs-scene
```

### Stage 10 — Test

```bash
~/.local/bin/obs-scene Laptop
```

then:

```bash
~/.local/bin/obs-scene Tablet
```

### Stage 11 — Global shortcuts

```text
Ctrl + Alt + 1
        ↓
/home/navdeep/.local/bin/obs-scene Laptop
```

and:

```text
Ctrl + Alt + 2
        ↓
/home/navdeep/.local/bin/obs-scene Tablet
```

### Stage 12 — Final test

From VS Code/Chrome/Terminal:

```text
Ctrl + Alt + 1 → Laptop
Ctrl + Alt + 2 → Tablet
```

Done.

---

# PART 27 — The one-page "DO NOT DELETE" list

I'd actually save this somewhere.

```text
==================================================
OBS + SAMSUNG TABLET RECORDING SYSTEM
IMPORTANT FILES
==================================================

OBS CONFIGURATION
~/.var/app/com.obsproject.Studio/config/obs-studio/

OBS WEBSOCKET PASSWORD
~/.config/obs-hotkeys/password

SCENE SWITCH SCRIPT
~/.local/bin/obs-scene

PYTHON ENVIRONMENT
~/.obs-hotkey-venv/

SCRCPY
~/scrcpy-linux-x86_64-v4.1/

GNOME SHORTCUT BACKUP
~/OBS-Recording-Backup/obs-gnome-shortcuts.dconf


GLOBAL SHORTCUTS
Ctrl + Alt + 1 → Laptop
Ctrl + Alt + 2 → Tablet


OBS WEBSOCKET
Port: 4455
Authentication: ON


OBS SCENES
Laptop
Tablet


DO NOT DELETE
~/.var/app/com.obsproject.Studio/config/obs-studio/
~/.config/obs-hotkeys/password
~/.local/bin/obs-scene
~/scrcpy-linux-x86_64-v4.1/


EASY TO RECREATE
~/.obs-hotkey-venv/


NOT NEEDED
~/obs-wayland-hotkeys/
==================================================
```

## One final distinction that is important

There are **two different things to preserve**:

**Your OBS setup**:

```text
~/.var/app/com.obsproject.Studio/config/obs-studio/
```

and **your automation setup**:

```text
~/.config/obs-hotkeys/password
~/.local/bin/obs-scene
GNOME custom shortcuts
~/.obs-hotkey-venv/
```

If you preserve those first three automation items plus your OBS configuration, **you have essentially preserved the entire solution we built**. The Python environment and scrcpy installation can be recreated if necessary.

Also, the WebSocket approach is preferable here because OBS officially supports WebSocket automation natively, including scene control, rather than relying on the Wayland-specific plugin we tried initially. ([OBS Studio][5])

[1]: https://obsproject.com/kb/plugins-guide?utm_source=chatgpt.com
[2]: https://obsproject.com/kb/linux-installation?utm_source=chatgpt.com
[3]: https://github.com/Genymobile/scrcpy/blob/master/doc/linux.md?plain=1&utm_source=chatgpt.com
[4]: https://github.com/GenyMobile/scrcpy?utm_source=chatgpt.com
[5]: https://obsproject.com/kb/remote-control-guide?utm_source=chatgpt.com
[6]: https://github.com/obsproject/obs-websocket/blob/master/src/requesthandler/RequestHandler_Scenes.cpp?utm_source=chatgpt.com
[7]: https://help.ubuntu.com/community/KeyboardShortcuts?utm_source=chatgpt.com
