"""The Devices page: a live map of your network, plus the phone link and the PC link."""
from __future__ import annotations

import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from . import config, netmap, peers, phonelink, tools_v4

KIND_COLOUR = {"internet": "#8190b3", "router": "#f59e0b", "me": "#38bdf8", "laptop_ai": "#22c55e", "pc": "#a78bfa",
               "phone": "#f472b6", "tv": "#fb923c", "printer": "#94a3b8", "speaker": "#2dd4bf", "camera": "#f87171", "other": "#64748b"}
BG = "#09101f"


class TopologyCanvas(tk.Canvas):
    """The map. Click a device to select it; small light-dots travel along the lines so it feels alive."""

    def __init__(self, master, on_select: Callable, **kw):
        super().__init__(master, bg=BG, highlightthickness=0, **kw)
        self.devices: list = []
        self.on_select = on_select
        self.selected = ""
        self.hit: list = []
        self._phase = 0.0
        self._pos: dict = {}
        self._edges: list = []
        self._job = None
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._click)
        self.after(120, self._tick)

    def set_devices(self, devices: list) -> None:
        self.devices = devices
        self.redraw()

    def _click(self, e) -> None:
        for dev_id, x, y in self.hit:
            if abs(e.x - x) < 34 and abs(e.y - y) < 30:
                self.selected = dev_id
                self.on_select(next((d for d in self.devices if d.ip == dev_id), None))
                self.redraw()
                return

    def redraw(self) -> None:
        if not self.winfo_exists():
            return
        self.delete("all")
        w, h = max(self.winfo_width(), 360), max(self.winfo_height(), 260)
        if not self.devices:
            self.create_text(w / 2, h / 2, text="No scan yet.\nPress  Scan network  to see everything on your Wi-Fi.",
                             fill="#8190b3", font=("Segoe UI", 12), justify="center")
            return
        lay = netmap.layout(self.devices, w, h)
        pos = {nid: (x, y) for nid, x, y, _k, _l in lay["nodes"]}
        self._pos, self._edges = pos, lay["edges"]
        for a, b in lay["edges"]:
            (x1, y1), (x2, y2) = pos[a], pos[b]
            self.create_line(x1, y1, x2, y2, fill="#1b2a52", width=2)
        self.hit = []
        me = next((d for d in self.devices if d.is_me), None)
        by_ip = {d.ip: d for d in self.devices}
        for nid, x, y, kind, label in lay["nodes"]:
            colour = KIND_COLOUR.get(kind, "#64748b")
            if me and nid == me.ip:
                kind, colour = "me", KIND_COLOUR["me"]
            sel = nid == self.selected
            r = 26 if kind in ("router", "internet") else 22
            if sel:
                self.create_oval(x - r - 8, y - r - 8, x + r + 8, y + r + 8, outline=colour, width=1, dash=(3, 3))
            self.create_oval(x - r, y - r, x + r, y + r, fill="#0d1730", outline=colour, width=2)
            self._icon(kind, x, y, colour)
            short = label if len(label) <= 16 else label[:15] + "…"
            self.create_text(x, y + r + 12, text=short, fill="#e8eefc", font=("Segoe UI", 9))
            dev = by_ip.get(nid)
            if dev is not None and dev.is_new:
                self.create_oval(x + r - 7, y - r - 1, x + r + 7, y - r + 13, fill="#fbbf24", outline="")
                self.create_text(x + r, y - r + 6, text="N", fill="#1a1200", font=("Segoe UI", 8, "bold"))
            if nid not in ("internet",):
                self.hit.append((nid, x, y))

    def _icon(self, kind: str, x: float, y: float, col: str) -> None:
        """Little line drawings (no special characters, so they look the same on every PC)."""
        L = lambda *pts, **k: self.create_line(*pts, fill=col, width=2, capstyle="round", **k)       # noqa: E731
        if kind == "internet":
            self.create_oval(x - 11, y - 11, x + 11, y + 11, outline=col, width=2)
            self.create_oval(x - 5, y - 11, x + 5, y + 11, outline=col, width=1)
            L(x - 11, y, x + 11, y)
        elif kind == "router":
            self.create_rectangle(x - 12, y - 1, x + 12, y + 9, outline=col, width=2)
            L(x - 7, y - 1, x - 10, y - 11)
            L(x + 7, y - 1, x + 10, y - 11)
            self.create_oval(x - 1.5, y + 3, x + 1.5, y + 6, fill=col, outline="")
        elif kind in ("me", "pc", "laptop_ai"):
            self.create_rectangle(x - 10, y - 9, x + 10, y + 4, outline=col, width=2)
            L(x - 13, y + 8, x + 13, y + 8)
            if kind == "laptop_ai":
                self.create_oval(x - 2, y - 5, x + 2, y - 1, fill=col, outline="")
        elif kind == "phone":
            self.create_rectangle(x - 6, y - 11, x + 6, y + 11, outline=col, width=2)
            L(x - 2, y + 7, x + 2, y + 7)
        elif kind == "tv":
            self.create_rectangle(x - 12, y - 8, x + 12, y + 5, outline=col, width=2)
            L(x - 5, y + 10, x + 5, y + 10)
        elif kind == "printer":
            self.create_rectangle(x - 11, y - 3, x + 11, y + 8, outline=col, width=2)
            self.create_rectangle(x - 6, y - 10, x + 6, y - 3, outline=col, width=2)
        elif kind == "speaker":
            self.create_oval(x - 9, y - 9, x + 9, y + 9, outline=col, width=2)
            self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=col, outline="")
        elif kind == "camera":
            self.create_rectangle(x - 11, y - 6, x + 11, y + 7, outline=col, width=2)
            self.create_oval(x - 4, y - 3, x + 4, y + 4, outline=col, width=2)
        else:
            self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=col, outline="")

    def _tick(self) -> None:
        """Move a light-dot along each line, only while the page is on screen."""
        try:
            if self.winfo_ismapped() and self._edges:
                self.delete("dot")
                self._phase = (self._phase + 0.04) % 1.0
                for i, (a, b) in enumerate(self._edges):
                    if a in self._pos and b in self._pos:
                        (x1, y1), (x2, y2) = self._pos[a], self._pos[b]
                        t = (self._phase + i * 0.13) % 1.0
                        x, y = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
                        self.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill="#38bdf8", outline="", tags="dot")
            self._job = self.after(110, self._tick)
        except tk.TclError:
            pass


