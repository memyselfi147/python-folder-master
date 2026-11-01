import json, os, queue, subprocess, threading, time
import psutil
import sounddevice as sd
from ctypes import (windll, wintypes, byref, sizeof, Structure, POINTER,
                    WINFUNCTYPE, c_int, create_unicode_buffer)
from vosk import Model, KaldiRecognizer, SetLogLevel
import obsws_python as obs

# ---------------- SETTINGS ----------------
PASSWORD = "UU7b3A70Aadvaq1o"                 # OBS WebSocket password
PORT = 4455
DELAY = 30                                 # seconds to wait after the phrase before saving
PHRASE = "jarvis clip that"
DEBUG = True                               # writes detection info to the log;
                                           # set to False once everything works
TOL = 2                                    # pixels of slack when checking "fullscreen"

OBS_PATH = r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"
CLOSE_OBS_WITH_GAME = True                 # close OBS when the game closes
                                           # (only if this script opened it)

# Extra games by exe name (lowercase). Fullscreen apps are detected automatically.
LAUNCHER_PATHS = ["steamapps\\common"]
GAME_EXES = {"robloxplayerbeta.exe",        # Roblox
             "javaw.exe",                    # Minecraft Java (Modrinth / CurseForge)
             "minecraft.windows.exe",        # Minecraft Bedrock
             "aces.exe",                     # War Thunder
             "spider-man.exe"}               # Marvel's Spider-Man Remastered
IGNORE = {"steamwebhelper.exe", "steam.exe", "wallpaper32.exe", "wallpaper64.exe"}
NOT_GAMES = {"explorer.exe", "chrome.exe", "msedge.exe", "firefox.exe",
             "brave.exe", "opera.exe", "vlc.exe", "obs64.exe",
             "applicationframehost.exe", "searchhost.exe", "python.exe",
             "pythonw.exe", "taskmgr.exe", "lockapp.exe", "logonui.exe",
             "screenclippinghost.exe", "snippingtool.exe",
             "startmenuexperiencehost.exe", "shellexperiencehost.exe"}
# ------------------------------------------

BASE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE, "vosk-model-small-en-us-0.15")
LOG_FILE = os.path.join(BASE, "jarvis_log.txt")

SetLogLevel(-1)
client = None
pending = 0
lock = threading.Lock()
obs_lock = threading.RLock()   # OBS connection is used by several threads


def log(msg):
    """Errors go to jarvis_log.txt (a .pyw has no console window)."""
    try:
        with open(LOG_FILE, "a") as f:
            f.write(time.strftime("%H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass


_last_debug = None


def debug(msg):
    """Log detection details, but only when they change."""
    global _last_debug
    if DEBUG and msg != _last_debug:
        _last_debug = msg
        log("DEBUG " + msg)


# ---------- Windows API setup ----------
user32 = windll.user32
EnumProc = WINFUNCTYPE(c_int, wintypes.HWND, wintypes.LPARAM)


class MONITORINFO(Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, POINTER(wintypes.RECT)]
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, POINTER(MONITORINFO)]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]

# Use real pixel sizes on scaled (125%/150%) laptop displays
try:
    windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


# ---------- OBS control ----------
def get_client():
    global client
    if client is None:
        client = obs.ReqClient(host="localhost", port=PORT,
                               password=PASSWORD, timeout=5)
    return client


def obs_call(name):
    global client
    try:
        with obs_lock:
            getattr(get_client(), name)()
        return True
    except Exception as e:
        client = None
        log(f"OBS call {name} failed: {e}")
        return False


def obs_procs():
    return [p for p in psutil.process_iter(["name"])
            if (p.info["name"] or "").lower() == "obs64.exe"]


def launch_obs():
    subprocess.Popen([OBS_PATH, "--minimize-to-tray", "--disable-shutdown-check"],
                     cwd=os.path.dirname(OBS_PATH))


def close_obs():
    """Ask OBS to close normally (like clicking X) so it shuts down cleanly."""
    global client
    procs = obs_procs()
    pids = {p.pid for p in procs}

    def cb(hwnd, lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, byref(pid))
        if pid.value in pids:
            buf = create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value.startswith("OBS "):
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    psutil.wait_procs(procs, timeout=15)
    client = None


