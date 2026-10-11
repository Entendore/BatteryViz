import sys
import math
import random
from collections import deque
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QFont, QLinearGradient, 
                           QRadialGradient, QPainterPath)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QPushButton, QComboBox, QSlider, 
                               QLabel, QGroupBox, QTextEdit, QTabWidget, QButtonGroup)

STYLESHEET = """
QMainWindow { background-color: #0f1115; }
QGroupBox { 
    border: 1px solid #2a2e36; border-radius: 8px; margin-top: 12px; 
    padding-top: 12px; color: #aab2bd; font-weight: bold; 
    background-color: #16181d;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QPushButton { 
    background-color: #232730; border: 1px solid #3a4049; border-radius: 4px; 
    padding: 8px; color: #e0e6ed; font-weight: bold;
}
QPushButton:hover { background-color: #2d333e; border: 1px solid #4a525e; }
QPushButton:pressed { background-color: #1a1d23; }
QPushButton:checked { background-color: #0d47a1; border: 1px solid #2196f3; color: white; }
QSlider::groove:horizontal { border: 1px solid #2a2e36; height: 6px; background: #1a1d23; border-radius: 3px; }
QSlider::handle:horizontal { background: #2196f3; border: 2px solid #0d47a1; width: 12px; height: 12px; margin: -5px 0; border-radius: 9px; }
QSlider::handle:horizontal:hover { background: #64b5f6; }
QComboBox { background-color: #232730; border: 1px solid #3a4049; padding: 6px; color: #e0e6ed; border-radius: 4px; }
QComboBox::drop-down { border: none; width: 20px; }
QTextEdit, QLabel { background-color: transparent; color: #cfd5dd; border: none; }
QTabWidget::pane { border: 1px solid #2a2e36; background: #121418; }
QTabBar::tab { background: #1a1d23; color: #888; padding: 8px 20px; border: 1px solid #2a2e36; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; }
QTabBar::tab:selected { background: #121418; color: #fff; border-color: #2196f3; }
"""

class Battery:
    def __init__(self, name, chemistry, nominal_v, max_v, min_v, capacity_mah, 
                 rechargeable, internal_resistance_ohms, degradation_rate, thermal_limit, 
                 color_hex, info_html):
        self.name = name
        self.chemistry = chemistry
        self.nominal_v = nominal_v
        self.max_v = max_v
        self.min_v = min_v
        
        self.design_capacity_mah = capacity_mah
        self.current_capacity_mah = capacity_mah * 0.5
        
        self.rechargeable = rechargeable
        self.internal_resistance = internal_resistance_ohms
        self.degradation_rate = degradation_rate
        self.thermal_limit = thermal_limit
        self.color_hex = color_hex
        self.info_html = info_html
        
        self.soh = 1.0
        self.cycle_count = 0
        self.temperature_c = 25.0
        self.total_energy_wh = 0.0
        
        self.is_charging = False
        self.is_discharging = False
        self.charge_target_ma = 0
        self.discharge_target_ma = 0
        
        self.actual_current_ma = 0
        self.charge_phase = "Idle"
        
        self.last_soc_for_cycle = self.get_soc()
        self.safety_shutdown = False

    def get_soc(self):
        actual_max = self.design_capacity_mah * self.soh
        return self.current_capacity_mah / actual_max if actual_max > 0 else 0

    def get_open_circuit_voltage(self, soc=None):
        if soc is None: soc = self.get_soc()
        if soc > 0.9:
            return self.nominal_v + (self.max_v - self.nominal_v) * ((soc - 0.9) / 0.1)
        elif soc > 0.15:
            return self.min_v + (self.nominal_v - self.min_v) * (0.6 + (soc - 0.15) * 0.4)
        else:
            return self.min_v * (soc / 0.15)

    def get_terminal_voltage(self, soc=None, current_ma=None):
        if soc is None: soc = self.get_soc()
        if current_ma is None: current_ma = self.actual_current_ma
        v_oc = self.get_open_circuit_voltage(soc)
        if self.is_discharging:
            return v_oc - (current_ma / 1000.0) * self.internal_resistance
        elif self.is_charging:
            return v_oc + (current_ma / 1000.0) * self.internal_resistance
        return v_oc

    def get_power_watts(self):
        return (self.get_terminal_voltage() * self.actual_current_ma) / 1000.0

    def get_load_resistance(self):
        if self.actual_current_ma > 0 and self.is_discharging:
            return self.get_terminal_voltage() / (self.actual_current_ma / 1000.0)
        return 0.0

    def update(self, dt_seconds):
        self.current_capacity_mah -= self.design_capacity_mah * 0.000003 * dt_seconds * 3600
        actual_max = self.design_capacity_mah * self.soh
        heat_generated = 0
        self.actual_current_ma = 0

        if self.temperature_c >= self.thermal_limit and not self.safety_shutdown:
            self.safety_shutdown = True
            self.is_charging = False
            self.is_discharging = False
        elif self.temperature_c <= 40.0 and self.safety_shutdown:
            self.safety_shutdown = False

        if not self.safety_shutdown:
            if self.is_charging and self.rechargeable:
                v_oc = self.get_open_circuit_voltage()
                if v_oc < self.max_v * 0.95:
                    self.charge_phase = "CC (Constant Current)"
                    self.actual_current_ma = self.charge_target_ma
                else:
                    self.charge_phase = "CV (Constant Voltage)"
                    taper_factor = max(0, (self.max_v - v_oc) / (self.max_v * 0.05))
                    self.actual_current_ma = max(50, self.charge_target_ma * taper_factor)

                self.current_capacity_mah = min(actual_max, self.current_capacity_mah + (self.actual_current_ma * dt_seconds / 3600))
                heat_generated = (self.actual_current_ma / 1000.0)**2 * self.internal_resistance * 0.05
                self.total_energy_wh += self.get_power_watts() * (dt_seconds / 3600)
                
                if self.current_capacity_mah >= actual_max:
                    self.is_charging = False
                    self.charge_phase = "Full"
                    self.actual_current_ma = 0
                    
            elif self.is_discharging:
                self.charge_phase = "Discharging"
                peukert_factor = 1.0 + (self.discharge_target_ma / 2000.0)
                self.actual_current_ma = self.discharge_target_ma * peukert_factor
                
                self.current_capacity_mah = max(0, self.current_capacity_mah - (self.actual_current_ma * dt_seconds / 3600))
                heat_generated = (self.actual_current_ma / 1000.0)**2 * self.internal_resistance * 0.05
                self.total_energy_wh += self.get_power_watts() * (dt_seconds / 3600)
                
                if self.current_capacity_mah <= 0:
                    self.is_discharging = False
                    self.charge_phase = "Empty"
                    self.actual_current_ma = 0
            else:
                self.charge_phase = "Idle"

        current_soc = self.get_soc()
        if self.last_soc_for_cycle <= 0.05 and current_soc > 0.1:
            self.cycle_count += 1
            self.soh = max(0.5, self.soh - self.degradation_rate)
        self.last_soc_for_cycle = current_soc

        ambient_temp = 25.0
        cooling_rate = 0.3 if self.safety_shutdown else 0.1
        self.temperature_c += heat_generated * dt_seconds * 10
        self.temperature_c -= (self.temperature_c - ambient_temp) * dt_seconds * cooling_rate
        self.temperature_c = max(20.0, self.temperature_c)


class GraphWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(140)
        self.history = {'voltage': deque(maxlen=150), 'current': deque(maxlen=150), 'temp': deque(maxlen=150)}
        self.max_v = 5.0; self.max_i = 2000.0; self.max_t = 160.0

    def add_data(self, v, i, t):
        self.history['voltage'].append(v); self.history['current'].append(i); self.history['temp'].append(t)
        self.update()

    def draw_line(self, painter, data, color, max_val, fill=False):
        if len(data) < 2: return
        w, h = self.width(), self.height()
        painter.setPen(QPen(color, 2))
        points = [QPointF((i / (len(data)-1)) * w, h - (val / max_val) * h) for i, val in enumerate(data)]
        if fill:
            poly = points + [QPointF(points[-1].x(), h), QPointF(points[0].x(), h)]
            painter.setBrush(QBrush(color)); painter.setPen(Qt.NoPen); painter.drawPolygon(poly)
        else: painter.drawPolyline(points)

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(15, 17, 21))
        painter.setPen(QPen(QColor(35, 40, 49), 1, Qt.DashLine))
        for i in range(1, 5):
            painter.drawLine(0, int((self.height() / 5) * i), self.width(), int((self.height() / 5) * i))
            painter.drawLine(int((self.width() / 6) * i), 0, int((self.width() / 6) * i), self.height())
        self.draw_line(painter, self.history['voltage'], QColor(50, 200, 255, 40), self.max_v, fill=True)
        self.draw_line(painter, self.history['current'], QColor(255, 200, 50, 40), self.max_i, fill=True)
        self.draw_line(painter, self.history['temp'], QColor(255, 50, 50, 40), self.max_t, fill=True)
        self.draw_line(painter, self.history['voltage'], QColor(50, 200, 255), self.max_v)
        self.draw_line(painter, self.history['current'], QColor(255, 200, 50), self.max_i)
        self.draw_line(painter, self.history['temp'], QColor(255, 50, 50), self.max_t)
        painter.setPen(QPen(QColor(150, 150, 150))); painter.setFont(QFont("Arial", 8))
        painter.drawText(10, 15, f"V (Max {self.max_v:.1f}V)"); painter.drawText(10, 30, f"I (Max {self.max_i:.0f}mA)"); painter.drawText(10, 45, f"T (Max {self.max_t:.0f}°C)")