class LivePCView(ctk.CTkToplevel):
    """A small window that keeps asking a linked PC for its numbers every few seconds and draws them."""

    def __init__(self, master, name: str, fonts, colours: dict, post: Callable):
        super().__init__(master)
        self.name, self.F, self.c, self.post = name, fonts, colours, post
        self.title(f"{name} — live")
        self.geometry("360x320")
        self.configure(fg_color=colours.get("bg", BG))
        self._history: dict[str, list] = {"cpu": [], "ram": [], "disk": []}
        self._alive = True
        ctk.CTkLabel(self, text=name, font=fonts(18, "bold"), text_color=colours["text"]).pack(pady=(14, 2))
        self.status = ctk.CTkLabel(self, text="Asking...", font=fonts(12), text_color=colours["muted"])
        self.status.pack()
        self.bars: dict[str, ctk.CTkProgressBar] = {}
        self.labels: dict[str, ctk.CTkLabel] = {}
        for key, label in (("cpu", "CPU"), ("ram", "Memory"), ("disk", "Disk"), ("battery", "Battery")):
            row = ctk.CTkFrame(self, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=6)
            ctk.CTkLabel(row, text=label, width=70, anchor="w", font=fonts(13), text_color=colours["text"]).pack(side="left")
            bar = ctk.CTkProgressBar(row, width=160, progress_color=colours["accent"])
            bar.set(0)
            bar.pack(side="left", padx=8)
            val = ctk.CTkLabel(row, text="—", width=40, font=fonts(13, "bold"), text_color=colours["text"])
            val.pack(side="left")
            self.bars[key], self.labels[key] = bar, val
        self.top_label = ctk.CTkLabel(self, text="", font=fonts(12), text_color=colours["muted"], wraplength=300, justify="left")
        self.top_label.pack(pady=(10, 4), padx=20, anchor="w")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._poll()

    def close(self) -> None:
        self._alive = False
        self.destroy()

    def _poll(self) -> None:
        if not self._alive:
            return
        threading.Thread(target=self._fetch, daemon=True).start()
        self.after(3000, self._poll)

    def _fetch(self) -> None:
        d = peers.fetch_status(self.name)
        if self._alive:
            self.post(lambda: self._show(d))               # we're on a worker thread: never call Tk's .after() from here

    def _show(self, d: dict) -> None:
        if not self.winfo_exists():
            return
        if "error" in d:
            self.status.configure(text=d["error"], text_color="#f87171")
            return
        self.status.configure(text="Live", text_color=self.c["accent"])
        for key in ("cpu", "ram", "disk", "battery"):
            v = d.get(key)
            if v is None:
                continue
            self.bars[key].set(max(0.0, min(1.0, v / 100)))
            self.labels[key].configure(text=f"{v:.0f}%")
        self.top_label.configure(text="Busiest: " + ", ".join(d.get("top") or []) if d.get("top") else "")