def start_buffer_when_ready():
    """OBS can take a few seconds to start; keep trying."""
    global client
    for _ in range(20):
        try:
            with obs_lock:
                c = get_client()
                if c.get_replay_buffer_status().output_active:
                    return True
                c.start_replay_buffer()
            log("Replay buffer started.")
            return True
        except Exception as e:
            client = None
            log(f"Waiting for OBS: {e}")
            time.sleep(2)
    log("Could not start the replay buffer.")
    return False


# ---------- Game detection ----------
current_pid = None


def fullscreen_pid():
    """PID of the foreground app if it fills its whole monitor, else None."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        debug("no foreground window")
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, byref(pid))
    try:
        name = psutil.Process(pid.value).name().lower()
    except Exception as e:
        debug(f"could not read foreground process: {e}")
        return None
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, byref(r))
    mi = MONITORINFO()
    mi.cbSize = sizeof(MONITORINFO)
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), byref(mi))
    m = mi.rcMonitor
    full = (r.left <= m.left + TOL and r.top <= m.top + TOL and
            r.right >= m.right - TOL and r.bottom >= m.bottom - TOL)
    debug(f"foreground={name} window=({r.left},{r.top},{r.right},{r.bottom}) "
          f"monitor=({m.left},{m.top},{m.right},{m.bottom}) fullscreen={full}")
    if not full or name in NOT_GAMES or name in IGNORE:
        return None
    return pid.value


def listed_game_running():
    for p in psutil.process_iter(["name", "exe"]):
        try:
            name = (p.info["name"] or "").lower()
            exe = (p.info["exe"] or "").lower()
        except Exception:
            continue
        if name in IGNORE:
            continue
        if name in GAME_EXES or any(k in exe for k in LAUNCHER_PATHS):
            debug(f"listed game running: {name}")
            return True
    return False


def game_running():
    """Stays True until the detected game closes (alt-tab won't stop it)."""
    global current_pid
    if current_pid and psutil.pid_exists(current_pid):
        return True
    current_pid = fullscreen_pid()
    return current_pid is not None or listed_game_running()


# ---------- Voice trigger ----------
def save_later():
    global pending
    try:
        time.sleep(DELAY)
        if obs_call("save_replay_buffer"):
            log("Clip saved.")
    finally:
        with lock:
            pending -= 1


def listen(stop_evt):
    global pending
    model = Model(MODEL_PATH)   # loaded only while a game is running
    rec = KaldiRecognizer(model, 16000, f'["{PHRASE}", "[unk]"]')
    q = queue.Queue()

    def cb(indata, frames, t, status):
        q.put(bytes(indata))

    with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype="int16",
                           channels=1, callback=cb):
        while not stop_evt.is_set():
            try:
                data = q.get(timeout=1)
            except queue.Empty:
                continue
            if rec.AcceptWaveform(data):
                text = json.loads(rec.Result()).get("text", "")
                if "clip that" in text:
                    log(f"Heard: {text!r} - saving in {DELAY}s")
                    with lock:
                        pending += 1
                    threading.Thread(target=save_later, daemon=True).start()
    del rec, model


def listen_safe(stop_evt):
    try:
        listen(stop_evt)
    except Exception as e:
        log(f"Voice listener error: {e}")


# ---------- Main loop ----------
def main():
    log("Script started.")
    stop_evt, thread, we_launched_obs = None, None, False
    while True:
        try:
            running = game_running()
            if running and thread is None:
                log("Game detected.")
                if not obs_procs():
                    try:
                        launch_obs()
                        we_launched_obs = True
                    except Exception as e:
                        log(f"Could not open OBS (check OBS_PATH): {e}")
                stop_evt = threading.Event()
                thread = threading.Thread(target=listen_safe, args=(stop_evt,),
                                          daemon=True)
                thread.start()
                start_buffer_when_ready()
            elif not running and thread is not None:
                log("Game closed.")
                stop_evt.set()
                thread.join()
                thread = None
                while pending > 0:      # finish any clip still waiting to save
                    time.sleep(1)
                obs_call("stop_replay_buffer")
                if CLOSE_OBS_WITH_GAME and we_launched_obs:
                    time.sleep(3)
                    close_obs()
                    we_launched_obs = False
                    log("OBS closed.")
        except Exception as e:
            log(f"Unexpected error: {e}")
        time.sleep(5)


if __name__ == "__main__":
    main()