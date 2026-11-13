# -*- coding: utf-8 -*-
"""
main.py - 按键输入自动化工具 (KeyAuto)  v1.1
交互形态: 主界面选择功能 -> 主界面隐藏 -> 悬浮窗设置并运行
悬浮窗右键 -> 返回主界面 / 急停 / 保存配置 / 退出

v1.1 修复:
  1. 全局浅色主题 QSS, 修复深色系统下白底白字不可读
  2. 悬浮窗标题栏成为专用拖动区, 面板打开时也可拖动
  3. 悬浮窗宽度固定、高度随内容自适应, 修复面板内容重叠
  4. 序列编辑器新增"屏幕拾取"圆圈覆盖层: 点击圆圈添加鼠标动作、按键添加键盘动作, 同时锁定坐标
  5. 热键修改后所有界面按钮文字同步刷新
"""
import sys
import time
import json
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QPoint, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QStackedWidget, QComboBox, QSpinBox, QSlider,
    QRadioButton, QButtonGroup, QLineEdit, QMessageBox, QMenu, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QCheckBox, QGroupBox,
)

import core
from core import Recorder, Replayer, SequencePlayer, perform_input, random_interval_ms

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
    "hotkeys": {"record": "f6", "stop_record": "f7", "click": "f8", "panic": "f9"},
}

MOUSE_LABELS = [("left", "鼠标左键"), ("right", "鼠标右键"), ("middle", "鼠标中键")]

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
QTableWidget {
    background: #ffffff; color: #263238; gridline-color: #dfe3e8;
    border: 1px solid #cfd8dc; selection-background-color: #bbdefb; selection-color: #263238;
}
QHeaderView::section {
    background: #eceff1; color: #37474f; border: 0;
    border-right: 1px solid #cfd8dc; border-bottom: 1px solid #cfd8dc;
    padding: 4px; font-weight: bold;
}
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("捕获按键")
        self.setFixedWidth(320)
        self.captured = None
        lay = QVBoxLayout(self)
        tip = QLabel("请在下方区域按下要绑定的按键…")
        tip.setWordWrap(True)
        self.show_label = QLabel("等待输入…")
        self.show_label.setAlignment(Qt.AlignCenter)
        self.show_label.setMinimumHeight(60)
        self.show_label.setStyleSheet(
            "border:1px solid #b0bec5; border-radius:6px; font-size:16px; font-weight:bold; color:#1565c0; background:#ffffff;")
        btn_ok = QPushButton("确定")
        btn_cancel = QPushButton("取消")
        row = QHBoxLayout()
        row.addWidget(btn_ok)
        row.addWidget(btn_cancel)
        lay.addWidget(tip)
        lay.addWidget(self.show_label)
        lay.addLayout(row)
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        self._listener = None
        self._start_capture()

    def _start_capture(self):
        from pynput import keyboard as pkb

        def on_press(key):
            s = core.serialize_key(key)
            if s:
                self.captured = s
                self.show_label.setText(core.key_display_name(s))
                return False
            return True

        self._listener = pkb.Listener(on_press=on_press)
        self._listener.start()

    def done(self, r):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
        super().done(r)


# ============================================================
# 屏幕拾取覆盖层: 可移动圆圈 + 十字线
#   - 鼠标移动: 圆圈跟随
#   - 左键点击: 发出 picked(x, y, "mouse", 按钮名)
#   - 键盘按键: 发出 picked(x, y, "key", 键数据)   (allow_keys=True 时)
#   - 右键 / Esc: 关闭
# ============================================================
class CoordinateOverlay(QWidget):
    picked = Signal(int, int, str, object)  # x, y, kind, payload(Qt 队列跨线程投递)

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

        tip_text = "移动鼠标定位圆圈 · 左键点击确认坐标"
        if allow_keys:
            tip_text += " · 按任意键添加键盘动作"
        tip_text += " · 右键或 Esc 结束"
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

        if allow_keys:
            self._start_kb_capture()
        self.showFullScreen()

    def _start_kb_capture(self):
        from pynput import keyboard as pkb

        def on_press(key):
            if key == pkb.Key.esc:
                QTimer.singleShot(0, self._close)
                return False
            s = core.serialize_key(key)
            if s:
                pos = QCursor.pos()
                self.picked.emit(int(pos.x()), int(pos.y()), "key", s)
            return True

        self._kb_listener = pkb.Listener(on_press=on_press)
        self._kb_listener.start()

    def _update_coord_label(self):
        self.coord.setText("X: %d   Y: %d" % (int(self._cur.x()), int(self._cur.y())))
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
        p.setPen(QPen(QColor(229, 57, 53, 120), 1))
        p.drawLine(cx - 24, cy, cx - 16, cy)
        p.drawLine(cx + 16, cy, cx + 24, cy)
        p.drawLine(cx, cy - 24, cx, cy - 16)
        p.drawLine(cx, cy + 16, cx, cy + 24)
        p.end()

    def mouseMoveEvent(self, e):
        self._cur = e.globalPosition().toPoint()
        self._update_coord_label()
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.picked.emit(int(e.globalPosition().x()), int(e.globalPosition().y()),
                             "mouse", self._mouse_button)
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
        if self._kb_listener:
            try:
                self._kb_listener.stop()
            except Exception:
                pass
        if self._on_closed:
            self._on_closed()
        e.accept()