class LivePhoneView(ctk.CTkToplevel):
    """A mirror of the phone: a picture of its screen that refreshes every few seconds, plus battery/storage/app text.
    Read-only, like looking over its shoulder — it never taps or types anything on the phone."""

    MIRROR_EVERY_MS = 1500          # the gap between pictures; each picture itself still takes adb a moment to grab

    def __init__(self, master, fonts, colours: dict, post: Callable, shot_path=None):
        super().__init__(master)
        self.F, self.c, self.post = fonts, colours, post
        self.shot_path = shot_path or (config.APP_DIR / "phone_live.png")
        self.title("Phone — live")
        self.geometry("300x560")
        self.configure(fg_color=colours.get("bg", BG))
        self._alive = True
        self._busy = False               # don't stack up a second screenshot while one is still being fetched
        self._ctk_image = None
        self.screen = ctk.CTkLabel(self, text="Asking for a picture of the screen...", font=fonts(12),
                                   text_color=colours["muted"], fg_color="#0d1730", corner_radius=14,
                                   width=260, height=440, wraplength=220)
        self.screen.pack(padx=18, pady=(18, 10))
        self.text = ctk.CTkLabel(self, text="", font=fonts(12), text_color=colours["text"], wraplength=260, justify="left")
        self.text.pack(padx=18, pady=(0, 16), anchor="w")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._poll()

    def close(self) -> None:
        self._alive = False
        self.destroy()

    def _poll(self) -> None:
        if not self._alive:
            return
        if not self._busy:                # a slow phone shouldn't make screenshots pile up behind each other
            self._busy = True
            threading.Thread(target=self._fetch, daemon=True).start()
        self.after(self.MIRROR_EVERY_MS, self._poll)

    def _fetch(self) -> None:
        st = phonelink.status()
        problem = None if "error" in st else phonelink.screenshot(self.shot_path)
        self._busy = False
        if self._alive:
            self.post(lambda: self._show(st, problem))      # we're on a worker thread: never call Tk's .after() from here

    def _show(self, st: dict, shot_problem: Optional[str]) -> None:
        if not self.winfo_exists():
            return
        if "error" in st:
            self.text.configure(text=st["error"])
            self.screen.configure(image=None, text=st["error"])
            return
        self.text.configure(text=phonelink.describe(st))
        if shot_problem:
            self.screen.configure(image=None, text=shot_problem)
            return
        try:
            from PIL import Image
            img = Image.open(self.shot_path)
            img.thumbnail((260, 440))
            self._ctk_image = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self.screen.configure(image=self._ctk_image, text="")
        except Exception:
            self.screen.configure(image=None, text="Couldn't show the screen picture.")


