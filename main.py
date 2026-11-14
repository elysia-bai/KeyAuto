# -*- coding: utf-8 -*-
"""
main.py - 按键输入自动化工具 (KeyAuto)  v1.2
交互形态: 主界面选择功能 -> 主界面隐藏 -> 悬浮窗设置并运行
悬浮窗右键 -> 返回主界面 / 急停 / 保存配置 / 退出

v1.2 变更:
  1. 序列编辑器改为非模态: 打开时悬浮窗仍可拖动/操作
  2. 序列编辑改为"圆圈"模式: 空白圆点击录入按键, 圆内显示按键;
     新动作的属性(坐标/时长/延时/鼠标按钮)同步出现在悬浮窗, 可即时修改;
     添加先后顺序即执行顺序
  3. 录制器过滤开始/结束热键, 热键不再被录进事件流

v1.1 修复: 全局浅色主题; 悬浮窗标题栏拖动; 高度自适应防重叠; 屏幕圆圈拾取; 热键文字同步
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
    QHBoxLayout, QGridLayout, QStackedWidget, QComboBox, QSpinBox, QDoubleSpinBox,
    QSlider, QRadioButton, QButtonGroup, QLineEdit, QMessageBox, QMenu, QScrollArea,
    QFileDialog, QCheckBox, QGroupBox, QFrame,
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
        tip = QLabel("请按下要录入的按键…（Esc 取消）")
        tip.setWordWrap(True)
        self.show_label = QLabel("等待输入…")
        self.show_label.setAlignment(Qt.AlignCenter)
        self.show_label.setMinimumHeight(60)
        self.show_label.setStyleSheet(
            "border:1px solid #b0bec5; border-radius:6px; font-size:16px; font-weight:bold; color:#1565c0; background:#ffffff;")
        lay.addWidget(tip)
        lay.addWidget(self.show_label)

        self._listener = None
        self._start_capture()

    def _start_capture(self):
        from pynput import keyboard as pkb

        def on_press(key):
            if key == pkb.Key.esc:
                QTimer.singleShot(0, self.reject)
                return False
            s = core.serialize_key(key)
            if s:
                self.captured = s
                self.show_label.setText(core.key_display_name(s))
                QTimer.singleShot(0, self.accept)
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

        tip_text = "移动鼠标定位圆圈 · 左键点击确认坐标 · 右键或 Esc 结束"
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
        if self.mode == "blank" or not self.editor:
            return
        m = QMenu(self)
        if self.mode == "key":
            act_rebind = m.addAction("重新录入按键")
        act_del = m.addAction("删除此动作")
        act_up = m.addAction("左移(提前执行)")
        act_down = m.addAction("右移(延后执行)")
        chosen = m.exec(pos)
        if self.mode == "key" and chosen == act_rebind:
            self.editor.rebind_key(self.index)
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
        add_mouse.clicked.connect(lambda: self.append_item({"type": "mouse", "button": "left",
                                                            "x": 0, "y": 0, "duration": 0.05, "delay": 0.1}))
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
                short = {"left": "左键", "right": "右键", "middle": "中键"}.get(btn_name, btn_name)
                c = StepCircle(short, "mouse", i, self)
            c.setToolTip("第 %d 个动作: %s" % (i + 1, self._item_desc(it)))
            c.apply_style(selected=(i == self._selected))
            c.clicked.connect(lambda _=False, i=i: self.select(i))
            self.flow_lay.addWidget(c)
        blank = StepCircle("+", "blank", -1, self)
        blank.setToolTip("点击录入一个新按键")
        blank.clicked.connect(lambda: self._new_key_circle())
        self.flow_lay.addWidget(blank)
        self.flow_lay.addStretch(1)
        self.count_label.setText("共 %d 个动作" % len(self.items))

    def _item_desc(self, it):
        if it["type"] == "key":
            return "键盘 %s, 按住 %.2fs, 延时 %.2fs" % (
                core.key_display_name(it.get("key")), it.get("duration", 0.05), it.get("delay", 0))
        return "鼠标 %s @(%d,%d), 按住 %.2fs, 延时 %.2fs" % (
            it.get("button", "left"), it.get("x", 0), it.get("y", 0),
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
            self.append_item({"type": "key", "key": dlg.captured,
                              "x": 0, "y": 0, "duration": 0.05, "delay": 0})

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
    def __init__(self, app_ctx):
        super().__init__()
        self.ctx = app_ctx
        self.player = None
        self.editor = None
        self._loading_props = False

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
        pl.addWidget(QLabel("X:"), 1, 0)
        pl.addWidget(self.prop_x, 1, 1)
        pl.addWidget(QLabel("Y:"), 2, 0)
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
        pl.addWidget(QLabel("鼠标按钮:"), 5, 0)
        pl.addWidget(self.prop_btn_combo, 5, 1)
        self.props_group.setVisible(False)
        lay.addWidget(self.props_group, 3, 0, 1, 2)

        # 属性写回
        self.prop_x.valueChanged.connect(lambda v: self._write_prop("x", v))
        self.prop_y.valueChanged.connect(lambda v: self._write_prop("y", v))
        self.prop_duration.valueChanged.connect(lambda v: self._write_prop("duration", v))
        self.prop_delay.valueChanged.connect(lambda v: self._write_prop("delay", v))
        self.prop_btn_combo.currentIndexChanged.connect(
            lambda i: self._write_prop("button", MOUSE_LABELS[i][0]))

        self.toggle_btn = QPushButton("执行")
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
        if it["type"] == "key":
            self.prop_index.setText("第 %d 个 · 键盘 %s" % (n, core.key_display_name(it.get("key"))))
            self.prop_btn_combo.setEnabled(False)
        else:
            self.prop_index.setText("第 %d 个 · 鼠标动作" % n)
            self.prop_btn_combo.setEnabled(True)
            self.prop_btn_combo.setCurrentIndex(
                [b for b, _ in MOUSE_LABELS].index(it.get("button", "left")))
        self.prop_x.setValue(int(it.get("x", 0)))
        self.prop_y.setValue(int(it.get("y", 0)))
        self.prop_duration.setValue(float(it.get("duration", 0.05)))
        self.prop_delay.setValue(float(it.get("delay", 0)))
        self._loading_props = False
        self.props_group.setVisible(True)
        self.ctx.floating.adjustSize()

    def _write_prop(self, field, value):
        if self._loading_props or not self.editor:
            return
        index = self.editor._selected
        if 0 <= index < len(self.editor.items):
            self.editor.set_prop(index, field, value)

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
        self.close_editor()


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


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(GLOBAL_QSS)
    if "--selftest" in sys.argv:
        ctx = AppContext()
        ctx.main.show()
        # 实例化各面板并打开一次编辑器, 验证非模态生命周期
        ctx.floating.show_panel(0)
        ctx.floating.show_panel(2)
        ctx.floating.panel_seq._edit()
        if ctx.floating.panel_seq.editor:
            ctx.floating.panel_seq.editor.append_item(
                {"type": "key", "key": {"k": "char", "v": "a"}, "x": 0, "y": 0,
                 "duration": 0.05, "delay": 0})
            ctx.floating.panel_seq.editor.select(0)
            ctx.floating.panel_seq._write_prop("x", 100)
            assert ctx.floating.panel_seq.editor.items[0]["x"] == 100
        print("SELFTEST OK")
        return 0
    ctx = AppContext()
    ctx.main.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