# ============================================================
# 连点面板 (定频 / 随机 共用)
# ============================================================
class ClickPanel(QWidget):
    def __init__(self, app_ctx, random_mode=False):
        super().__init__()
        self.ctx = app_ctx
        self.random_mode = random_mode
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.running = False
        self.key_dict = None
        self._pick_overlay = None

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

        pos_group = QGroupBox("点击位置")
        pl = QVBoxLayout(pos_group)
        pl.setContentsMargins(10, 14, 10, 8)
        pl.setSpacing(6)
        self.radio_follow = QRadioButton("跟随当前鼠标")
        self.radio_fixed = QRadioButton("固定坐标")
        bg = QButtonGroup(self)
        bg.addButton(self.radio_follow)
        bg.addButton(self.radio_fixed)
        pl.addWidget(self.radio_follow)
        pl.addWidget(self.radio_fixed)
        pick_row = QHBoxLayout()
        self.pick_btn = QPushButton("拾取坐标")
        self.pick_btn.clicked.connect(self._pick_pos)
        self.pos_label = QLabel("X:-  Y:-")
        pick_row.addWidget(self.pick_btn)
        pick_row.addWidget(self.pos_label)
        pick_row.addStretch(1)
        pl.addLayout(pick_row)
        self.radio_follow.setChecked(True)
        self.radio_fixed.toggled.connect(lambda on: self.pick_btn.setEnabled(on))
        self.pick_btn.setEnabled(False)
        lay.addWidget(pos_group, row, 0, 1, 2)
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

    def _pick_pos(self):
        """屏幕拾取: 悬浮窗先隐藏, 覆盖层圆圈定位, 左键点击确认坐标"""
        if self._pick_overlay:
            return
        floating = self.window()
        floating.hide()

        def on_closed():
            self._pick_overlay = None
            floating.show()

        ov = CoordinateOverlay(allow_keys=False, one_shot=True, on_closed=on_closed)
        self._pick_overlay = ov

        def got(x, y, kind, payload):
            self.pos_label.setText("X:%d  Y:%d" % (x, y))
            self.radio_fixed.setChecked(True)

        ov.picked.connect(got)

    def apply_settings(self):
        cap = int(self.ctx.settings.get("rate_cap", 10))
        for name in ("rate", "rate_lo", "rate_hi"):
            w = getattr(self, name, None)
            if w is not None:
                w.setMaximum(max(1, cap))
                w.setValue(min(w.value(), cap))
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
        interval = 1000.0 / self.rate.value()
        if self.random_mode:
            interval = random_interval_ms(self.rate_lo.value(), self.rate_hi.value())
        self.timer.start(int(max(1, interval)))
        self.ctx.notify("连点任务已启动")

    def stop(self):
        self.running = False
        self.timer.stop()
        self.toggle_btn.setChecked(False)
        self.toggle_btn.setText("启动 (%s)" % hk(self.ctx, "click", "F8"))

    def _tick(self):
        idx = self.type_combo.currentIndex()
        if idx < len(MOUSE_LABELS):
            fixed = None
            if self.radio_fixed.isChecked():
                text = self.pos_label.text()
                try:
                    xs, ys = text.replace("X:", "").replace("Y:", "").split()
                    fixed = (float(xs), float(ys))
                except Exception:
                    fixed = None
            perform_input("mouse", mouse_button=MOUSE_LABELS[idx][0], fixed_pos=fixed)
        else:
            perform_input("key", key_dict=self.key_dict)
        if self.random_mode:
            interval = random_interval_ms(self.rate_lo.value(), self.rate_hi.value())
            self.timer.setInterval(int(max(1, interval)))


