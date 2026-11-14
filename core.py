# -*- coding: utf-8 -*-
"""
core.py - 按键输入自动化工具核心引擎
包含: 键鼠输入序列化、录制器、回放器、自定义序列执行器
依赖: pynput
"""
import json
import time
import random
import threading
from pathlib import Path

from pynput import mouse, keyboard
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button

MOUSE_BUTTONS = {"left": Button.left, "right": Button.right, "middle": Button.middle}

_kb = keyboard.Controller()
_mouse = mouse.Controller()


# ---------- 键值序列化 / 反序列化 ----------
def serialize_key(k):
    """把 pynput 按键对象转为可 JSON 化的字典"""
    if isinstance(k, Key):
        return {"k": "key", "v": k.name}
    if isinstance(k, KeyCode):
        if k.char:
            return {"k": "char", "v": k.char}
        if k.vk is not None:
            return {"k": "vk", "v": k.vk}
    return None


def deserialize_key(d):
    """字典还原为 pynput 按键对象"""
    try:
        if d["k"] == "key":
            return Key[d["v"]]
        if d["k"] == "char":
            return KeyCode.from_char(d["v"])
        if d["k"] == "vk":
            return KeyCode.from_vk(d["v"])
    except Exception:
        return None
    return None


def key_display_name(d):
    """按键的友好显示名"""
    if not d:
        return "(未设置)"
    if d["k"] == "key":
        return d["v"]
    if d["k"] == "char":
        return d["v"]
    return "vk:%s" % d["v"]


# ---------- 录制器 ----------
class Recorder:
    """
    录制用户的键盘与鼠标输入。
    事件按毫秒级相对时间轴记录:
      {"t": 毫秒, "type": "key_down|key_up|mouse_down|mouse_up|mouse_move|scroll",
       "key": {...} 或 "button": "left", "x": int, "y": int}
    """

    def __init__(self, move_sample_ms=15, ignore_names=None):
        """
        ignore_names: 热键名集合(如 {"f6","f7"}), 这些键的事件不进入录制,
        避免录制的开始/结束热键本身被录进事件流。
        """
        self.events = []
        self._t0 = 0.0
        self._recording = False
        self._last_move_t = 0.0
        self._move_sample_ms = move_sample_ms
        self._ignore = set(ignore_names or ())
        self._kb_listener = None
        self._mouse_listener = None
        self._lock = threading.Lock()

    def _ignored(self, key):
        name = getattr(key, "name", None)
        return name is not None and name in self._ignore

    @property
    def recording(self):
        return self._recording

    def start(self):
        with self._lock:
            self.events = []
            self._t0 = time.perf_counter()
            self._recording = True

        def rel_t():
            return round((time.perf_counter() - self._t0) * 1000, 1)

        def on_press(key):
            if self._ignored(key):
                return
            s = serialize_key(key)
            if s and self._recording:
                with self._lock:
                    self.events.append({"t": rel_t(), "type": "key_down", "key": s})

        def on_release(key):
            if self._ignored(key):
                return
            s = serialize_key(key)
            if s and self._recording:
                with self._lock:
                    self.events.append({"t": rel_t(), "type": "key_up", "key": s})

        def on_move(x, y):
            now = time.perf_counter()
            if not self._recording:
                return
            if (now - self._last_move_t) * 1000 >= self._move_sample_ms:
                self._last_move_t = now
                with self._lock:
                    self.events.append({"t": rel_t(), "type": "mouse_move", "x": x, "y": y})

        def on_click(x, y, button, pressed):
            if not self._recording:
                return
            name = button.name if button.name in MOUSE_BUTTONS else "left"
            with self._lock:
                self.events.append({
                    "t": rel_t(),
                    "type": "mouse_down" if pressed else "mouse_up",
                    "button": name, "x": x, "y": y,
                })

        def on_scroll(x, y, dx, dy):
            if not self._recording:
                return
            with self._lock:
                self.events.append({"t": rel_t(), "type": "scroll", "x": x, "y": y, "dx": dx, "dy": dy})

        self._kb_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
        self._kb_listener.start()
        self._mouse_listener.start()

    def stop(self):
        self._recording = False
        for ln in (self._kb_listener, self._mouse_listener):
            try:
                if ln:
                    ln.stop()
            except Exception:
                pass
        self._kb_listener = self._mouse_listener = None
        return self.events

    def summary(self):
        kb = sum(1 for e in self.events if e["type"].startswith("key"))
        ms = sum(1 for e in self.events if e["type"].startswith("mouse") or e["type"] == "scroll")
        dur = self.events[-1]["t"] if self.events else 0
        return {"duration_ms": round(dur), "key_events": kb, "mouse_events": ms, "total": len(self.events)}

    def save(self, path, name="未命名"):
        data = {
            "name": name,
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_ms": self.summary()["duration_ms"],
            "move_sample_ms": self._move_sample_ms,
            "events": self.events,
        }
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return data


