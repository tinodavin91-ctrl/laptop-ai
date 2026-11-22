"""PC health watchdog: looks for signs of malware and hardware trouble and raises alerts.

Everything it knows comes from Windows itself (Defender, the System event log, disk health, device
problems, the GPU driver) plus a few safe process checks. These are WARNING SIGNS, never proof, and the
watchdog only looks: it never deletes or changes anything.

The probes are injectable (`Runner`) so the logic is testable without a real PC.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from typing import Callable, Optional

import psutil

from . import config

CRITICAL, WARNING, INFO = "critical", "warning", "info"      # info: worth knowing, never alerts
Runner = Callable[[str], str]        # PowerShell script -> text output ("" when unavailable)


@dataclass
class Finding:
    key: str            # stable id, used so the same problem isn't announced over and over
    level: str
    area: str           # malware | cpu | gpu | disk | memory | motherboard | battery | system
    message: str
    advice: str = ""

    def text(self) -> str:
        return self.message + (f" {self.advice}" if self.advice else "")


def powershell(script: str, timeout: int = 25) -> str:
    if not sys.platform.startswith("win"):
        return ""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=timeout, creationflags=flags)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _json(text: str):
    try:
        data = json.loads(text) if text else None
    except ValueError:
        return None
    return data


def _rows(text: str) -> list[dict]:
    d = _json(text)
    if d is None:
        return []
    return [d] if isinstance(d, dict) else [x for x in d if isinstance(x, dict)]


# ---------------------------------------------------------------- malware
def check_defender(run: Runner) -> list[Finding]:
    out: list[Finding] = []
    st = _rows(run("Get-MpComputerStatus | Select-Object AntivirusEnabled,RealTimeProtectionEnabled,"
                   "AntivirusSignatureAge | ConvertTo-Json -Compress"))
    if st:
        s = st[0]
        if s.get("AntivirusEnabled") is False or s.get("RealTimeProtectionEnabled") is False:
            out.append(Finding("av_off", CRITICAL, "malware", "Windows virus protection is switched off.",
                               "Turn it on in Windows Security > Virus & threat protection."))
        age = s.get("AntivirusSignatureAge")
        if isinstance(age, (int, float)) and age >= 14:
            out.append(Finding("av_old", WARNING, "malware", f"Virus definitions are {int(age)} days old.",
                               "Open Windows Update to refresh them."))
    since = "(Get-Date).AddDays(-7)"
    for t in _rows(run(f"Get-MpThreatDetection | Where-Object {{ $_.InitialDetectionTime -gt {since} }} | "
                       "Select-Object -First 5 ThreatID,ProcessName,Resources,ActionSuccess | ConvertTo-Json -Compress")):
        name = t.get("ProcessName") or "a program"
        out.append(Finding(f"threat_{t.get('ThreatID')}", CRITICAL, "malware",
                           f"Windows Defender detected a threat in the last 7 days (involving {name}).",
                           "Open Windows Security > Protection history and run a full scan."))
    fw = _rows(run("Get-NetFirewallProfile | Select-Object Name,Enabled | ConvertTo-Json -Compress"))
    if fw and all(p.get("Enabled") in (False, 0) for p in fw):
        out.append(Finding("firewall_off", WARNING, "malware", "The Windows firewall is off for every network type.",
                           "Turn it on in Windows Security > Firewall & network protection."))
    return out


def check_processes(procs: Optional[list[dict]] = None) -> list[Finding]:
    """Programs wearing a Windows system name from the wrong place are a classic malware trick."""
    from .tools_pro import judge_process
    if procs is None:
        procs = []
        for p in psutil.process_iter(["name", "exe", "cpu_percent"]):
            try:
                procs.append({"pid": p.pid, **p.info})
            except psutil.Error:
                continue
    out: list[Finding] = []
    for p in procs:
        name, exe, cpu = p.get("name") or "", p.get("exe") or "", p.get("cpu_percent") or 0
        reasons = judge_process(name, exe, cpu)
        strong = [r for r in reasons if "system program" in r or "CPU" in r]    # skip the plain "runs from Downloads"
        if strong:
            out.append(Finding(f"proc_{name.lower()}_{(exe or '').lower()}", CRITICAL, "malware",
                               f"Suspicious program: {name} {strong[0]} ({exe or 'location unknown'}).",
                               "Ask me to \"close " + name + "\" and run a full Windows Security scan."))
    return out


# ---------------------------------------------------------------- hardware
def check_event_log(run: Runner) -> list[Finding]:
    """Hardware errors (WHEA), unexpected power loss, disk and bad-block errors in the last 7 days."""
    out: list[Finding] = []
    rows = _rows(run(
        "$s=(Get-Date).AddDays(-7); Get-WinEvent -FilterHashtable @{LogName='System';StartTime=$s} -ErrorAction SilentlyContinue | "
        "Where-Object { ($_.ProviderName -eq 'Microsoft-Windows-WHEA-Logger') -or "
        "($_.ProviderName -eq 'Microsoft-Windows-Kernel-Power' -and $_.Id -eq 41) -or "
        "($_.ProviderName -in 'disk','Ntfs','storahci','stornvme' -and $_.Id -in 7,11,51,55,98,153,129,157) -or "
        "($_.ProviderName -eq 'BugCheck') -or ($_.ProviderName -eq 'nvlddmkm') -or ($_.ProviderName -like 'Display' -and $_.Id -eq 4101) } | "
        "Group-Object ProviderName,Id | Select-Object Name,Count | ConvertTo-Json -Compress"))
    for r in rows:
        name, count = str(r.get("Name", "")), int(r.get("Count") or 0)
        if "WHEA" in name:
            out.append(Finding("whea", CRITICAL if count >= 3 else WARNING, "motherboard",
                               f"Windows logged {count} hardware error(s) this week (CPU, memory, motherboard or PCIe).",
                               "Back up your files. If it keeps happening, have the laptop checked."))
        elif "Kernel-Power" in name:
            out.append(Finding("power_loss", INFO if count < 2 else WARNING if count < 4 else CRITICAL, "motherboard",
                               f"The PC lost power or froze unexpectedly {count} time(s) this week.",
                               "Check the charger, battery and cooling; repeated cases can mean a failing board or PSU."))
        elif name.startswith(("disk", "Ntfs", "storahci", "stornvme")):
            out.append(Finding("disk_errors", CRITICAL if count >= 6 else WARNING if count >= 3 else INFO, "disk",
                               f"Windows logged {count} disk error(s) this week."
                               + (" One or two are usually harmless (for example a USB drive pulled out)." if count < 3 else ""),
                               "Back up your files now and check the drive." if count >= 3 else "Keep an eye on it."))
        elif "BugCheck" in name:
            out.append(Finding("bsod", WARNING, "system", f"The PC crashed with a blue screen {count} time(s) this week.",
                               "Update drivers; frequent crashes can point to faulty RAM or a failing component."))
        elif "nvlddmkm" in name or "Display" in name:
            out.append(Finding("gpu_driver", WARNING, "gpu", f"The graphics driver crashed or reset {count} time(s) this week.",
                               "Update or reinstall the graphics driver; overheating can also cause this."))
    return out


def check_disks(run: Runner) -> list[Finding]:
    out = []
    for d in _rows(run("Get-PhysicalDisk | Select-Object FriendlyName,HealthStatus,OperationalStatus | ConvertTo-Json -Compress")):
        status = str(d.get("HealthStatus", ""))
        if status and status.lower() not in ("healthy", "0"):
            out.append(Finding(f"disk_health_{d.get('FriendlyName')}", CRITICAL, "disk",
                               f"Drive '{d.get('FriendlyName')}' reports health: {status}. It may be failing.",
                               "Back up your files immediately."))
    return out


DEVICE_AREAS = {"Display": "gpu", "System": "motherboard", "Processor": "motherboard", "HDC": "motherboard", "Net": "network",
                "Bluetooth": "bluetooth", "Media": "audio", "AudioEndpoint": "audio", "Camera": "camera", "Image": "camera",
                "USB": "usb", "Keyboard": "input", "HIDClass": "input", "Monitor": "screen", "DiskDrive": "disk"}


def check_devices(run: Runner) -> list[Finding]:
    """Hardware devices Windows says are broken (driver or physical faults)."""
    out = []
    for d in _rows(run("Get-CimInstance Win32_PnPEntity | Where-Object { $_.ConfigManagerErrorCode -ne 0 -and $_.PNPClass -in "
                       "'Display','Net','DiskDrive','Processor','System','HDC','Bluetooth','Media','Camera','Image','USB','Keyboard','HIDClass','Monitor','AudioEndpoint' } | "
                       "Select-Object -First 5 Name,PNPClass,ConfigManagerErrorCode | ConvertTo-Json -Compress")):
        cls = str(d.get("PNPClass", ""))
        area = DEVICE_AREAS.get(cls, "system")
        out.append(Finding(f"dev_{d.get('Name')}", WARNING, area,
                           f"Windows reports a problem with '{d.get('Name')}' (error code {d.get('ConfigManagerErrorCode')}).",
                           "Open Device Manager and update or reinstall its driver."))
    return out


def check_gpu(run: Runner, smi: Optional[str] = None) -> list[Finding]:
    """NVIDIA GPUs: temperature and throttling via nvidia-smi (other GPUs are covered by the device/event checks)."""
    out = []
    if smi is None:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return out
        try:
            smi = subprocess.run([exe, "--query-gpu=name,temperature.gpu,clocks_throttle_reasons.hw_thermal_slowdown,"
                                       "ecc.errors.uncorrected.volatile.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=8,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        except (OSError, subprocess.SubprocessError):
            return out
    for line in smi.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            temp = float(parts[1])
        except ValueError:
            continue
        if temp >= 90:
            out.append(Finding("gpu_hot", CRITICAL, "gpu", f"The GPU ({parts[0]}) is very hot: {temp:.0f}°C.",
                               "Close games or heavy apps and clean the vents."))
        elif temp >= 85:
            out.append(Finding("gpu_warm", WARNING, "gpu", f"The GPU ({parts[0]}) is running hot: {temp:.0f}°C."))
        if len(parts) > 2 and parts[2].lower() in ("active", "1"):
            out.append(Finding("gpu_throttle", WARNING, "gpu", "The GPU is slowing itself down to avoid overheating."))
        if len(parts) > 3 and parts[3].isdigit() and int(parts[3]) > 0:
            out.append(Finding("gpu_ecc", CRITICAL, "gpu", f"The GPU reports {parts[3]} uncorrectable memory error(s)."))
    return out


def check_temperatures(temps: Optional[dict] = None, run: Optional[Runner] = None) -> list[Finding]:
    """CPU / board temperatures when the PC exposes them (psutil on Linux; ACPI thermal zone on Windows)."""
    out = []
    readings: list[tuple[str, float]] = []
    if temps is None:
        temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
    for label, entries in (temps or {}).items():
        for e in entries:
            if getattr(e, "current", None):
                readings.append((label, float(e.current)))
    if not readings and run:
        for z in _rows(run("Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue | "
                           "Select-Object CurrentTemperature | ConvertTo-Json -Compress")):
            try:
                c = float(z.get("CurrentTemperature")) / 10 - 273.15      # tenths of a kelvin
                if 0 < c < 150:
                    readings.append(("the system", c))
            except (TypeError, ValueError):
                continue
    if readings:
        label, hottest = max(readings, key=lambda x: x[1])
        if hottest >= 95:
            out.append(Finding("cpu_hot", CRITICAL, "cpu", f"Temperature is dangerously high ({hottest:.0f}°C near {label}).",
                               "Save your work, close heavy programs, and make sure the vents aren't blocked."))
        elif hottest >= 88:
            out.append(Finding("cpu_warm", WARNING, "cpu", f"The PC is running hot ({hottest:.0f}°C).",
                               "Check the vents and fans."))
    return out


def check_battery(run: Runner) -> list[Finding]:
    out = []
    for b in _rows(run("Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity -ErrorAction SilentlyContinue | "
                       "Select-Object FullChargedCapacity | ConvertTo-Json -Compress")):
        full = b.get("FullChargedCapacity")
        for s in _rows(run("Get-CimInstance -Namespace root/wmi -ClassName BatteryStaticData -ErrorAction SilentlyContinue | "
                           "Select-Object DesignedCapacity | ConvertTo-Json -Compress")):
            design = s.get("DesignedCapacity")
            if isinstance(full, (int, float)) and isinstance(design, (int, float)) and design > 0:
                pct = full / design * 100
                if pct < 50:
                    out.append(Finding("battery_worn", WARNING, "battery",
                                       f"The battery holds only {pct:.0f}% of its original capacity.",
                                       "Consider replacing it."))
    return out


def check_memory_pressure() -> list[Finding]:
    sw = psutil.swap_memory()
    vm = psutil.virtual_memory()
    if vm.percent >= 95 and sw.percent >= 90:
        return [Finding("mem_exhausted", WARNING, "memory", "Memory and swap are almost full; the PC may freeze.",
                        "Close some programs. If this happens often, the PC needs more RAM.")]
    return []


# ---------------------------------------------------------------- orchestration
class CpuWatch:
    """Sustained very high CPU (a miner or a stuck program) rather than a brief spike."""

    def __init__(self, samples: int = 8, threshold: float = 95.0):
        self.window, self.threshold = deque(maxlen=samples), threshold

    def feed(self, cpu: Optional[float]) -> Optional[Finding]:
        if cpu is None:
            return None
        self.window.append(cpu)
        if len(self.window) == self.window.maxlen and min(self.window) >= self.threshold:
            top = ""
            try:
                procs = sorted(psutil.process_iter(["name", "cpu_percent"]), key=lambda p: p.info.get("cpu_percent") or 0, reverse=True)
                if procs:
                    top = f" The busiest program is {procs[0].info.get('name')}."
            except psutil.Error:
                pass
            return Finding("cpu_pinned", WARNING, "cpu", f"The CPU has been at {min(self.window):.0f}%+ for several minutes.{top}",
                           "A hidden miner or a stuck program can cause this. Ask me \"what's using my CPU\".")
        return None


def scan(run: Runner = powershell, quick: bool = False) -> list[Finding]:
    """Run every check. A failing check is skipped, never fatal. quick=True skips the slow Windows queries."""
    checks: list[Callable[[], list[Finding]]] = [check_processes, check_memory_pressure,
                                                 lambda: check_gpu(run), lambda: check_temperatures(run=run)]
    if not quick:
        checks += [lambda: check_defender(run), lambda: check_event_log(run), lambda: check_disks(run),
                   lambda: check_devices(run), lambda: check_battery(run)]
    found: list[Finding] = []
    for c in checks:
        try:
            found.extend(c())
        except Exception:
            continue
    seen, unique = set(), []
    for f in sorted(found, key=lambda f: f.level != CRITICAL):
        if f.key not in seen:
            seen.add(f.key)
            unique.append(f)
    return unique


def report(findings: list[Finding], area: str = "") -> str:
    """area: when the user asked about one part (gpu, cpu, disk...), answer about that part first."""
    names = {"gpu": "GPU", "cpu": "CPU", "disk": "storage", "memory": "memory", "malware": "viruses or malware",
             "motherboard": "motherboard", "battery": "battery", "network": "Wi-Fi or network", "bluetooth": "Bluetooth",
             "audio": "audio", "camera": "camera", "usb": "USB ports", "input": "keyboard or touchpad", "screen": "screen"}
    mine = [f for f in findings if not area or f.area == area]
    rest = [f for f in findings if area and f.area != area]
    lines = []
    if area and not mine:
        lines.append(f"No signs of {names.get(area, area)} trouble found.")
    elif not findings:
        return "No signs of malware or hardware trouble found."
    if mine:
        lines.append(f"Found {len(mine)} thing(s) to look at:" if not area else f"{names.get(area, area).capitalize()}:")
        lines += [f"- [{'NOTE' if f.level == INFO else f.level.upper()}] {f.text()}" for f in mine]
    if rest:
        lines.append(f"Elsewhere: {len(rest)} other thing(s). Ask \"is my PC healthy?\" for the full list.")
    lines.append("These are warning signs, not proof. I only looked; nothing was changed.")
    return "\n".join(lines)


class Watchdog:
    """Runs scans in the background and alerts (once per cooldown) when something is wrong."""
    COOLDOWN = {CRITICAL: 3600.0, WARNING: 6 * 3600.0}      # info findings are never announced

    def __init__(self, settings: config.Settings, on_alert: Callable[[str], None],
                 run: Runner = powershell, clock: Callable[[], float] = time.time):
        self.s, self.on_alert, self.run, self.clock = settings, on_alert, run, clock
        self._last: dict[str, float] = {}
        self._cpu = CpuWatch()
        self._stop = threading.Event()
        self.latest: list[Finding] = []
        self.on_result: Optional[Callable[[list[Finding]], None]] = None     # the home page's laptop model listens here

    def feed(self, stats: dict) -> None:
        """Called with every monitor reading; raises the sustained-CPU alert."""
        if not getattr(self.s, "watchdog_enabled", True):
            return
        f = self._cpu.feed(stats.get("cpu"))
        if f:
            self._announce([f])

    def _announce(self, findings: list[Finding]) -> list[str]:
        now, sent = self.clock(), []
        for f in findings:
            if f.level in self.COOLDOWN and now - self._last.get(f.key, -1e12) >= self.COOLDOWN[f.level]:
                self._last[f.key] = now
                msg = ("Serious problem: " if f.level == CRITICAL else "Heads up: ") + f.text()
                sent.append(msg)
                self.on_alert(msg)
        return sent

    def tick(self, quick: bool = False) -> list[str]:
        if not getattr(self.s, "watchdog_enabled", True):
            return []
        self.latest = scan(self.run, quick=quick)
        if self.on_result:
            self.on_result(self.latest)
        return self._announce(self.latest)

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        if self._stop.wait(45):
            return
        while True:
            try:
                self.tick()
            except Exception:
                pass
            minutes = max(5, int(getattr(self.s, "watchdog_interval_min", 15) or 15))
            if self._stop.wait(minutes * 60):
                break

    def stop(self) -> None:
        self._stop.set()


def as_dicts(findings: list[Finding]) -> list[dict]:
    return [asdict(f) for f in findings]