# ============================================================
# 自定义输入序列面板
# ============================================================
class SequencePanel(QWidget):
    def __init__(self, app_ctx):
        super().__init__()
        self.ctx = app_ctx
        self.player = None
        lay = QGridLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setHorizontalSpacing(10)
        lay.setVerticalSpacing(8)

        lay.addWidget(QLabel("序列:"), 0, 0)
        self.seq_combo = QComboBox()
        lay.addWidget(self.seq_combo, 0, 1)
        edit_btn = QPushButton("编辑序列…")
        edit_btn.clicked.connect(self._edit)
        lay.addWidget(edit_btn, 1, 1)
        lay.addWidget(QLabel("循环次数:"), 2, 0)
        self.loop_spin = QSpinBox()
        self.loop_spin.setRange(0, 9999)
        self.loop_spin.setValue(1)
        self.loop_spin.setToolTip("0 = 无限循环")
        lay.addWidget(self.loop_spin, 2, 1)

        self.toggle_btn = QPushButton("执行")
        self.toggle_btn.setMinimumHeight(36)
        self.toggle_btn.setStyleSheet(
            "QPushButton{background:#2e7d32;color:#ffffff;border:none;border-radius:6px;font-weight:bold;font-size:13px;}"
            "QPushButton:checked{background:#c62828;}")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.clicked.connect(self._toggle)
        lay.addWidget(self.toggle_btn, 3, 0, 1, 2)

        self.status = QLabel("空闲")
        lay.addWidget(self.status, 4, 0, 1, 2)
        self.reload()

    def reload(self):
        self.seq_combo.clear()
        for f in sorted(SEQ_DIR.glob("*.json")):
            data = core.load_json(f, {})
            self.seq_combo.addItem(data.get("name", f.stem), str(f))

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
                QMessageBox.warning(self, "提示", "没有可用的序列, 请先编辑创建")
                self.toggle_btn.setChecked(False)
                return
            loops = self.loop_spin.value()
            self.toggle_btn.setChecked(True)
            self.toggle_btn.setText("停止")
            self.status.setText("执行中…")
            self.ctx.notify("序列执行已启动")

            def done(interrupted):
                self.player = None
                self.toggle_btn.setChecked(False)
                self.toggle_btn.setText("执行")
                self.status.setText("已中断" if interrupted else "完成")

            self.player = SequencePlayer(items, loops=loops, on_done=done)
            self.player.start()

    def stop(self):
        if self.player:
            self.player.stop()

    def _edit(self):
        dlg = SequenceEditorDialog(self)
        dlg.exec()
        self.reload()


