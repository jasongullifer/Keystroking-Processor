import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import csv
from datetime import datetime
from lxml import etree
from docx import Document
from PIL import Image, ImageDraw, ImageFont
from moviepy import ImageSequenceClip
import tkinter.font as tkfont
import tempfile
import threading
import sys
import json
import ijson
import math
import random
from itertools import accumulate

# Output video framerate used in save_video; time trims snap to this grid when timing controls are on.
VIDEO_OUTPUT_FPS = 20
_PROGRAM_DIR = os.path.dirname(os.path.abspath(__file__))
_SETTINGS_JSON = os.path.join(_PROGRAM_DIR, 'xml-to-text-settings.json')

# Main settings panel: smooth scroll uses yview_moveto (Canvas yview_scroll has no "pixels" mode).
_MAIN_SCROLL_PIXELS_PER_WHEEL_NOTCH = 52.0  # one typical mouse notch (delta ±120 on Windows)
_MAIN_SCROLL_MAC_DELTA_TO_PIXELS = 9.0  # Tk delta on macOS is small integers per gesture
_MAIN_SCROLL_LINUX_BUTTON_PIXELS = 40.0  # Button-4/5 one "click"


def _batch_upload_subdir_name():
    return f"BATCH UPLOAD {datetime.now().strftime('%Y-%m-%d %H-%M-%S')}"


def _frame_starts_new_word(text_states, i):
    """First character of a word: after whitespace or at document start."""
    if i < 0 or i >= len(text_states):
        return False
    if i == 0:
        return True
    prev, t = text_states[i - 1], text_states[i]
    if len(t) < len(prev):
        return False
    if len(t) != len(prev) + 1:
        return False
    c = t[-1]
    if c.isspace():
        return False
    return not prev or prev[-1].isspace()


def _frame_at_word_end_boundary(text_states, i, n):
    """After a word delimiter, or last frame of the session."""
    t = text_states[i]
    if not t:
        return True
    if t[-1].isspace():
        return True
    return i >= n - 1


def _text_ends_with_sentence_closer(s):
    """Text ends with sentence-ending punctuation (ignores trailing whitespace)."""
    s = (s or "").rstrip()
    return bool(s) and s[-1] in ".!?"


def _document_has_sentence_punctuation(text_states):
    return any(any(c in t for c in ".!?") for t in text_states)


def _document_has_newline(text_states):
    return any("\n" in t for t in text_states)


def _frame_starts_sentence(text_states, i):
    """First character of input, first char after . ! ? (ignoring trailing ws), or first char of a new line."""
    if i < 0 or i >= len(text_states):
        return False
    cur = text_states[i]
    if not cur:
        return False
    if i == 0:
        return True
    prev = text_states[i - 1]
    if len(cur) != len(prev) + 1:
        return False
    new_ch = cur[-1]
    if new_ch.isspace():
        return False
    pst = prev.rstrip()
    if pst and pst[-1] in ".!?":
        return True
    if prev and prev[-1] == "\n":
        return True
    return False


def _frame_immediately_before_newline(text_states, j, n):
    """Frame j is the line content right before Enter adds a newline."""
    if j < 0 or j + 1 >= n:
        return False
    prev, nxt = text_states[j], text_states[j + 1]
    if len(nxt) != len(prev) + 1:
        return False
    return nxt[-1] == "\n" and not prev.endswith("\n")


def _xml_output_is_stackable(o):
    """Keyboard outputs that change text in XML mode (SPACE or single character)."""
    if o is None:
        return False
    if o == "SPACE":
        return True
    return isinstance(o, str) and len(o) == 1


def _data_event_is_stackable(event):
    """Events that change text in data.txt / IDFX mode (same rules as reconstruct)."""
    o = event.get("output")
    if not isinstance(o, str):
        return False
    if o.lower() == "backspace":
        return False
    if o in ("space", "enter"):
        return True
    return len(o) == 1


def filter_events_remove_backspace_edits(events, event_format="data"):
    """
    Remove backspace key events and any typing events that were undone by backspace,
    so the video only shows the surviving text (e.g. type 's', backspace, type 'a' -> only 'a').
    Non-stackable pass-through events (e.g. odd XML keys) are kept in order.
    event_format: 'xml' (SPACE, BACK, single char) or 'data' (space, enter, backspace, single char).
    """
    if not events:
        return []
    result = []
    stack = []
    for event in events:
        o = event.get("output")
        if event_format == "xml":
            if o == "BACK":
                if stack:
                    stack.pop()
                    for i in range(len(result) - 1, -1, -1):
                        if _xml_output_is_stackable(result[i].get("output")):
                            del result[i]
                            break
                continue
            if _xml_output_is_stackable(o):
                stack.append(event)
            result.append(event)
        else:
            if isinstance(o, str) and o.lower() == "backspace":
                if stack:
                    stack.pop()
                    for i in range(len(result) - 1, -1, -1):
                        if _data_event_is_stackable(result[i]):
                            del result[i]
                            break
                continue
            if _data_event_is_stackable(event):
                stack.append(event)
            result.append(event)
    return result


def filter_idfx_strip_backspace_blocks_and_preceding_keys(events):
    """
    IDFX-specific backspace cleanup when "Hide backspace edits" is on.

    For each contiguous run of backspace events:
      1) Remove the entire backspace run.
      2) Let K = number of backspaces in that run.
      3) Remove the K text-producing (stackable) key events immediately before that
         run in the timeline (walking backward, skipping non-stackable events).

    Repeat until no backspaces remain.

    Example: S, A, BS, BS, F, A, C, E  ->  F, A, C, E
    (two backspaces removed; two stackable keys before them — S and A — removed.)
    """
    if not events:
        return []
    # Work on shallow copies so callers' lists are not mutated
    result = [{"output": e.get("output"), "start_time": e.get("start_time")} for e in events]
    while True:
        first_bs = None
        for i, e in enumerate(result):
            o = e.get("output")
            if isinstance(o, str) and o.lower() == "backspace":
                first_bs = i
                break
        if first_bs is None:
            break
        start_bs = first_bs
        end_bs = start_bs
        while end_bs + 1 < len(result):
            o = result[end_bs + 1].get("output")
            if isinstance(o, str) and o.lower() == "backspace":
                end_bs += 1
            else:
                break
        k_bs = end_bs - start_bs + 1
        remove = set(range(start_bs, end_bs + 1))
        need = k_bs
        pos = start_bs - 1
        while pos >= 0 and need > 0:
            if _data_event_is_stackable(result[pos]):
                remove.add(pos)
                need -= 1
            pos -= 1
        for idx in sorted(remove, reverse=True):
            del result[idx]
    return result


def stitch_frame_times_after_backspace_strip(events, settings, event_format="data"):
    """
    When backspace stripping removed events, recompute per-frame durations using only
    gaps between consecutive *surviving* events (no time from session start to first
    survivor — that gap was "typing" deleted content). Same word/space overrides and
    video_speed as reconstruct_*.
    events: list aligned with one frame each (data: valid_events; xml: ev).
    """
    if not events:
        return []
    n = len(events)
    frame_times = []
    for i in range(n):
        if i == 0:
            frame_times.append(0.05)  # placeholder; overrides below set word/space
        else:
            prev_t = events[i - 1]["start_time"] / 1000.0
            cur_t = events[i]["start_time"] / 1000.0
            frame_times.append(max(0.05, cur_t - prev_t))
    word_speed = settings["word_speed"]
    space_duration = settings["space_duration"]
    for i, event in enumerate(events):
        output = event["output"]
        if event_format == "xml":
            if output == "SPACE":
                frame_times[i] = space_duration
            elif output and len(output) == 1:
                if i == 0 or events[i - 1]["output"] == "SPACE":
                    frame_times[i] = word_speed
        else:
            if output == "space":
                frame_times[i] = space_duration
            elif output and isinstance(output, str) and len(output) == 1:
                if i == 0 or events[i - 1]["output"] == "space":
                    frame_times[i] = word_speed
    speed_mult = settings["video_speed"]
    return [ft / speed_mult for ft in frame_times]


def _poisson_sample(lam):
    """Knuth's algorithm; mean lam."""
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while p > L:
        k += 1
        p *= random.random()
    return k - 1


def inject_fake_backspaces_into_timeline(text_states, frame_times, settings):
    """
    Insert random bursts of fake backspaces followed by re-typing the deleted
    characters so the final text is unchanged but the video gains extra frames.
    settings: fake_backspace_enabled, fake_backspaces_per_minute, word_speed, video_speed.
    """
    if not settings.get("fake_backspace_enabled", False):
        return text_states, frame_times
    if not text_states or not frame_times or len(text_states) != len(frame_times):
        return text_states, frame_times
    ts = list(text_states)
    ft = list(frame_times)
    total_duration = sum(ft)
    if total_duration <= 0:
        return text_states, frame_times
    rate = float(settings.get("fake_backspaces_per_minute", 2.0))
    rate = max(0.0, rate)
    duration_min = total_duration / 60.0
    n_inject = _poisson_sample(rate * duration_min)
    if n_inject <= 0:
        return text_states, frame_times
    word_speed = float(settings.get("word_speed", 0.15))
    speed_mult = float(settings.get("video_speed", 1.0))
    if speed_mult <= 0:
        speed_mult = 1.0
    # Random duration ranges (seconds, after video_speed divide)
    bs_lo = max(0.05, word_speed * 0.3) / speed_mult
    bs_hi = max(0.08, word_speed * 1.5) / speed_mult
    if bs_hi <= bs_lo:
        bs_hi = bs_lo + 0.03
    pause_lo = max(0.05, word_speed * 0.3) / speed_mult
    pause_hi = max(0.12, word_speed * 3.0) / speed_mult
    if pause_hi <= pause_lo:
        pause_hi = pause_lo + 0.05
    char_lo = max(0.05, word_speed * 0.5) / speed_mult
    char_hi = max(0.08, word_speed * 1.8) / speed_mult
    if char_hi <= char_lo:
        char_hi = char_lo + 0.03

    valid = [i for i in range(len(ts)) if ts[i]]
    if not valid:
        return text_states, frame_times
    n_inject = min(n_inject, len(valid))
    after_indices = random.sample(valid, n_inject)
    injections = []
    for after_idx in after_indices:
        text = ts[after_idx]
        max_k = min(5, len(text))
        if max_k < 1:
            continue
        k = random.randint(1, max_k)
        chars_del = text[-k:]
        block_states = []
        block_times = []
        cur = text
        for _ in range(k):
            cur = cur[:-1]
            block_states.append(cur)
            block_times.append(random.uniform(bs_lo, bs_hi))
        # Random pause (same text frozen) before retyping deleted characters
        pre_pause = random.uniform(pause_lo, pause_hi)
        block_states.append(cur)
        block_times.append(pre_pause)
        for ch in chars_del:
            cur = cur + ch
            block_states.append(cur)
            block_times.append(random.uniform(char_lo, char_hi))
        injections.append((after_idx, block_states, block_times))
    if not injections:
        return text_states, frame_times
    injections.sort(key=lambda x: -x[0])
    for after_idx, block_states, block_times in injections:
        ts[after_idx + 1 : after_idx + 1] = block_states
        ft[after_idx + 1 : after_idx + 1] = block_times
    return ts, ft