class DevicesPage(ctk.CTkFrame):
    def __init__(self, master, post: Callable, fonts, colours: dict, my_name: Callable[[], str], get_settings: Callable):
        super().__init__(master, fg_color="transparent")
        self.post, self.F, self.c = post, fonts, colours
        self.my_name, self.get_settings = my_name, get_settings
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        c = colours
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=34, pady=(26, 8))
        ctk.CTkLabel(head, text="Devices", font=fonts(28, "bold"), text_color=c["text"], anchor="w").pack(fill="x")
        ctk.CTkLabel(head, text="Everything on your network, your phone, and other PCs running Laptop AI.", font=fonts(14),
                     text_color=c["muted"], anchor="w").pack(fill="x")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=26, pady=(0, 18))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2, minsize=320)
        body.grid_rowconfigure(0, weight=1)

        # ---- left: the map
        left = ctk.CTkFrame(body, fg_color=BG, corner_radius=18, border_width=1, border_color=c["line"])
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 8))
        bar = ctk.CTkFrame(left, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(bar, text="NETWORK MAP", font=fonts(12, "bold"), text_color=c["muted"]).pack(side="left")
        self.scan_btn = ctk.CTkButton(bar, text="Scan network", height=32, width=120, corner_radius=16, font=fonts(13, "bold"),
                                      fg_color=c["accent"], hover_color=c["accent_hover"], command=self.scan)
        self.scan_btn.pack(side="right")
        self.watch_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(bar, text="Keep watching", variable=self.watch_var, font=fonts(12), progress_color=c["accent"],
                      command=self._toggle_watch).pack(side="right", padx=10)
        self.scan_text = ctk.CTkLabel(left, text="", font=fonts(12), text_color=c["muted"], anchor="w")
        self.scan_text.pack(fill="x", padx=18)
        self.wifi_card = ctk.CTkFrame(left, fg_color="transparent")
        self.wifi_card.pack(fill="x", padx=18, pady=(2, 0))
        self.wifi_label = ctk.CTkLabel(self.wifi_card, text="", font=fonts(12), text_color=c["muted"], anchor="w")
        self.wifi_label.pack(side="left")
        self._refresh_wifi()
        self.map = TopologyCanvas(left, self._select)
        self.map.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self.detail = ctk.CTkLabel(left, text="Click a device to see details.", font=fonts(13), text_color=c["muted"],
                                   justify="left", anchor="w", wraplength=440)
        self.detail.pack(fill="x", padx=18, pady=(0, 6))
        self.rename_row = ctk.CTkFrame(left, fg_color="transparent")
        self.rename_row.pack(fill="x", padx=18, pady=(0, 14))
        self.rename_entry = ctk.CTkEntry(self.rename_row, width=170, placeholder_text="Give it a name", fg_color=c["card2"], border_color=c["line"])
        self.rename_entry.pack(side="left")
        self.rename_entry.bind("<Return>", lambda _e: self._rename())
        ctk.CTkButton(self.rename_row, text="Save name", width=90, height=28, corner_radius=14, fg_color=c["card2"], hover_color=c["line"],
                      text_color=c["text"], font=fonts(12), command=self._rename).pack(side="left", padx=8)
        self.link_btn = ctk.CTkButton(self.rename_row, text="Link to this PC", width=120, height=28, corner_radius=14, fg_color=c["accent"],
                                      hover_color=c["accent_hover"], font=fonts(12, "bold"), command=self._link_selected)
        self.chosen = None

        # ---- right: phone and PC link
        right = ctk.CTkScrollableFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        self.phone_card = self._card(right, "Phone link (Android)")
        self.phone_text = ctk.CTkLabel(self.phone_card, text="Not linked yet. Plug your phone in with a USB cable and switch on USB debugging "
                                       "(Settings > Developer options). iPhones can't be linked this way; use the Telegram link.",
                                       font=fonts(12), text_color=c["muted"], justify="left", anchor="w", wraplength=270)
        self.phone_text.pack(fill="x", pady=(0, 8))
        row = ctk.CTkFrame(self.phone_card, fg_color="transparent")
        row.pack(fill="x")
        for i, (text, cmd) in enumerate((("Set up", self.setup_phone), ("Check phone", self.check_phone),
                                         ("Notifications", self.phone_notes), ("Screen picture", self.phone_screen),
                                         ("Live view", self.phone_live), ("Send file", self.phone_send_file),
                                         ("Apps", self.phone_apps_list))):
            ctk.CTkButton(row, text=text, width=118, height=30, corner_radius=15, font=fonts(12), fg_color=c["card2"],
                          hover_color=c["line"], text_color=c["text"], command=cmd).grid(row=i // 2, column=i % 2, padx=(0, 6), pady=3, sticky="w")
        self.pc_card = self._card(right, "PC link (another Laptop AI)")
        self.allow_var = ctk.BooleanVar(value=bool(get_settings().allow_pc_link))
        ctk.CTkSwitch(self.pc_card, text="Allow other PCs to link to this one", variable=self.allow_var, font=fonts(13),
                      progress_color=c["accent"], command=self.toggle_allow).pack(anchor="w")
        self.code_label = ctk.CTkLabel(self.pc_card, text="", font=fonts(22, "bold"), text_color=c["accent"], anchor="w")
        self.code_label.pack(anchor="w", pady=(6, 0))
        self._code_until = 0.0
        self.pc_text = ctk.CTkLabel(self.pc_card, text="They can only look at this PC's health. They can't change anything.", font=fonts(12),
                                    text_color=c["muted"], justify="left", anchor="w", wraplength=270)
        self.pc_text.pack(fill="x", pady=(4, 8))
        lrow = ctk.CTkFrame(self.pc_card, fg_color="transparent")
        lrow.pack(fill="x")
        self.host_entry = ctk.CTkEntry(lrow, width=120, placeholder_text="Their address", fg_color=c["card2"], border_color=c["line"])
        self.host_entry.pack(side="left")
        self.code_entry = ctk.CTkEntry(lrow, width=80, placeholder_text="Code", fg_color=c["card2"], border_color=c["line"])
        self.code_entry.pack(side="left", padx=5)
        ctk.CTkButton(lrow, text="Link", width=50, height=28, corner_radius=14, font=fonts(12, "bold"), fg_color=c["accent"],
                      hover_color=c["accent_hover"], command=self.link_manual).pack(side="left")
        self.linked_box = ctk.CTkFrame(self.pc_card, fg_color="transparent")
        self.linked_box.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(self.pc_card, text="PCs allowed to check this one", font=fonts(12, "bold"),
                     text_color=c["muted"], anchor="w").pack(fill="x", pady=(14, 2))
        self.incoming_box = ctk.CTkFrame(self.pc_card, fg_color="transparent")
        self.incoming_box.pack(fill="x")
        ctk.CTkLabel(self.pc_card, text="Files received over the link", font=fonts(12, "bold"),
                     text_color=c["muted"], anchor="w").pack(fill="x", pady=(14, 2))
        self.received_box = ctk.CTkFrame(self.pc_card, fg_color="transparent")
        self.received_box.pack(fill="x")
        self._show_linked()
        self._show_incoming()
        self._show_received()
        self._load_last()

    def _card(self, parent, title: str) -> ctk.CTkFrame:
        c = self.c
        card = ctk.CTkFrame(parent, corner_radius=16, fg_color=c["card"], border_width=1, border_color=c["line"])
        card.pack(fill="x", padx=4, pady=(0, 10))
        ctk.CTkLabel(card, text=title, font=self.F(15, "bold"), text_color=c["text"], anchor="w").pack(fill="x", padx=16, pady=(12, 6))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=(0, 12))
        return inner

    # ---- work in the background, show on the screen thread
    def _bg(self, work: Callable, done: Callable) -> None:
        def run():
            try:
                result = work()
            except Exception as e:              # never let a worker crash the app
                result = {"error": str(e)}
            self.post(lambda: done(result))
        threading.Thread(target=run, daemon=True).start()

    # ---- the map
    def _load_last(self) -> None:
        when, devices = netmap.load()
        if devices:
            self.map.set_devices(devices)
            self.scan_text.configure(text=f"Last scan {time.strftime('%H:%M', time.localtime(when))}: {len(devices) - 1} other devices.")

    def scan(self) -> None:
        if getattr(self, "_scanning", False):
            return
        self._scanning = True
        self.scan_btn.configure(state="disabled", text="Scanning...")
        self.scan_text.configure(text="Starting...")

        def progress(text, frac):
            self.post(lambda: self.scan_text.configure(text=text))

        self._bg(lambda: netmap.scan(progress), self._scan_done)

    def _scan_done(self, result) -> None:
        self._scanning = False
        self.scan_btn.configure(state="normal", text="Scan network")
        if isinstance(result, dict):
            self.scan_text.configure(text=f"The scan didn't work: {result.get('error')}")
            return
        self.map.set_devices(result)
        new_ones = [d for d in result if d.is_new]
        text = f"Found {len(result) - 1} other devices. Click one for details."
        if new_ones:
            text += f"  {len(new_ones)} new: " + ", ".join(d.label for d in new_ones) + "."
        self.scan_text.configure(text=text)
        self._show_linked()

    # ---- keep watching: a quiet re-scan every few minutes while this page is open
    def _toggle_watch(self) -> None:
        if self.watch_var.get():
            self._watch_tick()

    def _watch_tick(self) -> None:
        if not self.watch_var.get() or not self.winfo_exists():
            return
        if not getattr(self, "_scanning", False) and self.winfo_ismapped():
            self.scan()
        self.after(300_000, self._watch_tick)          # every 5 minutes

    def _refresh_wifi(self) -> None:
        self._bg(netmap.wifi_info, self._wifi_done)

    def _wifi_done(self, info) -> None:
        if not isinstance(info, dict) or not info.get("connected"):
            self.wifi_label.configure(text="")
            return
        bits = [f"Your Wi-Fi: {info['ssid']}" if info.get("ssid") else "Your Wi-Fi"]
        if info.get("signal_pct") is not None:
            bits.append(f"signal {info['signal_pct']}%")
        if info.get("mbps"):
            bits.append(f"{info['mbps']:.0f} Mbps")
        self.wifi_label.configure(text="  ·  ".join(bits))

    def _select(self, dev) -> None:
        self.chosen = dev
        if dev is None:
            return
        bits = [f"{dev.label}  ·  {dev.kind_label}" + (f" ({dev.vendor})" if dev.vendor else "") + ("   (new)" if dev.is_new else ""),
                f"Address {dev.ip}" + (f"   Hardware {dev.mac}" if dev.mac else "")]
        if dev.latency_ms is not None:
            bits.append(f"Answered in {dev.latency_ms:.0f} ms" + (" (fast)" if dev.latency_ms < 10 else
                                                                   " (slow — may be far from the router or busy)" if dev.latency_ms > 100 else "."))
        if dev.is_new:
            bits.append("First time I've seen this on your network.")
        if netmap.private_mac(dev.mac) and dev.kind == "phone":
            bits.append("Uses a private address, which phones do to protect you.")
        if dev.ports:
            bits.append("Open doors: " + ", ".join(str(p) for p in dev.ports))
        if dev.risk:
            bits.append("⚠ " + dev.risk)
        self.detail.configure(text="\n".join(bits))
        self.rename_entry.delete(0, "end")
        self.rename_entry.insert(0, dev.alias)
        if dev.kind == "laptop_ai":
            self.link_btn.pack(side="left")
        else:
            self.link_btn.pack_forget()
        if dev.kind == "phone":
            self.phone_text.configure(text=f"{dev.label} is on your network. To see inside it, plug it in with USB and tap Set up.")

    def _rename(self) -> None:
        if self.chosen is not None:
            netmap.rename(self.chosen, self.rename_entry.get())
            netmap.save(self.map.devices)
            self.map.redraw()
            self._select(self.chosen)

    def _link_selected(self) -> None:
        if self.chosen is not None:
            self.host_entry.delete(0, "end")
            self.host_entry.insert(0, self.chosen.ip)
            self.code_entry.focus_set()
            self.pc_text.configure(text="Type the 6-digit code shown on that PC (Devices > Allow other PCs), then press Link.")

    # ---- phone
    def _say_phone(self, text: str) -> None:
        self.phone_text.configure(text=text)

    def setup_phone(self) -> None:
        if phonelink.adb_path():
            self._say_phone("The phone tool is ready. On the phone: Settings > About phone > tap Build number 7 times, then Developer options > "
                            "USB debugging. Plug in the cable and tap Allow on the phone. Then press Check phone.")
            return
        self._say_phone("Downloading Google's phone tool (about 8 MB)...")
        self._bg(lambda: phonelink.download_adb(lambda t: self.post(lambda: self._say_phone(t))),
                 lambda r: self._say_phone(r if isinstance(r, str) and r else
                                           "Done. Now switch on USB debugging on the phone (Settings > Developer options), plug it in "
                                           "and tap Allow on the phone, then press Check phone."))

    def check_phone(self) -> None:
        self._say_phone("Looking for your phone...")
        self._bg(phonelink.status, lambda st: self._say_phone(phonelink.describe(st)))

    def phone_notes(self) -> None:
        self._say_phone("Reading notifications...")
        self._bg(lambda: tools_v4.phone_notifications(), lambda raw: self._say_phone(_text(raw)))

    def phone_screen(self) -> None:
        self._say_phone("Taking a picture of the phone's screen...")
        self._bg(lambda: tools_v4.phone_screenshot(), lambda raw: self._say_phone(_text(raw)))

    def phone_live(self) -> None:
        LivePhoneView(self, self.F, self.c, self.post)

    def phone_send_file(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self)
        if not path:
            return
        self._say_phone(f"Sending {Path(path).name} to the phone...")
        self._bg(lambda: phonelink.push_file(path), lambda r: self._say_phone(r[1]))

    def phone_apps_list(self) -> None:
        self._say_phone("Reading the phone's apps...")
        self._bg(phonelink.list_apps, self._show_phone_apps)

    def _show_phone_apps(self, apps: list) -> None:
        if not apps:
            self._say_phone("I couldn't list the phone's apps. Is it linked and unlocked?")
            return
        win = ctk.CTkToplevel(self)
        win.title("Phone apps")
        win.geometry("280x420")
        win.configure(fg_color=self.c.get("bg", BG))
        box = ctk.CTkScrollableFrame(win, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=10, pady=10)
        for a in apps:
            ctk.CTkButton(box, text=a["label"], anchor="w", height=32, corner_radius=10, font=self.F(13),
                          fg_color=self.c["card2"], hover_color=self.c["line"], text_color=self.c["text"],
                          command=lambda pkg=a["package"]: (self._bg(lambda: phonelink.launch_app(pkg),
                                                                     lambda r: self._say_phone(r[1])), win.destroy())
                          ).pack(fill="x", pady=2)

    # ---- other PCs
    def toggle_allow(self) -> None:
        on = self.allow_var.get()
        self._bg(lambda: tools_v4.allow_pc_link(on), self._allow_done)

    def _allow_done(self, raw) -> None:
        import json
        try:
            d = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            d = {"status": "error", "message": "Something went wrong."}
        if d.get("status") == "ok":
            if d.get("code"):
                self._code = d["code"]
                self._code_until = time.time() + d.get("seconds_left", 600)
                self._code_tick()
                self.pc_text.configure(text=(f"On the other PC, type this PC's address ({netmap.local_ip()}) and the code below. "
                                             "If Windows asks about the firewall, allow Private networks — Laptop AI tries to "
                                             "add that permission itself, but Windows sometimes still asks."))
            else:
                self._code_until = 0.0
                self.code_label.configure(text="")
                self.pc_text.configure(text="Other PCs can no longer link to this one.")
        else:
            self.allow_var.set(False)
            self.pc_text.configure(text=d.get("message", "I couldn't switch that on."))

    def _code_tick(self) -> None:
        if not self.winfo_exists():
            return
        left = self._code_until - time.time()
        if left <= 0:
            self.code_label.configure(text="Code expired — flip the switch off and on for a new one", text_color=self.c["muted"])
            return
        self.code_label.configure(text=f"Code  {getattr(self, '_code', '')}   ({int(left // 60)}:{int(left % 60):02d} left)",
                                  text_color=self.c["accent"])
        self.after(1000, self._code_tick)

    def link_manual(self) -> None:
        host, code = self.host_entry.get().strip(), self.code_entry.get().strip()
        if not host or not code:
            self.pc_text.configure(text="Type the other PC's address and the 6-digit code.")
            return
        self.pc_text.configure(text="Linking...")
        self._bg(lambda: peers.pair(host, code, self.my_name()), self._link_done)

    def _link_done(self, result) -> None:
        if isinstance(result, dict):
            self.pc_text.configure(text=result.get("error", "Failed."))
            return
        worked, message = result
        self.pc_text.configure(text=message)
        if worked:
            self.code_entry.delete(0, "end")
            self._show_linked()

    def _show_linked(self) -> None:
        for child in self.linked_box.winfo_children():
            child.destroy()
        for name in peers.linked():
            row = ctk.CTkFrame(self.linked_box, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text="●  " + name, font=self.F(13), text_color=self.c["text"], anchor="w").pack(side="left")
            ctk.CTkButton(row, text="Unlink", width=56, height=24, corner_radius=12, font=self.F(11), fg_color=self.c["card2"],
                          hover_color=self.c["line"], text_color=self.c["muted"], command=lambda n=name: self._unlink(n)).pack(side="right")
            ctk.CTkButton(row, text="Check", width=56, height=24, corner_radius=12, font=self.F(11), fg_color=self.c["accent"],
                          hover_color=self.c["accent_hover"], command=lambda n=name: self._check_pc(n)).pack(side="right", padx=4)
            ctk.CTkButton(row, text="Live", width=48, height=24, corner_radius=12, font=self.F(11), fg_color=self.c["card2"],
                          hover_color=self.c["line"], text_color=self.c["text"],
                          command=lambda n=name: LivePCView(self, n, self.F, self.c, self.post)).pack(side="right", padx=4)
            ctk.CTkButton(row, text="Send file", width=72, height=24, corner_radius=12, font=self.F(11), fg_color=self.c["card2"],
                          hover_color=self.c["line"], text_color=self.c["text"],
                          command=lambda n=name: self._send_file(n)).pack(side="right", padx=4)

    def _check_pc(self, name: str) -> None:
        self.pc_text.configure(text=f"Asking {name}...")
        self._bg(lambda: peers.fetch_status(name), lambda d: self.pc_text.configure(text=peers.describe(d)))

    def _unlink(self, name: str) -> None:
        peers.unlink(name)
        self._show_linked()

    def _send_file(self, name: str) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self)
        if not path:
            return
        self.pc_text.configure(text=f"Sending to {name}...")
        self._bg(lambda: peers.send_file(name, path), lambda r: (self.pc_text.configure(text=r[1]), self._show_received()))

    def _show_incoming(self) -> None:
        for child in self.incoming_box.winfo_children():
            child.destroy()
        entries = peers.list_incoming()
        if not entries:
            ctk.CTkLabel(self.incoming_box, text="No PC has linked to this one yet.", font=self.F(12),
                        text_color=self.c["muted"], anchor="w").pack(fill="x")
            return
        for e in entries:
            row = ctk.CTkFrame(self.incoming_box, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text="●  " + e["name"], font=self.F(13), text_color=self.c["text"], anchor="w").pack(side="left")
            ctk.CTkButton(row, text="Revoke", width=64, height=24, corner_radius=12, font=self.F(11), fg_color=self.c["card2"],
                          hover_color="#7f1d1d", text_color=self.c["muted"],
                          command=lambda k=e["key"]: self._revoke_incoming(k)).pack(side="right")

    def _revoke_incoming(self, key: str) -> None:
        peers.revoke_incoming(key)
        self._show_incoming()

    def _show_received(self) -> None:
        for child in self.received_box.winfo_children():
            child.destroy()
        files = peers.list_received()
        if not files:
            ctk.CTkLabel(self.received_box, text="No files received yet.", font=self.F(12),
                        text_color=self.c["muted"], anchor="w").pack(fill="x")
            return
        for f in files[:8]:
            row = ctk.CTkFrame(self.received_box, fg_color="transparent")
            row.pack(fill="x", pady=2)
            kb = f["bytes"] / 1024
            size = f"{kb / 1024:.1f} MB" if kb > 1024 else f"{kb:.0f} KB"
            ctk.CTkLabel(row, text=f"●  {f['name']}  ({size})", font=self.F(12), text_color=self.c["text"],
                        anchor="w", wraplength=220).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(self.received_box, text="Open folder", width=100, height=24, corner_radius=12, font=self.F(11),
                      fg_color=self.c["card2"], hover_color=self.c["line"], text_color=self.c["text"],
                      command=self._open_received_folder).pack(anchor="w", pady=(4, 0))

    def _open_received_folder(self) -> None:
        import os
        import sys
        folder = str(peers._received_dir())
        try:
            if sys.platform == "win32":
                os.startfile(folder)           # noqa: S606
        except OSError:
            pass


def _text(raw) -> str:
    import json
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
        return d.get("text") or d.get("message") or d.get("error") or "Done."
    except (ValueError, AttributeError):
        return str(raw)