class SequenceEditorDialog(QDialog):
    """序列编辑器: 表格增删改 + 上下移动 + 屏幕拾取(圆圈) + 保存"""

    COLUMNS = ["类型", "按键/按钮", "X", "Y", "按下时长(s)", "延时(s)"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑输入序列")
        self.resize(600, 430)
        self.current_file = None
        self._overlay = None

        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        top = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("序列名称")
        load_btn = QPushButton("载入…")
        load_btn.clicked.connect(self._load)
        top.addWidget(self.name_edit, 1)
        top.addWidget(load_btn)
        lay.addLayout(top)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay.addWidget(self.table, 1)

        # 拾取工具行: 选择鼠标按钮 + 进入屏幕圆圈拾取模式
        pick_row = QHBoxLayout()
        pick_row.addWidget(QLabel("拾取动作按钮:"))
        self.pick_btn_combo = QComboBox()
        for _, label in MOUSE_LABELS:
            self.pick_btn_combo.addItem(label)
        pick_row.addWidget(self.pick_btn_combo)
        self.overlay_btn = QPushButton("屏幕拾取 (圆圈模式)")
        self.overlay_btn.setStyleSheet("font-weight:bold;")
        self.overlay_btn.clicked.connect(self._toggle_overlay)
        pick_row.addWidget(self.overlay_btn)
        pick_row.addStretch(1)
        self.overlay_hint = QLabel("")
        self.overlay_hint.setStyleSheet("color:#1565c0;background:transparent;")
        pick_row.addWidget(self.overlay_hint)
        lay.addLayout(pick_row)

        btns = QHBoxLayout()
        add_mouse = QPushButton("+ 鼠标动作")
        add_mouse.clicked.connect(lambda: self._add_row("mouse"))
        add_key = QPushButton("+ 键盘动作")
        add_key.clicked.connect(lambda: self._add_row("key"))
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self._del_row)
        up_btn = QPushButton("上移")
        up_btn.clicked.connect(lambda: self._move(-1))
        down_btn = QPushButton("下移")
        down_btn.clicked.connect(lambda: self._move(1))
        for b in (add_mouse, add_key, del_btn, up_btn, down_btn):
            btns.addWidget(b)
        lay.addLayout(btns)

        bottom = QHBoxLayout()
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        bottom.addStretch(1)
        bottom.addWidget(save_btn)
        bottom.addWidget(close_btn)
        lay.addLayout(bottom)

    # ---- 屏幕圆圈拾取 ----
    def _toggle_overlay(self):
        if self._overlay:
            self._overlay._close()
            return
        self._overlay = CoordinateOverlay(
            allow_keys=True, one_shot=False,
            mouse_button=MOUSE_LABELS[self.pick_btn_combo.currentIndex()][0],
            on_closed=self._overlay_done)
        self.overlay_hint.setText("圆圈模式中: 点击=加鼠标动作, 按键=加键盘动作, 右键结束")
        self._overlay.picked.connect(self._add_picked)

    def _overlay_done(self):
        self._overlay = None
        self.overlay_hint.setText("拾取已结束")

    def _add_picked(self, x, y, kind, payload):
        """由覆盖层(含 pynput 线程)发来的信号, Qt 自动队列投递到 GUI 线程"""
        r = self.table.rowCount()
        self.table.insertRow(r)
        if kind == "mouse":
            values = ["mouse", payload, str(x), str(y), "0.05", "0.1"]
            for c, v in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(v))
        else:
            item_btn = QTableWidgetItem(core.key_display_name(payload))
            item_btn.setData(Qt.UserRole, payload)
            values = ["key", None, str(x), str(y), "0.05", "0"]
            for c, v in enumerate(values):
                if c == 1:
                    self.table.setItem(r, c, item_btn)
                else:
                    self.table.setItem(r, c, QTableWidgetItem(v))
        self.table.scrollToBottom()
        self.table.setCurrentCell(r, 0)

    def _add_row(self, type_):
        r = self.table.rowCount()
        self.table.insertRow(r)
        if type_ == "mouse":
            values = ["mouse", "left", "0", "0", "0.05", "0.1"]
        else:
            values = ["key", "(未设置)", "-", "-", "0.05", "0"]
        for c, v in enumerate(values):
            self.table.setItem(r, c, QTableWidgetItem(v))

    def _del_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def _move(self, delta):
        r = self.table.currentRow()
        if r < 0:
            return
        target = r + delta
        if 0 <= target < self.table.rowCount():
            values = []
            for c in range(self.table.columnCount()):
                it = self.table.takeItem(r, c)
                values.append((it.text() if it else "", it.data(Qt.UserRole) if it else None))
            self.table.removeRow(r)
            self.table.insertRow(target)
            for c, (text, role) in enumerate(values):
                item = QTableWidgetItem(text)
                if role is not None:
                    item.setData(Qt.UserRole, role)
                self.table.setItem(target, c, item)
            self.table.setCurrentCell(target, 0)

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(self, "载入序列", str(SEQ_DIR), "JSON (*.json)")
        if not path:
            return
        data = core.load_json(path, {})
        self.current_file = path
        self.name_edit.setText(data.get("name", ""))
        self.table.setRowCount(0)
        for it in data.get("items", []):
            r = self.table.rowCount()
            self.table.insertRow(r)
            if it["type"] == "mouse":
                values = ["mouse", it.get("button", "left"), str(it.get("x", 0)), str(it.get("y", 0)),
                          str(it.get("duration", 0.05)), str(it.get("delay", 0))]
            else:
                kd = it.get("key")
                values = ["key", core.key_display_name(kd), str(it.get("x", "-")), str(it.get("y", "-")),
                          str(it.get("duration", 0.05)), str(it.get("delay", 0))]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if it["type"] == "key" and c == 1 and kd:
                    item.setData(Qt.UserRole, kd)
                self.table.setItem(r, c, item)

    def _save(self):
        if self._overlay:
            self._overlay._close()
        name = self.name_edit.text().strip() or "未命名序列"
        items = []
        for r in range(self.table.rowCount()):
            def cell(c):
                it = self.table.item(r, c)
                return it.text().strip() if it else ""

            def num(c, default):
                try:
                    return float(cell(c))
                except Exception:
                    return default

            type_ = cell(0)
            if type_ not in ("mouse", "key"):
                continue
            if type_ == "mouse":
                items.append({
                    "type": "mouse", "button": cell(1) or "left",
                    "x": int(num(2, 0)), "y": int(num(3, 0)),
                    "duration": num(4, 0.05), "delay": num(5, 0),
                })
            else:
                it = self.table.item(r, 1)
                kd = it.data(Qt.UserRole) if it else None
                if not kd:
                    kd = {"k": "char", "v": "a"}
                item = {"type": "key", "key": kd,
                        "duration": num(4, 0.05), "delay": num(5, 0)}
                x, y = cell(2), cell(3)
                if x not in ("-", "") and y not in ("-", ""):
                    try:
                        item["x"], item["y"] = int(float(x)), int(float(y))
                    except Exception:
                        pass
                items.append(item)
        if not items:
            QMessageBox.warning(self, "提示", "序列为空, 请先添加动作")
            return
        path = self.current_file or str(SEQ_DIR / ("%s.json" % name))
        core.save_json(path, {"name": name, "items": items})
        QMessageBox.information(self, "已保存", "序列已保存到:\n%s" % path)