class PhysicalView(QWidget):
    def __init__(self, battery):
        super().__init__(); self.battery = battery; self.ions = []; self.external_phase = 0.0
        self.init_ions(); self.setMinimumSize(500, 400)

    def init_ions(self):
        self.ions = []
        for lane in range(5):
            for _ in range(8): self.ions.append({'x': random.uniform(0.05, 0.95), 'lane': lane, 'speed': random.uniform(0.8, 1.2)})

    def update_battery(self, battery): self.battery = battery; self.init_ions()

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(10, 12, 16))
        
        # Drop Shadow
        shadow_grad = QRadialGradient(w/2, h/2 + 140, 120)
        shadow_grad.setColorAt(0, QColor(0, 0, 0, 120)); shadow_grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(shadow_grad)); p.setPen(Qt.NoPen); p.drawEllipse(QPointF(w/2, h/2 + 140), 120, 30)

        bat_w, bat_h = 160, 260; bat_x, bat_y = (w / 2) - bat_w - 40, (h - bat_h) / 2

        grad = QLinearGradient(bat_x, 0, bat_x + bat_w, 0)
        base = QColor(self.battery.color_hex); temp_factor = max(0, (self.battery.temperature_c - 25) / 50.0)
        r = int(base.red() + temp_factor * (255 - base.red())); g = int(base.green() - temp_factor * base.green() * 0.5); b = int(base.blue() - temp_factor * base.blue() * 0.8)
        grad.setColorAt(0.0, QColor(r//2, g//2, b//2)); grad.setColorAt(0.3, QColor(r, g, b)); grad.setColorAt(0.5, QColor(min(255, r+60), min(255, g+60), min(255, b+60))); grad.setColorAt(0.7, QColor(r, g, b)); grad.setColorAt(1.0, QColor(r//2, g//2, b//2))
        p.setBrush(QBrush(grad)); p.setPen(QPen(QColor(10, 10, 10), 2)); p.drawRoundedRect(int(bat_x), int(bat_y), int(bat_w), int(bat_h), 15, 15)

        term_grad = QLinearGradient(bat_x + bat_w/2 - 30, 0, bat_x + bat_w/2 + 30, 0)
        term_grad.setColorAt(0, QColor(80, 80, 80)); term_grad.setColorAt(0.5, QColor(220, 220, 220)); term_grad.setColorAt(1, QColor(80, 80, 80))
        p.setBrush(QBrush(term_grad)); p.drawRect(int(bat_x + bat_w/2 - 30), int(bat_y - 15), 60, 15)

        inner_x, inner_y, inner_w, inner_h = bat_x + 12, bat_y + 12, bat_w - 24, bat_h - 24
        p.setBrush(QBrush(QColor(10, 10, 10, 200))); p.drawRect(int(inner_x), int(inner_y), int(inner_w), int(inner_h))

        # Structural Layers
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(200, 50, 50, 50))); p.drawRect(int(inner_x), int(inner_y), int(inner_w * 0.4), int(inner_h))
        p.setBrush(QBrush(QColor(50, 50, 200, 50))); p.drawRect(int(inner_x + inner_w * 0.6), int(inner_y), int(inner_w * 0.4), int(inner_h))
        p.setPen(QPen(QColor(150, 150, 150, 100), 1, Qt.DashLine))
        p.drawLine(int(inner_x + inner_w * 0.4), int(inner_y), int(inner_x + inner_w * 0.4), int(inner_y + inner_h))
        p.drawLine(int(inner_x + inner_w * 0.6), int(inner_y), int(inner_x + inner_w * 0.6), int(inner_y + inner_h))
        
        # Labels
        p.setPen(QPen(QColor(255, 100, 100, 150))); p.setFont(QFont("Arial", 8))
        p.drawText(QRectF(inner_x, inner_y + 10, inner_w * 0.4, 20), Qt.AlignCenter, "Anode")
        p.setPen(QPen(QColor(100, 100, 255, 150)))
        p.drawText(QRectF(inner_x + inner_w * 0.6, inner_y + 10, inner_w * 0.4, 20), Qt.AlignCenter, "Cathode")

        soc = self.battery.get_soc(); fluid_h = int(inner_h * soc); fluid_y = inner_y + inner_h - fluid_h
        fluid_grad = QLinearGradient(0, fluid_y, 0, fluid_y + fluid_h)
        fluid_grad.setColorAt(0, QColor(min(255, r+40), min(255, g+40), min(255, b+40), 150)); fluid_grad.setColorAt(1, QColor(r, g, b, 150))
        p.setBrush(QBrush(fluid_grad)); p.setPen(Qt.NoPen); p.drawRect(int(inner_x), int(fluid_y), int(inner_w), int(fluid_h))

        direction = 1 if self.battery.is_discharging else -1 if self.battery.is_charging else 0
        for ion in self.ions:
            if direction != 0:
                ion['x'] += ion['speed'] * direction * 0.015
                if ion['x'] > 1.0: ion['x'] = 0.0
                if ion['x'] < 0.0: ion['x'] = 1.0
            px, py = inner_x + ion['x'] * inner_w, inner_y + (ion['lane'] + 0.5) * (inner_h / 5.0)
            ion_grad = QRadialGradient(px, py, 6)
            ion_grad.setColorAt(0, QColor(255, 255, 255, 255)); ion_grad.setColorAt(0.5, QColor(150, 200, 255, 150)); ion_grad.setColorAt(1, QColor(0, 0, 0, 0))
            p.setBrush(QBrush(ion_grad)); p.drawEllipse(QPointF(px, py), 6, 6)

        term_x, term_y = bat_x + bat_w / 2, bat_y - 15
        wire_top_y, bulb_x, bulb_y, bottom_wire_y = bat_y - 70, bat_x + bat_w + 120, bat_y + bat_h / 2, bat_y + bat_h
        path = QPainterPath(); path.moveTo(term_x, term_y); path.lineTo(term_x, wire_top_y); path.lineTo(bulb_x, wire_top_y); path.lineTo(bulb_x, bulb_y); path.lineTo(bulb_x, bottom_wire_y); path.lineTo(term_x, bottom_wire_y); path.lineTo(term_x, bat_y + bat_h)
        
        if self.battery.actual_current_ma > 0:
            p.setPen(QPen(QColor(50, 200, 50, 50), 8, Qt.SolidLine, Qt.RoundCap)); p.drawPath(path) # Glow
        p.setPen(QPen(QColor(80, 80, 80), 5, Qt.RoundCap)); p.drawPath(path)

        if self.battery.is_discharging:
            brightness = max(0.2, self.battery.actual_current_ma / 2000.0)
            glow_grad = QRadialGradient(bulb_x, bulb_y, 80)
            glow_grad.setColorAt(0, QColor(255, 255, 200, int(150 * brightness))); glow_grad.setColorAt(0.3, QColor(255, 255, 100, int(80 * brightness))); glow_grad.setColorAt(1, QColor(255, 255, 0, 0))
            p.setBrush(QBrush(glow_grad)); p.setPen(Qt.NoPen); p.drawEllipse(QPointF(bulb_x, bulb_y), 80, 80)
            bulb_grad = QRadialGradient(bulb_x-5, bulb_y-5, 20); bulb_grad.setColorAt(0, QColor(255, 255, 255)); bulb_grad.setColorAt(1, QColor(255, 215, 0))
            p.setBrush(QBrush(bulb_grad)); p.setPen(QPen(QColor(50, 50, 50), 2)); p.drawEllipse(QPointF(bulb_x, bulb_y), 20, 20)
        elif self.battery.is_charging:
            p.setBrush(QBrush(QColor(40, 40, 50))); p.setPen(QPen(QColor(100, 100, 100), 2)); p.drawRoundedRect(QRectF(bulb_x - 25, bulb_y - 30, 50, 60), 5, 5)
            p.setPen(QPen(QColor(255, 255, 0), 2)); p.setFont(QFont("Arial", 10, QFont.Bold)); p.drawText(QRectF(bulb_x - 25, bulb_y - 10, 50, 20), Qt.AlignCenter, "AC")

        if self.battery.actual_current_ma > 0:
            ext_dir = -1 if self.battery.is_discharging else 1
            offset = (self.external_phase * 12 * ext_dir) % 16
            pen = QPen(QColor(50, 200, 50), 4, Qt.CustomDashLine); pen.setDashPattern([2, 14]); pen.setDashOffset(offset)
            p.setPen(pen); p.setBrush(Qt.NoBrush); p.drawPath(path)

        p.setPen(QPen(QColor(255, 255, 255))); font = QFont("Arial", 16, QFont.Bold); p.setFont(font)
        p.drawText(QRectF(bat_x, bat_y, bat_w, bat_h), Qt.AlignCenter, f"{int(soc*100)}%")