# ---------- 回放器 ----------
class Replayer(threading.Thread):
    """在后台线程按原始时间轴复现录制事件, 支持倍速与急停"""

    def __init__(self, events, speed=1.0, on_done=None):
        super().__init__(daemon=True)
        self.events = sorted(events, key=lambda e: e["t"])
        self.speed = max(0.1, speed)
        self.on_done = on_done
        self._stop = threading.Event()
        self.total_ms = self.events[-1]["t"] if self.events else 0
        self.elapsed_ms = 0

    def run(self):
        t0 = time.perf_counter()
        try:
            for ev in self.events:
                target = ev["t"] / 1000.0 / self.speed
                while True:
                    if self._stop.is_set():
                        return
                    remain = target - (time.perf_counter() - t0)
                    if remain <= 0:
                        break
                    time.sleep(min(remain, 0.02))
                self.elapsed_ms = ev["t"]
                self._dispatch(ev)
        finally:
            if self.on_done:
                self.on_done(self._stop.is_set())

    def _dispatch(self, ev):
        et = ev["type"]
        try:
            if et == "key_down":
                k = deserialize_key(ev["key"])
                if k:
                    _kb.press(k)
            elif et == "key_up":
                k = deserialize_key(ev["key"])
                if k:
                    _kb.release(k)
            elif et == "mouse_move":
                _mouse.position = (ev["x"], ev["y"])
            elif et == "mouse_down":
                _mouse.position = (ev["x"], ev["y"])
                _mouse.press(MOUSE_BUTTONS.get(ev["button"], Button.left))
            elif et == "mouse_up":
                _mouse.position = (ev["x"], ev["y"])
                _mouse.release(MOUSE_BUTTONS.get(ev["button"], Button.left))
            elif et == "scroll":
                _mouse.position = (ev["x"], ev["y"])
                _mouse.scroll(ev.get("dx", 0), ev.get("dy", 0))
        except Exception:
            pass

    def stop(self):
        self._stop.set()

    @property
    def stopped(self):
        return self._stop.is_set()


# ---------- 连点线程 ----------
class ClickerThread(threading.Thread):
    """
    独立后台连点线程(替代 GUI 线程里的 QTimer):
    - 不依赖 Qt 事件循环, 界面失焦/弹窗/菜单/系统节流时点击不中断
    - fire: 执行一次输入的回调; interval_fn: 返回下一次间隔(秒)
    """

    def __init__(self, fire, interval_fn, on_done=None):
        super().__init__(daemon=True)
        self.fire = fire
        self.interval_fn = interval_fn
        self.on_done = on_done
        self._stop = threading.Event()

    def run(self):
        try:
            while not self._stop.is_set():
                interval = max(0.005, float(self.interval_fn()))
                end = time.perf_counter() + interval
                while time.perf_counter() < end:
                    if self._stop.is_set():
                        return
                    time.sleep(min(0.005, max(0.0, end - time.perf_counter())))
                if self._stop.is_set():
                    return
                self.fire()
        finally:
            if self.on_done:
                self.on_done()

    def stop(self):
        self._stop.set()

    @property
    def stopped(self):
        return self._stop.is_set()


# ---------- 自定义序列执行器 ----------
class SequencePlayer(threading.Thread):
    """
    执行用户编排的输入序列。
    序列条目格式:
      {"type": "mouse", "button": "left", "x": 100, "y": 200, "duration": 0.05, "delay": 0.1}
      {"type": "key", "key": {...}, "duration": 0.05, "delay": 0}
    """

    def __init__(self, items, loops=1, on_done=None):
        super().__init__(daemon=True)
        self.items = items
        self.loops = loops  # 0 表示无限循环
        self.on_done = on_done
        self._stop = threading.Event()

    def run(self):
        try:
            n = 0
            while not self._stop.is_set() and (self.loops == 0 or n < self.loops):
                for it in self.items:
                    if self._stop.is_set():
                        return
                    self._exec_item(it)
                n += 1
        finally:
            if self.on_done:
                self.on_done(self._stop.is_set())

    def _exec_item(self, it):
        duration = max(0.0, float(it.get("duration", 0.05)))
        delay = max(0.0, float(it.get("delay", 0.0)))
        try:
            if it["type"] == "mouse":
                _mouse.position = (int(it.get("x", 0)), int(it.get("y", 0)))
                btn = MOUSE_BUTTONS.get(it.get("button", "left"), Button.left)
                _mouse.press(btn)
                self._sleep(duration)
                _mouse.release(btn)
            elif it["type"] == "key":
                k = deserialize_key(it.get("key"))
                if k:
                    _kb.press(k)
                    self._sleep(duration)
                    _kb.release(k)
        except Exception:
            pass
        self._sleep(delay)

    def _sleep(self, seconds):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            if self._stop.is_set():
                return
            time.sleep(min(0.01, max(0, end - time.perf_counter())))

    def stop(self):
        self._stop.set()

    @property
    def stopped(self):
        return self._stop.is_set()


# ---------- 单次输入执行(供连点器调用) ----------
def perform_input(input_type, key_dict=None, mouse_button="left", fixed_pos=None):
    """执行一次点击或按键. input_type: 'mouse' | 'key'"""
    try:
        if input_type == "mouse":
            if fixed_pos:
                _mouse.position = (int(fixed_pos[0]), int(fixed_pos[1]))
            btn = MOUSE_BUTTONS.get(mouse_button, Button.left)
            _mouse.press(btn)
            _mouse.release(btn)
        else:
            k = deserialize_key(key_dict)
            if k:
                _kb.press(k)
                _kb.release(k)
    except Exception:
        pass


def random_interval_ms(lo, hi, jitter=0.12):
    """随机模式: 每秒次数在 [lo,hi] 随机, 换算成间隔并加抖动"""
    rate = random.randint(int(lo), int(hi))
    base = 1000.0 / max(1, rate)
    return base * random.uniform(1 - jitter, 1 + jitter)


# ---------- JSON 读写 ----------
def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
