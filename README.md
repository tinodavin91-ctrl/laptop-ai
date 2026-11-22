# Laptop AI

A chat + voice assistant for your Windows laptop, with a dark dashboard-style window
(sidebar, live CPU / memory / disk / battery gauges, chat, voice, tools, settings). Ask it things like:

- "What's using my memory?" / "Is my battery OK?"
- "Close Chrome" (it asks your OK first)
- "Clean my temp files" / "How much space could I free?"
- "Remind me to drink water in 30 minutes"
- "Every hour, give me a system report"
- "Open Task Manager"

It also watches the laptop in the background and warns you when CPU, memory, disk or battery cross a limit.

## Install (Windows, PowerShell)

1. Install Python 3.10+ from python.org (tick **Add Python to PATH**).
2. In PowerShell, inside this folder:
   ```
   pip install -r requirements.txt
   python run.py
   ```
   Already installed an earlier version? You only need:  `pip install customtkinter pillow sounddevice`
   (Voice listening also needs `pip install sounddevice`. Without it the app still works with typing and spoken replies.)

## The window

| Page | What it does |
|---|---|
| Home | Welcome screen, live gauges (green / amber / red), quick-question buttons, type box |
| Chat | The conversation. "New chat" clears it and makes the AI forget it |
| Voice Assistant | Big microphone button, optional spoken replies |
| Files & Tools | Quick-action buttons, and the full list of what the AI may do with its risk level |
| Settings | Brain, autonomy, voice, and alert limits (saved automatically) |
| Help & Support | Things to try, fixes, how safety works, open the action log |

The box at the bottom-left shows whether an AI brain is ready ("System Online") or missing ("No AI brain").
A "● new" marker next to Chat means something arrived while you were on another page.

## Give it a brain (pick one or both)

**Offline (private, free): Ollama**
1. Install Ollama from ollama.com.
2. `ollama pull llama3.2`  (a small model that supports tools; needs roughly 4 GB of RAM free)
3. Leave Ollama running. Set **Brain** to `offline` or `auto`.

**Online (smarter): Claude API**
1. Get an API key at console.anthropic.com.
2. In PowerShell: `setx ANTHROPIC_API_KEY "your-key"` then reopen PowerShell.
3. Set **Brain** to `online` or `auto`. The key is only read from that variable and is never saved by the app.

`auto` tries offline first and falls back to online if Ollama isn't available. Online mode sends your messages and the tool results (for example the list of running programs) to Anthropic.

## Phone reminders (Telegram)

1. In Telegram, message **@BotFather**, send `/newbot`, follow the steps, and copy the token.
2. In Laptop AI: Settings > Phone (Telegram). Paste the token, press **Save**.
3. Open your new bot in Telegram, send it `hi`, then press **Link my chat** and **Send test**.

Your token is kept in Windows Credential Manager (needs `pip install keyring`), never in a settings file.
Reminders, scheduled reports and CPU/memory/disk/battery warnings are sent to your phone (each can be switched off).
The optional "ask from my phone" switch is **read-only**: the AI is not even told about tools that change
things, so nobody can close programs or delete files from the phone. Only the one chat you linked is
answered; everyone else is ignored. Telegram messages are not end-to-end encrypted, so do not send secrets through them.
The laptop must be on and online for messages to arrive.

## "Trix" (hands-free)

Off by default. `pip install vosk`, then Settings > Hands-free > **Download voice model** (~40 MB, one time;
if it fails the message tells you where to download it by hand), then switch on **Listen for "Trix"**.

* While it waits, audio is analysed **on this PC** by a model that only recognises the one word "Trix" (the model spells the sound "tricks"; both count).
  Nothing is saved and nothing is sent anywhere.
* After the wake word, ONE command is recorded. "google" turns it into text through Google (more accurate,
  the short clip leaves the PC); "offline" keeps it on the PC.
* A red "Microphone is live" note shows in the sidebar the whole time. Closing the app stops it.
* A command given this way can never act silently: anything beyond a plain lookup asks for your OK, even in auto_safe mode.
* Honest limit: a small offline model will sometimes miss the wake word or hear it by mistake. Say it clearly, close to the mic.