class SchematicView(QWidget):
    def __init__(self, battery):
        super().__init__(); self.battery = battery; self.external_phase = 0.0; self.setMinimumSize(500, 400)

    def update_battery(self, battery): self.battery = battery

    def draw_resistor(self, p, x, y, w, h, label="", value=""):
        path = QPainterPath(); path.moveTo(x, y + h/2); step = w / 6
        for i in range(6): path.lineTo(x + step*(i+1), y + h/2 - (h/2 if i%2==0 else -h/2))
        path.lineTo(x + w, y + h/2)
        p.drawPath(path)
        if label: p.setPen(QPen(QColor(255, 100, 100), 1)); p.setFont(QFont("Courier", 8)); p.drawText(QRectF(x, y - 15, w, 10), Qt.AlignCenter, label)
        if value: p.drawText(QRectF(x, y + h, w, 10), Qt.AlignCenter, value)

    def draw_ground(self, p, x, y):
        p.setPen(QPen(QColor(0, 255, 255), 2))
        p.drawLine(x, y, x, y+10)
        p.drawLine(x-10, y+10, x+10, y+10)
        p.drawLine(x-7, y+14, x+7, y+14)
        p.drawLine(x-4, y+18, x+4, y+18)

    def draw_mosfet(self, p, x, y, state):
        # state: "ON" or "OFF"
        p.setPen(QPen(QColor(0, 255, 255), 2))
        # Drain and Source lines
        p.drawLine(x, y-20, x, y-10)
        p.drawLine(x, y+10, x, y+20)
        # Channel
        if state == "ON":
            p.drawLine(x-5, y-10, x-5, y+10)
        else:
            p.drawLine(x-5, y-10, x-5, y-2)
            p.drawLine(x-5, y+2, x-5, y+10)
        # Gate
        p.drawLine(x-15, y-10, x-15, y+10)
        p.drawLine(x-15, y, x-25, y)
        
        if state == "OFF":
            p.setPen(QPen(QColor(255, 50, 50), 2)); p.setFont(QFont("Arial", 8, QFont.Bold))
            p.drawText(QRectF(x-40, y-30, 80, 15), Qt.AlignCenter, "BMS CUTOFF")
            p.setPen(QPen(QColor(0, 255, 255), 2))

    def draw_diode(self, p, x, y, direction=1):
        # direction 1: points right, -1: points left
        p.setPen(QPen(QColor(0, 255, 255), 2))
        p.drawLine(x-10, y, x+10, y)
        if direction == 1:
            p.setBrush(QBrush(QColor(0, 255, 255)))
            p.drawPolygon(QPointF(x, y-8), QPointF(x+8, y), QPointF(x, y+8))
            p.drawLine(x+8, y-8, x+8, y+8)
        else:
            p.setBrush(QBrush(QColor(0, 255, 255)))
            p.drawPolygon(QPointF(x, y-8), QPointF(x-8, y), QPointF(x, y+8))
            p.drawLine(x-8, y-8, x-8, y+8)

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(8, 20, 35)) # Blueprint bg
        p.setPen(QPen(QColor(20, 40, 60), 1))
        for x in range(0, w, 20): p.drawLine(x, 0, x, h)
        for y in range(0, h, 20): p.drawLine(0, y, w, y)
        p.setPen(QPen(QColor(30, 60, 90), 1, Qt.DashLine))
        for x in range(0, w, 100): p.drawLine(x, 0, x, h)
        for y in range(0, h, 100): p.drawLine(0, y, w, y)

        line_pen = QPen(QColor(0, 255, 255), 2); p.setPen(line_pen); p.setBrush(Qt.NoBrush)
        cx, cy = w/2, h/2
        bat_x, wire_top, wire_bot = cx - 180, cy - 120, cy + 120
        comp_x = cx + 140

        # 1. Battery Cell & Internal R
        p.drawLine(bat_x, cy, bat_x + 20, cy) 
        self.draw_resistor(p, bat_x + 20, cy - 10, 40, 20, "R_int", f"{self.battery.internal_resistance}Ω")
        p.drawLine(bat_x + 60, cy, bat_x + 80, cy)
        p.drawLine(bat_x + 80, cy - 20, bat_x + 80, cy) 
        p.drawLine(bat_x + 75, cy - 20, bat_x + 85, cy - 20) # Long line (+)
        p.drawLine(bat_x + 78, cy + 5, bat_x + 82, cy + 5)   # Short line (-)
        
        # 2. BMS MOSFET
        mosfet_x = bat_x + 80
        p.drawLine(mosfet_x, cy - 20, mosfet_x, wire_top + 20)
        self.draw_mosfet(p, mosfet_x, wire_top + 30, "ON" if not self.battery.safety_shutdown else "OFF")
        p.drawLine(mosfet_x, wire_top + 40, mosfet_x, wire_top)

        # 3. Wires & Ground
        p.drawLine(mosfet_x, wire_top, comp_x, wire_top) 
        p.drawLine(comp_x, wire_bot, bat_x, wire_bot) 
        p.drawLine(bat_x, wire_bot, bat_x, cy + 5)
        self.draw_ground(p, bat_x, wire_bot + 10)

        # 4. Voltmeter
        p.drawLine(comp_x - 60, wire_top, comp_x - 60, wire_top - 30)
        p.drawLine(mosfet_x, wire_top, comp_x - 60, wire_top) # tap for V
        p.drawEllipse(QPointF(comp_x - 60, wire_top - 30), 18, 18)
        p.setPen(QPen(QColor(255, 255, 0), 1)); p.setFont(QFont("Courier", 9, QFont.Bold))
        p.drawText(QRectF(comp_x - 78, wire_top - 48, 36, 36), Qt.AlignCenter, f"V\n{self.battery.get_terminal_voltage():.2f}")
        p.setPen(line_pen)

        # 5. Ammeter
        ammeter_x = comp_x - 35
        p.drawLine(comp_x - 60, wire_top, ammeter_x, wire_top)
        p.drawLine(ammeter_x + 30, wire_top, comp_x, wire_top)
        p.drawEllipse(QPointF(ammeter_x + 15, wire_top), 15, 15)
        p.setPen(QPen(QColor(255, 255, 0), 1))
        p.drawText(QRectF(ammeter_x, wire_top - 15, 30, 30), Qt.AlignCenter, f"A\n{self.battery.actual_current_ma:.0f}m")
        p.setPen(line_pen)

        # 6. Component (Load or PSU + Diode)
        if self.battery.is_charging:
            self.draw_diode(p, comp_x - 80, wire_top, 1) # Blocking diode
            p.drawEllipse(QRectF(comp_x - 30, wire_top, 60, wire_bot - wire_top))
            p.setPen(QPen(QColor(255, 255, 0), 2)); p.setFont(QFont("Arial", 14, QFont.Bold))
            p.drawText(QRectF(comp_x - 30, wire_top, 60, wire_bot - wire_top), Qt.AlignCenter, "PSU")
        else:
            self.draw_resistor(p, comp_x - 20, wire_top, 40, wire_bot - wire_top, "R_load", f"{self.battery.get_load_resistance():.2f}Ω")

        # 7. Wire Labels
        p.setPen(QPen(QColor(200, 200, 200), 1)); p.setFont(QFont("Courier", 8))
        p.drawText(QRectF(mosfet_x + 20, wire_top - 20, 100, 15), Qt.AlignLeft, f"I = {self.battery.actual_current_ma:.0f} mA")
        p.drawText(QRectF(comp_x + 50, cy - 10, 100, 15), Qt.AlignLeft, f"V_node = {self.battery.get_terminal_voltage():.2f} V")

        # 8. Electron flow
        if self.battery.actual_current_ma > 0:
            ext_dir = -1 if self.battery.is_discharging else 1
            offset = (self.external_phase * 12 * ext_dir) % 16
            pen = QPen(QColor(0, 255, 100), 3, Qt.CustomDashLine); pen.setDashPattern([2, 14]); pen.setDashOffset(offset)
            p.setPen(pen)
            path = QPainterPath()
            path.moveTo(mosfet_x, wire_top); path.lineTo(comp_x, wire_top); path.lineTo(comp_x, wire_bot); path.lineTo(bat_x, wire_bot); path.lineTo(bat_x, cy)
            p.drawPath(path)

