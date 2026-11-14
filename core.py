# -*- coding: utf-8 -*-
"""
core.py - 按键输入自动化工具核心引擎
包含: 键鼠输入序列化、录制器、回放器、自定义序列执行器
依赖: pynput
"""
import json
import time
import random
import ctypes
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
    """
    在后台线程按原始时间轴复现录制事件, 支持倍速与急停。

    v1.4: 增加事件级日志(log_path)与线程级异常兜底——
    复现"提前停止"时打开 data/replay.log 即可看到执行到了第几个事件、
    是否被停止指令打断、线程是否抛了异常, 不再静默死亡。
    """

    log_path = None  # 由 main.py 注入: data/replay.log
    _log_lock = threading.Lock()

    def _log_reset(self):
        if Replayer.log_path:
            try:
                Path(Replayer.log_path).write_text(
                    "KeyAuto 回放日志 %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"),
                    encoding="utf-8")
            except Exception:
                pass

    def _log(self, text):
        if Replayer.log_path:
            try:
                with Replayer._log_lock:
                    with open(Replayer.log_path, "a", encoding="utf-8") as f:
                        f.write(text + "\n")
            except Exception:
                pass

    def __init__(self, events, speed=1.0, on_done=None, max_gap_ms=None):
        """
        max_gap_ms: 压缩长等待——录制中超过该值的空闲间隔被压缩为该值,
        避免录制时的发呆时间在回放时表现为"卡住/暂停"。None 表示不压缩。
        """
        super().__init__(daemon=True)
        self._compressed_ms = 0
        if max_gap_ms:
            orig_total = max((e["t"] for e in events), default=0)
            events = self.compress_timeline(events, max_gap_ms)
            self._compressed_ms = orig_total - (events[-1]["t"] if events else 0)
        self.events = sorted(events, key=lambda e: e["t"])
        self.speed = max(0.1, speed)
        self.on_done = on_done
        self._stop = threading.Event()
        self.total_ms = self.events[-1]["t"] if self.events else 0
        self.elapsed_ms = 0

    @staticmethod
    def compress_timeline(events, max_gap_ms):
        """把超过 max_gap_ms 的事件间隔压缩为 max_gap_ms, 返回新列表(不改原数据)。"""
        out, shift, prev = [], 0.0, 0.0
        for ev in events:
            t = float(ev["t"])
            if t - prev > max_gap_ms:
                shift += (t - prev) - max_gap_ms
            prev = t
            out.append(dict(ev, t=round(t - shift, 1)))
        return out

    def run(self):
        t0 = time.perf_counter()
        self._log_reset()
        self._log("开始回放: %d 个事件, 总时长 %.0f ms, 倍速 %.1fx%s"
                  % (len(self.events), self.total_ms, self.speed,
                     (", 已压缩长等待 %.1f 秒" % (self._compressed_ms / 1000.0)) if self._compressed_ms else ""))
        interrupted = True
        try:
            for i, ev in enumerate(self.events):
                target = ev["t"] / 1000.0 / self.speed
                while True:
                    if self._stop.is_set():
                        self._log("在第 %d/%d 个事件前收到停止指令" % (i + 1, len(self.events)))
                        return
                    remain = target - (time.perf_counter() - t0)
                    if remain <= 0:
                        break
                    time.sleep(min(remain, 0.02))
                if ev["type"] != "mouse_move":
                    self._log("执行 %d/%d  t=%6.0fms  %s"
                              % (i + 1, len(self.events), ev["t"], ev["type"]))
                self.elapsed_ms = ev["t"]
                self._dispatch(ev)
            interrupted = False
            self._log("事件列表全部执行完毕, 回放正常结束")
        except Exception:
            import traceback
            self._log("回放线程异常(已记录, 线程不再静默死亡):\n" + traceback.format_exc())
        finally:
            if self.on_done:
                self.on_done(interrupted or self._stop.is_set())

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
                btn_name = it.get("button", "left")
                if btn_name == "double":
                    # 双击动作: 两次点击位置严格一致, 由系统判定为双击。
                    # (用两个单击动作拼双击时, 位置差超过 4 像素系统就不认)
                    _mouse.click(Button.left, 2)
                else:
                    btn = MOUSE_BUTTONS.get(btn_name, Button.left)
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
    """执行一次点击或按键. input_type: 'mouse' | 'key'; mouse_button 可为 'double'"""
    try:
        if input_type == "mouse":
            if fixed_pos:
                _mouse.position = (int(fixed_pos[0]), int(fixed_pos[1]))
            if mouse_button == "double":
                _mouse.click(Button.left, 2)
            else:
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


# ---------- Windows 前台窗口工具 ----------
# 模拟键鼠输入(SendInput)只发给"前台窗口"; 全屏游戏只有处于前台时才响应输入。
# 任务启动前若焦点被悬浮窗/其他程序抢走, 需要把焦点还给目标程序。
def _user32():
    return ctypes.windll.user32


def get_foreground_hwnd():
    """当前前台窗口句柄, 失败返回 0"""
    try:
        return int(_user32().GetForegroundWindow())
    except Exception:
        return 0


def hwnd_pid(hwnd):
    """窗口所属进程 PID"""
    try:
        pid = ctypes.c_ulong()
        _user32().GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def set_foreground_hwnd(hwnd):
    """把窗口带回前台(最小化时先恢复)。"""
    try:
        u = _user32()
        if hwnd:
            if u.IsIconic(int(hwnd)):
                u.ShowWindow(int(hwnd), 9)  # SW_RESTORE
            u.SetForegroundWindow(int(hwnd))
            return True
    except Exception:
        pass
    return False


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