## Make it a real app (no terminal needed)

Double-click **make_app.bat** in this folder and wait a few minutes. It builds `LaptopAI.exe` and then:

* **If you have the free Inno Setup 6** (https://jrsoftware.org/isdl.php): it makes
  `installer_output\LaptopAI_Setup.exe`. Double-click that to install Laptop AI like any other program:
  Start menu entry, optional desktop icon, optional "start with Windows", and an entry in
  Settings > Apps so you can uninstall it normally. No administrator prompt.
* **If you don't:** it installs the app straight to `C:\Users\<you>\AppData\Local\Programs\Laptop AI`
  and adds "Laptop AI" to your Start menu and desktop.

After that you never need PowerShell or Python again. Open "Laptop AI" from the Start menu.

How it behaves like a normal app:
* **Icon by the clock:** closing the window keeps it running (reminders, phone link and "Trix" keep working).
  Click the icon to open it again; right-click > Quit to really close it. A red dot on the icon means the microphone is live.
* **Start with Windows** (Settings > App): starts hidden by the clock when you sign in.
* **One copy at a time:** opening it again just brings the first window forward.
* **Pop-ups:** reminders and warnings show as Windows notifications while the window is hidden.
* Your settings, notes and voice model live in `C:\Users\<you>\.laptop_ai` and are kept when you uninstall.
* If it ever fails to start, it shows a message and writes `.laptop_ai\crash.log`.
* Windows SmartScreen or your antivirus may warn about a program you built yourself. That's normal for
  unsigned programs: choose "More info > Run anyway". Only run an installer you built.
* You still need Ollama (offline brain) or an `ANTHROPIC_API_KEY` (online brain).

## Safety: what it can and cannot do

The AI can only use a fixed list of tools. There is **no** "run any command" tool, so it cannot run arbitrary PowerShell, edit files or read your documents.

| Tool risk | Examples | When it asks you |
|---|---|---|
| Read | system status, list programs, time | never |
| Safe | reminders, schedules | never |
| Low | clean temp files, open an app | in `ask` mode; automatic in `auto_safe` |
| High | close a program | **always** |

Autonomy modes (top of the window): `ask` (default), `auto_safe`, `report_only` (it can only look, never change anything).

Other guards: Windows system processes and the assistant itself can't be closed; temp cleaning only touches old files in your temp folder and never follows shortcuts; a dialog with no answer after 2 minutes counts as "no"; every change is logged to `%USERPROFILE%\.laptop_ai\actions.log`.

## Honest limits

- It is not "every detail of the laptop". It handles what's in `laptop_ai/tools.py`. Small local models also make mistakes, so keep `ask` mode on until you trust it.
- Voice **listening** uses Google's free speech recogniser (your voice is sent to Google). Speaking replies is fully offline. For fully offline listening, replace `Voice.listen` in `voice.py` with Vosk.
- Scheduled jobs only run while the app is open.
- Opening apps is Windows-only.

## Add your own tool

In `laptop_ai/tools.py`:

```python
@tool("get_wifi_name", "Get the name of the Wi-Fi network.", risk=READ)
def get_wifi_name() -> str:
    ...
    return ok(ssid="MyWifi")
```

Pick the lowest risk level that is honest. Anything that changes or deletes something should be `LOW` or `HIGH`.

## Files

```
run.py                 start here
laptop_ai/tools.py     what the AI may do + the safety gate (Executor)
laptop_ai/brain.py     Ollama (offline) and Claude (online) + memory
laptop_ai/monitor.py   background alerts
laptop_ai/scheduler.py reminders and recurring jobs
laptop_ai/voice.py     speak / listen (optional)
laptop_ai/wakeword.py  "Trix" wake word (offline, opt-in)
laptop_ai/notify.py    Telegram reminders / phone questions
laptop_ai/app.py       start-up wrapper with crash log (used by the .exe)
make_app.bat           one double-click: builds LaptopAI.exe + installer
installer.iss          the installer definition (Inno Setup)
laptop_ai/gui.py       the window
laptop_ai/assets/      logo and icons (re-draw them with  python assets/make_logo.py  and  python assets/make_icons.py)
tests/                 python -m pytest
```