class XMLToVideoApp:
    def __init__(self, root):
        self.root = root
        self.root.title("XML to Video")
        self.xml_path = None
        self.word_path = None
        self.xml_queue = []
        self.data_queue = []
        self.processing = False
        self.create_widgets()

    def create_widgets(self):
        # Scrollable main area (vertical + horizontal) so all settings stay reachable on small windows
        scroll_outer = tk.Frame(self.root)
        scroll_outer.pack(fill="both", expand=True, padx=10, pady=10)
        scroll_outer.grid_rowconfigure(0, weight=1)
        scroll_outer.grid_columnconfigure(0, weight=1)

        self._scroll_canvas = tk.Canvas(scroll_outer, highlightthickness=0)
        vsb = tk.Scrollbar(scroll_outer, orient="vertical", command=self._scroll_canvas.yview)
        hsb = tk.Scrollbar(scroll_outer, orient="horizontal", command=self._scroll_canvas.xview)
        self._scroll_canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._scroll_canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        corner = tk.Frame(scroll_outer)
        corner.grid(row=1, column=1)

        self.main_frame = tk.Frame(self._scroll_canvas)
        self._canvas_window = self._scroll_canvas.create_window((0, 0), window=self.main_frame, anchor="nw")

        def _update_scrollregion():
            bbox = self._scroll_canvas.bbox("all")
            if bbox:
                self._scroll_canvas.configure(scrollregion=bbox)

        def _sync_canvas_inner_width():
            """Inner width = max(viewport, requested) so narrow windows still fill; wide content can scroll horizontally."""
            self._scroll_canvas.update_idletasks()
            cw = self._scroll_canvas.winfo_width()
            if cw <= 1:
                return
            try:
                reqw = self.main_frame.winfo_reqwidth()
            except tk.TclError:
                reqw = cw
            w = max(cw, reqw)
            self._scroll_canvas.itemconfig(self._canvas_window, width=w)

        def _on_main_configure(event):
            _sync_canvas_inner_width()
            _update_scrollregion()

        def _on_canvas_configure(event):
            _sync_canvas_inner_width()
            _update_scrollregion()

        self.main_frame.bind("<Configure>", _on_main_configure)
        self._scroll_canvas.bind("<Configure>", _on_canvas_configure)

        def _wheel_should_scroll(event):
            w = event.widget
            while w is not None:
                if isinstance(w, (tk.Listbox, tk.Entry, tk.Text, ttk.Entry, ttk.Combobox)):
                    return False
                if w == self.main_frame:
                    return True
                w = w.master
            return False

        def _apply_main_scroll_pixel_motion(motion_px):
            """Smooth pixel-style scroll via yview_moveto (Canvas has no yview_scroll 'pixels').
            motion_px > 0 = scroll down (show content below)."""
            if motion_px == 0:
                return
            canvas = self._scroll_canvas
            canvas.update_idletasks()
            sr = canvas.cget("scrollregion")
            if not sr:
                return
            parts = sr.split()
            if len(parts) != 4:
                return
            _x1, y1, _x2, y2 = map(float, parts)
            H = y2 - y1
            if H <= 1.0:
                return
            V = float(canvas.winfo_height())
            if V >= H - 1.0:
                return
            top, _ = canvas.yview()
            new_top = top + (motion_px / H)
            max_top = max(0.0, 1.0 - (V / H))
            new_top = max(0.0, min(max_top, new_top))
            canvas.yview_moveto(new_top)

        def _apply_main_scroll_horizontal_motion(motion_px):
            """motion_px > 0 = scroll right (show content to the right of the viewport)."""
            if motion_px == 0:
                return
            canvas = self._scroll_canvas
            canvas.update_idletasks()
            sr = canvas.cget("scrollregion")
            if not sr:
                return
            parts = sr.split()
            if len(parts) != 4:
                return
            x1, _, x2, _ = map(float, parts)
            W = x2 - x1
            if W <= 1.0:
                return
            Cw = float(canvas.winfo_width())
            if Cw >= W - 1.0:
                return
            left, _ = canvas.xview()
            new_left = left + (motion_px / W)
            max_left = max(0.0, 1.0 - (Cw / W))
            new_left = max(0.0, min(max_left, new_left))
            canvas.xview_moveto(new_left)

        def _wheel_motion_from_delta(d):
            if sys.platform == "darwin":
                if abs(d) >= 120:
                    return (-float(d) / 120.0) * _MAIN_SCROLL_PIXELS_PER_WHEEL_NOTCH
                return -float(d) * _MAIN_SCROLL_MAC_DELTA_TO_PIXELS
            return (-float(d) / 120.0) * _MAIN_SCROLL_PIXELS_PER_WHEEL_NOTCH

        def _event_has_shift(event):
            """Shift + wheel → horizontal scroll (same convention as many apps)."""
            s = getattr(event, "state", 0) or 0
            return bool(s & 0x0001)

        def _on_mousewheel(event):
            if not _wheel_should_scroll(event):
                return
            d = getattr(event, "delta", 0)
            if d == 0:
                return
            motion = _wheel_motion_from_delta(d)
            if _event_has_shift(event):
                _apply_main_scroll_horizontal_motion(motion)
            else:
                _apply_main_scroll_pixel_motion(motion)

        def _on_mousewheel_linux_up(event):
            if not _wheel_should_scroll(event):
                return
            _apply_main_scroll_pixel_motion(-_MAIN_SCROLL_LINUX_BUTTON_PIXELS)

        def _on_mousewheel_linux_down(event):
            if not _wheel_should_scroll(event):
                return
            _apply_main_scroll_pixel_motion(_MAIN_SCROLL_LINUX_BUTTON_PIXELS)

        def _on_mousewheel_linux_left(event):
            if not _wheel_should_scroll(event):
                return
            _apply_main_scroll_horizontal_motion(-_MAIN_SCROLL_LINUX_BUTTON_PIXELS)

        def _on_mousewheel_linux_right(event):
            if not _wheel_should_scroll(event):
                return
            _apply_main_scroll_horizontal_motion(_MAIN_SCROLL_LINUX_BUTTON_PIXELS)

        self.root.bind_all("<MouseWheel>", _on_mousewheel)
        self.root.bind_all("<Button-4>", _on_mousewheel_linux_up)
        self.root.bind_all("<Button-5>", _on_mousewheel_linux_down)
        # X11 horizontal wheel (Linux). macOS Aqua Tk rejects Button-6/7 at bind time.
        for _seq, _fn in (
            ("<Button-6>", _on_mousewheel_linux_left),
            ("<Button-7>", _on_mousewheel_linux_right),
        ):
            try:
                self.root.bind_all(_seq, _fn)
            except tk.TclError:
                pass

        # File type selector dropdown
        file_type_frame = tk.Frame(self.main_frame)
        file_type_frame.pack(pady=10, fill="x")
        
        tk.Label(file_type_frame, text="File Type:", font=("Arial", 10, "bold")).pack(side="left", padx=5)
        self.file_type_var = tk.StringVar(value="XML/Word")
        self.file_type_dropdown = ttk.Combobox(file_type_frame, textvariable=self.file_type_var, 
                                               values=["XML/Word", "Data TXT", "IDFX"], 
                                               state="readonly", width=20)
        self.file_type_dropdown.pack(side="left", padx=5)
        self.file_type_dropdown.bind("<<ComboboxSelected>>", self.on_file_type_change)

        # Create frames for each file type
        self.xml_frame = tk.Frame(self.main_frame)
        self.data_frame = tk.Frame(self.main_frame)
        self.idfx_frame = tk.Frame(self.main_frame)

        # --- XML/Word Frame ---

        # Batch processing frame for XML
        batch_frame_xml = tk.LabelFrame(self.xml_frame, text="Batch Processing")
        batch_frame_xml.pack(pady=5, fill="x", padx=10)

        self.add_xml_btn = tk.Button(batch_frame_xml, text="Add XML to Queue", command=self.add_xml_to_queue, bg="white", fg="black")
        self.add_xml_btn.pack(side="left", padx=5, pady=5)

        self.clear_xml_queue_btn = tk.Button(batch_frame_xml, text="Clear Queue", command=self.clear_xml_queue, bg="white", fg="black")
        self.clear_xml_queue_btn.pack(side="left", padx=5, pady=5)

        self.xml_queue_label = tk.Label(batch_frame_xml, text="Queue: 0 files", fg="blue")
        self.xml_queue_label.pack(side="left", padx=5, pady=5)

        # Queue list for XML files
        queue_list_frame_xml = tk.Frame(self.xml_frame)
        queue_list_frame_xml.pack(pady=5, fill="both", expand=True, padx=10)

        tk.Label(queue_list_frame_xml, text="Files in Queue:", font=("Arial", 9, "bold")).pack(anchor="w", pady=(0, 5))
        
        # Scrollbar for queue list
        xml_scrollbar = tk.Scrollbar(queue_list_frame_xml)
        xml_scrollbar.pack(side="right", fill="y")
        
        # Listbox for queue
        self.xml_queue_listbox = tk.Listbox(queue_list_frame_xml, yscrollcommand=xml_scrollbar.set, height=8)
        self.xml_queue_listbox.pack(side="left", fill="both", expand=True)
        xml_scrollbar.config(command=self.xml_queue_listbox.yview)

        # Progress bar for XML/Word tab
        self.progress_xml = ttk.Progressbar(self.xml_frame, mode="determinate")
        self.progress_xml.pack(pady=5, fill="x", padx=10)
        self.progress_xml.pack_forget()

        # Status label for XML processing
        self.xml_status_label = tk.Label(self.xml_frame, text="", fg="blue")
        self.xml_status_label.pack(pady=2)

        self.batch_generate_btn = tk.Button(self.xml_frame, text="Process All XML Files in Queue", command=self.process_xml_queue, bg="white", fg="black")
        self.batch_generate_btn.pack(pady=5)

        # --- Data TXT Frame ---
        # Batch processing frame for data.txt
        batch_frame_data = tk.LabelFrame(self.data_frame, text="Batch Processing")
        batch_frame_data.pack(pady=5, fill="x", padx=10)

        self.add_data_btn = tk.Button(batch_frame_data, text="Add data.txt to Queue", command=self.add_data_to_queue, bg="white", fg="black")
        self.add_data_btn.pack(side="left", padx=5, pady=5)

        self.clear_data_queue_btn = tk.Button(batch_frame_data, text="Clear Queue", command=self.clear_data_queue, bg="white", fg="black")
        self.clear_data_queue_btn.pack(side="left", padx=5, pady=5)

        self.data_queue_label = tk.Label(batch_frame_data, text="Queue: 0 files", fg="blue")
        self.data_queue_label.pack(side="left", padx=5, pady=5)

        # Queue list for data.txt files
        queue_list_frame_data = tk.Frame(self.data_frame)
        queue_list_frame_data.pack(pady=5, fill="both", expand=True, padx=10)

        tk.Label(queue_list_frame_data, text="Files in Queue:", font=("Arial", 9, "bold")).pack(anchor="w", pady=(0, 5))
        
        # Scrollbar for queue list
        data_scrollbar = tk.Scrollbar(queue_list_frame_data)
        data_scrollbar.pack(side="right", fill="y")
        
        # Listbox for queue
        self.data_queue_listbox = tk.Listbox(queue_list_frame_data, yscrollcommand=data_scrollbar.set, height=8)
        self.data_queue_listbox.pack(side="left", fill="both", expand=True)
        data_scrollbar.config(command=self.data_queue_listbox.yview)

        # Progress bar for data.txt tab
        self.progress_data = ttk.Progressbar(self.data_frame, mode="determinate")
        self.progress_data.pack(pady=5, fill="x", padx=10)
        self.progress_data.pack_forget()

        # Status label for data.txt processing
        self.data_status_label = tk.Label(self.data_frame, text="", fg="blue")
        self.data_status_label.pack(pady=2)

        self.batch_generate_data_btn = tk.Button(self.data_frame, text="Process All data.txt Files in Queue", command=self.process_data_queue, bg="white", fg="black")
        self.batch_generate_data_btn.pack(pady=5)

        # --- IDFX Frame ---
        # Batch processing frame for .idfx
        batch_frame_idfx = tk.LabelFrame(self.idfx_frame, text="Batch Processing")
        batch_frame_idfx.pack(pady=5, fill="x", padx=10)

        self.add_idfx_btn = tk.Button(batch_frame_idfx, text="Add .idfx to Queue", command=self.add_idfx_to_queue, bg="white", fg="black")
        self.add_idfx_btn.pack(side="left", padx=5, pady=5)

        self.clear_idfx_queue_btn = tk.Button(batch_frame_idfx, text="Clear Queue", command=self.clear_idfx_queue, bg="white", fg="black")
        self.clear_idfx_queue_btn.pack(side="left", padx=5, pady=5)

        self.idfx_queue_label = tk.Label(batch_frame_idfx, text="Queue: 0 files", fg="blue")
        self.idfx_queue_label.pack(side="left", padx=5, pady=5)

        # Queue list for IDFX files
        queue_list_frame_idfx = tk.Frame(self.idfx_frame)
        queue_list_frame_idfx.pack(pady=5, fill="both", expand=True, padx=10)

        tk.Label(queue_list_frame_idfx, text="Files in Queue:", font=("Arial", 9, "bold")).pack(anchor="w", pady=(0, 5))
        
        # Scrollbar for queue list
        idfx_scrollbar = tk.Scrollbar(queue_list_frame_idfx)
        idfx_scrollbar.pack(side="right", fill="y")
        
        # Listbox for queue
        self.idfx_queue_listbox = tk.Listbox(queue_list_frame_idfx, yscrollcommand=idfx_scrollbar.set, height=8)
        self.idfx_queue_listbox.pack(side="left", fill="both", expand=True)
        idfx_scrollbar.config(command=self.idfx_queue_listbox.yview)

        # Progress bar for .idfx tab
        self.progress_idfx = ttk.Progressbar(self.idfx_frame, mode="determinate")
        self.progress_idfx.pack(pady=5, fill="x", padx=10)
        self.progress_idfx.pack_forget()

        # Status label for .idfx processing
        self.idfx_status_label = tk.Label(self.idfx_frame, text="", fg="blue")
        self.idfx_status_label.pack(pady=2)

        self.batch_generate_idfx_btn = tk.Button(self.idfx_frame, text="Process All .idfx Files in Queue", command=self.process_idfx_queue, bg="white", fg="black")
        self.batch_generate_idfx_btn.pack(pady=5)

        # --- Shared Settings (visible on all file types) ---
        # Font settings frame
        font_frame = tk.LabelFrame(self.main_frame, text="Text Settings")
        font_frame.pack(pady=5, fill="x", padx=10)

        tk.Label(font_frame, text="Font:").grid(row=0, column=0, sticky="w", padx=5)
        self.font_families = sorted(set(tkfont.families()))
        self.font_family_var = tk.StringVar(value="Arial")
        self.font_family_menu = ttk.Combobox(font_frame, textvariable=self.font_family_var, values=self.font_families, state="readonly", width=20)
        self.font_family_menu.grid(row=0, column=1, padx=5)

        tk.Label(font_frame, text="Size:").grid(row=0, column=2, sticky="w", padx=5)
        self.font_size_var = tk.IntVar(value=30)
        self.font_size_entry = tk.Entry(font_frame, textvariable=self.font_size_var, width=5)
        self.font_size_entry.grid(row=0, column=3, padx=5)

        self.bold_var = tk.BooleanVar(value=True)
        self.bold_check = tk.Checkbutton(font_frame, text="Bold", variable=self.bold_var)
        self.bold_check.grid(row=0, column=4, padx=5)
        
        # Add margin control on a new row
        tk.Label(font_frame, text="Margin:").grid(row=1, column=0, sticky="w", padx=5)
        self.margin_var = tk.IntVar(value=20)
        self.margin_entry = tk.Entry(font_frame, textvariable=self.margin_var, width=5)
        self.margin_entry.grid(row=1, column=1, padx=5)

        self.show_caret_var = tk.BooleanVar(value=True)
        self.show_caret_check = tk.Checkbutton(font_frame, text="Show caret", variable=self.show_caret_var)
        self.show_caret_check.grid(row=1, column=2, padx=5)

        self.strip_backspace_edits_var = tk.BooleanVar(value=False)
        self.strip_backspace_edits_check = tk.Checkbutton(
            font_frame,
            text="Hide backspace edits (video shows only surviving text; no deleted keystrokes)",
            variable=self.strip_backspace_edits_var,
        )
        self.strip_backspace_edits_check.grid(row=2, column=0, columnspan=6, sticky="w", padx=5, pady=(2, 0))

        fake_row = tk.Frame(font_frame)
        fake_row.grid(row=3, column=0, columnspan=6, sticky="w", padx=5, pady=(4, 0))
        self.fake_backspace_enabled_var = tk.BooleanVar(value=False)
        self.fake_backspace_check = tk.Checkbutton(
            fake_row,
            text="Add random fake backspaces (video length increases; final text unchanged)",
            variable=self.fake_backspace_enabled_var,
        )
        self.fake_backspace_check.pack(side="left", padx=(0, 8))
        tk.Label(fake_row, text="Approx. bursts / minute:").pack(side="left", padx=5)
        self.fake_backspaces_per_minute_var = tk.DoubleVar(value=2.0)
        self.fake_backspaces_per_minute_entry = tk.Entry(fake_row, textvariable=self.fake_backspaces_per_minute_var, width=6)
        self.fake_backspaces_per_minute_entry.pack(side="left", padx=5)

        window_frame = tk.LabelFrame(self.main_frame, text="Moving Window")
        window_frame.pack(pady=5, fill="x", padx=10)
        self.moving_window_var = tk.BooleanVar(value=False)
        self.moving_window_check = tk.Checkbutton(window_frame, text="Enable Moving Window", variable=self.moving_window_var, command=self.update_window_controls)
        self.moving_window_check.grid(row=0, column=0, sticky="w", padx=5)
        tk.Label(window_frame, text="Window Size (chars):").grid(row=0, column=1, sticky="w", padx=5)
        self.window_size_var = tk.IntVar(value=10)
        self.window_size_entry = tk.Entry(window_frame, textvariable=self.window_size_var, width=5, state="disabled")
        self.window_size_entry.grid(row=0, column=2, padx=5)
        self.window_wordonly_var = tk.BooleanVar(value=False)
        self.window_wordonly_check = tk.Checkbutton(window_frame, text="Window Only Current Word", variable=self.window_wordonly_var, state="disabled")
        self.window_wordonly_check.grid(row=0, column=3, padx=5)
        
        # Add mask character controls on a new row
        tk.Label(window_frame, text="Mask (narrow):").grid(row=1, column=0, sticky="w", padx=5)
        self.mask_narrow_var = tk.StringVar(value="_")
        self.mask_narrow_entry = tk.Entry(window_frame, textvariable=self.mask_narrow_var, width=3, state="disabled")
        self.mask_narrow_entry.grid(row=1, column=1, padx=5)
        tk.Label(window_frame, text="Mask (wide):").grid(row=1, column=2, sticky="w", padx=5)
        self.mask_wide_var = tk.StringVar(value="#")
        self.mask_wide_entry = tk.Entry(window_frame, textvariable=self.mask_wide_var, width=3, state="disabled")
        self.mask_wide_entry.grid(row=1, column=3, padx=5)

        uniform_frame = tk.LabelFrame(self.main_frame, text="Uniform Typing Mode")
        uniform_frame.pack(pady=5, fill="x", padx=10)
        self.uniform_typing_var = tk.BooleanVar(value=False)
        self.uniform_typing_check = tk.Checkbutton(uniform_frame, text="Uniform Typing Speed (use Word file as reference)", variable=self.uniform_typing_var, command=self.update_uniform_typing_controls)
        self.uniform_typing_check.grid(row=0, column=0, sticky="w", padx=5)
        tk.Label(uniform_frame, text="Characters per Second:").grid(row=0, column=1, sticky="w", padx=5)
        self.chars_per_sec_var = tk.DoubleVar(value=10.0)
        self.chars_per_sec_entry = tk.Entry(uniform_frame, textvariable=self.chars_per_sec_var, width=5, state="disabled")
        self.chars_per_sec_entry.grid(row=0, column=2, padx=5)
        tk.Label(uniform_frame, text="Video Speed Multiplier:").grid(row=0, column=3, sticky="w", padx=5)
        self.video_speed_var = tk.DoubleVar(value=1.0)
        self.video_speed_entry = tk.Entry(uniform_frame, textvariable=self.video_speed_var, width=5, state="disabled")
        self.video_speed_entry.grid(row=0, column=4, padx=5)
        tk.Label(uniform_frame, text="Word Typing Speed (s/word):").grid(row=1, column=0, sticky="w", padx=5)
        self.word_speed_var = tk.DoubleVar(value=0.15)
        self.word_speed_entry = tk.Entry(uniform_frame, textvariable=self.word_speed_var, width=5, state="disabled")
        self.word_speed_entry.grid(row=1, column=1, padx=5)
        tk.Label(uniform_frame, text="Space Duration (s):").grid(row=1, column=2, sticky="w", padx=5)
        self.space_duration_var = tk.DoubleVar(value=0.25)
        self.space_duration_entry = tk.Entry(uniform_frame, textvariable=self.space_duration_var, width=5, state="disabled")
        self.space_duration_entry.grid(row=1, column=3, padx=5)

        # Video Timing Controls
        timing_frame = tk.LabelFrame(self.main_frame, text="Video Timing Controls")
        timing_frame.pack(pady=5, fill="x", padx=10)
        
        # Enable timing controls checkbox
        self.enable_timing_var = tk.BooleanVar(value=False)
        self.enable_timing_check = tk.Checkbutton(timing_frame, text="Enable Video Timing Controls", 
                                                 variable=self.enable_timing_var, command=self.update_timing_controls)
        self.enable_timing_check.grid(row=0, column=0, columnspan=6, sticky="w", padx=5, pady=5)
        
        # Start time controls
        tk.Label(timing_frame, text="Start Time (ms):").grid(row=1, column=0, sticky="w", padx=5)
        self.start_time_var = tk.IntVar(value=0)
        self.start_time_entry = tk.Entry(timing_frame, textvariable=self.start_time_var, width=10, state="disabled")
        self.start_time_entry.grid(row=1, column=1, padx=5)
        
        # End time controls
        tk.Label(timing_frame, text="End Time (ms):").grid(row=1, column=2, sticky="w", padx=5)
        self.end_time_var = tk.IntVar(value=0)
        self.end_time_entry = tk.Entry(timing_frame, textvariable=self.end_time_var, width=10, state="disabled")
        self.end_time_entry.grid(row=1, column=3, padx=5)
        
        # Duration percentage controls
        tk.Label(timing_frame, text="Duration (%):").grid(row=2, column=0, sticky="w", padx=5)
        self.duration_percent_var = tk.DoubleVar(value=100.0)
        self.duration_percent_entry = tk.Entry(timing_frame, textvariable=self.duration_percent_var, width=10, state="disabled")
        self.duration_percent_entry.grid(row=2, column=1, padx=5)
        
        # Timing mode selection
        self.timing_mode_var = tk.StringVar(value="absolute")
        timing_mode_frame = tk.Frame(timing_frame)
        timing_mode_frame.grid(row=2, column=2, columnspan=4, sticky="w", padx=5)
        tk.Radiobutton(timing_mode_frame, text="Absolute Time", variable=self.timing_mode_var, 
                      value="absolute", command=self.update_timing_mode).pack(side="left", padx=5)
        tk.Radiobutton(timing_mode_frame, text="Percentage", variable=self.timing_mode_var, 
                      value="percentage", command=self.update_timing_mode).pack(side="left", padx=5)

        self.word_boundary_trim_var = tk.BooleanVar(value=False)
        self.word_boundary_trim_check = tk.Checkbutton(
            timing_frame,
            text="Trim start/end on word boundaries (with timing trim)",
            variable=self.word_boundary_trim_var,
        )
        self.word_boundary_trim_check.grid(row=3, column=0, columnspan=6, sticky="w", padx=5, pady=2)

        self.sentence_boundary_trim_var = tk.BooleanVar(value=False)
        self.sentence_boundary_trim_check = tk.Checkbutton(
            timing_frame,
            text="Trim start/end on sentence boundaries (with timing trim)",
            variable=self.sentence_boundary_trim_var,
        )
        self.sentence_boundary_trim_check.grid(row=4, column=0, columnspan=6, sticky="w", padx=5, pady=2)

        options_frame = tk.Frame(self.main_frame)
        options_frame.pack(pady=5, fill="x", padx=10)
        self.save_video_var = tk.BooleanVar(value=True)
        self.save_video_check = tk.Checkbutton(options_frame, text="Save Video", variable=self.save_video_var)
        self.save_video_check.pack(side="left", padx=5)
        self.preview_btn = tk.Button(options_frame, text="Preview Video", command=self.preview_video, bg="white", fg="black")
        self.preview_btn.pack(side="left", padx=5)
        self.save_settings_btn = tk.Button(options_frame, text="Save Settings", command=self.save_settings, bg="white", fg="black")
        self.save_settings_btn.pack(side="left", padx=5)
        self.load_csv_settings_btn = tk.Button(options_frame, text="Load Settings from CSV", command=self.load_settings_from_csv, bg="white", fg="black")
        self.load_csv_settings_btn.pack(side="left", padx=5)

        # Initialize queue displays
        self.update_xml_queue_display()
        self.update_data_queue_display()
        self.update_idfx_queue_display()

        # Load settings if available
        self.load_settings()
        self.update_timing_controls()

        # Apply file-type layout (ensures correct layout on first boot)
        self.on_file_type_change()

    def on_file_type_change(self, event=None):
        """Handle file type dropdown change"""
        file_type = self.file_type_var.get()
        
        # Hide all frames
        self.xml_frame.pack_forget()
        self.data_frame.pack_forget()
        self.idfx_frame.pack_forget()
        
        # Show selected frame
        if file_type == "XML/Word":
            self.xml_frame.pack(fill="both", expand=True)
        elif file_type == "Data TXT":
            self.data_frame.pack(fill="both", expand=True)
        elif file_type == "IDFX":
            self.idfx_frame.pack(fill="both", expand=True)

    def select_xml(self):
        path = filedialog.askopenfilename(filetypes=[("XML Files", "*.xml")])
        if path:
            self.xml_path = path

    def select_word(self):
        path = filedialog.askopenfilename(filetypes=[("Word Files", "*.docx")])
        if path:
            self.word_path = path

    def select_data_txt(self):
        path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if path:
            self.data_txt_path = path

    def select_idfx(self):
        path = filedialog.askopenfilename(filetypes=[("IDFX Files", "*.idfx"), ("XML Files", "*.xml"), ("All Files", "*.*")])
        if path:
            self.idfx_path = path

    def add_xml_to_queue(self):
        paths = filedialog.askopenfilenames(filetypes=[("XML Files", "*.xml")])
        if paths:
            for path in paths:
                if path not in [item['xml_path'] for item in self.xml_queue]:
                    self.xml_queue.append({
                        'xml_path': path,
                        'word_path': self.word_path if self.word_path else None
                    })
            self.update_xml_queue_display()
            messagebox.showinfo("Files Added", f"Added {len(paths)} XML file(s) to queue")

    def add_data_to_queue(self):
        paths = filedialog.askopenfilenames(filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if paths:
            for path in paths:
                if path not in [item['data_path'] for item in self.data_queue]:
                    self.data_queue.append({
                        'data_path': path
                    })
            self.update_data_queue_display()
            messagebox.showinfo("Files Added", f"Added {len(paths)} data.txt file(s) to queue")

    def add_idfx_to_queue(self):
        paths = filedialog.askopenfilenames(filetypes=[("IDFX Files", "*.idfx"), ("XML Files", "*.xml"), ("All Files", "*.*")])
        if paths:
            for path in paths:
                if path not in [item['idfx_path'] for item in getattr(self, 'idfx_queue', [])]:
                    if not hasattr(self, 'idfx_queue'):
                        self.idfx_queue = []
                    self.idfx_queue.append({'idfx_path': path})
            self.update_idfx_queue_display()
            messagebox.showinfo("Files Added", f"Added {len(paths)} .idfx file(s) to queue")

    def clear_xml_queue(self):
        self.xml_queue.clear()
        self.update_xml_queue_display()

    def clear_data_queue(self):
        self.data_queue.clear()
        self.update_data_queue_display()

    def clear_idfx_queue(self):
        if hasattr(self, 'idfx_queue'):
            self.idfx_queue.clear()
        self.update_idfx_queue_display()

    def update_xml_queue_display(self):
        count = len(self.xml_queue)
        self.xml_queue_label.config(text=f"Queue: {count} files")
        
        # Update listbox
        self.xml_queue_listbox.delete(0, tk.END)
        for i, xml_path in enumerate(self.xml_queue, 1):
            filename = os.path.basename(xml_path)
            self.xml_queue_listbox.insert(tk.END, f"{i}. {filename}")
        
        if count > 0:
            self.batch_generate_btn.config(state=tk.NORMAL)
        else:
            self.batch_generate_btn.config(state=tk.DISABLED)

    def update_data_queue_display(self):
        count = len(self.data_queue)
        self.data_queue_label.config(text=f"Queue: {count} files")
        
        # Update listbox
        self.data_queue_listbox.delete(0, tk.END)
        for i, data_path in enumerate(self.data_queue, 1):
            filename = os.path.basename(data_path['data_path'])
            self.data_queue_listbox.insert(tk.END, f"{i}. {filename}")
        
        if count > 0:
            self.batch_generate_data_btn.config(state=tk.NORMAL)
        else:
            self.batch_generate_data_btn.config(state=tk.DISABLED)

    def update_idfx_queue_display(self):
        idfx_queue = getattr(self, 'idfx_queue', [])
        count = len(idfx_queue)
        self.idfx_queue_label.config(text=f"Queue: {count} files")
        
        # Update listbox
        self.idfx_queue_listbox.delete(0, tk.END)
        for i, item in enumerate(idfx_queue, 1):
            idfx_path = item.get('idfx_path', '') if isinstance(item, dict) else item
            filename = os.path.basename(idfx_path) if idfx_path else 'Unknown'
            self.idfx_queue_listbox.insert(tk.END, f"{i}. {filename}")
        
        if count > 0:
            self.batch_generate_idfx_btn.config(state=tk.NORMAL)
        else:
            self.batch_generate_idfx_btn.config(state=tk.DISABLED)

    # Batch UI must run on the Tk main thread (worker thread + messagebox/widgets is unsafe and can raise).
    def _batch_xml_finish_success(self, output_folder, n_files):
        self.xml_status_label.config(
            text=f"Batch processing complete! Videos saved to {output_folder}", fg="green"
        )
        messagebox.showinfo(
            "Complete",
            f"Processed {n_files} files. Videos saved to {output_folder}",
        )
        self.processing = False
        self.batch_generate_btn.config(state=tk.NORMAL)
        self.progress_xml.pack_forget()
        self.xml_status_label.config(text="")

    def _batch_xml_finish_error(self, err):
        error_msg = f"Batch processing failed: {err}"
        self.xml_status_label.config(text=error_msg, fg="red")
        messagebox.showerror("Error", error_msg)
        self.processing = False
        self.batch_generate_btn.config(state=tk.NORMAL)
        self.progress_xml.pack_forget()
        self.xml_status_label.config(text="")

    def _batch_data_finish_success(self, output_folder, n_files):
        self.data_status_label.config(
            text=f"Batch processing complete! Videos saved to {output_folder}", fg="green"
        )
        messagebox.showinfo(
            "Complete",
            f"Processed {n_files} files. Videos saved to {output_folder}",
        )
        self.processing = False
        self.batch_generate_data_btn.config(state=tk.NORMAL)
        self.progress_data.pack_forget()
        self.data_status_label.config(text="")

    def _batch_data_finish_error(self, err):
        error_msg = f"Batch processing failed: {err}"
        self.data_status_label.config(text=error_msg, fg="red")
        messagebox.showerror("Error", error_msg)
        self.processing = False
        self.batch_generate_data_btn.config(state=tk.NORMAL)
        self.progress_data.pack_forget()
        self.data_status_label.config(text="")

    def _batch_idfx_finish_success(self, output_folder, n_files):
        self.idfx_status_label.config(
            text=f"Batch processing complete! Videos saved to {output_folder}", fg="green"
        )
        messagebox.showinfo(
            "Complete",
            f"Processed {n_files} files. Videos saved to {output_folder}",
        )
        self.processing = False
        self.batch_generate_idfx_btn.config(state=tk.NORMAL)
        self.progress_idfx.pack_forget()
        self.idfx_status_label.config(text="")

    def _batch_idfx_finish_error(self, err):
        error_msg = f"Batch processing failed: {err}"
        self.idfx_status_label.config(text=error_msg, fg="red")
        messagebox.showerror("Error", error_msg)
        self.processing = False
        self.batch_generate_idfx_btn.config(state=tk.NORMAL)
        self.progress_idfx.pack_forget()
        self.idfx_status_label.config(text="")

    def process_xml_queue(self):
        if not self.xml_queue:
            messagebox.showwarning("Warning", "No files in queue")
            return
        
        if self.processing:
            messagebox.showwarning("Warning", "Already processing files")
            return
        
        self.processing = True
        self.batch_generate_btn.config(state=tk.DISABLED)
        self.progress_xml.pack(pady=5, fill="x", padx=10)
        self.progress_xml.config(maximum=len(self.xml_queue), value=0)
        
        def process_queue():
            try:
                # Create output folder (timestamped batch subfolder only when 2+ files)
                base_output = os.path.join(_PROGRAM_DIR, 'xml-to-text-video-output')
                if len(self.xml_queue) > 1:
                    output_folder = os.path.join(base_output, _batch_upload_subdir_name())
                else:
                    output_folder = base_output
                os.makedirs(output_folder, exist_ok=True)
                
                for i, item in enumerate(self.xml_queue):
                    try:
                        xml_path = item['xml_path']
                        word_path = item['word_path']
                        
                        # Update status (main thread only)
                        filename = os.path.basename(xml_path)
                        self.root.after(
                            0,
                            lambda fn=filename: self.xml_status_label.config(
                                text=f"Processing: {fn}", fg="blue"
                            ),
                        )
                        
                        # Simulate individual processing by temporarily setting the paths
                        original_xml_path = self.xml_path
                        original_word_path = self.word_path
                        
                        self.xml_path = xml_path
                        self.word_path = word_path
                        
                        # Use the existing generate_video logic
                        events = self.parse_xml_events(xml_path)
                        settings = self.get_settings()
                        
                        if os.path.exists(_SETTINGS_JSON):
                            with open(_SETTINGS_JSON, 'r') as f:
                                settings = json.load(f)
                        else:
                            settings = self.get_settings()
                        
                        # Reconstruct text as it grows (uses speed settings)
                        text_states, frame_times = self.reconstruct_text_states(events, settings)
                        
                        # Get font settings from settings
                        font_family = settings["font_family"]
                        font_size = settings["font_size"]
                        bold = settings["bold"]
                        
                        # Generate frames with font settings from JSON
                        frames, frame_times = self.generate_frames(
                            text_states, frame_times, font_family, font_size, bold,
                            settings.get("moving_window", False),
                            settings.get("window_size", 10),
                            settings.get("window_wordonly", False),
                            settings.get("mask_narrow", "_"),
                            settings.get("mask_wide", "#"),
                            settings.get("margin", 20),
                            None,  # progress_callback
                            self.enable_timing_var.get(),
                            self.start_time_var.get(),
                            self.end_time_var.get(),
                            self.duration_percent_var.get(),
                            self.timing_mode_var.get(),
                            settings.get("show_caret", True),
                            word_boundary_trim=settings.get("word_boundary_trim", False),
                            sentence_boundary_trim=settings.get("sentence_boundary_trim", False),
                        )
                        
                        # Assemble video
                        if settings["save_video"]:
                            xml_filename = os.path.splitext(os.path.basename(xml_path))[0]
                            output_path = os.path.join(output_folder, f'{xml_filename}.mp4')
                            self.save_video(frames, frame_times, output_path)
                            self.export_settings_to_csv(settings, output_path)
                        
                        # Restore original paths
                        self.xml_path = original_xml_path
                        self.word_path = original_word_path
                        
                        # Update progress (main thread only)
                        self.root.after(0, lambda v=i + 1: self.progress_xml.config(value=v))
                        
                    except Exception as e:
                        error_msg = f"Failed to process {os.path.basename(xml_path)}: {str(e)}"
                        print(f"DEBUG: {error_msg}")
                        self.root.after(
                            0,
                            lambda msg=error_msg: messagebox.showerror("Error", msg),
                        )
                
                n_done = len(self.xml_queue)
                self.root.after(
                    0,
                    lambda of=output_folder, n=n_done: self._batch_xml_finish_success(of, n),
                )
                
            except Exception as e:
                err = str(e)
                print(f"DEBUG: Batch processing failed: {err}")
                self.root.after(0, lambda e=err: self._batch_xml_finish_error(e))
        
        threading.Thread(target=process_queue, daemon=True).start()

    def process_data_queue(self):
        if not self.data_queue:
            messagebox.showwarning("Warning", "No files in queue")
            return
        
        if self.processing:
            messagebox.showwarning("Warning", "Already processing files")
            return
        
        self.processing = True
        self.batch_generate_data_btn.config(state=tk.DISABLED)
        self.progress_data.pack(pady=5, fill="x", padx=10)
        self.progress_data.config(maximum=len(self.data_queue), value=0)
        
        def process_queue():
            try:
                base_output = os.path.join(_PROGRAM_DIR, 'xml-to-text-video-output')
                if len(self.data_queue) > 1:
                    output_folder = os.path.join(base_output, _batch_upload_subdir_name())
                else:
                    output_folder = base_output
                os.makedirs(output_folder, exist_ok=True)
                
                for i, item in enumerate(self.data_queue):
                    try:
                        data_path = item['data_path']
                        
                        # Update status (main thread only)
                        filename = os.path.basename(data_path)
                        self.root.after(
                            0,
                            lambda fn=filename: self.data_status_label.config(
                                text=f"Processing: {fn}", fg="blue"
                            ),
                        )
                        
                        # Simulate individual processing by temporarily setting the path
                        original_data_path = getattr(self, 'data_txt_path', None)
                        self.data_txt_path = data_path
                        
                        # Use the existing generate_video_from_data_txt logic
                        events = self.parse_data_txt_events(data_path)
                        settings = self.get_settings()
                        
                        if os.path.exists(_SETTINGS_JSON):
                            with open(_SETTINGS_JSON, 'r') as f:
                                settings = json.load(f)
                        else:
                            settings = self.get_settings()
                        
                        text_states, frame_times = self.reconstruct_data_txt_text_states(events, settings)
                        
                        if not text_states or not frame_times:
                            continue
                        
                        # Get font settings from settings
                        font_family = settings["font_family"]
                        font_size = settings["font_size"]
                        bold = settings["bold"]
                        
                        # Generate frames with font settings from JSON
                        frames, frame_times = self.generate_frames(
                            text_states, frame_times, font_family, font_size, bold,
                            settings.get("moving_window", False),
                            settings.get("window_size", 10),
                            settings.get("window_wordonly", False),
                            settings.get("mask_narrow", "_"),
                            settings.get("mask_wide", "#"),
                            settings.get("margin", 20),
                            None,  # progress_callback
                            self.enable_timing_var.get(),
                            self.start_time_var.get(),
                            self.end_time_var.get(),
                            self.duration_percent_var.get(),
                            self.timing_mode_var.get(),
                            settings.get("show_caret", True),
                            word_boundary_trim=settings.get("word_boundary_trim", False),
                            sentence_boundary_trim=settings.get("sentence_boundary_trim", False),
                        )
                        
                        # Save video
                        data_filename = os.path.splitext(os.path.basename(data_path))[0]
                        output_path = os.path.join(output_folder, f'{data_filename}_data.mp4')
                        self.save_video(frames, frame_times, output_path)
                        self.export_settings_to_csv(settings, output_path)
                        
                        # Restore original path
                        self.data_txt_path = original_data_path
                        
                        # Update progress (main thread only)
                        self.root.after(0, lambda v=i + 1: self.progress_data.config(value=v))
                        
                    except Exception as e:
                        error_msg = f"Failed to process {os.path.basename(data_path)}: {str(e)}"
                        print(f"DEBUG: {error_msg}")
                        self.root.after(
                            0,
                            lambda msg=error_msg: messagebox.showerror("Error", msg),
                        )
                
                n_done = len(self.data_queue)
                self.root.after(
                    0,
                    lambda of=output_folder, n=n_done: self._batch_data_finish_success(of, n),
                )
                
            except Exception as e:
                err = str(e)
                print(f"DEBUG: Batch processing failed: {err}")
                self.root.after(0, lambda e=err: self._batch_data_finish_error(e))
        
        threading.Thread(target=process_queue, daemon=True).start()

    def process_idfx_queue(self):
        if not getattr(self, 'idfx_queue', []):
            messagebox.showwarning("Warning", "No files in queue")
            return
        if self.processing:
            messagebox.showwarning("Warning", "Already processing files")
            return
        self.processing = True
        self.batch_generate_idfx_btn.config(state=tk.DISABLED)
        self.progress_idfx.pack(pady=5, fill="x", padx=10)
        self.progress_idfx.config(maximum=len(self.idfx_queue), value=0)
        def process_queue():
            try:
                base_output = os.path.join(_PROGRAM_DIR, 'xml-to-text-video-output')
                if len(self.idfx_queue) > 1:
                    output_folder = os.path.join(base_output, _batch_upload_subdir_name())
                else:
                    output_folder = base_output
                os.makedirs(output_folder, exist_ok=True)
                for i, item in enumerate(self.idfx_queue):
                    try:
                        idfx_path = item['idfx_path']
                        filename = os.path.basename(idfx_path)
                        self.root.after(
                            0,
                            lambda fn=filename: self.idfx_status_label.config(
                                text=f"Processing: {fn}", fg="blue"
                            ),
                        )
                        events = self.parse_idfx_events(idfx_path)
                        settings = self.get_settings()
                        text_states, frame_times = self.reconstruct_idfx_text_states(events, settings)
                        if not text_states or not frame_times:
                            continue
                        font_family = settings["font_family"]
                        font_size = settings["font_size"]
                        bold = settings["bold"]
                        frames, frame_times = self.generate_frames(
                            text_states, frame_times, font_family, font_size, bold,
                            settings.get("moving_window", False),
                            settings.get("window_size", 10),
                            settings.get("window_wordonly", False),
                            settings.get("mask_narrow", "_"),
                            settings.get("mask_wide", "#"),
                            settings.get("margin", 20),
                            None,  # progress_callback
                            self.enable_timing_var.get(),
                            self.start_time_var.get(),
                            self.end_time_var.get(),
                            self.duration_percent_var.get(),
                            self.timing_mode_var.get(),
                            settings.get("show_caret", True),
                            word_boundary_trim=settings.get("word_boundary_trim", False),
                            sentence_boundary_trim=settings.get("sentence_boundary_trim", False),
                        )
                        idfx_filename = os.path.splitext(os.path.basename(idfx_path))[0]
                        output_path = os.path.join(output_folder, f'{idfx_filename}_idfx.mp4')
                        self.save_video(frames, frame_times, output_path)
                        self.export_settings_to_csv(settings, output_path)
                        self.root.after(0, lambda v=i + 1: self.progress_idfx.config(value=v))
                    except Exception as e:
                        error_msg = f"Failed to process {os.path.basename(idfx_path)}: {str(e)}"
                        print(f"DEBUG: {error_msg}")
                        self.root.after(
                            0,
                            lambda msg=error_msg: messagebox.showerror("Error", msg),
                        )
                n_done = len(self.idfx_queue)
                self.root.after(
                    0,
                    lambda of=output_folder, n=n_done: self._batch_idfx_finish_success(of, n),
                )
            except Exception as e:
                err = str(e)
                print(f"DEBUG: Batch processing failed: {err}")
                self.root.after(0, lambda e=err: self._batch_idfx_finish_error(e))
        threading.Thread(target=process_queue, daemon=True).start()

    def check_ready(self):
        # Method kept for compatibility but no longer needed
        pass

    def generate_video(self):
        if not self.xml_path or not self.word_path:
            messagebox.showerror("Error", "Both XML and Word files must be selected.")
            return
        # Indicate video generation is in progress
        self.status_label = getattr(self, 'status_label', None)
        if not self.status_label:
            self.status_label = tk.Label(self.root, text="Generating video, please wait...", fg="blue")
            self.status_label.pack(pady=5)
        else:
            self.status_label.config(text="Generating video, please wait...", fg="blue")
        self.root.update_idletasks()
        self.progress_xml.pack(pady=5, fill="x", padx=10)
        self.progress_xml.start()
        try:
            # Parse XML and reconstruct typing sequence
            events = self.parse_xml_events(self.xml_path)
            if os.path.exists(_SETTINGS_JSON):
                with open(_SETTINGS_JSON, 'r') as f:
                    settings = json.load(f)
            else:
                settings = self.get_settings()
            # Reconstruct text as it grows (uses speed settings)
            text_states, frame_times = self.reconstruct_text_states(events, settings)
            # Get font settings from settings
            font_family = settings["font_family"]
            font_size = settings["font_size"]
            bold = settings["bold"]
            # Generate frames with font settings from JSON
            frames, frame_times = self.generate_frames(
                text_states, frame_times, font_family, font_size, bold,
                settings.get("moving_window", False),
                settings.get("window_size", 10),
                settings.get("window_wordonly", False),
                settings.get("mask_narrow", "_"),
                settings.get("mask_wide", "#"),
                settings.get("margin", 20),
                None,  # progress_callback
                self.enable_timing_var.get(),
                self.start_time_var.get(),
                self.end_time_var.get(),
                self.duration_percent_var.get(),
                self.timing_mode_var.get(),
                settings.get("show_caret", True),
                word_boundary_trim=settings.get("word_boundary_trim", False),
                sentence_boundary_trim=settings.get("sentence_boundary_trim", False),
            )
            # Assemble video
            if settings["save_video"]:
                output_folder = os.path.join(_PROGRAM_DIR, 'xml-to-text-video-output')
                os.makedirs(output_folder, exist_ok=True)
                xml_filename = os.path.splitext(os.path.basename(self.xml_path))[0]
                output_path = os.path.join(output_folder, f'{xml_filename}.mp4')
                self.save_video(frames, frame_times, output_path)
                self.export_settings_to_csv(settings, output_path)
                self.status_label.config(text=f"Video saved to {output_path}", fg="green")
                messagebox.showinfo("Done", f"Video saved to {output_path}")
            else:
                self.status_label.config(text="Video generated (not saved)", fg="green")
                messagebox.showinfo("Done", "Video generated (not saved)")
            self.progress_xml.stop()
            self.progress_xml.pack_forget()
        except Exception as e:
            self.status_label.config(text=f"Error: {e}", fg="red")
            messagebox.showerror("Error", str(e))
            self.progress_xml.stop()
            self.progress_xml.pack_forget()

    def generate_video_from_data_txt(self):
        if not hasattr(self, 'data_txt_path') or not self.data_txt_path:
            messagebox.showerror("Error", "No data.txt file selected.")
            return
        self.data_status_label.config(text="Generating video, please wait...", fg="blue")
        self.root.update_idletasks()
        self.progress_data.pack(pady=5, fill="x", padx=10)
        self.progress_data.start()

        def do_generate():
            try:
                self.data_status_label.config(text="Parsing data.txt...", fg="blue")
                print("[DEBUG] Starting to parse data.txt events...")
                events = self.parse_data_txt_events(self.data_txt_path)
                print(f"[DEBUG] Parsed {len(events)} events from data.txt.")
                self.data_status_label.config(text=f"Parsed {len(events)} events. Reconstructing text states...", fg="blue")
                settings = self.get_settings()
                text_states, frame_times = self.reconstruct_data_txt_text_states(events, settings)
                print(f"[DEBUG] Reconstructed {len(text_states)} text states.")
                self.data_status_label.config(text=f"Reconstructed {len(text_states)} text states. Generating frames...", fg="blue")
                if not text_states or not frame_times or len(text_states) != len(frame_times):
                    self.data_status_label.config(text="Error: No valid typing events found in file or data is malformed.", fg="red")
                    messagebox.showerror("Error", "No valid typing events found in file or data is malformed.")
                    self.progress_data.stop()
                    self.progress_data.pack_forget()
                    return
                font_family = settings["font_family"]
                font_size = settings["font_size"]
                bold = settings["bold"]
                print("[DEBUG] Generating frames...")
                def update_progress(current, total):
                    self.data_status_label.config(text=f"Generating frames: {current}/{total}", fg="blue")
                frames, frame_times = self.generate_frames(
                    text_states, frame_times, font_family, font_size, bold,
                    settings.get("moving_window", False),
                    settings.get("window_size", 10),
                    settings.get("window_wordonly", False),
                    settings.get("mask_narrow", "_"),
                settings.get("mask_wide", "#"),
                    settings.get("margin", 20),
                    progress_callback=update_progress,
                    enable_timing=self.enable_timing_var.get(),
                    start_time=self.start_time_var.get(),
                    end_time=self.end_time_var.get(),
                    duration_percent=self.duration_percent_var.get(),
                    timing_mode=self.timing_mode_var.get(),
                    show_caret=settings.get("show_caret", True),
                    word_boundary_trim=settings.get("word_boundary_trim", False),
                    sentence_boundary_trim=settings.get("sentence_boundary_trim", False),
                )
                print(f"[DEBUG] Generated {len(frames)} frames.")
                self.data_status_label.config(text=f"Generated {len(frames)} frames. Saving video...", fg="blue")
                output_folder = os.path.join(_PROGRAM_DIR, 'xml-to-text-video-output')
                os.makedirs(output_folder, exist_ok=True)
                data_filename = os.path.splitext(os.path.basename(self.data_txt_path))[0]
                output_path = os.path.join(output_folder, f'{data_filename}_data.mp4')
                print(f"[DEBUG] Saving video to {output_path}...")
                self.save_video(frames, frame_times, output_path)
                self.export_settings_to_csv(settings, output_path)
                print(f"[DEBUG] Video saved to {output_path}.")
                self.data_status_label.config(text=f"Video saved to {output_path}", fg="green")
                messagebox.showinfo("Done", f"Video saved to {output_path}")
                self.progress_data.stop()
                self.progress_data.pack_forget()
            except Exception as e:
                print(f"[DEBUG] Exception: {e}")
                self.data_status_label.config(text=f"Error: {e}", fg="red")
                messagebox.showerror("Error", str(e))
                self.progress_data.stop()
                self.progress_data.pack_forget()

        threading.Thread(target=do_generate, daemon=True).start()

    def generate_video_from_idfx(self):
        if not hasattr(self, 'idfx_path') or not self.idfx_path:
            messagebox.showerror("Error", "No .idfx file selected.")
            return
        self.idfx_status_label.config(text="Generating video, please wait...", fg="blue")
        self.root.update_idletasks()
        self.progress_idfx.pack(pady=5, fill="x", padx=10)
        self.progress_idfx.start()
        def do_generate():
            try:
                self.idfx_status_label.config(text="Parsing .idfx...", fg="blue")
                events = self.parse_idfx_events(self.idfx_path)
                self.idfx_status_label.config(text=f"Parsed {len(events)} events. Reconstructing text states...", fg="blue")
                settings = self.get_settings()
                text_states, frame_times = self.reconstruct_idfx_text_states(events, settings)
                if not text_states or not frame_times or len(text_states) != len(frame_times):
                    self.idfx_status_label.config(text="Error: No valid typing events found in file or data is malformed.", fg="red")
                    messagebox.showerror("Error", "No valid typing events found in file or data is malformed.")
                    self.progress_idfx.stop()
                    self.progress_idfx.pack_forget()
                    return
                font_family = settings["font_family"]
                font_size = settings["font_size"]
                bold = settings["bold"]
                def update_progress(current, total):
                    self.idfx_status_label.config(text=f"Generating frames: {current}/{total}", fg="blue")
                frames, frame_times = self.generate_frames(
                    text_states, frame_times, font_family, font_size, bold,
                    settings.get("moving_window", False),
                    settings.get("window_size", 10),
                    settings.get("window_wordonly", False),
                    settings.get("mask_narrow", "_"),
                settings.get("mask_wide", "#"),
                    settings.get("margin", 20),
                    progress_callback=update_progress,
                    enable_timing=self.enable_timing_var.get(),
                    start_time=self.start_time_var.get(),
                    end_time=self.end_time_var.get(),
                    duration_percent=self.duration_percent_var.get(),
                    timing_mode=self.timing_mode_var.get(),
                    show_caret=settings.get("show_caret", True),
                    word_boundary_trim=settings.get("word_boundary_trim", False),
                    sentence_boundary_trim=settings.get("sentence_boundary_trim", False),
                )
                output_folder = os.path.join(_PROGRAM_DIR, 'xml-to-text-video-output')
                os.makedirs(output_folder, exist_ok=True)
                idfx_filename = os.path.splitext(os.path.basename(self.idfx_path))[0]
                output_path = os.path.join(output_folder, f'{idfx_filename}_idfx.mp4')
                self.save_video(frames, frame_times, output_path)
                self.export_settings_to_csv(settings, output_path)
                self.idfx_status_label.config(text=f"Video saved to {output_path}", fg="green")
                messagebox.showinfo("Done", f"Video saved to {output_path}")
                self.progress_idfx.stop()
                self.progress_idfx.pack_forget()
            except Exception as e:
                self.idfx_status_label.config(text=f"Error: {e}", fg="red")
                messagebox.showerror("Error", str(e))
                self.progress_idfx.stop()
                self.progress_idfx.pack_forget()
        threading.Thread(target=do_generate, daemon=True).start()

    def parse_xml_events(self, xml_path):
        tree = etree.parse(xml_path)
        root = tree.getroot()
        events = []
        for event in root.findall(".//event"):
            if event.findtext("type") == "keyboard":
                output = event.findtext("output")
                start_time = event.findtext("startTime")
                if output and start_time:
                    events.append({
                        'output': output,
                        'start_time': int(start_time)
                    })
        return events

    def reconstruct_text_states(self, events, settings):
        # If uniform typing mode is enabled, ignore events and use Word file
        if settings["uniform_typing"] and hasattr(self, 'word_path') and self.word_path:
            # Read text from Word file
            doc = Document(self.word_path)
            full_text = '\n'.join([p.text for p in doc.paragraphs])
            text_states = []
            frame_times = []
            chars_per_sec = settings["chars_per_sec"]
            interval = 1.0 / chars_per_sec if chars_per_sec > 0 else 0.1
            text = ""
            for c in full_text:
                text += c
                text_states.append(text)
                frame_times.append(interval)
            # Apply video speed multiplier
            speed_mult = settings["video_speed"]
            frame_times = [ft / speed_mult for ft in frame_times]
            text_states, frame_times = inject_fake_backspaces_into_timeline(text_states, frame_times, settings)
            return text_states, frame_times
        ev = events
        if settings.get("strip_backspace_edits", False):
            ev = filter_events_remove_backspace_edits(events, "xml")
        text = ""
        text_states = []
        frame_times = []
        last_time = 0
        for event in ev:
            output = event['output']
            t = event['start_time'] / 1000.0  # ms to seconds
            if output == "SPACE":
                text += " "
            elif output == "BACK":
                text = text[:-1]
            elif output and len(output) == 1:
                text += output
            # Save state and time delta
            text_states.append(text)
            frame_times.append(max(t - last_time, 0.05))  # at least 0.05s per frame
            last_time = t
        if settings.get("strip_backspace_edits", False):
            # Do not count time before first survivor or between removed keys; stitch survivor deltas only
            frame_times = stitch_frame_times_after_backspace_strip(ev, settings, event_format="xml")
        else:
            # Adjust frame_times for word/space speed overrides
            word_speed = settings["word_speed"]
            space_duration = settings["space_duration"]
            for i, event in enumerate(ev):
                output = event['output']
                if output == "SPACE":
                    frame_times[i] = space_duration
                elif output and len(output) == 1:
                    # Only set for the first char of a word (after a space or at start)
                    if i == 0 or ev[i - 1]['output'] == "SPACE":
                        frame_times[i] = word_speed
            # Apply video speed multiplier
            speed_mult = settings["video_speed"]
            frame_times = [ft / speed_mult for ft in frame_times]
        text_states, frame_times = inject_fake_backspaces_into_timeline(text_states, frame_times, settings)
        return text_states, frame_times

    def parse_data_txt_events(self, data_txt_path):
        try:
            import ijson
        except ImportError:
            messagebox.showerror("Missing Dependency", "Please install the 'ijson' package to handle large data.txt files: pip install ijson")
            return []
        events = []
        time_accum = 0
        try:
            with open(data_txt_path, 'r') as f:
                print('DEBUG: Starting to parse data.txt with ijson')
                debug_count = 0
                found_any = False
                for entry in ijson.items(f, 'data.item'):
                    found_any = True
                    if debug_count < 5:
                        print('DEBUG ENTRY:', entry)
                        debug_count += 1
                    if not isinstance(entry, dict):
                        continue  # skip non-dict entries
                    key = entry.get('response_new_keyboard_response_1_1_4_1')
                    if key is None:
                        key = entry.get('response_new_keyboard_response_1_1_4')
                    t = entry.get('response_time_new_keyboard_response_1_1_4_1')
                    if t is None:
                        t = entry.get('response_time_new_keyboard_response_1_1_4')
                    if key is not None and t is not None:
                        time_accum += int(t)
                        events.append({'output': key, 'start_time': time_accum})
                if not found_any:
                    print('DEBUG: No items found in data.txt. Is it an empty file or not a top-level array?')
        except Exception as e:
            messagebox.showerror("Error", f"Failed to parse data.txt: {e}")
            return []
        return events

    def parse_idfx_events(self, idfx_path):
        # Parse TypingDNA .idfx log structure into normalized events
        # Output format: {'output': one of 'space','enter','backspace' or single-character string, 'start_time': ms}
        events = []
        try:
            tree = etree.parse(idfx_path)
            root = tree.getroot()
            # Iterate keyboard events
            for event in root.findall(".//event[@type='keyboard']"):
                winlog = None
                for part in event.findall("part"):
                    if part.get("type") == "winlog":
                        winlog = part
                        break
                if winlog is None:
                    continue
                key = (winlog.findtext("key") or "").strip()
                val = winlog.findtext("value")
                start_time_txt = winlog.findtext("startTime")
                if start_time_txt is None:
                    continue
                try:
                    start_time = int(start_time_txt)
                except Exception:
                    continue
                # Normalize output
                output = None
                if key == "VK_SPACE":
                    output = "space"
                elif key == "VK_RETURN":
                    output = "enter"
                elif key in ("VK_BACK", "VK_BACKSPACE"):
                    output = "backspace"
                else:
                    # Use 'value' if present and printable single character
                    if val is not None and len(val) == 1:
                        output = val
                    else:
                        # Some logs may encode backspace as value "\u0008" (&#x8;)
                        if val is not None and (val == "\b" or val == "\u0008" or "#x8" in val):
                            output = "backspace"
                        else:
                            continue
                events.append({'output': output, 'start_time': start_time})
        except Exception as e:
            messagebox.showerror("Error", f"Failed to parse .idfx: {e}")
            return []
        return events

    def reconstruct_data_txt_text_states(self, events, settings, apply_data_strip_filter=True):
        ev = events
        if apply_data_strip_filter and settings.get("strip_backspace_edits", False):
            ev = filter_events_remove_backspace_edits(events, "data")
        text = ""
        text_states = []
        frame_times = []
        last_time = 0
        valid_events = []
        for event in ev:
            output = event['output']
            t = event['start_time'] / 1000.0  # ms to seconds
            if output == "space":
                text += " "
            elif output == "enter":
                text += "\n"
            elif output == "backspace":
                text = text[:-1]
            elif output and isinstance(output, str) and len(output) == 1:
                text += output
            else:
                continue  # skip events that don't add a char/space/enter/backspace
            text_states.append(text)
            frame_times.append(max(t - last_time, 0.05))
            last_time = t
            valid_events.append(event)
        if settings.get("strip_backspace_edits", False):
            # Survivor-only deltas: removed keys/backspaces must not add duration
            frame_times = stitch_frame_times_after_backspace_strip(
                valid_events, settings, event_format="data"
            )
        else:
            # Adjust frame_times for word/space speed overrides
            word_speed = settings["word_speed"]
            space_duration = settings["space_duration"]
            for i, event in enumerate(valid_events):
                output = event['output']
                if output == "space":
                    frame_times[i] = space_duration
                elif output and isinstance(output, str) and len(output) == 1:
                    if i == 0 or valid_events[i - 1]['output'] == "space":
                        frame_times[i] = word_speed
            speed_mult = settings["video_speed"]
            frame_times = [ft / speed_mult for ft in frame_times]
        text_states, frame_times = inject_fake_backspaces_into_timeline(text_states, frame_times, settings)
        return text_states, frame_times

    def reconstruct_idfx_text_states(self, events, settings):
        """Build text timeline from IDFX events. Uses block-based backspace stripping when enabled."""
        ev = events
        if settings.get("strip_backspace_edits", False):
            ev = filter_idfx_strip_backspace_blocks_and_preceding_keys(events)
        # Do not apply legacy stack-based strip; IDFX already used filter_idfx_strip_backspace_blocks_and_preceding_keys
        return self.reconstruct_data_txt_text_states(ev, settings, apply_data_strip_filter=False)

    def _try_load_font_with_matplotlib(self, font_family, font_size, bold, font_manager):
        """Try to load font using matplotlib font manager"""
        if not font_manager:
            return None
        try:
            font_props = font_manager.FontProperties(family=font_family, weight='bold' if bold else 'normal')
            font_path = font_manager.findfont(font_props, fallback_to_default=False)
            if font_path and os.path.exists(font_path):
                from PIL import ImageFont
                return ImageFont.truetype(font_path, font_size)
        except Exception:
            pass
        return None

    def _try_load_system_fonts(self, font_family, font_size, bold):
        """Try to load common system fonts"""
        from PIL import ImageFont
        
        # Common font mappings
        font_mappings = {
            'Arial': ['arial', 'Arial', 'ArialMT'],
            'Times': ['times', 'Times', 'Times New Roman'],
            'Courier': ['courier', 'Courier', 'Courier New'],
            'Helvetica': ['helvetica', 'Helvetica'],
            'Verdana': ['verdana', 'Verdana'],
            'Georgia': ['georgia', 'Georgia'],
            'Comic Sans': ['comic', 'Comic Sans MS'],
        }
        
        # Try the exact font name first
        try:
            return ImageFont.truetype(font_family, font_size)
        except Exception:
            pass
        
        # Try mapped variations
        for base_name, variations in font_mappings.items():
            if font_family.lower() in [v.lower() for v in variations]:
                for variation in variations:
                    try:
                        return ImageFont.truetype(variation, font_size)
                    except Exception:
                        continue
        
        # Try system-specific paths
        if os.name == "nt":  # Windows
            font_dir = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
            fallback = "arialbd.ttf" if bold else "arial.ttf"
            font_path = os.path.join(font_dir, fallback)
            if os.path.exists(font_path):
                try:
                    return ImageFont.truetype(font_path, font_size)
                except Exception:
                    pass
        elif os.name == "posix":  # macOS/Linux
            font_paths = [
                "/System/Library/Fonts/Arial.ttf",
                "/System/Library/Fonts/Helvetica.ttc",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/TTF/arial.ttf"
            ]
            for font_path in font_paths:
                if os.path.exists(font_path):
                    try:
                        return ImageFont.truetype(font_path, font_size)
                    except Exception:
                        continue
        
        return None

    def _try_load_pil_font(self, font_family, font_size, bold):
        """Try PIL's built-in font loading"""
        from PIL import ImageFont
        try:
            # Try common font names that PIL might recognize
            common_fonts = ['Arial', 'Helvetica', 'Times', 'Courier']
            for font_name in common_fonts:
                try:
                    return ImageFont.truetype(font_name, font_size)
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def apply_video_time_controls(
        self,
        text_states,
        frame_times,
        enable_timing,
        start_time_ms,
        end_time_ms,
        duration_percent,
        timing_mode,
        output_fps=VIDEO_OUTPUT_FPS,
        word_boundary_trim=False,
        sentence_boundary_trim=False,
    ):
        # Cumulative timeline (s per frame); trims snap to 1/output_fps (matches save_video expansion).
        if not enable_timing or not text_states or not frame_times:
            return text_states, frame_times
        if len(text_states) != len(frame_times):
            return text_states, frame_times

        n = len(frame_times)
        cum_start = [0.0] + list(accumulate(frame_times[:-1]))
        _of = max(1, output_fps)

        def snap_down(t):
            return round(t * _of) / _of

        total_duration = cum_start[-1] + frame_times[-1]

        if timing_mode == "absolute":
            start_s = snap_down(max(0.0, start_time_ms * 0.001))
            if end_time_ms > 0:
                end_raw = min(total_duration, end_time_ms * 0.001)
                end_s = snap_down(end_raw)
                # snap_down can round small positives to 0 (e.g. 0.01s at 20fps); keep a valid window
                if end_s <= start_s and total_duration > start_s:
                    end_s = end_raw
            else:
                end_s = total_duration
            if end_s <= start_s:
                return [], []
        else:
            start_s = snap_down(max(0.0, start_time_ms * 0.001))
            if start_s >= total_duration:
                return [], []
            keep_dur = snap_down(0.0 if (duration_percent == 0) else max(
                0.0, (total_duration - start_s) * (float(duration_percent) * 0.01)))
            end_s = min(total_duration, start_s + keep_dur)
            if end_s <= start_s:
                return [], []

        # Plain timing trim (before boundary snapping); used as fallback if snap yields no frames
        time_trim_start_s, time_trim_end_s = start_s, end_s

        if sentence_boundary_trim:
            cum_end = [cum_start[i] + frame_times[i] for i in range(n)]
            i0 = next((i for i in range(n) if cum_end[i] > start_s), None)
            if i0 is None:
                return [], []
            i_start = next((i for i in range(i0, n) if _frame_starts_sentence(text_states, i)), None)
            if i_start is None:
                i_start = i0
            start_s = cum_start[i_start]

            doc_has_period = _document_has_sentence_punctuation(text_states)
            doc_has_nl = _document_has_newline(text_states)

            j_end = None
            if doc_has_period:
                for j in range(n - 1, -1, -1):
                    if cum_end[j] > end_s:
                        continue
                    if _text_ends_with_sentence_closer(text_states[j]):
                        j_end = j
                        break
            if j_end is None and not doc_has_period and doc_has_nl:
                for j in range(n - 1, -1, -1):
                    if cum_end[j] > end_s:
                        continue
                    if _frame_immediately_before_newline(text_states, j, n):
                        j_end = j
                        break
            if j_end is None:
                for j in range(n - 1, -1, -1):
                    if cum_end[j] > end_s:
                        continue
                    if _frame_at_word_end_boundary(text_states, j, n):
                        j_end = j
                        break
            if j_end is None:
                for j in range(n - 1, -1, -1):
                    if cum_end[j] <= end_s:
                        j_end = j
                        break
                if j_end is None:
                    return [], []
            end_s = cum_end[j_end]
            if end_s <= start_s:
                return [], []
        elif word_boundary_trim:
            cum_end = [cum_start[i] + frame_times[i] for i in range(n)]
            i0 = next((i for i in range(n) if cum_end[i] > start_s), None)
            if i0 is None:
                return [], []
            i_start = next((i for i in range(i0, n) if _frame_starts_new_word(text_states, i)), None)
            # Trim can start mid-word: there may be no "new word" frame at/after i0 — use first visible frame
            if i_start is None:
                i_start = i0
            start_s = cum_start[i_start]
            j_end = None
            for j in range(n - 1, -1, -1):
                if cum_end[j] > end_s:
                    continue
                if _frame_at_word_end_boundary(text_states, j, n):
                    j_end = j
                    break
            if j_end is None:
                for j in range(n - 1, -1, -1):
                    if cum_end[j] <= end_s:
                        j_end = j
                        break
                if j_end is None:
                    return [], []
            end_s = cum_end[j_end]
            if end_s <= start_s:
                return [], []

        def _collect_trim_segments(t0, t1):
            """Collect overlapping frame segments for [t0, t1) on the timeline."""
            out_s, out_t = [], []
            app_s, app_t = out_s.append, out_t.append
            for i in range(n):
                seg_left = cum_start[i]
                seg_right = seg_left + frame_times[i]
                a = seg_left if seg_left > t0 else t0
                b = seg_right if seg_right < t1 else t1
                if b > a + 1e-12:
                    app_t(b - a)
                    app_s(text_states[i])
            return out_s, out_t

        out_states, out_times = _collect_trim_segments(start_s, end_s)
        # Boundary snap can make start/end incompatible with any frame overlap; fall back to plain timing trim
        if not out_states and n > 0 and (word_boundary_trim or sentence_boundary_trim):
            out_states, out_times = _collect_trim_segments(time_trim_start_s, time_trim_end_s)

        return out_states, out_times

    def generate_frames(self, text_states, frame_times, font_family=None, font_size=None, bold=None, moving_window=False, window_size=10, window_wordonly=False, mask_narrow="_", mask_wide="#", margin=20, progress_callback=None, enable_timing=False, start_time=0, end_time=0, duration_percent=100.0, timing_mode="absolute", show_caret=True, word_boundary_trim=False, sentence_boundary_trim=False):
        from PIL import ImageFont, Image, ImageDraw
        try:
            from matplotlib import font_manager
        except ImportError:
            font_manager = None
        
        # Use UI values as defaults if not provided
        if font_family is None:
            font_family = self.font_family_var.get()
        if font_size is None:
            font_size = self.font_size_var.get()
        if bold is None:
            bold = self.bold_var.get()
        if moving_window:
            window_size = self.window_size_var.get()
            window_wordonly = self.window_wordonly_var.get()
            mask_narrow = self.mask_narrow_var.get() or "_"
            mask_wide = self.mask_wide_var.get() or "#"
        if margin == 20:  # Only use UI margin if default was passed
            margin = self.margin_var.get()
        
        font = None
        font_loaded = True
        original_font_family = font_family
        
        # Try multiple font loading strategies
        font_strategies = [
            # Strategy 1: Try with matplotlib font manager
            lambda: self._try_load_font_with_matplotlib(font_family, font_size, bold, font_manager),
            # Strategy 2: Try common system fonts
            lambda: self._try_load_system_fonts(font_family, font_size, bold),
            # Strategy 3: Try PIL's built-in font loading
            lambda: self._try_load_pil_font(font_family, font_size, bold),
            # Strategy 4: Fallback to default
            lambda: ImageFont.load_default()
        ]
        
        for strategy in font_strategies:
            try:
                font = strategy()
                if font is not None:
                    break
            except Exception:
                continue
        
        # If we couldn't load the requested font, show a warning
        if font_loaded and font is not None:
            try:
                # Test if the font actually works
                test_img = Image.new("RGB", (10, 10))
                test_draw = ImageDraw.Draw(test_img)
                test_draw.text((0, 0), "Test", font=font)
            except Exception:
                font_loaded = False
        
        if not font_loaded:
            try:
                self.root.after(0, lambda: messagebox.showwarning(
                    "Font Warning",
                    f"Could not load the selected font '{original_font_family}'. Using system default font instead."
                ))
            except Exception:
                pass
        width, height = 1280, 720
        frames = []
        blink_period = 1.0
        caret_width = 1
        caret_color = "black"
        last_text = None
        blink_time = 0.0
        
        # Apply timing controls: cumulative timeline (seconds) + output FPS grid
        text_states, frame_times = self.apply_video_time_controls(
            text_states,
            frame_times,
            enable_timing,
            int(start_time),
            int(end_time),
            float(duration_percent),
            timing_mode,
            output_fps=VIDEO_OUTPUT_FPS,
            word_boundary_trim=word_boundary_trim,
            sentence_boundary_trim=sentence_boundary_trim,
        )

        for idx, text in enumerate(text_states):
            # Layout constants - use the margin parameter
            # Wrap text within the visible frame accounting for left+right margins
            lines = self.wrap_text(text, font, width - 2 * margin)
            if moving_window:
                # White background, show all text but mask characters outside window
                img = Image.new("RGB", (width, height), color="white")
                draw = ImageDraw.Draw(img)
                # Stable baseline line height for caret anchoring to avoid early-line jitter
                try:
                    ascent, descent = font.getmetrics()
                    base_line_h = ascent + descent
                except Exception:
                    base_bbox = draw.textbbox((0, 0), "Ag", font=font)
                    base_line_h = (base_bbox[3] - base_bbox[1]) if base_bbox else font_size
                
                # Get the final text to show full text from the beginning
                final_text = text_states[-1] if text_states else ""
                final_lines = self.wrap_text(final_text, font, width - 2 * margin)
                
                # Calculate window boundaries centered on the caret position
                # Double the UI window size for the actual moving window
                actual_window_size = window_size * 2
                caret_pos = len(text)
                half_window = actual_window_size // 2
                
                # Always try to show exactly actual_window_size characters
                if len(final_text) <= actual_window_size:
                    # Text is shorter than window - show all text
                    window_start = 0
                    window_end = len(final_text)
                elif caret_pos <= half_window:
                    # Near the beginning - show first actual_window_size characters
                    window_start = 0
                    window_end = actual_window_size
                elif caret_pos >= len(final_text) - half_window:
                    # Near the end - show last actual_window_size characters
                    window_start = len(final_text) - actual_window_size
                    window_end = len(final_text)
                else:
                    # In the middle - center the window around caret
                    window_start = caret_pos - half_window
                    window_end = window_start + actual_window_size
                
                # Draw the complete final text with hiding characters
                y = margin
                char_idx = 0
                last_line_y = y
                caret_x = margin
                caret_y = margin
                
                # Draw ALL final lines with proper spacing
                for i, line in enumerate(final_lines):
                    x = margin
                    for c in line:
                        # Calculate the actual character index in the final text
                        actual_char_idx = char_idx
                        
                        # Determine if this character should be visible based on window position
                        should_show = window_start <= actual_char_idx < window_end
                        
                        if should_show:
                            # Show the actual character (only if it's been typed)
                            if actual_char_idx < len(text):
                                draw.text((x, y), c, font=font, fill="black")
                                # Use natural character spacing for readable text
                                char_width = draw.textbbox((x, y), c, font=font)[2] - draw.textbbox((x, y), c, font=font)[0]
                            elif c == ' ':
                                # Keep spaces as spaces, never mask them
                                draw.text((x, y), ' ', font=font, fill="black")
                                char_width = draw.textbbox((x, y), ' ', font=font)[2] - draw.textbbox((x, y), ' ', font=font)[0]
                            else:
                                # Show mask for untyped text: use narrow or wide based on char width
                                cw = draw.textbbox((x, y), c, font=font)[2] - draw.textbbox((x, y), c, font=font)[0]
                                ref_w = draw.textbbox((x, y), "n", font=font)[2] - draw.textbbox((x, y), "n", font=font)[0]
                                m = mask_wide if cw > ref_w * 1.1 else mask_narrow
                                draw.text((x, y), m, font=font, fill="black")
                                char_width = draw.textbbox((x, y), m, font=font)[2] - draw.textbbox((x, y), m, font=font)[0]
                        else:
                            # Show the mask character (respecting line breaks and spaces)
                            if c == '\n':
                                # Keep newlines as newlines
                                draw.text((x, y), '\n', font=font, fill="black")
                                char_width = 0  # Newlines don't advance x position
                            elif c == ' ':
                                # Keep spaces as spaces, never mask them
                                draw.text((x, y), ' ', font=font, fill="black")
                                char_width = draw.textbbox((x, y), ' ', font=font)[2] - draw.textbbox((x, y), ' ', font=font)[0]
                            else:
                                # Replace with narrow or wide mask based on char width
                                cw = draw.textbbox((x, y), c, font=font)[2] - draw.textbbox((x, y), c, font=font)[0]
                                ref_w = draw.textbbox((x, y), "n", font=font)[2] - draw.textbbox((x, y), "n", font=font)[0]
                                m = mask_wide if cw > ref_w * 1.1 else mask_narrow
                                draw.text((x, y), m, font=font, fill="black")
                                char_width = draw.textbbox((x, y), m, font=font)[2] - draw.textbbox((x, y), m, font=font)[0]
                        
                        x += char_width
                        char_idx += 1
                        
                        # Update caret position as we go (accounting for actual character widths)
                        if actual_char_idx == len(text) - 1:  # Last character
                            caret_x = x
                            caret_y = y
                    
                    # Track the last line for caret positioning
                    if i == len(final_lines) - 1:
                        last_line_y = y
                    
                    # Advance by exactly one baseline height (no extra spacing)
                    y += base_line_h
                
                # Caret position is already calculated in the drawing loop above
                caret_h = max(1, int(round(font_size * 0.9)))
            else:
                # White background, draw all wrapped text in black
                img = Image.new("RGB", (width, height), color="white")
                draw = ImageDraw.Draw(img)
                # Stable baseline line height for caret anchoring to avoid early-line jitter
                try:
                    ascent, descent = font.getmetrics()
                    base_line_h = ascent + descent
                except Exception:
                    base_bbox = draw.textbbox((0, 0), "Ag", font=font)
                    base_line_h = (base_bbox[3] - base_bbox[1]) if base_bbox else font_size
                caret_h = max(1, int(round(font_size * 0.9)))
                
                # Calculate how many lines can fit in the visible area
                available_height = height - 2 * margin
                max_visible_lines = max(1, available_height // base_line_h)
                
                # Auto-scroll: if we have more lines than can fit, start from a later line
                start_line_idx = 0
                if len(lines) > max_visible_lines:
                    # Start from the line that puts the last line at the bottom
                    start_line_idx = max(0, len(lines) - max_visible_lines)
                
                # Draw only the visible lines
                y = margin
                last_line_y = y
                visible_lines = lines[start_line_idx:start_line_idx + max_visible_lines]
                
                for i, line in enumerate(visible_lines):
                    actual_line_idx = start_line_idx + i
                    draw.text((margin, y), line, font=font, fill="black")
                    bbox = draw.textbbox((margin, y), line, font=font)
                    line_height = bbox[3] - bbox[1]
                    if actual_line_idx == len(lines) - 1:
                        last_line_y = y
                        # Use baseline height to determine line bottom consistently
                        last_line_height = base_line_h
                    # Advance by exactly one baseline height (no extra spacing)
                    y += base_line_h
                
                # Place caret at the end of the last line
                if lines:
                    last_line = lines[-1]
                    safe_last_line = last_line.split('\n')[-1]
                    caret_x = margin + draw.textlength(safe_last_line, font=font)
                    # Shorter fixed caret height (0.9x font size); bottom anchored to baseline
                    caret_h = max(1, int(round(font_size * 0.9)))
                    caret_y = last_line_y + base_line_h - caret_h - 2
                else:
                    caret_h = max(1, int(round(font_size * 0.9)))
                    caret_x, caret_y = margin, margin + base_line_h - caret_h - 2
            # Blinking caret logic with reset on new character (only if show_caret enabled)
            if last_text is None or text != last_text:
                blink_time = 0.0
                caret_visible = True
            else:
                caret_visible = ((blink_time % blink_period) < (blink_period / 2))
            if show_caret and caret_visible:
                draw.rectangle(
                    [caret_x, caret_y, caret_x + caret_width, caret_y + caret_h],
                    fill=caret_color
                )
            frames.append(img)
            last_text = text
            if idx < len(frame_times):
                blink_time += frame_times[idx]
            if progress_callback and idx % 100 == 0:
                progress_callback(idx, len(text_states))
        return frames, frame_times

    def wrap_text(self, text, font, max_width):
        # Handle both explicit newlines and word wrapping
        dummy_img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(dummy_img)
        
        # First split by explicit newlines
        paragraphs = text.split('\n')
        lines = []
        
        for paragraph in paragraphs:
            if not paragraph:  # Empty paragraph from consecutive newlines
                lines.append('')
                continue
                
            # Word wrap within each paragraph
            words = paragraph.split(' ')
            line = ''
            for word in words:
                test_line = line + (' ' if line else '') + word
                bbox = draw.textbbox((0, 0), test_line, font=font)
                if bbox[2] - bbox[0] <= max_width:
                    line = test_line
                else:
                    if line:
                        lines.append(line)
                    line = word
            if line:
                lines.append(line)
        
        return lines

    def save_video(self, frames, frame_times, output_path):
        if not frames or not frame_times or len(frames) != len(frame_times):
            raise ValueError(
                "No video frames to save. If timing controls are enabled, start/end times may exclude all content."
            )
        # Convert PIL images to numpy arrays
        import numpy as np
        frame_arrays = [np.array(f) for f in frames]
        # Use frame_times as durations
        durations = frame_times
        # MoviePy expects fps, so we use variable durations by repeating frames
        # We'll use a workaround: create a list of (frame, duration) pairs
        clips = []
        for arr, dur in zip(frame_arrays, durations):
            clips.append((arr, dur))
        # Flatten to frames at output FPS (must match apply_video_time_controls / VIDEO_OUTPUT_FPS)
        fps = VIDEO_OUTPUT_FPS
        video_frames = []
        for arr, dur in clips:
            count = max(1, int(round(dur * fps)))
            video_frames.extend([arr] * count)
        clip = ImageSequenceClip(video_frames, fps=fps)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        clip.write_videofile(output_path, codec='libx264', audio=False)

    def preview_video(self):
        # Generate and preview the video in a separate thread to avoid blocking the GUI
        def do_preview():
            try:
                events = self.parse_xml_events(self.xml_path)
                text_states, frame_times = self.reconstruct_text_states(events, self.get_settings())
                font_family = self.font_family_var.get()
                font_size = self.font_size_var.get()
                bold = self.bold_var.get()
                frames, frame_times = self.generate_frames(
                    text_states, frame_times, font_family, font_size, bold,
                    self.moving_window_var.get(),
                    self.window_size_var.get(),
                    self.window_wordonly_var.get(),
                    self.mask_narrow_var.get(),
                    self.mask_wide_var.get(),
                    self.margin_var.get(),
                    None,  # progress_callback
                    self.enable_timing_var.get(),
                    self.start_time_var.get(),
                    self.end_time_var.get(),
                    self.duration_percent_var.get(),
                    self.timing_mode_var.get(),
                    show_caret=self.show_caret_var.get(),
                    word_boundary_trim=self.word_boundary_trim_var.get(),
                    sentence_boundary_trim=self.sentence_boundary_trim_var.get(),
                )
                # Save to a temporary file
                with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmpfile:
                    temp_path = tmpfile.name
                self.save_video(frames, frame_times, temp_path)
                # Play the video using the default system player
                if os.name == 'nt':
                    os.startfile(temp_path)
                else:
                    import subprocess
                    subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', temp_path])
            except Exception as e:
                messagebox.showerror("Preview Error", str(e))
        threading.Thread(target=do_preview, daemon=True).start()

    def get_settings(self):
        return {
            "font_family": self.font_family_var.get(),
            "font_size": self.font_size_var.get(),
            "bold": self.bold_var.get(),
            "margin": self.margin_var.get(),
            "show_caret": self.show_caret_var.get(),
            "uniform_typing": self.uniform_typing_var.get(),
            "chars_per_sec": self.chars_per_sec_var.get(),
            "video_speed": self.video_speed_var.get(),
            "word_speed": self.word_speed_var.get(),
            "space_duration": self.space_duration_var.get(),
            "save_video": self.save_video_var.get(),
            "moving_window": self.moving_window_var.get(),
            "window_size": self.window_size_var.get(),
            "window_wordonly": self.window_wordonly_var.get(),
            "mask_narrow": self.mask_narrow_var.get(),
            "mask_wide": self.mask_wide_var.get(),
            "enable_timing": self.enable_timing_var.get(),
            "start_time": self.start_time_var.get(),
            "end_time": self.end_time_var.get(),
            "duration_percent": self.duration_percent_var.get(),
            "timing_mode": self.timing_mode_var.get(),
            "word_boundary_trim": self.word_boundary_trim_var.get(),
            "sentence_boundary_trim": self.sentence_boundary_trim_var.get(),
            "strip_backspace_edits": self.strip_backspace_edits_var.get(),
            "fake_backspace_enabled": self.fake_backspace_enabled_var.get(),
            "fake_backspaces_per_minute": self.fake_backspaces_per_minute_var.get(),
        }

    def set_settings(self, settings):
        self.font_family_var.set(settings.get("font_family", "Arial"))
        self.font_size_var.set(settings.get("font_size", 30))
        self.bold_var.set(settings.get("bold", True))
        self.margin_var.set(settings.get("margin", 20))
        self.show_caret_var.set(settings.get("show_caret", True))
        self.uniform_typing_var.set(settings.get("uniform_typing", False))
        self.chars_per_sec_var.set(settings.get("chars_per_sec", 10.0))
        self.video_speed_var.set(settings.get("video_speed", 1.0))
        self.word_speed_var.set(settings.get("word_speed", 0.15))
        self.space_duration_var.set(settings.get("space_duration", 0.25))
        self.save_video_var.set(settings.get("save_video", True))
        self.moving_window_var.set(settings.get("moving_window", False))
        self.window_size_var.set(settings.get("window_size", 10))
        self.window_wordonly_var.set(settings.get("window_wordonly", False))
        self.mask_narrow_var.set(settings.get("mask_narrow", settings.get("mask_character", "_")))
        self.mask_wide_var.set(settings.get("mask_wide", "#"))
        self.enable_timing_var.set(settings.get("enable_timing", False))
        self.start_time_var.set(settings.get("start_time", 0))
        self.end_time_var.set(settings.get("end_time", 0))
        self.duration_percent_var.set(settings.get("duration_percent", 100.0))
        self.timing_mode_var.set(settings.get("timing_mode", "absolute"))
        self.word_boundary_trim_var.set(settings.get("word_boundary_trim", False))
        self.sentence_boundary_trim_var.set(settings.get("sentence_boundary_trim", False))
        self.strip_backspace_edits_var.set(settings.get("strip_backspace_edits", False))
        self.fake_backspace_enabled_var.set(settings.get("fake_backspace_enabled", False))
        self.fake_backspaces_per_minute_var.set(settings.get("fake_backspaces_per_minute", 2.0))
        self.update_window_controls()
        self.update_timing_controls()
        self.update_uniform_typing_controls()

    def save_settings(self):
        settings = self.get_settings()
        try:
            with open(_SETTINGS_JSON, 'w') as f:
                json.dump(settings, f, indent=2)
            messagebox.showinfo("Settings Saved", f"Settings saved to {_SETTINGS_JSON}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save settings: {e}")

    def load_settings(self):
        try:
            if os.path.exists(_SETTINGS_JSON):
                with open(_SETTINGS_JSON, 'r') as f:
                    self.set_settings(json.load(f))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load settings: {e}")

    def export_settings_to_csv(self, settings, output_path):
        """Export settings to a CSV file alongside the video output."""
        try:
            base = os.path.splitext(output_path)[0]
            csv_path = base + "_settings.csv"
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["setting", "value"])
                for k, v in settings.items():
                    writer.writerow([k, v])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export settings CSV: {e}")

    def load_settings_from_csv(self):
        """Load settings from a CSV file (exported by this program)."""
        path = filedialog.askopenfilename(
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Select settings CSV file"
        )
        if not path:
            return
        try:
            settings = {}
            with open(path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    k = row.get("setting")
                    v = row.get("value")
                    if k is not None and v is not None:
                        if v == "True":
                            settings[k] = True
                        elif v == "False":
                            settings[k] = False
                        else:
                            try:
                                if '.' in v:
                                    settings[k] = float(v)
                                else:
                                    settings[k] = int(v)
                            except ValueError:
                                settings[k] = v
            self.set_settings(settings)
            messagebox.showinfo("Settings Loaded", f"Settings loaded from {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load settings from CSV: {e}")

    def update_uniform_typing_controls(self):
        """Enable/disable uniform typing options based on checkbox state"""
        state = "normal" if self.uniform_typing_var.get() else "disabled"
        self.chars_per_sec_entry.config(state=state)
        self.video_speed_entry.config(state=state)
        self.word_speed_entry.config(state=state)
        self.space_duration_entry.config(state=state)

    def update_window_controls(self):
        if self.moving_window_var.get():
            self.window_size_entry.config(state="normal")
            self.window_wordonly_check.config(state="normal")
            self.mask_narrow_entry.config(state="normal")
            self.mask_wide_entry.config(state="normal")
        else:
            self.window_size_entry.config(state="disabled")
            self.window_wordonly_check.config(state="disabled")
            self.mask_narrow_entry.config(state="disabled")
            self.mask_wide_entry.config(state="disabled")

    def update_timing_controls(self):
        """Update timing controls based on checkbox state"""
        state = "normal" if self.enable_timing_var.get() else "disabled"
        self.start_time_entry.config(state=state)
        self.end_time_entry.config(state=state)
        self.duration_percent_entry.config(state=state)
        self.word_boundary_trim_check.config(state=state)
        self.sentence_boundary_trim_check.config(state=state)
        self.update_timing_mode()

    def update_timing_mode(self):
        """Update timing mode controls based on radio button selection"""
        if not self.enable_timing_var.get():
            return
            
        if self.timing_mode_var.get() == "absolute":
            self.duration_percent_entry.config(state="disabled")
            self.start_time_entry.config(state="normal")
            self.end_time_entry.config(state="normal")
        else:  # percentage mode
            self.start_time_entry.config(state="normal")
            self.end_time_entry.config(state="disabled")
            self.duration_percent_entry.config(state="normal")

if __name__ == "__main__":
    root = tk.Tk()
    app = XMLToVideoApp(root)
    root.mainloop() 