# -*- coding: utf-8 -*-
"""
main.py - 按键输入自动化工具 (KeyAuto)
交互形态: 主界面选择功能 -> 主界面隐藏 -> 悬浮窗设置并运行
悬浮窗右键 -> 返回主界面 / 急停 / 保存配置 / 退出
依赖: PySide6, pynput
"""
import sys
import time
import json
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QPoint
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QStackedWidget, QComboBox, QSpinBox, QSlider,
    QRadioButton, QButtonGroup, QLineEdit, QMessageBox, QMenu, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QCheckBox, QFrame, QGroupBox,
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
    "rate_cap": 10,          # 频率上限(默认10, 最大50)
    "opacity": 0.92,         # 悬浮窗不透明度
    "sound": True,           # 提示音
    "hotkeys": {"record": "f6", "stop_record": "f7", "click": "f8", "panic": "f9"},
}

MOUSE_LABELS = [("left", "鼠标左键"), ("right", "鼠标右键"), ("middle", "鼠标中键")]


def load_settings():
    s = core.load_json(CONFIG_FILE, DEFAULT_SETTINGS)
    for k, v in DEFAULT_SETTINGS.items():
        s.setdefault(k, v)
    return s


def save_settings(s):
    core.save_json(CONFIG_FILE, s)


# ============================================================
# 全局热键 (pynput 后台监听 -> Qt 信号)
# ============================================================
class GlobalHotkeys(QWidget):
    fired = Signal(str)  # 参数: record / stop_record / click / panic

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
            for action, hk in hotkey_map.items():
                if name == hk:
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
# 按键捕获对话框 (点击"设置按键"后按任意键完成绑定)
# ============================================================
class CaptureKeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("捕获按键")
        self.setFixedWidth(300)
        self.captured = None
        lay = QVBoxLayout(self)
        tip = QLabel("请在下方区域按下要绑定的按键…")
        tip.setWordWrap(True)
        self.show_label = QLabel("等待输入…")
        self.show_label.setAlignment(Qt.AlignCenter)
        self.show_label.setMinimumHeight(60)
        self.show_label.setStyleSheet("border:1px solid #bbb; border-radius:6px; font-size:16px; font-weight:bold;")
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
                return False  # 停止监听
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

        lay = QGridLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        row = 0

        # 输入类型
        lay.addWidget(QLabel("输入类型:"), row, 0)
        self.type_combo = QComboBox()
        for _, label in MOUSE_LABELS:
            self.type_combo.addItem(label)
        self.type_combo.addItem("键盘按键")
        self.type_combo.currentIndexChanged.connect(self._type_changed)
        lay.addWidget(self.type_combo, row, 1)
        row += 1

        # 键盘键选择
        self.key_btn = QPushButton("设置按键…")
        self.key_btn.clicked.connect(self._capture_key)
        self.key_btn.setVisible(False)
        lay.addWidget(self.key_btn, row, 1)
        self.key_label = QLabel("")
        self.key_label.setVisible(False)
        lay.addWidget(self.key_label, row, 0)
        row += 1

        # 频率
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

        # 位置模式
        pos_group = QGroupBox("点击位置")
        pl = QVBoxLayout(pos_group)
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
        pl.addLayout(pick_row)
        self.radio_follow.setChecked(True)
        self.radio_fixed.toggled.connect(lambda on: self.pick_btn.setEnabled(on))
        self.pick_btn.setEnabled(False)
        lay.addWidget(pos_group, row, 0, 1, 2)
        row += 1

        # 启停
        self.toggle_btn = QPushButton("启动 (F8)")
        self.toggle_btn.setMinimumHeight(34)
        self.toggle_btn.setStyleSheet("QPushButton{background:#2e7d32;color:white;border-radius:6px;font-weight:bold;}"
                                      "QPushButton:checked{background:#c62828;}")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.clicked.connect(self.toggle)
        lay.addWidget(self.toggle_btn, row, 0, 1, 2)

        self._pick_countdown = None
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

    def _pick_pos(self):
        """3 秒倒计时后捕获当前鼠标位置(悬浮窗先隐藏避免遮挡)"""
        if self._pick_countdown:
            return
        floating = self.window()
        floating.hide()
        count = [3]
        self._pick_countdown = QTimer(self)
        self._pick_countdown.setInterval(1000)

        def step():
            if count[0] > 0:
                count[0] -= 1
            else:
                self._pick_countdown.stop()
                self._pick_countdown = None
                pos = core._mouse.position
                self.pos_label.setText("X:%d  Y:%d" % (int(pos[0]), int(pos[1])))
                self.radio_fixed.setChecked(True)
                floating.show()

        step()
        self._pick_countdown.timeout.connect(step)
        self._pick_countdown.start()

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
        self.toggle_btn.setText("停止 (F8)")
        interval = 1000.0 / self.rate.value()
        if self.random_mode:
            interval = random_interval_ms(self.rate_lo.value(), self.rate_hi.value())
        self.timer.start(int(max(1, interval)))
        self.ctx.notify("连点任务已启动")

    def stop(self):
        self.running = False
        self.timer.stop()
        self.toggle_btn.setChecked(False)
        self.toggle_btn.setText("启动 (F8)")

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
        lay.setContentsMargins(10, 8, 10, 8)

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
        self.toggle_btn.setMinimumHeight(34)
        self.toggle_btn.setStyleSheet("QPushButton{background:#2e7d32;color:white;border-radius:6px;font-weight:bold;}"
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
    """序列编辑器: 表格增删改 + 上下移动 + 保存"""

    COLUMNS = ["类型", "按键/按钮", "X", "Y", "按下时长(s)", "延时(s)"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑输入序列")
        self.resize(560, 400)
        self.current_file = None

        lay = QVBoxLayout(self)
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
        lay.addWidget(self.table)

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
        bottom.addWidget(save_btn)
        bottom.addWidget(close_btn)
        lay.addLayout(bottom)

    def _add_row(self, type_):
        r = self.table.rowCount()
        self.table.insertRow(r)
        if type_ == "mouse":
            values = ["mouse", "left", "0", "0", "0.05", "0.1"]
        else:
            values = ["key", "(未设置)", "-", "-", "0.05", "0"]
        for c, v in enumerate(values):
            self.table.setItem(r, c, QTableWidgetItem(v))

    def _edit_key_cell(self, row):
        dlg = CaptureKeyDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.captured:
            self.table.setItem(row, 1, QTableWidgetItem(core.key_display_name(dlg.captured)))
            # 序列化后暂存到行数据
            self.table.item(row, 1).setData(Qt.UserRole, dlg.captured)

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
                values = ["key", core.key_display_name(kd), "-", "-",
                          str(it.get("duration", 0.05)), str(it.get("delay", 0))]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if it["type"] == "key" and c == 1 and kd:
                    item.setData(Qt.UserRole, kd)
                self.table.setItem(r, c, item)

    def _save(self):
        name = self.name_edit.text().strip() or "未命名序列"
        items = []
        for r in range(self.table.rowCount()):
            def cell(c):
                it = self.table.item(r, c)
                return it.text().strip() if it else ""

            type_ = cell(0)
            if type_ not in ("mouse", "key"):
                continue
            def num(c, default):
                try:
                    return float(cell(c))
                except Exception:
                    return default
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
                items.append({
                    "type": "key", "key": kd,
                    "duration": num(4, 0.05), "delay": num(5, 0),
                })
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
        lay.setContentsMargins(10, 8, 10, 8)

        lay.addWidget(QLabel("录制文件:"), 0, 0)
        self.file_combo = QComboBox()
        lay.addWidget(self.file_combo, 0, 1)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("新录制保存名称")
        lay.addWidget(self.name_edit, 1, 1)

        self.rec_btn = QPushButton("开始录制 (F6)")
        self.rec_btn.setMinimumHeight(32)
        self.rec_btn.setStyleSheet("QPushButton{background:#b71c1c;color:white;border-radius:6px;font-weight:bold;}")
        self.rec_btn.clicked.connect(self.toggle_record)
        lay.addWidget(self.rec_btn, 2, 0, 1, 2)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("回放倍速:"))
        self.speed_combo = QComboBox()
        for s, label in [(2.0, "2.0x"), (1.0, "1.0x"), (0.5, "0.5x")]:
            self.speed_combo.addItem(label, s)
        self.speed_combo.setCurrentIndex(1)
        speed_row.addWidget(self.speed_combo)
        self.play_btn = QPushButton("回放")
        self.play_btn.clicked.connect(self._play)
        self.play_btn.setMinimumHeight(30)
        self.play_btn.setStyleSheet("QPushButton{background:#1565c0;color:white;border-radius:6px;}")
        speed_row.addWidget(self.play_btn)
        lay.addLayout(speed_row, 3, 0, 1, 2)

        self.status = QLabel("空闲")
        lay.addWidget(self.status, 4, 0, 1, 2)
        self.reload()

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
        self.rec_btn.setText("结束录制 (F6/F7)")
        self.status.setText("● 录制中…")
        self.ctx.enter_rec_state()
        if self.ctx.settings.get("sound", True):
            beep("start")
        self.ctx.notify("录制已开始")

    def stop_record(self, save=True):
        if not (self.recorder and self.recorder.recording):
            return
        events = self.recorder.stop()
        summary = self.recorder.summary()
        self.name_edit.setEnabled(True)
        self.rec_btn.setText("开始录制 (F6)")
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
# 悬浮窗
# ============================================================
class FloatingWindow(QWidget):
    def __init__(self, app_ctx):
        super().__init__(None,
                         Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.ctx = app_ctx
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(280, 210)
        self.setWindowTitle("KeyAuto")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.header = QLabel("KeyAuto · 悬浮窗")
        self.header.setAlignment(Qt.AlignCenter)
        self.header.setObjectName("header")
        outer.addWidget(self.header)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        self.panel_fixed = ClickPanel(app_ctx, random_mode=False)
        self.panel_random = ClickPanel(app_ctx, random_mode=True)
        self.panel_seq = SequencePanel(app_ctx)
        self.panel_rec = RecordPanel(app_ctx)
        for p in (self.panel_fixed, self.panel_random, self.panel_seq, self.panel_rec):
            self.stack.addWidget(p)

        self.apply_style()
        self._drag_pos = None

    def apply_style(self, recording=False):
        opacity = self.ctx.settings.get("opacity", 0.92)
        self.setWindowOpacity(opacity)
        if recording:
            self.header.setText("● REC 录制中 — 00:00")
            self.header.setStyleSheet(
                "background:#c62828;color:white;padding:6px;font-weight:bold;font-size:13px;")
        else:
            self.header.setText("KeyAuto · 悬浮窗 (右键菜单)")
            self.header.setStyleSheet(
                "background:#37474f;color:white;padding:6px;font-weight:bold;font-size:13px;")
        self.setStyleSheet("""
            QWidget { font-size: 12px; }
            QFrame#body { background: white; }
            QGroupBox { border:1px solid #cfd8dc; border-radius:6px; margin-top:8px; }
            QPushButton { padding:4px 8px; border:1px solid #90a4ae; border-radius:4px; background:white; }
            QPushButton:hover { background:#eceff1; }
            QComboBox, QSpinBox, QLineEdit { padding:3px; border:1px solid #90a4ae; border-radius:4px; }
        """)

    def show_panel(self, idx):
        self.stack.setCurrentIndex(idx)
        self.show()
        self.activateWindow()

    # ---- 拖动 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        elif e.button() == Qt.RightButton:
            self._menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = None

    # ---- 右键菜单 ----
    def _menu(self, pos):
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

    # ---- 录制态 ----
    def enter_rec_state(self):
        self.apply_style(recording=True)
        self._rec_t0 = time.time()
        self._rec_timer = QTimer(self)
        self._rec_timer.timeout.connect(self._rec_tick)
        self._rec_timer.start(1000)

    def exit_rec_state(self):
        if getattr(self, "_rec_timer", None):
            self._rec_timer.stop()
            self._rec_timer = None
        self.apply_style(recording=False)

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
        self.setFixedWidth(420)
        self.setFixedHeight(430)

        central = QWidget()
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(18, 18, 18, 18)

        title = QLabel("选择一个功能")
        title.setStyleSheet("font-size:16px;font-weight:bold;")
        lay.addWidget(title)
        tip = QLabel("选择后主界面隐藏, 悬浮窗出现; 右键悬浮窗可返回此界面")
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
        for i, (name, desc) in enumerate(cards):
            btn = QPushButton("%s\n%s" % (name, desc))
            btn.setMinimumHeight(70)
            btn.setStyleSheet(
                "QPushButton{text-align:left;padding:10px;font-size:13px;"
                "background:#eceff1;border:1px solid #b0bec5;border-radius:8px;}"
                "QPushButton:hover{background:#e3f2fd;border-color:#64b5f6;}")
            btn.clicked.connect(lambda _=False, idx=i: self.ctx.select_function(idx))
            grid.addWidget(btn, i // 2, i % 2)
        lay.addLayout(grid)

        settings_btn = QPushButton("全局设置")
        settings_btn.clicked.connect(self._open_settings)
        lay.addWidget(settings_btn)

        info = QLabel("热键: F6 录制 · F7 结束录制 · F8 连点启停 · F9 急停")
        info.setStyleSheet("color:#90a4ae;font-size:11px;")
        info.setWordWrap(True)
        lay.addWidget(info)

    def _open_settings(self):
        dlg = SettingsDialog(self, self.ctx.settings)
        if dlg.exec() == QDialog.Accepted:
            self.ctx.settings = dlg.values
            save_settings(self.ctx.settings)
            self.ctx.floating.apply_style()
            for i in range(self.ctx.floating.stack.count()):
                p = self.ctx.floating.stack.widget(i)
                if hasattr(p, "apply_settings"):
                    p.apply_settings()
            self.ctx.hotkeys.start(self.ctx.settings["hotkeys"])


class SettingsDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("全局设置")
        self.setFixedWidth(340)
        self.values = dict(settings)

        lay = QGridLayout(self)
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
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row, 7 + len(hotkey_labels), 0, 1, 2)

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
        original = "KeyAuto · 悬浮窗 (右键菜单)"
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
    if "--selftest" in sys.argv:
        # 冒烟测试: 仅实例化所有窗口, 不进入事件循环
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