class ThermalView(QWidget):
    def __init__(self, battery):
        super().__init__(); self.battery = battery; self.setMinimumSize(500, 400)
    def update_battery(self, battery): self.battery = battery
    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(0, 0, 0))
        bat_w, bat_h = 200, 320; bat_x, bat_y = (w - bat_w) / 2, (h - bat_h) / 2
        
        temp = self.battery.temperature_c
        ratio = max(0, min(1, (temp - 20) / 80))
        if ratio < 0.25: c = QColor(int(ratio*4*255), 0, int(ratio*4*150))
        elif ratio < 0.5: c = QColor(255, int((ratio-0.25)*4*255), 0)
        elif ratio < 0.75: c = QColor(255, 255, int((ratio-0.5)*4*200))
        else: c = QColor(255, 255, 255)

        core_grad = QRadialGradient(bat_x + bat_w/2, bat_y + bat_h/2, bat_w)
        core_grad.setColorAt(0, c); core_grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(core_grad)); p.setPen(Qt.NoPen)
        p.drawRect(int(bat_x - 50), int(bat_y - 50), int(bat_w + 100), int(bat_h + 100))

        p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawRoundedRect(int(bat_x), int(bat_y), int(bat_w), int(bat_h), 15, 15)
        p.setFont(QFont("Arial", 20, QFont.Bold)); p.setPen(QPen(QColor(255, 255, 255)))
        p.drawText(QRectF(bat_x, bat_y, bat_w, bat_h), Qt.AlignCenter, f"{temp:.1f}°C\n{self.battery.name}")

        p.setFont(QFont("Arial", 8))
        for i in range(11):
            y_pos = bat_y + bat_h - (i * (bat_h/10)); map_temp = 20 + (i * 8); map_ratio = max(0, min(1, (map_temp - 20) / 80))
            if map_ratio < 0.25: mc = QColor(int(map_ratio*4*255), 0, int(map_ratio*4*150))
            elif map_ratio < 0.5: mc = QColor(255, int((map_ratio-0.25)*4*255), 0)
            elif map_ratio < 0.75: mc = QColor(255, 255, int((map_ratio-0.5)*4*200))
            else: mc = QColor(255, 255, 255)
            p.setBrush(QBrush(mc)); p.setPen(Qt.NoPen); p.drawRect(int(bat_x + bat_w + 20), int(y_pos - 10), 20, 20)
            p.setPen(QPen(QColor(255, 255, 255))); p.drawText(int(bat_x + bat_w + 45), int(y_pos + 5), f"{map_temp}°C")

