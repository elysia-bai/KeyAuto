# -*- coding: utf-8 -*-
"""
main.py - 按键输入自动化工具 (KeyAuto)  v1.6
交互形态: 主界面选择功能 -> 主界面隐藏 -> 悬浮窗设置并运行
悬浮窗右键 -> 返回主界面 / 全局设置 / 急停 / 保存配置 / 退出

v1.6 变更(全屏程序/游戏支持):
  1. 焦点自动归还: 模拟键鼠只发给"前台窗口", 全屏游戏失焦时会忽略全部输入。
     程序持续追踪上一个前台窗口, 回放/序列/连点启动后自动把焦点还给它
  2. 新增全局热键: 回放启停(默认 F10)、序列启停(默认 F12)——
     全屏游戏下不用点悬浮窗按钮, 焦点保持在游戏上直接热键启停
  3. 旧设置文件自动补全新热键默认值

v1.5: 鼠标双击动作; 回放压缩长等待; 悬浮窗全局设置入口; 热键提示周期刷新
v1.4: 键盘动作属性去XY; 鼠标左/右/中键; DPI物理坐标; 关编辑器不退出程序;
      悬浮窗鼠标穿透; replay.log 诊断日志
v1.3: 连点仅跟随鼠标; 捕获框确定按钮; 鼠标点选XY; 热键提示刷新; 连点后台线程
v1.2: 编辑器非模态化; 序列圆圈模式; 录制热键过滤
v1.1: 全局浅色主题; 悬浮窗标题栏拖动; 高度自适应; 热键文字同步
"""
import sys
import os
import time
import json
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QPoint, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QStackedWidget, QComboBox, QSpinBox, QDoubleSpinBox,
    QSlider, QRadioButton, QButtonGroup, QLineEdit, QMessageBox, QMenu, QScrollArea,
    QFileDialog, QCheckBox, QGroupBox, QFrame,
)

import core
from core import (Recorder, Replayer, SequencePlayer, perform_input,
                  random_interval_ms, ClickerThread)

# 应用目录: 打包成 onefile exe 后 __file__ 指向临时解压目录, 数据须落在 exe 旁边
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
RECORD_DIR = DATA_DIR / "recordings"
RECORD_DIR.mkdir(exist_ok=True)
SEQ_DIR = DATA_DIR / "sequences"
SEQ_DIR.mkdir(exist_ok=True)
CONFIG_FILE = DATA_DIR / "settings.json"
# 回放诊断日志: 复现"提前停止"时打开 data/replay.log 查看执行到哪个事件
core.Replayer.log_path = str(DATA_DIR / "replay.log")

try:
    import winsound

    def beep(kind):
        try:
            if kind == "start":
                winsound.Beep(1000, 150)
            elif kind == "end":
                winsound.Beep(600, 200)
            elif kind == "error":
                winsound.Beep(300, 250)
        except Exception:
            pass
except ImportError:
    def beep(kind):
        pass


DEFAULT_SETTINGS = {
    "rate_cap": 10,
    "opacity": 0.92,
    "sound": True,
    "hotkeys": {"record": "f6", "stop_record": "f7", "click": "f8", "panic": "f9",
                "play": "f10", "seq": "f12"},
}

MOUSE_LABELS = [("left", "鼠标左键"), ("right", "鼠标右键"),
                ("middle", "鼠标中键"), ("double", "鼠标双击")]
MOUSE_SHORT = {"left": "左键", "right": "右键", "middle": "中键", "double": "双击"}

# ---------- 全局浅色主题: 显式指定前景/背景色, 不依赖系统深色模式 ----------
GLOBAL_QSS = """
* { font-size: 12px; color: #263238; }
QMainWindow, QDialog, QWidget#plainBg { background: #f5f6f8; }
QPushButton {
    background: #ffffff; color: #263238;
    border: 1px solid #b0bec5; border-radius: 4px; padding: 5px 10px;
}
QPushButton:hover { background: #e3f2fd; border-color: #64b5f6; }
QPushButton:pressed { background: #bbdefb; }
QComboBox, QSpinBox, QLineEdit, QDoubleSpinBox {
    background: #ffffff; color: #263238;
    border: 1px solid #b0bec5; border-radius: 4px; padding: 3px 5px;
}
QComboBox QAbstractItemView {
    background: #ffffff; color: #263238;
    selection-background-color: #bbdefb; selection-color: #263238;
}
QGroupBox {
    border: 1px solid #cfd8dc; border-radius: 6px; margin-top: 10px;
    color: #37474f; font-weight: bold;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; color: #37474f; }
QScrollArea { border: 1px solid #cfd8dc; border-radius: 6px; background: #ffffff; }
QMenu { background: #ffffff; color: #263238; border: 1px solid #b0bec5; }
QMenu::item { padding: 5px 24px; }
QMenu::item:selected { background: #e3f2fd; }
QLabel { background: transparent; color: #263238; }
QCheckBox, QRadioButton { color: #263238; }
QSlider::groove:horizontal { height: 4px; background: #cfd8dc; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -6px 0; background: #42a5f5; border-radius: 7px; }
"""


def load_settings():
    s = core.load_json(CONFIG_FILE, DEFAULT_SETTINGS)
    for k, v in DEFAULT_SETTINGS.items():
        s.setdefault(k, v)
    # 旧设置文件缺少新增热键(play/seq)时补默认值
    if isinstance(s.get("hotkeys"), dict):
        for k2, v2 in DEFAULT_SETTINGS["hotkeys"].items():
            s["hotkeys"].setdefault(k2, v2)
    return s


def save_settings(s):
    core.save_json(CONFIG_FILE, s)


def hk(ctx, action, default):
    """读取当前设置中的热键名并大写显示, 如 F8"""
    return ctx.settings.get("hotkeys", {}).get(action, default).upper()