# ============================================================
# 录制回放面板
# ============================================================
class RecordPanel(QWidget):
    def __init__(self, app_ctx):
        super().__init__()
        self.ctx = app_ctx
        self.recorder = None
        self.replayer = None
        self.progress_timer = QTimer(self)
        self.progress_timer.timeout.connect(self._update_progress)

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
        else:
            self.rec_btn.setText("开始录制 (%s)" % hk(self.ctx, "record", "F6"))

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
        self.recorder = Recorder()
        self.recorder.start()
        self.refresh_labels()
        self.status.setText("● 录制中…")
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
        self.play_btn.setText("停止回放")
        self.status.setText("▶ 回放中…")
        self.ctx.notify("回放已开始 (倍速 %.1fx)" % speed)
        self.progress_timer.start(100)

        def done(interrupted):
            self.progress_timer.stop()
            self.replayer = None
            self.play_btn.setText("回放")
            self.status.setText("已中断" if interrupted else "回放完成")

        self.replayer = Replayer(events, speed=speed, on_done=done)
        self.replayer.start()

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
        self.apply_style()

    def apply_style(self, recording=False):
        opacity = self.ctx.settings.get("opacity", 0.92)
        self.setWindowOpacity(opacity)
        self.header._style(recording)
        self.setStyleSheet("QWidget { font-size: 12px; }")

    def show_panel(self, idx):
        self.stack.setCurrentIndex(idx)
        self.show()
        self.adjustSize()

    # ---- 右键菜单 (标题栏右键由 DragHeader 转发到这里) ----
    def show_menu(self, pos):
        m = QMenu(self)
        act_back = m.addAction("返回主界面(切换功能)")
        m.addSeparator()
        act_panic = m.addAction("急停(停止所有任务)")
        act_save = m.addAction("保存当前配置")
        m.addSeparator()
        act_exit = m.addAction("退出程序")
        chosen = m.exec(pos)
        if chosen == act_back:
            self.ctx.back_to_main()
        elif chosen == act_panic:
            self.ctx.panic_stop()
        elif chosen == act_save:
            self.ctx.save_current_config()
        elif chosen == act_exit:
            self.ctx.quit_app()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.show_menu(e.globalPosition().toPoint())

    # ---- 录制态 ----
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

    # ---- 停止一切 ----
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
            ("自定义输入序列", "编排键鼠动作并循环执行"),
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
        self.info.setText("热键: %s 录制 · %s 结束录制 · %s 连点启停 · %s 急停 (可在全局设置中修改)"
                          % (h["record"].upper(), h["stop_record"].upper(),
                             h["click"].upper(), h["panic"].upper()))

    def _open_settings(self):
        dlg = SettingsDialog(self, self.ctx.settings)
        if dlg.exec() == QDialog.Accepted:
            self.ctx.settings = dlg.values
            save_settings(self.ctx.settings)
            self.ctx.on_settings_changed()


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
                         ("连点启停", "click"), ("急停", "panic")]
        self.hk_combos = {}
        key_names = ["f%d" % i for i in range(1, 13)]
        for i, (label, key) in enumerate(hotkey_labels):
            lay.addWidget(QLabel("%s:" % label), 3 + i, 0)
            combo = QComboBox()
            combo.addItems(key_names)
            combo.setCurrentText(settings.get("hotkeys", {}).get(key, "f%d" % (6 + i)))
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
# 应用上下文 (串联主界面/悬浮窗/热键)
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

    def on_settings_changed(self):
        """设置保存后: 刷新悬浮窗样式、所有面板参数与热键文字、主界面提示"""
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
        # 在悬浮窗头部短暂显示通知(录制态时不覆盖 REC 计时)
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


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(GLOBAL_QSS)
    if "--selftest" in sys.argv:
        ctx = AppContext()
        ctx.main.show()
        ctx.floating.show_panel(0)
        print("SELFTEST OK")
        return 0
    ctx = AppContext()
    ctx.main.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