class DischargeCurveView(QWidget):
    def __init__(self, battery):
        super().__init__(); self.battery = battery; self.setMinimumSize(500, 400)
    def update_battery(self, battery): self.battery = battery
    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(10, 10, 20))
        p.setPen(QPen(QColor(40, 40, 40), 1, Qt.DashLine))
        for i in range(1, 10):
            p.drawLine(0, int(h/10*i), w, int(h/10*i)); p.drawLine(int(w/10*i), 0, int(w/10*i), h)

        # Draw OCV Curve (Dashed Cyan)
        p.setPen(QPen(QColor(0, 255, 255, 150), 2, Qt.DashLine))
        path1 = QPainterPath()
        for i in range(101):
            soc = i / 100.0; v = self.battery.get_open_circuit_voltage(soc)
            x = soc * w; y = h - ((v - self.battery.min_v) / (self.battery.max_v - self.battery.min_v + 0.01)) * h
            if i == 0: path1.moveTo(x, y)
            else: path1.lineTo(x, y)
        p.drawPath(path1)

        # Draw Terminal Voltage Curve (Solid Red/Yellow under current load)
        curr = self.battery.actual_current_ma if self.battery.is_discharging else self.battery.discharge_target_ma
        if curr > 0:
            p.setPen(QPen(QColor(255, 50, 50), 3))
            path2 = QPainterPath()
            for i in range(101):
                soc = i / 100.0; v = self.battery.get_terminal_voltage(soc, curr)
                x = soc * w; y = h - ((v - self.battery.min_v) / (self.battery.max_v - self.battery.min_v + 0.01)) * h
                if i == 0: path2.moveTo(x, y)
                else: path2.lineTo(x, y)
            p.drawPath(path2)

        # Current State Dot
        soc = self.battery.get_soc(); v = self.battery.get_terminal_voltage()
        x = soc * w; y = h - ((v - self.battery.min_v) / (self.battery.max_v - self.battery.min_v + 0.01)) * h
        p.setBrush(QBrush(QColor(255, 0, 0))); p.setPen(Qt.NoPen); p.drawEllipse(QPointF(x, y), 8, 8)
        p.setBrush(QBrush(QColor(255, 100, 100, 100))); p.drawEllipse(QPointF(x, y), 16, 16)

        p.setPen(QPen(QColor(200, 200, 200))); p.setFont(QFont("Arial", 10))
        p.drawText(10, 20, "Voltage vs State of Charge (SoC)")
        p.drawText(10, 35, "-- OCV (No Load)   ─ V_term (Under Load)")
        p.drawText(10, h - 10, "0% SoC"); p.drawText(w - 40, h - 10, "100%")