# ============================================================
# 全局热键 (pynput 后台监听 -> Qt 信号)
# ============================================================
class GlobalHotkeys(QWidget):
    fired = Signal(str)

    def __init__(self):
        super().__init__()
        self._listener = None

    def start(self, hotkey_map):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass

        def on_press(key):
            name = getattr(key, "name", None)
            if not name:
                return
            for action, hkv in hotkey_map.items():
                if name == hkv:
                    self.fired.emit(action)
                    break

        self._listener = __import__("pynput").keyboard.Listener(on_press=on_press)
        self._listener.start()

    def stop(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass


# ============================================================
# 按键捕获对话框
# ============================================================
class CaptureKeyDialog(QDialog):
    """
    按下按键 -> 显示按键名 -> 点击"确定"完成绑定(可反复按其他键改选)。
    pynput 监听回调在后台线程, 通过 Qt 信号(队列投递)回到 GUI 线程更新界面;
    之前在后台线程直接调 QTimer.singleShot(self.accept) 不会触发, 导致对话框卡死。
    """
    keyCaptured = Signal(object)  # 键数据字典; None 表示按了 Esc 取消

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("捕获按键")
        self.setFixedWidth(320)
        self.captured = None
        self._listener = None
        lay = QVBoxLayout(self)
        tip = QLabel("请按下要录入的按键，然后点击\"确定\"（Esc 取消）")
        tip.setWordWrap(True)
        self.show_label = QLabel("等待输入…")
        self.show_label.setAlignment(Qt.AlignCenter)
        self.show_label.setMinimumHeight(60)
        self.show_label.setStyleSheet(
            "border:1px solid #b0bec5; border-radius:6px; font-size:16px; font-weight:bold; color:#1565c0; background:#ffffff;")
        lay.addWidget(tip)
        lay.addWidget(self.show_label)

        row = QHBoxLayout()
        row.addStretch(1)
        self.ok_btn = QPushButton("确定")
        self.ok_btn.setEnabled(False)
        self.ok_btn.setDefault(True)
        self.ok_btn.setMinimumHeight(32)
        self.ok_btn.setStyleSheet(
            "QPushButton{background:#2e7d32;color:#ffffff;border:none;border-radius:5px;font-weight:bold;}"
            "QPushButton:disabled{background:#b0bec5;}")
        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumHeight(32)
        row.addWidget(self.ok_btn)
        row.addWidget(cancel_btn)
        lay.addLayout(row)
        self.ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

        self.keyCaptured.connect(self._on_key)
        self._start_capture()

    def _start_capture(self):
        from pynput import keyboard as pkb

        def on_press(key):
            if key == pkb.Key.esc:
                self.keyCaptured.emit(None)
                return False
            s = core.serialize_key(key)
            if s:
                self.keyCaptured.emit(s)
            return True  # 继续监听, 允许改按其他键

        self._listener = pkb.Listener(on_press=on_press)
        self._listener.start()

    def _on_key(self, data):
        # GUI 线程槽: 信号已由 Qt 自动队列投递
        if data is None:
            self.reject()
            return
        self.captured = data
        self.show_label.setText(core.key_display_name(data))
        self.ok_btn.setEnabled(True)
        self.ok_btn.setFocus()

    def done(self, r):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        super().done(r)


# ============================================================
# 屏幕拾取覆盖层 (供连点面板"拾取坐标"使用)
# ============================================================
class CoordinateOverlay(QWidget):
    picked = Signal(int, int, str, object)

    def __init__(self, allow_keys=True, mouse_button="left", one_shot=False, on_closed=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        self._cur = QCursor.pos()
        self._mouse_button = mouse_button
        self._allow_keys = allow_keys
        self._one_shot = one_shot
        self._on_closed = on_closed
        self._kb_listener = None
        self._closed = False

        tip_text = "移动鼠标定位圆圈 · 左键点击确认坐标 · 右键或 Esc 结束 (坐标为屏幕物理像素)"
        self.tip = QLabel(tip_text, self)
        self.tip.setStyleSheet(
            "background: rgba(38,50,56,230); color: #ffffff; padding: 8px 16px;"
            "border-radius: 6px; font-size: 13px; font-weight: bold;")
        self.tip.adjustSize()
        self.tip.move(20, 20)
        self.coord = QLabel(self)
        self.coord.setStyleSheet(
            "background: rgba(21,101,192,230); color: #ffffff; padding: 6px 12px;"
            "border-radius: 6px; font-size: 14px; font-weight: bold;")
        self._update_coord_label()
        self.showFullScreen()

    def _to_physical(self, logical_pt):
        """Qt 逻辑坐标 -> 屏幕物理像素(与 pynput 录制/回放坐标系一致)。
        系统显示缩放非 100% 时两套坐标系不一致——这正是此前"点选的 XY 与
        实际点击位置大幅偏移"的原因(偏差量 = 缩放比例)。"""
        dpr = self.devicePixelRatioF() or 1.0
        return round(logical_pt.x() * dpr), round(logical_pt.y() * dpr)

    def _update_coord_label(self):
        px, py = self._to_physical(self._cur)
        self.coord.setText("X: %d   Y: %d" % (px, py))
        self.coord.adjustSize()
        lx = min(self._cur.x() + 24, self.width() - self.coord.width() - 8)
        ly = min(self._cur.y() + 24, self.height() - self.coord.height() - 8)
        self.coord.move(int(lx), int(ly))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(10, 20, 30, 40))
        cx, cy = int(self._cur.x()), int(self._cur.y())
        p.setPen(QPen(QColor(229, 57, 53, 180), 1))
        p.drawLine(0, cy, self.width(), cy)
        p.drawLine(cx, 0, cx, self.height())
        p.setPen(QPen(QColor(229, 57, 53), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(cx - 16, cy - 16, 32, 32))
        p.end()

    def mouseMoveEvent(self, e):
        self._cur = e.globalPosition().toPoint()
        self._update_coord_label()
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            px, py = self._to_physical(e.globalPosition())
            self.picked.emit(px, py, "mouse", self._mouse_button)
            if self._one_shot:
                self._close()
        elif e.button() == Qt.RightButton:
            self._close()

    def _close(self):
        if self._closed:
            return
        self._closed = True
        if self._kb_listener:
            try:
                self._kb_listener.stop()
            except Exception:
                pass
        self.close()
        if self._on_closed:
            self._on_closed()

    def closeEvent(self, e):
        self._closed = True
        if self._on_closed:
            self._on_closed()
        e.accept()


# ============================================================
# 连点面板 (定频 / 随机 共用) — 只支持跟随鼠标, 后台线程点击
# ============================================================
class ClickPanel(QWidget):
    def __init__(self, app_ctx, random_mode=False):
        super().__init__()
        self.ctx = app_ctx
        self.random_mode = random_mode
        self.clicker = None  # ClickerThread
        self.running = False
        self.key_dict = None

        lay = QGridLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setHorizontalSpacing(10)
        lay.setVerticalSpacing(8)
        row = 0

        lay.addWidget(QLabel("输入类型:"), row, 0)
        self.type_combo = QComboBox()
        for _, label in MOUSE_LABELS:
            self.type_combo.addItem(label)
        self.type_combo.addItem("键盘按键")
        self.type_combo.currentIndexChanged.connect(self._type_changed)
        lay.addWidget(self.type_combo, row, 1)
        row += 1

        self.key_btn = QPushButton("设置按键…")
        self.key_btn.clicked.connect(self._capture_key)
        self.key_btn.setVisible(False)
        lay.addWidget(self.key_btn, row, 1)
        self.key_label = QLabel("")
        self.key_label.setVisible(False)
        lay.addWidget(self.key_label, row, 0)
        row += 1

        if random_mode:
            lay.addWidget(QLabel("每秒下限:"), row, 0)
            self.rate_lo = QSpinBox()
            self.rate_lo.setRange(1, 50)
            self.rate_lo.setValue(3)
            lay.addWidget(self.rate_lo, row, 1)
            row += 1
            lay.addWidget(QLabel("每秒上限:"), row, 0)
            self.rate_hi = QSpinBox()
            self.rate_hi.setRange(1, 50)
            self.rate_hi.setValue(8)
            lay.addWidget(self.rate_hi, row, 1)
            row += 1
        else:
            lay.addWidget(QLabel("每秒次数:"), row, 0)
            self.rate = QSpinBox()
            self.rate.setRange(1, 50)
            self.rate.setValue(5)
            lay.addWidget(self.rate, row, 1)
            row += 1

        mode_label = QLabel("点击位置: 跟随当前鼠标")
        mode_label.setStyleSheet("color:#607d8b;")
        lay.addWidget(mode_label, row, 0, 1, 2)
        row += 1

        self.toggle_btn = QPushButton("启动 (F8)")
        self.toggle_btn.setMinimumHeight(36)
        self.toggle_btn.setStyleSheet(
            "QPushButton{background:#2e7d32;color:#ffffff;border:none;border-radius:6px;font-weight:bold;font-size:13px;}"
            "QPushButton:checked{background:#c62828;}")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.clicked.connect(self.toggle)
        lay.addWidget(self.toggle_btn, row, 0, 1, 2)

        self.apply_settings()

    def _type_changed(self, idx):
        is_key = idx >= len(MOUSE_LABELS)
        self.key_btn.setVisible(is_key)
        self.key_label.setVisible(is_key)

    def _capture_key(self):
        dlg = CaptureKeyDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.captured:
            self.key_dict = dlg.captured
            self.key_label.setText("按键: %s" % core.key_display_name(self.key_dict))

    def apply_settings(self):
        cap = int(self.ctx.settings.get("rate_cap", 10))
        for name in ("rate", "rate_lo", "rate_hi"):
            w = getattr(self, name, None)
            if w is not None:
                w.setMaximum(max(1, cap))
                w.setValue(min(w.value(), cap))
        self.refresh_hotkey_text()

    def refresh_hotkey_text(self):
        """按当前设置刷新按钮上的热键提示文字(供周期刷新调用)"""
        self.toggle_btn.setText(("停止 (%s)" if self.running else "启动 (%s)")
                                % hk(self.ctx, "click", "F8"))

    def toggle(self, checked=None):
        if checked is None:
            checked = not self.running
        if checked:
            self.start()
        else:
            self.stop()

    def start(self):
        if self.type_combo.currentIndex() >= len(MOUSE_LABELS) and not self.key_dict:
            QMessageBox.warning(self, "提示", "请先设置要连发的键盘按键")
            self.toggle_btn.setChecked(False)
            return
        if self.random_mode and self.rate_lo.value() > self.rate_hi.value():
            QMessageBox.warning(self, "提示", "随机下限不能大于上限")
            self.toggle_btn.setChecked(False)
            return
        self.running = True
        self.toggle_btn.setChecked(True)
        self.toggle_btn.setText("停止 (%s)" % hk(self.ctx, "click", "F8"))

        # 参数快照: 后台线程不跨线程读 Qt 控件
        idx = self.type_combo.currentIndex()
        mouse_button = MOUSE_LABELS[idx][0] if idx < len(MOUSE_LABELS) else None
        key_dict = self.key_dict
        if self.random_mode:
            lo, hi = self.rate_lo.value(), self.rate_hi.value()
            interval_fn = lambda: random_interval_ms(lo, hi) / 1000.0
        else:
            rate = self.rate.value()
            interval_fn = lambda: 1.0 / max(1, rate)

        def fire():
            if mouse_button:
                perform_input("mouse", mouse_button=mouse_button)
            else:
                perform_input("key", key_dict=key_dict)

        self.clicker = ClickerThread(fire, interval_fn)
        self.clicker.start()
        # 连点期间悬浮窗鼠标穿透, 防止点击打在悬浮窗上
        self.ctx.floating.set_input_pass_through(True)
        # 把键盘焦点还给上一个前台程序(全屏游戏失焦时会忽略全部模拟输入)
        self.ctx.restore_foreground()
        self.ctx.notify("连点任务已启动(跟随鼠标)")

    def stop(self):
        self.running = False
        if self.clicker:
            self.clicker.stop()
            self.clicker = None
        self.ctx.floating.set_input_pass_through(False)
        self.toggle_btn.setChecked(False)
        self.toggle_btn.setText("启动 (%s)" % hk(self.ctx, "click", "F8"))


# ============================================================
# 序列动作圆圈 (编辑器中的可视单元)
# ============================================================
class StepCircle(QPushButton):
    """56px 圆形按钮: 空白圆=添加, 键盘圆=蓝, 鼠标圆=青, 选中=红圈"""

    SIZE = 56

    def __init__(self, text="", mode="blank", index=-1, editor=None):
        super().__init__(text)
        self.mode = mode
        self.index = index
        self.editor = editor
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.apply_style(selected=False)

    def apply_style(self, selected):
        border = "border: 3px solid #e53935;" if selected else "border: none;"
        if self.mode == "blank":
            self.setStyleSheet(
                "QPushButton{background:transparent;color:#90a4ae;"
                "border:2px dashed #b0bec5;border-radius:28px;font-size:20px;font-weight:bold;}"
                "QPushButton:hover{border-color:#64b5f6;color:#64b5f6;}")
        elif self.mode == "key":
            self.setStyleSheet(
                "QPushButton{background:#1565c0;color:#ffffff;border-radius:28px;"
                "font-weight:bold;font-size:12px;%s}"
                "QPushButton:hover{background:#1976d2;}" % border)
        else:  # mouse
            self.setStyleSheet(
                "QPushButton{background:#00897b;color:#ffffff;border-radius:28px;"
                "font-weight:bold;font-size:13px;%s}"
                "QPushButton:hover{background:#009688;}" % border)

    def _menu(self, pos):
        if not self.editor:
            return
        if self.mode == "blank":
            self.editor._add_menu()
            return
        m = QMenu(self)
        if self.mode == "key":
            act_rebind = m.addAction("重新录入按键")
        else:  # 鼠标圆: 可切换 左/右/中键
            sub = m.addMenu("切换鼠标按钮")
            for code, label in MOUSE_LABELS:
                sub.addAction(label).setData(code)
        act_del = m.addAction("删除此动作")
        act_up = m.addAction("左移(提前执行)")
        act_down = m.addAction("右移(延后执行)")
        chosen = m.exec(pos)
        if chosen is None:
            return
        if self.mode == "key" and chosen == act_rebind:
            self.editor.rebind_key(self.index)
        elif self.mode == "mouse" and chosen.data() is not None:
            self.editor.set_button(self.index, chosen.data())
        elif chosen == act_del:
            self.editor.delete_item(self.index)
        elif chosen == act_up:
            self.editor.move_item(self.index, -1)
        elif chosen == act_down:
            self.editor.move_item(self.index, 1)


# ============================================================
# 序列编辑器 (非模态 + 圆圈流程)
# ============================================================
class SequenceEditorDialog(QDialog):
    """
    圆圈模式序列编辑器:
    - 末尾常驻一个空白圆, 点击弹出按键捕获, 录入后圆内显示按键并自动选中
    - 左键点击圆 = 选中(悬浮窗显示其属性), 右键圆 = 重新录入/删除/移动
    - items 列表顺序即执行顺序
    - 属性(坐标/时长/延时/鼠标按钮)由悬浮窗的"动作属性"区编辑, 通过 set_prop 写回
    """

    def __init__(self, parent, on_select=None, on_items_changed=None):
        super().__init__(parent)
        self.setWindowTitle("编辑输入序列 (圆圈模式)")
        self.setModal(False)  # 非模态: 打开时悬浮窗仍可拖动与操作
        self.resize(560, 300)
        self.current_file = None
        self.items = []
        self._selected = -1
        self.on_select = on_select
        self.on_items_changed = on_items_changed

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("序列名称")
        top.addWidget(self.name_edit, 1)
        load_btn = QPushButton("载入…")
        load_btn.clicked.connect(self._load)
        top.addWidget(load_btn)
        lay.addLayout(top)

        tip = QLabel("点击末尾空白圆录入按键 · 点击圆圈选中(属性在悬浮窗修改) · 右键圆圈: 重录/删除/移动")
        tip.setStyleSheet("color:#607d8b;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # 圆圈流程区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(120)
        self.flow_widget = QWidget()
        self.flow_widget.setStyleSheet("background:#ffffff;")
        self.flow_lay = QHBoxLayout(self.flow_widget)
        self.flow_lay.setContentsMargins(10, 10, 10, 10)
        self.flow_lay.setSpacing(10)
        self.flow_lay.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.scroll.setWidget(self.flow_widget)
        lay.addWidget(self.scroll, 1)

        btns = QHBoxLayout()
        add_mouse = QPushButton("+ 鼠标动作")
        add_mouse.clicked.connect(self._mouse_button_menu)
        add_key = QPushButton("+ 键盘动作(弹捕获)")
        add_key.clicked.connect(self._new_key_circle)
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(lambda: self.delete_item(self._selected))
        for b in (add_mouse, add_key, del_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)

        bottom = QHBoxLayout()
        self.count_label = QLabel("共 0 个动作")
        self.count_label.setStyleSheet("color:#607d8b;")
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet("font-weight:bold;")
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        bottom.addWidget(self.count_label)
        bottom.addStretch(1)
        bottom.addWidget(save_btn)
        bottom.addWidget(close_btn)
        lay.addLayout(bottom)

    # ---------- 圆圈渲染 ----------
    def refresh_circles(self):
        while self.flow_lay.count():
            item = self.flow_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for i, it in enumerate(self.items):
            if it["type"] == "key":
                text = core.key_display_name(it.get("key"))
                text = text if len(text) <= 6 else text[:5] + "…"
                c = StepCircle(text, "key", i, self)
            else:
                btn_name = it.get("button", "left")
                short = MOUSE_SHORT.get(btn_name, btn_name)
                c = StepCircle(short, "mouse", i, self)
            c.setToolTip("第 %d 个动作: %s" % (i + 1, self._item_desc(it)))
            c.apply_style(selected=(i == self._selected))
            c.clicked.connect(lambda _=False, i=i: self.select(i))
            self.flow_lay.addWidget(c)
        blank = StepCircle("+", "blank", -1, self)
        blank.setToolTip("点击添加动作: 键盘按键 或 鼠标 左/右/中键")
        blank.clicked.connect(lambda: self._add_menu())
        self.flow_lay.addWidget(blank)
        self.flow_lay.addStretch(1)
        self.count_label.setText("共 %d 个动作" % len(self.items))

    def _item_desc(self, it):
        if it["type"] == "key":
            return "键盘 %s, 按住 %.2fs, 延时 %.2fs" % (
                core.key_display_name(it.get("key")), it.get("duration", 0.05), it.get("delay", 0))
        return "鼠标 %s @(%d,%d), 按住 %.2fs, 延时 %.2fs" % (
            MOUSE_SHORT.get(it.get("button", "left"), it.get("button")),
            it.get("x", 0), it.get("y", 0),
            it.get("duration", 0.05), it.get("delay", 0))

    # ---------- 选择 / 增删移 ----------
    def select(self, index):
        if index < 0 or index >= len(self.items):
            self._selected = -1
        else:
            self._selected = index
        self.refresh_circles()
        if self.on_select:
            self.on_select(self._selected)

    def append_item(self, item):
        self.items.append(item)
        self.select(len(self.items) - 1)
        if self.on_items_changed:
            self.on_items_changed()

    def _new_key_circle(self):
        dlg = CaptureKeyDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.captured:
            # 键盘动作不需要 X/Y 坐标属性
            self.append_item({"type": "key", "key": dlg.captured,
                              "duration": 0.05, "delay": 0})

    # ---------- 添加动作: 键盘 或 鼠标(左/右/中键) ----------
    def _add_menu(self):
        m = QMenu(self)
        act_key = m.addAction("键盘按键 (弹窗捕获)")
        m.addSeparator()
        for code, label in MOUSE_LABELS:
            m.addAction("添加 %s 动作" % label).setData(code)
        chosen = m.exec(QCursor.pos())
        if chosen is None:
            return
        if chosen == act_key:
            self._new_key_circle()
        else:
            self._add_mouse(chosen.data())

    def _mouse_button_menu(self):
        m = QMenu(self)
        for code, label in MOUSE_LABELS:
            m.addAction(label).setData(code)
        chosen = m.exec(QCursor.pos())
        if chosen is not None:
            self._add_mouse(chosen.data())

    def _add_mouse(self, button):
        # 新鼠标动作初始坐标 = 当前鼠标所在位置(换算为物理像素), 之后可用"点选 X/Y"精调
        scr = QApplication.primaryScreen()
        dpr = scr.devicePixelRatio() if scr else 1.0
        cur = QCursor.pos()
        self.append_item({"type": "mouse", "button": button,
                          "x": int(cur.x() * dpr), "y": int(cur.y() * dpr),
                          "duration": 0.05, "delay": 0.1})

    def set_button(self, index, button):
        if 0 <= index < len(self.items) and self.items[index]["type"] == "mouse":
            self.items[index]["button"] = button
            self.refresh_circles()
            if self._selected == index and self.on_select:
                self.on_select(self._selected)

    def rebind_key(self, index):
        if 0 <= index < len(self.items) and self.items[index]["type"] == "key":
            dlg = CaptureKeyDialog(self)
            if dlg.exec() == QDialog.Accepted and dlg.captured:
                self.items[index]["key"] = dlg.captured
                self.select(index)

    def delete_item(self, index):
        if 0 <= index < len(self.items):
            del self.items[index]
            self.select(-1)
            if self.on_items_changed:
                self.on_items_changed()

    def move_item(self, index, delta):
        target = index + delta
        if 0 <= index < len(self.items) and 0 <= target < len(self.items):
            self.items[index], self.items[target] = self.items[target], self.items[index]
            self.select(target)
            if self.on_items_changed:
                self.on_items_changed()

    # ---------- 悬浮窗属性写回 ----------
    def set_prop(self, index, field, value):
        if 0 <= index < len(self.items):
            self.items[index][field] = value
            self.refresh_circles()

    # ---------- 载入 / 保存 ----------
    def _load(self):
        path, _ = QFileDialog.getOpenFileName(self, "载入序列", str(SEQ_DIR), "JSON (*.json)")
        if not path:
            return
        data = core.load_json(path, {})
        self.current_file = path
        self.name_edit.setText(data.get("name", ""))
        self.items = list(data.get("items", []))
        self.select(-1)
        if self.on_items_changed:
            self.on_items_changed()

    def _save(self):
        name = self.name_edit.text().strip() or "未命名序列"
        if not self.items:
            QMessageBox.warning(self, "提示", "序列为空, 请先点击空白圆录入按键或添加动作")
            return
        path = self.current_file or str(SEQ_DIR / ("%s.json" % name))
        core.save_json(path, {"name": name, "items": self.items})
        self.current_file = path
        QMessageBox.information(self, "已保存", "序列已保存到:\n%s\n\n回悬浮窗点\"刷新\"即可执行" % path)


# ============================================================
# 自定义输入序列面板 (悬浮窗, 含"动作属性"编辑区)
# ============================================================
class SequencePanel(QWidget):
    playback_done = Signal(bool)  # 线程安全: 执行线程结束经 Qt 信号回到 GUI 线程

    def __init__(self, app_ctx):
        super().__init__()
        self.ctx = app_ctx
        self.player = None
        self.editor = None
        self._loading_props = False
        self._locate_overlay = None
        self.playback_done.connect(self._on_play_done)

        lay = QGridLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setHorizontalSpacing(10)
        lay.setVerticalSpacing(8)

        lay.addWidget(QLabel("序列:"), 0, 0)
        self.seq_combo = QComboBox()
        lay.addWidget(self.seq_combo, 0, 1)
        btn_row = QHBoxLayout()
        edit_btn = QPushButton("编辑序列…")
        edit_btn.clicked.connect(self._edit)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.reload)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(refresh_btn)
        lay.addLayout(btn_row, 1, 1)
        lay.addWidget(QLabel("循环次数:"), 2, 0)
        self.loop_spin = QSpinBox()
        self.loop_spin.setRange(0, 9999)
        self.loop_spin.setValue(1)
        self.loop_spin.setToolTip("0 = 无限循环")
        lay.addWidget(self.loop_spin, 2, 1)

        # ---- 动作属性区: 编辑器选中圆圈时出现 ----
        self.props_group = QGroupBox("动作属性 (选中编辑器中的圆圈)")
        pl = QGridLayout(self.props_group)
        pl.setContentsMargins(10, 16, 10, 8)
        pl.setHorizontalSpacing(8)
        pl.setVerticalSpacing(6)
        self.prop_index = QLabel("未选择")
        self.prop_index.setStyleSheet("color:#1565c0;font-weight:bold;")
        pl.addWidget(QLabel("动作:"), 0, 0)
        pl.addWidget(self.prop_index, 0, 1)
        self.prop_x = QSpinBox()
        self.prop_x.setRange(-10000, 10000)
        self.prop_y = QSpinBox()
        self.prop_y.setRange(-10000, 10000)
        self.prop_x_label = QLabel("X:")
        self.prop_y_label = QLabel("Y:")
        pl.addWidget(self.prop_x_label, 1, 0)
        pl.addWidget(self.prop_x, 1, 1)
        pl.addWidget(self.prop_y_label, 2, 0)
        pl.addWidget(self.prop_y, 2, 1)
        self.prop_duration = QDoubleSpinBox()
        self.prop_duration.setRange(0.01, 10.0)
        self.prop_duration.setDecimals(2)
        self.prop_duration.setSingleStep(0.05)
        self.prop_duration.setSuffix(" s")
        pl.addWidget(QLabel("按住时长:"), 3, 0)
        pl.addWidget(self.prop_duration, 3, 1)
        self.prop_delay = QDoubleSpinBox()
        self.prop_delay.setRange(0.0, 60.0)
        self.prop_delay.setDecimals(2)
        self.prop_delay.setSingleStep(0.1)
        self.prop_delay.setSuffix(" s")
        pl.addWidget(QLabel("执行后延时:"), 4, 0)
        pl.addWidget(self.prop_delay, 4, 1)
        self.prop_btn_combo = QComboBox()
        for _, label in MOUSE_LABELS:
            self.prop_btn_combo.addItem(label)
        self.prop_btn_label = QLabel("鼠标按钮:")
        pl.addWidget(self.prop_btn_label, 5, 0)
        pl.addWidget(self.prop_btn_combo, 5, 1)
        self.locate_btn = QPushButton("用鼠标点选 X/Y")
        self.locate_btn.setToolTip("点击后进入屏幕定位模式: 移动鼠标, 左键点选位置即写入 X/Y, 右键取消")
        self.locate_btn.clicked.connect(self._locate_xy)
        pl.addWidget(self.locate_btn, 6, 0, 1, 2)
        self.props_group.setVisible(False)
        lay.addWidget(self.props_group, 3, 0, 1, 2)

        # 属性写回
        self.prop_x.valueChanged.connect(lambda v: self._write_prop("x", v))
        self.prop_y.valueChanged.connect(lambda v: self._write_prop("y", v))
        self.prop_duration.valueChanged.connect(lambda v: self._write_prop("duration", v))
        self.prop_delay.valueChanged.connect(lambda v: self._write_prop("delay", v))
        self.prop_btn_combo.currentIndexChanged.connect(
            lambda i: self._write_prop("button", MOUSE_LABELS[i][0]))

        self.toggle_btn = QPushButton("执行 (F12)")
        self.toggle_btn.setMinimumHeight(36)
        self.toggle_btn.setStyleSheet(
            "QPushButton{background:#2e7d32;color:#ffffff;border:none;border-radius:6px;font-weight:bold;font-size:13px;}"
            "QPushButton:checked{background:#c62828;}")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.clicked.connect(self._toggle)
        lay.addWidget(self.toggle_btn, 4, 0, 1, 2)

        self.status = QLabel("空闲")
        lay.addWidget(self.status, 5, 0, 1, 2)
        self.reload()

    # ---------- 编辑器生命周期 (非模态) ----------
    def _edit(self):
        if self.editor:
            self.editor.show()
            self.editor.raise_()
            self.editor.activateWindow()
            return
        dlg = SequenceEditorDialog(self, on_select=self._show_props,
                                   on_items_changed=self.reload_status)
        dlg.finished.connect(self._editor_closed)
        dlg.refresh_circles()
        dlg.show()
        self.editor = dlg
        self.reload_status()

    def _editor_closed(self):
        self.editor = None
        self.props_group.setVisible(False)
        self.ctx.floating.adjustSize()
        self.reload()
        self.reload_status()

    def close_editor(self):
        if self.editor:
            self.editor.close()

    def reload_status(self):
        if self.editor:
            self.status.setText("编辑器开启: 共 %d 个动作 (点击圆圈在此处改属性)" % len(self.editor.items))
        else:
            self.reload()

    # ---------- 属性显示 / 写回 ----------
    def _show_props(self, index):
        if not self.editor or index < 0 or index >= len(self.editor.items):
            self.props_group.setVisible(False)
            self.ctx.floating.adjustSize()
            return
        it = self.editor.items[index]
        self._loading_props = True
        n = index + 1
        is_mouse = (it["type"] == "mouse")
        if is_mouse:
            self.prop_index.setText("第 %d 个 · 鼠标动作" % n)
            self.prop_btn_combo.setEnabled(True)
            self.prop_btn_combo.setCurrentIndex(
                [b for b, _ in MOUSE_LABELS].index(it.get("button", "left")))
            self.prop_x.setValue(int(it.get("x", 0)))
            self.prop_y.setValue(int(it.get("y", 0)))
        else:
            self.prop_index.setText("第 %d 个 · 键盘 %s" % (n, core.key_display_name(it.get("key"))))
            self.prop_btn_combo.setEnabled(False)
        self.prop_duration.setValue(float(it.get("duration", 0.05)))
        self.prop_delay.setValue(float(it.get("delay", 0)))
        self._loading_props = False
        # 键盘动作只显示 时长/延时; X/Y/鼠标按钮/点选定位 仅鼠标动作显示
        for w in (self.prop_x, self.prop_x_label, self.prop_y, self.prop_y_label,
                  self.locate_btn, self.prop_btn_combo, self.prop_btn_label):
            w.setVisible(is_mouse)
        self.props_group.setVisible(True)
        self.ctx.floating.adjustSize()

    def _write_prop(self, field, value):
        if self._loading_props or not self.editor:
            return
        index = self.editor._selected
        if 0 <= index < len(self.editor.items):
            self.editor.set_prop(index, field, value)

    # ---------- 鼠标点选 X/Y ----------
    def _locate_xy(self):
        """屏幕定位: 悬浮窗先隐藏, 圆圈覆盖层跟随鼠标, 左键点选位置写入 X/Y"""
        if self._locate_overlay or not self.editor:
            return
        floating = self.window()
        floating.hide()

        def on_closed():
            self._locate_overlay = None
            floating.show()

        ov = CoordinateOverlay(allow_keys=False, one_shot=True, on_closed=on_closed)
        self._locate_overlay = ov

        def got(x, y, kind, payload):
            self._loading_props = True
            self.prop_x.setValue(int(x))
            self.prop_y.setValue(int(y))
            self._loading_props = False
            # 屏蔽回写期间手动写入两项属性
            self._write_prop("x", int(x))
            self._write_prop("y", int(y))
            self.ctx.notify("已定位: X=%d Y=%d" % (x, y))

        ov.picked.connect(got)

    # ---------- 序列数据 ----------
    def reload(self):
        self.seq_combo.clear()
        for f in sorted(SEQ_DIR.glob("*.json")):
            data = core.load_json(f, {})
            self.seq_combo.addItem(data.get("name", f.stem), str(f))
        if not self.editor:
            self.status.setText("空闲")

    def current_items(self):
        idx = self.seq_combo.currentIndex()
        if idx < 0:
            return None, None
        path = self.seq_combo.itemData(idx)
        data = core.load_json(path, {})
        return data.get("items", []), path

    def _toggle(self):
        if self.player and not self.player.stopped:
            self.stop()
        else:
            items, _ = self.current_items()
            if not items:
                QMessageBox.warning(self, "提示", "没有可用的序列, 请先编辑并保存")
                self.toggle_btn.setChecked(False)
                return
            loops = self.loop_spin.value()
            self.toggle_btn.setChecked(True)
            self.toggle_btn.setText("停止")
            self.status.setText("执行中…")
            # 执行期间悬浮窗鼠标穿透: 模拟点击不会误触自身按钮, 也不会挡住目标位置
            self.ctx.floating.set_input_pass_through(True)
            # 把键盘焦点还给上一个前台程序(全屏游戏失焦时会忽略全部模拟输入)
            self.ctx.restore_foreground()
            self.ctx.notify("序列执行已启动")

            self.player = SequencePlayer(items, loops=loops, on_done=self.playback_done.emit)
            self.player.start()

    def _on_play_done(self, interrupted):
        self.player = None
        self.toggle_btn.setChecked(False)
        self.toggle_btn.setText("执行 (%s)" % hk(self.ctx, "seq", "F12"))
        self.status.setText("已中断" if interrupted else "完成")
        self.ctx.floating.set_input_pass_through(False)

    def refresh_hotkey_text(self):
        self.toggle_btn.setText(
            "停止" if (self.player and not self.player.stopped)
            else "执行 (%s)" % hk(self.ctx, "seq", "F12"))

    def stop(self):
        if self.player:
            self.player.stop()
        self.close_editor()


# ============================================================
# 录制回放面板
# ============================================================
class RecordPanel(QWidget):
    playback_done = Signal(bool)  # 线程安全: 回放线程结束经 Qt 信号回到 GUI 线程

    def __init__(self, app_ctx):
        super().__init__()
        self.ctx = app_ctx
        self.recorder = None
        self.replayer = None
        self.progress_timer = QTimer(self)
        self.progress_timer.timeout.connect(self._update_progress)
        self.playback_done.connect(self._on_play_done)

        lay = QGridLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setHorizontalSpacing(10)
        lay.setVerticalSpacing(8)

        lay.addWidget(QLabel("录制文件:"), 0, 0)
        self.file_combo = QComboBox()
        self.file_combo.setMinimumWidth(150)
        lay.addWidget(self.file_combo, 0, 1)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("新录制保存名称")
        lay.addWidget(self.name_edit, 1, 1)

        self.rec_btn = QPushButton("开始录制 (F6)")
        self.rec_btn.setMinimumHeight(36)
        self.rec_btn.setStyleSheet(
            "QPushButton{background:#b71c1c;color:#ffffff;border:none;border-radius:6px;font-weight:bold;font-size:13px;}")
        self.rec_btn.clicked.connect(self.toggle_record)
        lay.addWidget(self.rec_btn, 2, 0, 1, 2)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("回放倍速:"))
        self.speed_combo = QComboBox()
        for s, label in [(2.0, "2.0x"), (1.0, "1.0x"), (0.5, "0.5x")]:
            self.speed_combo.addItem(label, s)
        self.speed_combo.setCurrentIndex(1)
        speed_row.addWidget(self.speed_combo)
        # 压缩长等待: 录制中超过 2.5 秒的空闲在回放时缩短为 2.5 秒,
        # 否则录制时的发呆时间会让回放看起来"卡住/暂停"
        self.compress_chk = QCheckBox("压缩长等待")
        self.compress_chk.setChecked(True)
        self.compress_chk.setToolTip("回放时把超过 2.5 秒的空闲等待缩短为 2.5 秒\n(例: 录制里点完窗口后 7 秒没动, 回放只等 2.5 秒)")
        speed_row.addWidget(self.compress_chk)
        speed_row.addStretch(1)
        self.play_btn = QPushButton("回放")
        self.play_btn.clicked.connect(self._play)
        self.play_btn.setMinimumHeight(32)
        self.play_btn.setStyleSheet(
            "QPushButton{background:#1565c0;color:#ffffff;border:none;border-radius:6px;font-weight:bold;}")
        speed_row.addWidget(self.play_btn)
        lay.addLayout(speed_row, 3, 0, 1, 2)

        self.status = QLabel("空闲")
        lay.addWidget(self.status, 4, 0, 1, 2)
        self.refresh_labels()
        self.reload()

    def refresh_labels(self):
        if self.recorder and self.recorder.recording:
            self.rec_btn.setText("结束录制 (%s)" % hk(self.ctx, "record", "F6"))
            # 录制中的状态行提示也随热键设置更新
            if self.status.text().startswith("●"):
                self.status.setText("● 录制中… (热键 %s/%s 不录入)"
                                    % (hk(self.ctx, "record", "F6"), hk(self.ctx, "stop_record", "F7")))
        else:
            self.rec_btn.setText("开始录制 (%s)" % hk(self.ctx, "record", "F6"))
        self.play_btn.setText(
            "停止回放" if (self.replayer and not self.replayer.stopped)
            else "回放 (%s)" % hk(self.ctx, "play", "F10"))

    def reload(self):
        self.file_combo.clear()
        for f in sorted(RECORD_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            data = core.load_json(f, {})
            name = data.get("name", f.stem)
            dur = data.get("duration_ms", 0)
            self.file_combo.addItem("%s (%.1fs)" % (name, dur / 1000.0), str(f))

    def toggle_record(self):
        if self.recorder and self.recorder.recording:
            self.stop_record()
        else:
            self.start_record()

    def start_record(self):
        self.name_edit.setEnabled(False)
        # 录制开始/结束热键不录入事件流
        ignore = {self.ctx.settings["hotkeys"].get("record", "f6"),
                  self.ctx.settings["hotkeys"].get("stop_record", "f7")}
        self.recorder = Recorder(ignore_names=ignore)
        self.recorder.start()
        self.refresh_labels()
        self.status.setText("● 录制中… (热键 %s/%s 不录入)"
                            % (hk(self.ctx, "record", "F6"), hk(self.ctx, "stop_record", "F7")))
        self.ctx.enter_rec_state()
        if self.ctx.settings.get("sound", True):
            beep("start")
        self.ctx.notify("录制已开始")

    def stop_record(self, save=True):
        if not (self.recorder and self.recorder.recording):
            return
        self.recorder.stop()
        summary = self.recorder.summary()
        self.name_edit.setEnabled(True)
        self.refresh_labels()
        self.status.setText("空闲")
        self.ctx.exit_rec_state()
        if self.ctx.settings.get("sound", True):
            beep("end")
        if not save:
            self.ctx.notify("录制已取消")
            return
        name = self.name_edit.text().strip() or ("录制_%s" % time.strftime("%m%d_%H%M%S"))
        path = str(RECORD_DIR / ("%s.json" % name))
        self.recorder.save(path, name)
        self.reload()
        self.status.setText("已保存: %s (时长 %.1fs, %d 键盘事件, %d 鼠标事件)" % (
            name, summary["duration_ms"] / 1000.0, summary["key_events"], summary["mouse_events"]))
        self.ctx.notify("录制已保存: %s" % name)

    def _play(self):
        if self.replayer and not self.replayer.stopped:
            self._stop_play()
            return
        idx = self.file_combo.currentIndex()
        if idx < 0:
            QMessageBox.warning(self, "提示", "没有可回放的录制文件, 请先录制")
            return
        data = core.load_json(self.file_combo.itemData(idx), {})
        events = data.get("events", [])
        if not events:
            QMessageBox.warning(self, "提示", "该录制文件为空")
            return
        speed = self.speed_combo.currentData()
        max_gap = 2500 if self.compress_chk.isChecked() else None
        self.play_btn.setText("停止回放")
        self.status.setText("▶ 回放中…")
        # 回放期间悬浮窗鼠标穿透, 模拟点击不会点中悬浮窗自身按钮(如"停止回放")
        self.ctx.floating.set_input_pass_through(True)
        # 把键盘焦点还给上一个前台程序(全屏游戏失焦时会忽略全部模拟输入)
        self.ctx.restore_foreground()
        self.ctx.notify("回放已开始 (倍速 %.1fx, 急停 %s 可停)" % (speed, hk(self.ctx, "panic", "F9")))
        self.progress_timer.start(100)

        self.replayer = Replayer(events, speed=speed, on_done=self.playback_done.emit,
                                 max_gap_ms=max_gap)
        self.replayer.start()

    def _on_play_done(self, interrupted):
        self.progress_timer.stop()
        self.replayer = None
        self.play_btn.setText("回放 (%s)" % hk(self.ctx, "play", "F10"))
        self.status.setText("已中断" if interrupted else "回放完成")
        self.ctx.floating.set_input_pass_through(False)
        if interrupted:
            self.ctx.notify("回放已中断 (详情见 data/replay.log)")

    def _stop_play(self):
        if self.replayer:
            self.replayer.stop()

    def _update_progress(self):
        if self.replayer:
            self.status.setText("▶ 回放中… %.1fs / %.1fs" % (
                self.replayer.elapsed_ms / 1000.0, self.replayer.total_ms / 1000.0))

    def stop_all(self):
        if self.recorder and self.recorder.recording:
            self.stop_record(save=False)
        self._stop_play()


# ============================================================
# 悬浮窗 (标题栏专用拖动区 + 高度自适应)
# ============================================================
class DragHeader(QLabel):
    """悬浮窗标题栏: 左键拖动移动窗口, 右键唤出菜单"""

    def __init__(self, window):
        super().__init__("KeyAuto · 悬浮窗")
        self.win = window
        self._drag = None
        self.setFixedHeight(28)
        self.setAlignment(Qt.AlignCenter)

    def _style(self, recording):
        if recording:
            self.setText("● REC 录制中 — 00:00")
            self.setStyleSheet(
                "background:#c62828;color:#ffffff;padding:2px;font-weight:bold;font-size:13px;")
        else:
            self.setText("KeyAuto · 悬浮窗 (左键拖动 · 右键菜单)")
            self.setStyleSheet(
                "background:#37474f;color:#ffffff;padding:2px;font-weight:bold;font-size:13px;")
        self.setCursor(Qt.SizeAllCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.win.frameGeometry().topLeft()
        elif e.button() == Qt.RightButton:
            self.win.show_menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.win.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = None


class FloatingWindow(QWidget):
    def __init__(self, app_ctx):
        super().__init__(None,
                         Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.ctx = app_ctx
        self.setWindowTitle("KeyAuto")
        self.setFixedWidth(320)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = DragHeader(self)
        outer.addWidget(self.header)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        self.panel_fixed = ClickPanel(app_ctx, random_mode=False)
        self.panel_random = ClickPanel(app_ctx, random_mode=True)
        self.panel_seq = SequencePanel(app_ctx)
        self.panel_rec = RecordPanel(app_ctx)
        for p in (self.panel_fixed, self.panel_random, self.panel_seq, self.panel_rec):
            self.stack.addWidget(p)

        self._rec_timer = None
        self._pass_through = False
        self.apply_style()

    def set_input_pass_through(self, on):
        """任务执行期间让悬浮窗对鼠标"透明"(点击穿透):
        1) 置顶的悬浮窗不会挡住回放/序列要点击的目标位置;
        2) 模拟点击落在悬浮窗区域时直接穿透到下层, 不会误触悬浮窗自己的按钮
           (此前回放的点击若恰好点中"停止回放"按钮, 复现就会莫名中断)。"""
        on = bool(on)
        if self._pass_through == on:
            return
        self._pass_through = on
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowTransparentForInput, on)
        if visible:
            self.show()

    def apply_style(self, recording=False):
        opacity = self.ctx.settings.get("opacity", 0.92)
        self.setWindowOpacity(opacity)
        self.header._style(recording)
        self.setStyleSheet("QWidget { font-size: 12px; }")

    def show_panel(self, idx):
        self.stack.setCurrentIndex(idx)
        # 每次显示面板都按当前设置刷新热键文字, 杜绝改键后提示过期
        p = self.stack.widget(idx)
        if hasattr(p, "apply_settings"):
            p.apply_settings()
        if hasattr(p, "refresh_labels"):
            p.refresh_labels()
        self.show()
        self.adjustSize()

    def show_menu(self, pos):
        m = QMenu(self)
        act_back = m.addAction("返回主界面(切换功能)")
        act_settings = m.addAction("全局设置(热键/频率等)")
        m.addSeparator()
        act_panic = m.addAction("急停(停止所有任务)")
        act_save = m.addAction("保存当前配置")
        m.addSeparator()
        act_exit = m.addAction("退出程序")
        chosen = m.exec(pos)
        if chosen == act_back:
            self.ctx.back_to_main()
        elif chosen == act_settings:
            self.ctx.open_settings(self)
        elif chosen == act_panic:
            self.ctx.panic_stop()
        elif chosen == act_save:
            self.ctx.save_current_config()
        elif chosen == act_exit:
            self.ctx.quit_app()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.show_menu(e.globalPosition().toPoint())

    def enter_rec_state(self):
        self.apply_style(recording=True)
        self._rec_t0 = time.time()
        self._rec_timer = QTimer(self)
        self._rec_timer.timeout.connect(self._rec_tick)
        self._rec_timer.start(1000)
        self.adjustSize()

    def exit_rec_state(self):
        if self._rec_timer:
            self._rec_timer.stop()
            self._rec_timer = None
        self.apply_style(recording=False)
        self.adjustSize()

    def _rec_tick(self):
        secs = int(time.time() - self._rec_t0)
        self.header.setText("● REC 录制中 — %02d:%02d" % (secs // 60, secs % 60))

    def stop_all_tasks(self):
        self.panel_fixed.stop()
        self.panel_random.stop()
        self.panel_seq.stop()
        self.panel_rec.stop_all()


# ============================================================
# 主界面
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self, app_ctx):
        super().__init__()
        self.ctx = app_ctx
        self.setWindowTitle("KeyAuto — 按键输入自动化工具")
        self.setFixedWidth(440)
        self.setFixedHeight(460)
        self.setObjectName("plainBg")

        central = QWidget()
        central.setObjectName("plainBg")
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(18, 18, 18, 14)
        lay.setSpacing(10)

        title = QLabel("选择一个功能")
        title.setStyleSheet("font-size:16px;font-weight:bold;color:#263238;")
        lay.addWidget(title)
        tip = QLabel("选择后主界面隐藏, 悬浮窗出现; 左键拖动悬浮窗标题栏移动, 右键返回此界面")
        tip.setStyleSheet("color:#607d8b;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        cards = [
            ("定频连点", "固定频率重复点击/按键"),
            ("随机频率连点", "每秒次数在范围内随机"),
            ("自定义输入序列", "圆圈编排键鼠动作并循环执行"),
            ("录制与回放", "录制真实输入, 之后复现"),
        ]
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, (name, desc) in enumerate(cards):
            btn = QPushButton("%s\n%s" % (name, desc))
            btn.setMinimumHeight(72)
            btn.setStyleSheet(
                "QPushButton{text-align:left;padding:12px;font-size:13px;"
                "background:#ffffff;color:#263238;border:1px solid #b0bec5;border-radius:8px;}"
                "QPushButton:hover{background:#e3f2fd;border-color:#64b5f6;}")
            btn.clicked.connect(lambda _=False, idx=i: self.ctx.select_function(idx))
            grid.addWidget(btn, i // 2, i % 2)
        lay.addLayout(grid)

        settings_btn = QPushButton("全局设置")
        settings_btn.clicked.connect(self._open_settings)
        lay.addWidget(settings_btn)
        lay.addStretch(1)

        self.info = QLabel("")
        self.info.setStyleSheet("color:#90a4ae;font-size:11px;")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.refresh_info()

    def refresh_info(self):
        h = self.ctx.settings["hotkeys"]
        self.info.setText("热键: %s 录制 · %s 结束 · %s 连点 · %s 回放 · %s 序列 · %s 急停 (可在全局设置中修改)"
                          % (h.get("record", "f6").upper(), h.get("stop_record", "f7").upper(),
                             h.get("click", "f8").upper(), h.get("play", "f10").upper(),
                             h.get("seq", "f12").upper(), h.get("panic", "f9").upper()))

    def _open_settings(self):
        self.ctx.open_settings(self)


class SettingsDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("全局设置")
        self.setFixedWidth(360)
        self.values = dict(settings)

        lay = QGridLayout(self)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setHorizontalSpacing(10)
        lay.setVerticalSpacing(8)
        lay.addWidget(QLabel("频率上限(次/秒):"), 0, 0)
        self.cap = QSpinBox()
        self.cap.setRange(1, 50)
        self.cap.setValue(int(settings.get("rate_cap", 10)))
        lay.addWidget(self.cap, 0, 1)

        lay.addWidget(QLabel("悬浮窗不透明度:"), 1, 0)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setValue(int(settings.get("opacity", 0.92) * 100))
        lay.addWidget(self.opacity, 1, 1)

        lay.addWidget(QLabel("提示音:"), 2, 0)
        self.sound = QCheckBox("开启")
        self.sound.setChecked(bool(settings.get("sound", True)))
        lay.addWidget(self.sound, 2, 1)

        hotkey_labels = [("录制开始", "record"), ("录制结束", "stop_record"),
                         ("连点启停", "click"), ("回放启停", "play"),
                         ("序列启停", "seq"), ("急停", "panic")]
        default_hks = DEFAULT_SETTINGS["hotkeys"]
        self.hk_combos = {}
        key_names = ["f%d" % i for i in range(1, 13)]
        for i, (label, key) in enumerate(hotkey_labels):
            lay.addWidget(QLabel("%s:" % label), 3 + i, 0)
            combo = QComboBox()
            combo.addItems(key_names)
            combo.setCurrentText(settings.get("hotkeys", {}).get(key, default_hks.get(key, "f6")))
            lay.addWidget(combo, 3 + i, 1)
            self.hk_combos[key] = combo

        ok = QPushButton("保存")
        ok.clicked.connect(self._save)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row, 3 + len(hotkey_labels), 0, 1, 2)

    def _save(self):
        self.values["rate_cap"] = self.cap.value()
        self.values["opacity"] = self.opacity.value() / 100.0
        self.values["sound"] = self.sound.isChecked()
        self.values["hotkeys"] = {k: c.currentText() for k, c in self.hk_combos.items()}
        self.accept()


# ============================================================
# 应用上下文
# ============================================================
class AppContext:
    def __init__(self):
        self.settings = load_settings()
        self.app = QApplication.instance()
        self.main = MainWindow(self)
        self.floating = FloatingWindow(self)
        self.hotkeys = GlobalHotkeys()
        self._notify_timer = None
        self.hotkeys.fired.connect(self._on_hotkey)
        self.hotkeys.start(self.settings["hotkeys"])
        # 热键提示文字周期自动刷新(每 2 秒): 无论设置从哪条路径被修改,
        # 界面上所有括号里的热键提示都会跟着当前设置走, 杜绝任何"提示过期"
        self._hotkey_timer = QTimer()
        self._hotkey_timer.setInterval(2000)
        self._hotkey_timer.timeout.connect(self.refresh_hotkey_texts)
        self._hotkey_timer.start()
        # 前台窗口追踪: 记住"上一个不属于本程序的前台窗口"。
        # 模拟键鼠只发给前台窗口, 全屏游戏失焦时会忽略所有输入——
        # 任务启动后把焦点还给它, 游戏才能响应回放/连点/序列
        self._last_fg = 0
        self._fg_timer = QTimer()
        self._fg_timer.setInterval(400)
        self._fg_timer.timeout.connect(self._poll_foreground)
        self._fg_timer.start()

    def _poll_foreground(self):
        hwnd = core.get_foreground_hwnd()
        if hwnd and core.hwnd_pid(hwnd) != os.getpid():
            self._last_fg = hwnd

    def restore_foreground(self, delay_ms=250):
        """任务启动后把键盘焦点还给上一个前台程序(全屏游戏必须聚焦才接受模拟输入)"""
        hwnd = self._last_fg
        if not hwnd:
            return
        QTimer.singleShot(delay_ms, lambda: core.set_foreground_hwnd(hwnd))

    def open_settings(self, parent):
        """打开全局设置(主界面与悬浮窗右键菜单共用同一入口)"""
        dlg = SettingsDialog(parent, self.settings)
        if dlg.exec() == QDialog.Accepted:
            self.settings = dlg.values
            save_settings(self.settings)
            self.on_settings_changed()

    def refresh_hotkey_texts(self):
        """只刷新热键提示文字(不碰其他控件值, 可高频调用)"""
        self.floating.panel_rec.refresh_labels()
        for p in (self.floating.panel_fixed, self.floating.panel_random):
            p.refresh_hotkey_text()
        self.floating.panel_seq.refresh_hotkey_text()
        self.main.refresh_info()

    def on_settings_changed(self):
        self.floating.apply_style()
        for i in range(self.floating.stack.count()):
            p = self.floating.stack.widget(i)
            if hasattr(p, "apply_settings"):
                p.apply_settings()
            if hasattr(p, "refresh_labels"):
                p.refresh_labels()
        self.main.refresh_info()
        self.hotkeys.start(self.settings["hotkeys"])
        self.notify("设置已保存")

    def select_function(self, idx):
        self.main.hide()
        self.floating.show_panel(idx)

    def back_to_main(self):
        self.floating.stop_all_tasks()
        self.floating.hide()
        self.main.show()

    def panic_stop(self):
        self.floating.stop_all_tasks()
        self.notify("已急停: 所有任务已停止")

    def save_current_config(self):
        save_settings(self.settings)
        self.notify("设置已保存 (序列/录制文件保存在 data 目录)")

    def quit_app(self):
        self.floating.stop_all_tasks()
        self.hotkeys.stop()
        self.app.quit()

    def enter_rec_state(self):
        self.floating.enter_rec_state()

    def exit_rec_state(self):
        self.floating.exit_rec_state()

    def notify(self, text):
        if getattr(self.floating, "_rec_timer", None) is not None:
            return
        header = self.floating.header
        original = "KeyAuto · 悬浮窗 (左键拖动 · 右键菜单)"
        header.setText(text)
        if self._notify_timer:
            self._notify_timer.stop()
        self._notify_timer = QTimer(self.floating)
        self._notify_timer.setSingleShot(True)
        self._notify_timer.timeout.connect(lambda: header.setText(original))
        self._notify_timer.start(2500)

    def _on_hotkey(self, action):
        panel = self.floating.stack.currentWidget()
        if action == "panic":
            self.panic_stop()
        elif action == "click":
            if isinstance(panel, ClickPanel):
                panel.toggle()
        elif action == "record":
            if self.floating.stack.currentIndex() == 3:
                self.floating.panel_rec.toggle_record()
        elif action == "stop_record":
            if self.floating.panel_rec.recorder and self.floating.panel_rec.recorder.recording:
                self.floating.panel_rec.stop_record()
        elif action == "play":
            # 全屏游戏场景: 焦点保持在游戏上, 用热键启停回放, 无需点击悬浮窗
            if self.floating.stack.currentIndex() == 3:
                self.floating.panel_rec._play()
        elif action == "seq":
            if self.floating.stack.currentIndex() == 2:
                self.floating.panel_seq._toggle()


def main():
    app = QApplication(sys.argv)
    # 关键修复: 悬浮窗是 Tool 窗口, 不参与"最后一个窗口关闭即退出"的判定;
    # 主界面又是隐藏状态, 于是关闭编辑器/捕获对话框 = 最后一个常规窗口关闭
    # = 整个程序被连带退出(悬浮窗瞬间消失, 后台回放线程作为 daemon 一并被杀,
    # 表现为"复现莫名停止")。改为只能从右键菜单"退出程序"。
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(GLOBAL_QSS)
    if "--selftest" in sys.argv:
        assert not app.quitOnLastWindowClosed(), "必须关闭 lastWindowClosed 退出机制"
        ctx = AppContext()
        ctx.main.show()
        # 实例化各面板并打开一次编辑器, 验证非模态生命周期
        ctx.floating.show_panel(0)
        ctx.floating.show_panel(2)
        pseq = ctx.floating.panel_seq
        pseq._edit()
        ed = pseq.editor
        assert ed is not None
        # 鼠标动作: 属性区显示 X/Y/鼠标按钮, 可切换 左/右/中键
        ed.append_item({"type": "mouse", "button": "right", "x": 10, "y": 20,
                        "duration": 0.05, "delay": 0})
        ed.select(0)
        assert pseq.prop_x.isVisible()
        assert pseq.prop_btn_combo.isEnabled()
        ed.set_button(0, "middle")
        assert ed.items[0]["button"] == "middle"
        # 键盘动作: 不显示 X/Y/点选定位/鼠标按钮, 条目不含坐标
        ed.append_item({"type": "key", "key": {"k": "char", "v": "a"},
                        "duration": 0.05, "delay": 0})
        ed.select(1)
        assert not pseq.prop_x.isVisible()
        assert not pseq.locate_btn.isVisible()
        assert "x" not in ed.items[1]
        pseq._write_prop("duration", 0.2)
        assert ed.items[1]["duration"] == 0.2
        # 双击动作: 可添加/可切换, 属性下拉能选中
        ed.append_item({"type": "mouse", "button": "double", "x": 5, "y": 5,
                        "duration": 0.05, "delay": 0})
        ed.select(2)
        assert pseq.prop_btn_combo.currentText() == "鼠标双击"
        ed.set_button(2, "left")
        assert ed.items[2]["button"] == "left"
        # 回放时间轴压缩: 超过 2.5s 的间隔被压缩, 短间隔原样保留
        evs = [{"t": 100, "type": "a"}, {"t": 300, "type": "b"},
               {"t": 9300, "type": "c"}, {"t": 9800, "type": "d"}]
        comp = Replayer.compress_timeline(evs, 2500)
        assert comp[0]["t"] == 100 and comp[1]["t"] == 300
        assert comp[2]["t"] == 2800 and comp[3]["t"] == 3300
        # 关闭编辑器: 程序不得退出, 悬浮窗保持可见
        ed.close()
        app.processEvents()
        assert pseq.editor is None
        assert ctx.floating.isVisible()
        # 悬浮窗鼠标穿透开关可正常切换
        ctx.floating.set_input_pass_through(True)
        ctx.floating.set_input_pass_through(False)
        # 热键改键后提示文字必须同步刷新
        ctx.settings["hotkeys"]["record"] = "f8"
        ctx.settings["hotkeys"]["click"] = "f3"
        ctx.on_settings_changed()
        assert "F8" in ctx.floating.panel_rec.rec_btn.text(), ctx.floating.panel_rec.rec_btn.text()
        assert "F3" in ctx.floating.panel_fixed.toggle_btn.text(), ctx.floating.panel_fixed.toggle_btn.text()
        ctx.floating.show_panel(3)
        assert "F8" in ctx.floating.panel_rec.rec_btn.text()
        # 热键提示周期刷新链路可调用(主界面提示行同步)
        ctx.refresh_hotkey_texts()
        assert "F8" in ctx.main.info.text() or "F8" in ctx.floating.panel_rec.rec_btn.text()
        # v1.6: 新热键默认值、前台窗口追踪、回放/序列按钮热键提示
        assert "play" in ctx.settings["hotkeys"] and "seq" in ctx.settings["hotkeys"]
        ctx._poll_foreground()  # 只读系统前台窗口
        ctx.refresh_hotkey_texts()
        assert "F10" in ctx.floating.panel_rec.play_btn.text(), ctx.floating.panel_rec.play_btn.text()
        assert "F12" in ctx.floating.panel_seq.toggle_btn.text(), ctx.floating.panel_seq.toggle_btn.text()
        assert hasattr(ctx, "restore_foreground")
        # 回放器: 日志落盘 + 线程级异常兜底(用空事件类型, 不产生真实键鼠注入)
        rp = Replayer([{"t": 5, "type": "_test_noop"}], speed=10, on_done=lambda i: None)
        rp.start()
        rp.join(2)
        assert Path(core.Replayer.log_path).exists()
        assert "正常结束" in Path(core.Replayer.log_path).read_text(encoding="utf-8")
        # 连点线程可启停
        t = ClickerThread(lambda: None, lambda: 0.02)
        t.start()
        t.stop()
        t.join(1)
        assert t.stopped
        print("SELFTEST OK")
        return 0
    ctx = AppContext()
    ctx.main.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