def format_info(invented, energy_density, reaction, app, pros, cons, recycle, tip):
    return (f"<h3 style='color:#4db8ff; margin:0px;'>Battery Encyclopedia</h3>"
            f"<table style='color:#aaa; font-size:11px; margin-bottom:10px;'>"
            f"<tr><td><b>Invented:</b></td><td>{invented}</td></tr>"
            f"<tr><td><b>Energy Density:</b></td><td>{energy_density}</td></tr>"
            f"</table>"
            f"<b style='color:#ddd;'>Chemical Reaction:</b><br>"
            f"<code style='color:#ffcc00; background:#222; display:block; padding:5px; margin:3px 0px;'>{reaction}</code><br>"
            f"<b style='color:#ddd;'>Applications:</b> <span style='color:#ccc;'>{app}</span><br><br>"
            f"<b style='color:#5cd65c;'>✅ Pros:</b> <span style='color:#ccc;'>{pros}</span><br><br>"
            f"<b style='color:#ff6666;'>❌ Cons:</b> <span style='color:#ccc;'>{cons}</span><br><br>"
            f"<b style='color:#99ccff;'>♻️ Recycling & Disposal:</b> <span style='color:#ccc;'>{recycle}</span><br><br>"
            f"<b style='color:#ff9933;'>💡 Pro-Tip:</b> <i style='color:#ccc;'>{tip}</i>")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pro Battery Engineering Lab & Schematic Dashboard")
        self.resize(1400, 900)
        self.time_scale = 1.0

        self.batteries = {
            "Lithium-Ion (18650)": Battery("Li-ion 18650", "Lithium-Ion", 3.7, 4.2, 3.0, 3000, True, 0.05, 0.002, 75.0, "#707070",
                format_info("1991 (Sony)", "~250 Wh/kg", "C₆Li ⇌ Li⁺ + e⁻ + C₆", "Laptops, power tools, EVs, vapes.", "High energy density, no memory effect, slow self-discharge.", "Degrades over time even if not used, volatile if punctured/overcharged.", "Widely recycled. Contains valuable cobalt and nickel.", "Store at 40-50% charge in a cool place to maximize lifespan.")),
            "Alkaline (AA)": Battery("AA Alkaline", "Alkaline", 1.5, 1.5, 0.9, 2000, False, 0.15, 0.0, 60.0, "#604020",
                format_info("1955 (Lewis Urry)", "~100 Wh/kg", "Zn + 2MnO₂ → ZnO + Mn₂O₃", "TV remotes, clocks, children's toys.", "Cheap, widely available, long shelf life (5-10 years).", "Non-rechargeable, prone to leaking corrosive potassium hydroxide.", "Technically recyclable, but usually thrown in trash.", "Remove from devices if not used for months to prevent leaks.")),
            "NiMH (Rechargeable AA)": Battery("NiMH AA", "NiMH", 1.2, 1.4, 1.0, 1500, True, 0.08, 0.003, 65.0, "#406040",
                format_info("1987 (Stanford Ovshinsky)", "~100 Wh/kg", "NiOOH + MH ⇌ Ni(OH)₂ + M", "Digital cameras, hybrid cars (Prius).", "Rechargeable, safer than Lithium.", "High self-discharge, suffers from voltage sag.", "Fully recyclable. Nickel is highly reusable.", "Occasionally fully discharge to prevent 'voltage depression'.")),
            "LiFePO4 (LFP)": Battery("LiFePO4 26650", "LFP", 3.2, 3.65, 2.5, 3300, True, 0.02, 0.0005, 90.0, "#408080",
                format_info("1996 (John Goodenough)", "~150 Wh/kg", "LiFePO₄ ⇌ FePO₄ + Li⁺ + e⁻", "EVs, solar storage, power walls.", "Extremely safe, long cycle life.", "Lower voltage and energy density than standard Li-ion.", "Recyclable. Doesn't contain cobalt or nickel.", "Safe to charge to 100% regularly. The ethical choice for EVs.")),
            "Lead-Acid (Car)": Battery("Car Battery", "Lead-Acid", 12.0, 12.6, 10.5, 5000, True, 0.02, 0.001, 65.0, "#303030",
                format_info("1859 (Gaston Planté)", "~40 Wh/kg", "Pb + PbO₂ + 2H₂SO₄ ⇌ 2PbSO₄ + 2H₂O", "Car starter batteries, UPS backups.", "Cheap, robust, massive surge currents.", "Extremely heavy, toxic, only good for shallow discharges.", "99% recycled rate (most successful recycling program).", "Never let it sit fully discharged to prevent sulfation.")),
            "Solid State (Proto)": Battery("Solid State", "Solid", 4.5, 5.0, 3.5, 4000, True, 0.005, 0.0001, 110.0, "#202060",
                format_info("2020s (Prototypes)", "~400+ Wh/kg", "Li-metal + Solid Electrolyte", "Next-gen EVs, aerospace.", "Zero fire risk, massive capacity, ultra-fast charging.", "Astronomically expensive and hard to mass-produce.", "N/A (Still in R&D)", "Uses a solid glass/ceramic electrolyte. The 'holy grail' of batteries.")),
            "Lithium Polymer (LiPo)": Battery("LiPo Drone", "LiPo", 3.7, 4.2, 3.2, 4000, True, 0.01, 0.0025, 70.0, "#A00000",
                format_info("1990s", "~200 Wh/kg", "Same as Li-ion, Gel electrolyte", "Drones, RC vehicles, slim smartphones.", "Can be made paper-thin, massive discharge rates.", "Extremely volatile, degrades fast if stored fully charged.", "Recyclable.", "If a LiPo swells up, it is about to catch fire. Dispose immediately.")),
            "Aluminum-Air (Al-Air)": Battery("Al-Air", "AlAir", 1.2, 1.3, 0.8, 8000, False, 0.4, 0.0, 60.0, "#C0C0C0",
                format_info("1960s", "~1300 Wh/kg", "Al + 3O₂ → 2Al₂O₃", "Military backup, experimental EV range-extenders.", "Highest energy density of any battery (rivals gasoline).", "Completely non-rechargeable, high internal resistance.", "Aluminum is highly recyclable.", "Once activated, it corrodes itself to death within a month."))
        }
        self.current_battery = self.batteries["Lithium-Ion (18650)"]

        central_widget = QWidget(); self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # --- LEFT PANEL ---
        left_panel = QVBoxLayout()
        controls_group = QGroupBox("Battery & BMS Controls")
        clayout = QVBoxLayout(controls_group)
        clayout.addWidget(QLabel("Select Battery:"))
        self.battery_combo = QComboBox(); self.battery_combo.addItems(self.batteries.keys())
        self.battery_combo.currentTextChanged.connect(self.change_battery); clayout.addWidget(self.battery_combo)

        preset_layout = QHBoxLayout()
        self.btn_trickle = QPushButton("Trickle"); self.btn_trickle.clicked.connect(lambda: self.set_presets(200, 200)); preset_layout.addWidget(self.btn_trickle)
        self.btn_fast = QPushButton("Fast Charge"); self.btn_fast.clicked.connect(lambda: self.set_presets(1500, 1500)); preset_layout.addWidget(self.btn_fast)
        self.btn_heavy = QPushButton("Heavy Load"); self.btn_heavy.clicked.connect(lambda: self.set_presets(1000, 2000)); preset_layout.addWidget(self.btn_heavy)
        clayout.addLayout(preset_layout)

        self.charge_btn = QPushButton("Start Charging"); self.charge_btn.setCheckable(True); self.charge_btn.clicked.connect(self.toggle_charge); clayout.addWidget(self.charge_btn)
        self.charge_slider = QSlider(Qt.Horizontal); self.charge_slider.setRange(100, 2000); self.charge_slider.setValue(1000)
        self.charge_slider.valueChanged.connect(lambda v: self.charge_rate_label.setText(f"Target Charge Rate: {v} mA")); clayout.addWidget(self.charge_slider)
        self.charge_rate_label = QLabel("Target Charge Rate: 1000 mA"); clayout.addWidget(self.charge_rate_label)

        self.discharge_btn = QPushButton("Start Discharging (Load)"); self.discharge_btn.setCheckable(True); self.discharge_btn.clicked.connect(self.toggle_discharge); clayout.addWidget(self.discharge_btn)
        self.discharge_slider = QSlider(Qt.Horizontal); self.discharge_slider.setRange(100, 2000); self.discharge_slider.setValue(500)
        self.discharge_slider.valueChanged.connect(lambda v: self.discharge_rate_label.setText(f"Target Load Current: {v} mA")); clayout.addWidget(self.discharge_slider)
        self.discharge_rate_label = QLabel("Target Load Current: 500 mA"); clayout.addWidget(self.discharge_rate_label)

        # Time Scale Controls
        time_layout = QHBoxLayout()
        time_layout.addWidget(QLabel("Sim Speed:"))
        self.time_group = QButtonGroup(self)
        self.btn_1x = QPushButton("1x"); self.btn_1x.setCheckable(True); self.btn_1x.setChecked(True); self.time_group.addButton(self.btn_1x); time_layout.addWidget(self.btn_1x)
        self.btn_10x = QPushButton("10x"); self.btn_10x.setCheckable(True); self.time_group.addButton(self.btn_10x); time_layout.addWidget(self.btn_10x)
        self.btn_100x = QPushButton("100x"); self.btn_100x.setCheckable(True); self.time_group.addButton(self.btn_100x); time_layout.addWidget(self.btn_100x)
        self.btn_1x.clicked.connect(lambda: self.set_time_scale(1.0))
        self.btn_10x.clicked.connect(lambda: self.set_time_scale(10.0))
        self.btn_100x.clicked.connect(lambda: self.set_time_scale(100.0))
        clayout.addLayout(time_layout)

        self.reset_btn = QPushButton("Reset Health (SoH to 100%)"); self.reset_btn.clicked.connect(self.reset_battery); clayout.addWidget(self.reset_btn)
        left_panel.addWidget(controls_group)

        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("background-color: #181a20; color: #00e676; font-family: monospace; padding: 12px; border-radius: 5px; font-size: 13px; border: 1px solid #2a2e36;")
        left_panel.addWidget(self.stats_label)
        
        info_group = QGroupBox("Encyclopedia & Data")
        info_layout = QVBoxLayout(info_group)
        self.info_text_edit = QTextEdit(); self.info_text_edit.setReadOnly(True)
        self.info_text_edit.setStyleSheet("background-color: #121418; color: #DDD; border: none;")
        info_layout.addWidget(self.info_text_edit)
        left_panel.addWidget(info_group, 1)
        main_layout.addLayout(left_panel, 1)

        # --- RIGHT PANEL (Tabbed Dashboard) ---
        right_panel = QVBoxLayout()
        self.tabs = QTabWidget()
        self.physical_view = PhysicalView(self.current_battery)
        self.schematic_view = SchematicView(self.current_battery)
        self.thermal_view = ThermalView(self.current_battery)
        self.curve_view = DischargeCurveView(self.current_battery)
        self.tabs.addTab(self.physical_view, "Physical View")
        self.tabs.addTab(self.schematic_view, "Schematic View")
        self.tabs.addTab(self.thermal_view, "Thermal (FLIR) View")
        self.tabs.addTab(self.curve_view, "Discharge Curve")
        right_panel.addWidget(self.tabs, 3)
        
        self.graph_widget = GraphWidget(); self.graph_widget.max_v = self.current_battery.max_v * 1.2 
        right_panel.addWidget(self.graph_widget, 1)
        main_layout.addLayout(right_panel, 2)

        self.timer = QTimer(self); self.timer.timeout.connect(self.update_simulation); self.timer.start(50)
        self.update_ui()

    def set_time_scale(self, scale): self.time_scale = scale
    def set_presets(self, c, d): self.charge_slider.setValue(c); self.discharge_slider.setValue(d)
    def reset_battery(self):
        self.current_battery.soh = 1.0; self.current_battery.cycle_count = 0
        self.current_battery.current_capacity_mah = self.current_battery.design_capacity_mah * 0.5
        self.current_battery.temperature_c = 25.0; self.current_battery.safety_shutdown = False
        self.current_battery.total_energy_wh = 0.0; self.update_ui()

    def change_battery(self, name):
        self.current_battery = self.batteries[name]
        self.physical_view.update_battery(self.current_battery); self.schematic_view.update_battery(self.current_battery)
        self.thermal_view.update_battery(self.current_battery); self.curve_view.update_battery(self.current_battery)
        self.charge_btn.setChecked(False); self.discharge_btn.setChecked(False)
        self.current_battery.is_charging = False; self.current_battery.is_discharging = False
        self.charge_btn.setEnabled(self.current_battery.rechargeable)
        self.charge_slider.setEnabled(self.current_battery.rechargeable)
        self.btn_trickle.setEnabled(self.current_battery.rechargeable)
        self.btn_fast.setEnabled(self.current_battery.rechargeable)
        self.graph_widget.max_v = self.current_battery.max_v * 1.2
        for k in self.graph_widget.history: self.graph_widget.history[k].clear()
        self.update_ui()

    def toggle_charge(self):
        self.current_battery.is_charging = self.charge_btn.isChecked()
        if self.current_battery.is_charging: self.discharge_btn.setChecked(False); self.current_battery.is_discharging = False

    def toggle_discharge(self):
        self.current_battery.is_discharging = self.discharge_btn.isChecked()
        if self.current_battery.is_discharging: self.charge_btn.setChecked(False); self.current_battery.is_charging = False

    def update_simulation(self):
        dt = 0.05 * self.time_scale  # Apply time scale
        self.current_battery.charge_target_ma = self.charge_slider.value()
        self.current_battery.discharge_target_ma = self.discharge_slider.value()
        self.current_battery.update(dt)
        
        self.graph_widget.add_data(self.current_battery.get_terminal_voltage(), self.current_battery.actual_current_ma, self.current_battery.temperature_c)
        self.physical_view.external_phase += 0.5 * self.time_scale; self.physical_view.update()
        self.schematic_view.external_phase += 0.5 * self.time_scale; self.schematic_view.update()
        self.thermal_view.update(); self.curve_view.update()
        self.update_ui()
        
        if self.current_battery.safety_shutdown:
            if self.charge_btn.isChecked(): self.charge_btn.setChecked(False)
            if self.discharge_btn.isChecked(): self.discharge_btn.setChecked(False)
        elif not self.current_battery.is_charging and self.charge_btn.isChecked(): self.charge_btn.setChecked(False)
        elif not self.current_battery.is_discharging and self.discharge_btn.isChecked(): self.discharge_btn.setChecked(False)

    def update_ui(self):
        bat = self.current_battery
        bms_status = "NORMAL" if not bat.safety_shutdown else "⚠ SHUTDOWN"
        bms_color = "#00e676" if not bat.safety_shutdown else "#ff5252"
        info_text = (
            f"<b style='color:#fff;'>Battery:</b> {bat.name}<br>"
            f"<b>Terminal V:</b> {bat.get_terminal_voltage():.2f} V<br>"
            f"<b>Open Circuit V:</b> {bat.get_open_circuit_voltage():.2f} V<br>"
            f"<b>Actual Current:</b> <span style='color:#ffcc00;'>{bat.actual_current_ma} mA</span><br>"
            f"<b>Power (P=VI):</b> <span style='color:#ff66ff;'>{bat.get_power_watts():.2f} W</span><br>"
            f"<b>Total Energy:</b> <span style='color:#ff9933;'>{bat.total_energy_wh:.3f} Wh</span><br>"
            f"<b>Load Resistance:</b> <span style='color:#99ccff;'>{bat.get_load_resistance():.2f} Ω</span><br>"
            f"<b>Charge Phase:</b> {bat.charge_phase}<br>"
            f"<b>Capacity:</b> {int(bat.current_capacity_mah)}/{int(bat.design_capacity_mah * bat.soh)} mAh<br>"
            f"<b>State of Health:</b> <span style='color:yellow'>{bat.soh*100:.1f}%</span><br>"
            f"<b>Cycles:</b> {bat.cycle_count}<br>"
            f"<b>Temperature:</b> <span style='color:orange'>{bat.temperature_c:.1f} °C</span> / {bat.thermal_limit}°C<br>"
            f"<b>BMS Status:</b> <span style='color:{bms_color}; font-weight:bold;'>{bms_status}</span>"
        )
        self.stats_label.setText(info_text)
        self.info_text_edit.setHtml(bat.info_html)
        if bat.rechargeable: self.charge_btn.setText("Stop Charging" if bat.is_charging else "Start Charging")
        else: self.charge_btn.setText("Non-Rechargeable")
        self.discharge_btn.setText("Stop Discharging" if bat.is_discharging else "Start Discharging (Load)")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())