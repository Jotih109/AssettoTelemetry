"""
ui/components.py — Widgets do dashboard, no estilo MoTeC i2
==========================================================
Todos os painéis herdam a linguagem visual definida em `ui/theme.py`:
canto reto, borda de 1 px, faixa de título em maiúsculas, valores numéricos
monoespaçados alinhados à direita e densidade alta.

As APIs públicas (`update_*`, `set_value`, ...) são as mesmas de antes —
só a aparência mudou.
"""

import collections

from PyQt5.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView, QWidget, QComboBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor, QPainter, QPen
import pyqtgraph as pg

from ui import theme as T

# --- Pure Functions for Semantic Colors ---

def get_delta_color_bg(delta_val: float) -> str:
    if delta_val < 0:
        return "#0f2418"   # verde escuro
    elif delta_val > 0:
        return "#2a1214"   # vermelho escuro
    return T.BG_PANEL

def get_delta_color_text(delta_val: float) -> str:
    if delta_val < 0:
        return T.OK
    elif delta_val > 0:
        return T.BAD
    return T.TXT_DIM

def get_sector_color(sector_time_str: str, personal_best_str: str, session_record_str: str) -> str:
    """Retorna roxo (recorde), verde (melhor pessoal) ou vermelho (lento)."""
    if sector_time_str == "--:--" or not sector_time_str:
        return T.TXT_DIM

    if session_record_str and sector_time_str <= session_record_str:
        return T.PURPLE
    elif personal_best_str and sector_time_str <= personal_best_str:
        return T.OK

    return T.BAD

# --- Base UI Components ---

class BaseCard(T.Panel):
    """
    Painel base. Mantido como classe própria porque muitos widgets antigos
    dependem da assinatura (title, margins, spacing) e de `main_layout`.
    """
    def __init__(self, title=None, margins=(7, 5, 7, 6), spacing=3):
        super(BaseCard, self).__init__(title=title, body_margins=margins, spacing=spacing)


def _inset(padding=(6, 4, 6, 4)) -> QFrame:
    """Caixa afundada para destacar um valor."""
    box = QFrame()
    box.setStyleSheet(T.inset_qss())
    lay = QVBoxLayout(box)
    lay.setContentsMargins(*padding)
    lay.setSpacing(0)
    box._lay = lay
    return box


# --- Sidebar Components ---

class GearCard(QFrame):
    """Marcha atual em destaque, com a moldura mudando de cor no corte."""
    def __init__(self):
        super(GearCard, self).__init__()
        self.setStyleSheet(T.inset_qss())
        self.setFixedHeight(78)
        self._current_rpm = 0.0
        self._max_rpm = 8500.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(0)

        self.lbl_gear = QLabel("N")
        self.lbl_gear.setFont(QFont(T.FONT_MONO, 40, QFont.Bold))
        self.lbl_gear.setAlignment(Qt.AlignCenter)
        self.lbl_gear.setStyleSheet(
            f"color: {T.TXT_VALUE}; background: transparent; border: none;")

        lbl_cap = QLabel("GEAR")
        lbl_cap.setFont(T.f_title(7))
        lbl_cap.setAlignment(Qt.AlignCenter)
        lbl_cap.setStyleSheet(f"color: {T.TXT_UNIT}; background: transparent; border: none;")

        layout.addWidget(self.lbl_gear)
        layout.addWidget(lbl_cap)
        # Compatibilidade com o código antigo
        self.num_box = self

    def update_gear(self, gear: int, rpm: float = 0.0, max_rpm: float = 8500.0):
        self._current_rpm = rpm
        self._max_rpm = max_rpm
        gear_str = "R" if gear == 0 else ("N" if gear == 1 else str(gear - 1))
        self.lbl_gear.setText(gear_str)

        at_redline = max_rpm > 0 and rpm >= max_rpm * 0.97
        if gear_str == "R" or at_redline:
            fg, border = T.BAD, T.BAD
        elif gear_str == "N":
            fg, border = T.OK, T.BORDER_SOFT
        else:
            fg, border = T.TXT_VALUE, T.BORDER_SOFT
        self.setStyleSheet(T.inset_qss(border=border))
        self.lbl_gear.setStyleSheet(f"color: {fg}; background: transparent; border: none;")


class SpeedCard(QFrame):
    """Velocidade em km/h, leitura grande."""
    def __init__(self):
        super(SpeedCard, self).__init__()
        self.setStyleSheet(T.inset_qss())
        self.setFixedHeight(60)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(4)

        self.lbl_speed = QLabel("0")
        self.lbl_speed.setFont(QFont(T.FONT_MONO, 26, QFont.Bold))
        self.lbl_speed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_speed.setStyleSheet(
            f"color: {T.CH_SPEED}; background: transparent; border: none;")

        lbl_unit = QLabel("km/h")
        lbl_unit.setFont(T.f_label(8))
        lbl_unit.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        lbl_unit.setStyleSheet(f"color: {T.TXT_UNIT}; background: transparent; border: none;")

        layout.addStretch()
        layout.addWidget(self.lbl_speed)
        layout.addWidget(lbl_unit)

    def update_speed(self, speed_kmh: int):
        self.lbl_speed.setText(str(speed_kmh))


class PedalsBarCard(QFrame):
    """Barras verticais de acelerador e freio, com o percentual acima."""
    BAR_W = 22

    def __init__(self):
        super(PedalsBarCard, self).__init__()
        self.setStyleSheet(T.panel_qss())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(4)

        vals = QHBoxLayout()
        vals.setSpacing(8)
        self.lbl_gas_val = self._val_label(T.CH_THROTTLE)
        self.lbl_brk_val = self._val_label(T.CH_BRAKE)
        vals.addWidget(self.lbl_gas_val)
        vals.addWidget(self.lbl_brk_val)
        layout.addLayout(vals)

        bars = QHBoxLayout()
        bars.setSpacing(8)
        self.bar_gas = self._make_bar(T.CH_THROTTLE)
        self.bar_brake = self._make_bar(T.CH_BRAKE)
        bars.addWidget(self.bar_gas)
        bars.addWidget(self.bar_brake)
        layout.addLayout(bars, stretch=1)

        caps = QHBoxLayout()
        caps.setSpacing(8)
        caps.addWidget(self._cap_label("THR", T.CH_THROTTLE))
        caps.addWidget(self._cap_label("BRK", T.CH_BRAKE))
        layout.addLayout(caps)

    def _val_label(self, color):
        lbl = QLabel("0")
        lbl.setFont(QFont(T.FONT_MONO, 8, QFont.Bold))
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setFixedWidth(self.BAR_W)
        lbl.setStyleSheet(f"color: {color}; background: transparent; border: none;")
        return lbl

    def _cap_label(self, text, color):
        lbl = QLabel(text)
        lbl.setFont(T.f_title(7))
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setFixedWidth(self.BAR_W)
        lbl.setStyleSheet(f"color: {color}; background: transparent; border: none;")
        return lbl

    def _make_bar(self, color):
        bar = QProgressBar()
        bar.setOrientation(Qt.Vertical)
        bar.setTextVisible(False)
        bar.setRange(0, 100)
        bar.setFixedWidth(self.BAR_W)
        bar.setMinimumHeight(90)
        bar.setStyleSheet(
            f"QProgressBar {{ background-color: {T.BG_INSET};"
            f"border: 1px solid {T.BORDER_SOFT}; border-radius: 0px; }}"
            f"QProgressBar::chunk {{ background-color: {color}; }}"
        )
        return bar

    def update_pedals(self, gas: float, brake: float):
        g, b = int(gas * 100), int(brake * 100)
        self.bar_gas.setValue(g)
        self.bar_brake.setValue(b)
        self.lbl_gas_val.setText(str(g))
        self.lbl_brk_val.setText(str(b))


class RpmCard(QFrame):
    """Barra de RPM com marcas de escala, no estilo das barras do i2."""
    def __init__(self):
        super(RpmCard, self).__init__()
        self.setStyleSheet(T.panel_qss())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(4)
        lbl_cap = QLabel("ENGINE RPM")
        lbl_cap.setFont(T.f_title(7))
        lbl_cap.setStyleSheet(f"color: {T.TXT_TITLE}; background: transparent; border: none;")
        self.lbl_rpm = QLabel("0")
        self.lbl_rpm.setFont(QFont(T.FONT_MONO, 17, QFont.Bold))
        self.lbl_rpm.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_rpm.setStyleSheet(
            f"color: {T.TXT_VALUE}; background: transparent; border: none;")
        self.lbl_max = QLabel("/ --")
        self.lbl_max.setFont(T.f_label(8))
        self.lbl_max.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self.lbl_max.setStyleSheet(f"color: {T.TXT_UNIT}; background: transparent; border: none;")
        top.addWidget(lbl_cap)
        top.addStretch()
        top.addWidget(self.lbl_rpm)
        top.addWidget(self.lbl_max)
        layout.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(10)
        self.progress.setTextVisible(False)
        self._set_bar_color(T.CH_RPM)
        layout.addWidget(self.progress)

    def _set_bar_color(self, color: str):
        self.progress.setStyleSheet(
            f"QProgressBar {{ background-color: {T.BG_INSET};"
            f"border: 1px solid {T.BORDER_SOFT}; border-radius: 0px; }}"
            f"QProgressBar::chunk {{ background-color: {color}; }}"
        )

    def update_rpm(self, rpm: float, max_rpm: float):
        try:
            safe_rpm = int(rpm)
            safe_max = int(max_rpm)
        except (ValueError, OverflowError):
            safe_rpm, safe_max = 0, 1

        safe_rpm = max(0, min(safe_rpm, 100000))
        safe_max = max(1, min(safe_max, 100000))

        self.lbl_rpm.setText(f"{safe_rpm}")
        self.lbl_max.setText(f"/ {safe_max}")
        self.progress.setRange(0, safe_max)
        self.progress.setValue(safe_rpm)

        ratio = safe_rpm / safe_max if safe_max > 0 else 0
        if ratio >= 0.97:
            color = T.BAD
        elif ratio >= 0.85:
            color = "#ff8800"
        elif ratio >= 0.65:
            color = T.CH_STEER
        else:
            color = T.CH_RPM
        self._set_bar_color(color)
        val_color = T.BAD if ratio >= 0.97 else T.TXT_VALUE
        self.lbl_rpm.setStyleSheet(
            f"color: {val_color}; background: transparent; border: none;")


class CarDataCard(BaseCard):
    """Combustível, autonomia, turbo e volante — em linhas de canal."""
    def __init__(self):
        super(CarDataCard, self).__init__(title="Carro")

        self.row_fuel, self.lbl_fuel_v = T.channel_row("Combustível", "0.0", "L")
        self.row_laps, self.lbl_laps_v = T.channel_row("Voltas est.", "0.0", "")
        self.row_avg,  self.lbl_avg_v  = T.channel_row("Consumo", "--", "L/v")
        self.row_turbo, self.lbl_turbo_v = T.channel_row("Turbo", "0.00", "bar")
        self.row_steer, self.lbl_steer_v = T.channel_row("Volante", "0", "°")

        # Barra de nível do tanque logo abaixo da linha de combustível,
        # para ficar claro a qual canal ela se refere.
        self.bar_fuel = QProgressBar()
        self.bar_fuel.setFixedHeight(4)
        self.bar_fuel.setRange(0, 100)
        self.bar_fuel.setTextVisible(False)
        self._set_fuel_bar(T.OK)

        self.body.addWidget(self.row_fuel)
        self.body.addWidget(self.bar_fuel)
        for row in (self.row_laps, self.row_avg, self.row_turbo, self.row_steer):
            self.body.addWidget(row)

        # Aliases antigos (compatibilidade)
        self.lbl_fuel_avg = self.lbl_avg_v

    def _set_fuel_bar(self, color):
        self.bar_fuel.setStyleSheet(
            f"QProgressBar {{ background-color: {T.BG_INSET};"
            f"border: 1px solid {T.BORDER_SOFT}; border-radius: 0px; }}"
            f"QProgressBar::chunk {{ background-color: {color}; }}"
        )

    def update_data(self, fuel: float, laps: float, turbo: float, steer: float,
                    fuel_avg: float = 0.0, fuel_capacity: float = 0.0):
        self.lbl_fuel_v.setText(f"{fuel:.1f}")
        self.lbl_laps_v.setText(f"{laps:.1f}")
        self.lbl_turbo_v.setText(f"{turbo:.2f}")
        self.lbl_steer_v.setText(f"{int(steer)}")
        self.lbl_avg_v.setText(f"{fuel_avg:.2f}" if fuel_avg > 0 else "--")

        if fuel_capacity > 0:
            pct = max(0.0, min(1.0, fuel / fuel_capacity))
            color = T.BAD if pct < 0.10 else (T.WARN if pct < 0.25 else T.OK)
            self.bar_fuel.setValue(int(pct * 100))
            self._set_fuel_bar(color)
            self.lbl_fuel_v.setStyleSheet(
                f"color: {color}; background: transparent; border: none;")
            self.row_fuel.lbl_name.setText(f"Combustível ({fuel_capacity:.0f} L)")


class TireCard(BaseCard):
    """
    Pneus em grade 2×2 (planta do carro).

    Cada pneu traz: temperatura do núcleo (grande, colorida), barra de faixa
    térmica, pressão, desgaste e as temperaturas Interna/Meio/Externa da banda.
    """
    def __init__(self):
        super(TireCard, self).__init__(title="Pneus")
        grid = QGridLayout()
        grid.setSpacing(5)
        self.t_fl = self._create_tire_box("FL")
        self.t_fr = self._create_tire_box("FR")
        self.t_rl = self._create_tire_box("RL")
        self.t_rr = self._create_tire_box("RR")
        grid.addWidget(self.t_fl, 0, 0)
        grid.addWidget(self.t_fr, 0, 1)
        grid.addWidget(self.t_rl, 1, 0)
        grid.addWidget(self.t_rr, 1, 1)
        self.body.addLayout(grid)
        self.body.addStretch()

    def _create_tire_box(self, label):
        frame = QFrame()
        frame.setStyleSheet(T.inset_qss())
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(6, 4, 6, 4)
        vbox.setSpacing(1)

        head = QHBoxLayout()
        head.setSpacing(4)
        lbl_title = QLabel(label)
        lbl_title.setFont(T.f_title(7))
        lbl_title.setStyleSheet(f"color: {T.TXT_TITLE}; background: transparent; border: none;")
        lbl_temp = QLabel("--")
        lbl_temp.setFont(QFont(T.FONT_MONO, 14, QFont.Bold))
        lbl_temp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lbl_temp.setStyleSheet(f"color: {T.TXT_VALUE}; background: transparent; border: none;")
        lbl_deg = QLabel("°C")
        lbl_deg.setFont(T.f_label(7))
        lbl_deg.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        lbl_deg.setStyleSheet(f"color: {T.TXT_UNIT}; background: transparent; border: none;")
        head.addWidget(lbl_title)
        head.addStretch()
        head.addWidget(lbl_temp)
        head.addWidget(lbl_deg)
        vbox.addLayout(head)

        # Barra de faixa térmica
        bar = QProgressBar()
        bar.setFixedHeight(3)
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setStyleSheet(
            f"QProgressBar {{ background-color: {T.BG_APP}; border: none; }}"
            f"QProgressBar::chunk {{ background-color: {T.OK}; }}"
        )
        vbox.addWidget(bar)

        row_psi = QHBoxLayout()
        row_psi.setSpacing(4)
        lbl_psi = QLabel("-- psi")
        lbl_psi.setFont(QFont(T.FONT_MONO, 8))
        lbl_psi.setStyleSheet(f"color: {T.TXT_LABEL}; background: transparent; border: none;")
        lbl_wear = QLabel("--%")
        lbl_wear.setFont(QFont(T.FONT_MONO, 8))
        lbl_wear.setAlignment(Qt.AlignRight)
        lbl_wear.setStyleSheet(f"color: {T.TXT_LABEL}; background: transparent; border: none;")
        row_psi.addWidget(lbl_psi)
        row_psi.addStretch()
        row_psi.addWidget(lbl_wear)
        vbox.addLayout(row_psi)

        # Temperaturas Interna / Meio / Externa da banda de rodagem
        lbl_imo = QLabel("I -- M -- E --")
        lbl_imo.setFont(QFont(T.FONT_MONO, 7))
        lbl_imo.setStyleSheet(f"color: {T.TXT_UNIT}; background: transparent; border: none;")
        vbox.addWidget(lbl_imo)

        frame.lbl_title = lbl_title
        frame.lbl_val = lbl_temp   # nome mantido por compatibilidade
        frame.lbl_deg = lbl_deg
        frame.lbl_psi = lbl_psi
        frame.lbl_wear = lbl_wear
        frame.lbl_imo = lbl_imo
        frame.bar = bar
        return frame

    def update_tire(self, frame: QFrame, temp: float, pressure: float = 0.0, wear: float = 100.0,
                    t_inner: float = None, t_middle: float = None, t_outer: float = None):
        color = T.temp_color(temp)
        frame.lbl_val.setText(f"{temp:.0f}")
        frame.lbl_val.setStyleSheet(f"color: {color}; background: transparent; border: none;")
        frame.lbl_psi.setText(f"{pressure:.1f} psi")

        # Barra: 60 °C a 120 °C mapeados em 0-100 %
        frame.bar.setValue(int(max(0.0, min(1.0, (temp - 60.0) / 60.0)) * 100))
        frame.bar.setStyleSheet(
            f"QProgressBar {{ background-color: {T.BG_APP}; border: none; }}"
            f"QProgressBar::chunk {{ background-color: {color}; }}"
        )

        frame.lbl_wear.setText(f"{wear:.0f}%")
        wear_color = T.BAD if wear < 40 else (T.WARN if wear < 70 else T.TXT_LABEL)
        frame.lbl_wear.setStyleSheet(
            f"color: {wear_color}; background: transparent; border: none;")

        # I/M/E: diferença interna-externa acima de ~8 °C indica câmber fora do ponto
        if t_inner is not None and t_outer is not None:
            mid = t_middle if t_middle is not None else (t_inner + t_outer) / 2.0
            frame.lbl_imo.setText(f"I{t_inner:.0f} M{mid:.0f} E{t_outer:.0f}")
            spread = abs(t_inner - t_outer)
            imo_color = T.WARN if spread > 8 else T.TXT_UNIT
            frame.lbl_imo.setStyleSheet(
                f"color: {imo_color}; background: transparent; border: none;")


class AssistLED(QWidget):
    """Indicador retangular de estado (aceso/apagado)."""
    COLORS = {
        "ABS":  T.CH_STEER,
        "TC":   T.CH_SPEED,
        "PIT":  T.BAD,
        "DRS":  T.OK,
        "KERS": T.PURPLE,
        "BOX":  "#ff8800",
    }

    def __init__(self, label: str):
        super().__init__()
        self._label = label
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.pill = QLabel(label)
        self.pill.setFont(T.f_title(7))
        self.pill.setAlignment(Qt.AlignCenter)
        self.pill.setFixedHeight(18)
        self.pill.setMinimumWidth(40)
        self._set_inactive()
        layout.addWidget(self.pill)

    def _set_inactive(self):
        self.pill.setStyleSheet(
            f"color: {T.TXT_DIM}; background-color: {T.BG_INSET};"
            f"border: 1px solid {T.BORDER_SOFT}; border-radius: 0px;"
        )

    def set_active(self, active: bool, color: str = ""):
        if active:
            bg = color or self.COLORS.get(self._label, T.OK)
            self.pill.setStyleSheet(
                f"color: #0d0f11; background-color: {bg};"
                f"border: 1px solid {bg}; border-radius: 0px;"
            )
        else:
            self._set_inactive()


class AssistsCard(BaseCard):
    """
    Eletrônica do carro.

    No Assetto Corsa o ABS e o TC vêm como INTENSIDADE da intervenção, então
    as barras mostram o quanto cada sistema está atuando naquele instante.
    """
    def __init__(self):
        super(AssistsCard, self).__init__(title="Eletrônica")

        leds = QGridLayout()
        leds.setSpacing(4)
        self.led_abs = AssistLED("ABS")
        self.led_tc = AssistLED("TC")
        self.led_pit = AssistLED("PIT")
        self.led_drs = AssistLED("DRS")
        self.led_kers = AssistLED("KERS")
        self.led_box = AssistLED("BOX")
        for i, led in enumerate((self.led_abs, self.led_tc, self.led_pit,
                                 self.led_drs, self.led_kers, self.led_box)):
            leds.addWidget(led, i // 3, i % 3)
        self.body.addLayout(leds)

        self.bar_abs = self._make_bar(AssistLED.COLORS["ABS"])
        self.bar_tc = self._make_bar(AssistLED.COLORS["TC"])
        self.body.addWidget(self.bar_abs)
        self.body.addWidget(self.bar_tc)

        self.row_ffb, self.lbl_ffb_v = T.channel_row("Force feedback", "0", "%")
        self.body.addWidget(self.row_ffb)
        # Alias antigo
        self.lbl_ffb = self.lbl_ffb_v

    @staticmethod
    def _make_bar(color: str) -> QProgressBar:
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(4)
        bar.setStyleSheet(
            f"QProgressBar {{ background-color: {T.BG_INSET};"
            f"border: 1px solid {T.BORDER_SOFT}; border-radius: 0px; }}"
            f"QProgressBar::chunk {{ background-color: {color}; }}"
        )
        return bar

    def update_electronics(self, state):
        self.led_abs.set_active(state.abs_active)
        self.led_tc.set_active(state.tc_active)
        self.led_pit.set_active(state.pit_limiter)
        self.led_drs.set_active(state.drs_active)
        self.led_kers.set_active(state.kers_charge > 0.01)
        self.led_box.set_active(state.in_pit_lane or state.in_pit)

        self.bar_abs.setValue(int(state.abs_intervention * 100))
        self.bar_tc.setValue(int(state.tc_intervention * 100))

        ffb_pct = state.ffb_level * 100
        self.lbl_ffb_v.setText(f"{ffb_pct:.0f}")
        if ffb_pct > 95:
            color = T.BAD
            self.row_ffb.lbl_name.setText("Force feedback  CLIP")
        else:
            color = T.WARN if ffb_pct > 80 else T.TXT_VALUE
            self.row_ffb.lbl_name.setText("Force feedback")
        self.lbl_ffb_v.setStyleSheet(f"color: {color}; background: transparent; border: none;")


class GhostSelectorCard(QWidget):
    """Seletor da volta de referência (ghost)."""
    def __init__(self):
        super(GhostSelectorCard, self).__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        lbl_title = QLabel("REFERÊNCIA")
        lbl_title.setFont(T.f_title(10))
        lbl_title.setStyleSheet(f"color: {T.TXT_TITLE}; background: transparent;")

        self.combo = QComboBox()
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {T.BG_INSET};
                color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER};
                border-radius: 0px;
                padding: 4px 10px;
                font-family: "{T.FONT_UI}";
                font-size: 14px;
            }}
            QComboBox:hover {{ border: 1px solid #3d454c; }}
            QComboBox::drop-down {{ border: none; width: 16px; }}
            QComboBox QAbstractItemView {{
                background-color: {T.BG_PANEL};
                color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER};
                selection-background-color: {T.BG_HEADER};
                font-family: "{T.FONT_UI}";
                font-size: 14px;
                outline: none;
            }}
        """)
        self.combo.addItems(["Desativado", "Personal Best", "Sessão Atual", "Volta Ideal"])

        layout.addWidget(lbl_title, alignment=Qt.AlignVCenter)
        layout.addWidget(self.combo, alignment=Qt.AlignVCenter)


# --- Main Area Components ---

class TopMetricCard(BaseCard):
    """Cartão de métrica do topo (volta atual, melhor volta, delta)."""
    def __init__(self, title, initial_val, is_delta=False):
        super(TopMetricCard, self).__init__(title=title, margins=(9, 6, 9, 7))
        self.is_delta = is_delta
        self.lbl_val = QLabel(initial_val)
        self.lbl_val.setFont(QFont(T.FONT_MONO, 21 if is_delta else 17, QFont.Bold))
        self.lbl_val.setStyleSheet(
            f"color: {T.TXT_VALUE}; background: transparent; border: none;")
        self.body.addWidget(self.lbl_val)

    def set_value(self, val_str: str, delta_val: float = 0.0):
        self.lbl_val.setText(val_str)
        if self.is_delta:
            bg = get_delta_color_bg(delta_val)
            fg = get_delta_color_text(delta_val)
            self.setStyleSheet(T.panel_qss(bg=bg))
            self.lbl_val.setStyleSheet(f"color: {fg}; background: transparent; border: none;")
            self.lbl_title.setStyleSheet(
                f"color: {fg}; background-color: {T.BG_HEADER};"
                f"border: none; border-bottom: 1px solid {T.BORDER}; padding: 3px 7px;"
            )


class SectorCardInner(QFrame):
    """Coluna de um setor: tempo grande, referência e delta."""
    def __init__(self, title):
        super(SectorCardInner, self).__init__()
        self.setStyleSheet("background: transparent; border: none;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self.lbl_title = QLabel(title)
        self.lbl_title.setFont(T.f_title(7))
        self.lbl_title.setStyleSheet(f"color: {T.TXT_TITLE}; background: transparent;")

        self.lbl_time = QLabel("--:--.---")
        self.lbl_time.setFont(QFont(T.FONT_MONO, 15, QFont.Bold))
        self.lbl_time.setStyleSheet(f"color: {T.TXT_VALUE}; background: transparent;")

        ref_row = QHBoxLayout()
        ref_row.setContentsMargins(0, 0, 0, 0)
        ref_row.setSpacing(5)
        self.lbl_ref = QLabel("Ref --:--.---")
        self.lbl_ref.setFont(QFont(T.FONT_MONO, 8))
        self.lbl_ref.setStyleSheet(f"color: {T.TXT_UNIT}; background: transparent;")
        self.lbl_delta = QLabel("")
        self.lbl_delta.setFont(QFont(T.FONT_MONO, 8, QFont.Bold))
        self.lbl_delta.setStyleSheet(f"color: {T.TXT_DIM}; background: transparent;")
        ref_row.addWidget(self.lbl_ref)
        ref_row.addWidget(self.lbl_delta)
        ref_row.addStretch()

        layout.addWidget(self.lbl_title)
        layout.addWidget(self.lbl_time)
        layout.addLayout(ref_row)

    def set_values(self, current_time, ref_time, color=None, delta_str=""):
        self.lbl_time.setText(current_time)
        self.lbl_time.setStyleSheet(
            f"color: {color or T.TXT_VALUE}; background: transparent;")
        if ref_time:
            self.lbl_ref.setText(f"Ref {ref_time}")
            self.lbl_ref.show()
        else:
            self.lbl_ref.setText("")
            self.lbl_ref.hide()
        if delta_str:
            self.lbl_delta.setText(delta_str)
            d_color = T.OK if delta_str.startswith("-") else T.BAD
            self.lbl_delta.setStyleSheet(f"color: {d_color}; background: transparent;")
        else:
            self.lbl_delta.setText("")


class SectorsCard(BaseCard):
    def __init__(self):
        super(SectorsCard, self).__init__(title="Setores", margins=(9, 6, 9, 7))
        layout = QHBoxLayout()
        layout.setSpacing(14)
        self.s1 = SectorCardInner("S1")
        self.s2 = SectorCardInner("S2")
        self.s3 = SectorCardInner("S3")
        layout.addWidget(self.s1)
        layout.addWidget(self.s2)
        layout.addWidget(self.s3)
        self.body.addLayout(layout)

    def update_sectors(self, s1: str, s2: str, s3: str, pb1: str, pb2: str, pb3: str,
                       d1: str = "", d2: str = "", d3: str = ""):
        c1 = None if not d1 else get_sector_color(s1, pb1, None)
        c2 = None if not d2 else get_sector_color(s2, pb2, None)
        c3 = None if not d3 else get_sector_color(s3, pb3, None)

        self.s1.set_values(s1, pb1, c1, d1)
        self.s2.set_values(s2, pb2, c2, d2)
        self.s3.set_values(s3, pb3, c3, d3)


class WeatherCard(BaseCard):
    """
    Condições da pista.

    O Assetto Corsa 1 não tem clima dinâmico, então em vez de "chuva" mostramos
    o que o jogo realmente entrega e afeta o ritmo: aderência da pista e vento.
    """
    def __init__(self):
        super(WeatherCard, self).__init__(title="Pista")

        self.row_amb, self.lbl_amb_v = T.channel_row("Ar", "--", "°C")
        self.row_trk, self.lbl_trk_v = T.channel_row("Asfalto", "--", "°C")
        self.row_grip, self.lbl_grip_v = T.channel_row("Aderência", "--", "%")
        self.row_wind, self.lbl_wind_v = T.channel_row("Vento", "--", "km/h")
        for row in (self.row_amb, self.row_trk, self.row_grip, self.row_wind):
            self.body.addWidget(row)

        self.bar_grip = QProgressBar()
        self.bar_grip.setFixedHeight(5)
        self.bar_grip.setRange(90, 100)
        self.bar_grip.setTextVisible(False)
        self._set_grip_bar(T.OK)
        self.body.addWidget(self.bar_grip)

        # Aliases antigos: (label, valor)
        self.body.addStretch()

        # Aliases antigos: (label, valor)
        self.lbl_ambient = (self.row_amb.lbl_name, self.lbl_amb_v)
        self.lbl_track = (self.row_trk.lbl_name, self.lbl_trk_v)

    def _set_grip_bar(self, color):
        self.bar_grip.setStyleSheet(
            f"QProgressBar {{ background-color: {T.BG_INSET};"
            f"border: 1px solid {T.BORDER_SOFT}; border-radius: 0px; }}"
            f"QProgressBar::chunk {{ background-color: {color}; }}"
        )

    def update_weather(self, ambient: float, track: float,
                       grip: float = 1.0, wind_speed: float = 0.0,
                       wind_dir: float = 0.0):
        self.lbl_amb_v.setText(f"{ambient:.1f}")
        self.lbl_trk_v.setText(f"{track:.1f}")

        grip_pct = grip * 100.0
        self.lbl_grip_v.setText(f"{grip_pct:.1f}")
        # Abaixo de ~98 % a pista ainda está "verde" / suja
        if grip_pct < 95:
            grip_color = T.BAD
        elif grip_pct < 98:
            grip_color = T.WARN
        else:
            grip_color = T.OK
        self.lbl_grip_v.setStyleSheet(
            f"color: {grip_color}; background: transparent; border: none;")
        self.bar_grip.setValue(int(grip_pct))
        self._set_grip_bar(grip_color)

        # windSpeed vem em m/s no AC
        wind_kmh = wind_speed * 3.6
        self.lbl_wind_v.setText(f"{wind_kmh:.0f} {self._wind_arrow(wind_dir)}")
        wind_color = T.CH_SPEED if wind_kmh > 5 else T.TXT_VALUE
        self.lbl_wind_v.setStyleSheet(
            f"color: {wind_color}; background: transparent; border: none;")

    @staticmethod
    def _wind_arrow(direction_deg: float) -> str:
        """Converte a direção do vento em uma seta de 8 pontos."""
        arrows = ["↑", "↗", "→", "↘", "↓", "↙", "←", "↖"]
        idx = int(((direction_deg % 360) + 22.5) // 45) % 8
        return arrows[idx]


class SessionCard(BaseCard):
    """Tipo de sessão, posição, voltas, tempo restante, composto e danos."""
    def __init__(self):
        super(SessionCard, self).__init__(title="Sessão")

        self.row_pos, self.lbl_pos_v = T.channel_row("Posição", "--", "")
        self.row_laps, self.lbl_laps_v = T.channel_row("Volta", "--", "")
        self.row_left, self.lbl_left_v = T.channel_row("Restante", "--", "")
        self.row_comp, self.lbl_comp_v = T.channel_row("Composto", "--", "")
        self.row_dmg, self.lbl_dmg_v = T.channel_row("Danos", "0", "%")
        for row in (self.row_pos, self.row_laps, self.row_left,
                    self.row_comp, self.row_dmg):
            self.body.addWidget(row)

        self.lbl_flag = QLabel("")
        self.lbl_flag.setFont(T.f_title(8))
        self.lbl_flag.setAlignment(Qt.AlignCenter)
        self.lbl_flag.setVisible(False)
        self.body.addWidget(self.lbl_flag)
        self.body.addStretch()

        # Alias antigo
        self.lbl_header = self.lbl_title

    FLAG_COLORS = {
        "AZUL": "#3377ff", "AMARELA": "#ffdd00", "PRETA": "#ffffff",
        "BRANCA": "#dddddd", "XADREZ": T.OK, "PENALIDADE": T.BAD,
    }

    def update_session(self, state):
        self.lbl_title.setText(f"SESSÃO — {(state.session_type or '--').upper()}")

        self.lbl_pos_v.setText(f"{state.race_position}º" if state.race_position > 0 else "--")

        if state.total_laps > 0:
            self.lbl_laps_v.setText(f"{state.lap_number}/{state.total_laps}")
        else:
            self.lbl_laps_v.setText(f"{state.lap_number}")

        left = state.session_time_left
        self.lbl_left_v.setText(
            f"{int(left // 60)}:{int(left % 60):02d}" if left > 0 else "--")

        self.lbl_comp_v.setText(state.tyre_compound or "--")

        dmg = state.car_damage
        self.lbl_dmg_v.setText(f"{dmg:.0f}")
        dmg_color = T.BAD if dmg > 30 else (T.WARN if dmg > 5 else T.TXT_VALUE)
        self.lbl_dmg_v.setStyleSheet(
            f"color: {dmg_color}; background: transparent; border: none;")

        if state.flag:
            color = self.FLAG_COLORS.get(state.flag, "#ffffff")
            self.lbl_flag.setText(f"BANDEIRA {state.flag}")
            self.lbl_flag.setStyleSheet(
                f"color: #0d0f11; background-color: {color};"
                f"border: none; border-radius: 0px; padding: 2px;")
            self.lbl_flag.setVisible(True)
        else:
            self.lbl_flag.setVisible(False)


class BrakesCard(BaseCard):
    """Temperatura dos 4 discos + distribuição de frenagem."""
    def __init__(self):
        super(BrakesCard, self).__init__(title="Freios")
        grid = QGridLayout()
        grid.setSpacing(5)
        self.b_fl = self._make_brake("FL")
        self.b_fr = self._make_brake("FR")
        self.b_rl = self._make_brake("RL")
        self.b_rr = self._make_brake("RR")
        grid.addWidget(self.b_fl, 0, 0)
        grid.addWidget(self.b_fr, 0, 1)
        grid.addWidget(self.b_rl, 1, 0)
        grid.addWidget(self.b_rr, 1, 1)
        self.body.addLayout(grid)

        self.row_bias, self.lbl_bias_v = T.channel_row("Bias diant.", "--", "%")
        self.body.addWidget(self.row_bias)
        self.body.addStretch()
        # Alias antigo
        self.lbl_bias = self.lbl_bias_v

    def _make_brake(self, label):
        box = QFrame()
        box.setStyleSheet(T.inset_qss())
        vbox = QVBoxLayout(box)
        vbox.setContentsMargins(6, 3, 6, 3)
        vbox.setSpacing(1)

        head = QHBoxLayout()
        head.setSpacing(4)
        lbl_t = QLabel(label)
        lbl_t.setFont(T.f_title(7))
        lbl_t.setStyleSheet(f"color: {T.TXT_TITLE}; background: transparent; border: none;")
        lbl_v = QLabel("--")
        lbl_v.setFont(QFont(T.FONT_MONO, 13, QFont.Bold))
        lbl_v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lbl_v.setStyleSheet(f"color: {T.TXT_VALUE}; background: transparent; border: none;")
        lbl_u = QLabel("°C")
        lbl_u.setFont(T.f_label(7))
        lbl_u.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        lbl_u.setStyleSheet(f"color: {T.TXT_UNIT}; background: transparent; border: none;")
        head.addWidget(lbl_t)
        head.addStretch()
        head.addWidget(lbl_v)
        head.addWidget(lbl_u)
        vbox.addLayout(head)

        bar = QProgressBar()
        bar.setFixedHeight(3)
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setStyleSheet(
            f"QProgressBar {{ background-color: {T.BG_APP}; border: none; }}"
            f"QProgressBar::chunk {{ background-color: {T.OK}; }}"
        )
        vbox.addWidget(bar)

        box.lbl_val = lbl_v
        box.bar = bar
        return box

    def update_brakes(self, temps, bias: float = 0.0):
        for box, temp in zip((self.b_fl, self.b_fr, self.b_rl, self.b_rr), temps):
            color = T.brake_temp_color(temp)
            box.lbl_val.setText(f"{temp:.0f}")
            box.lbl_val.setStyleSheet(
                f"color: {color}; background: transparent; border: none;")
            # Escala 0-900 °C
            box.bar.setValue(int(max(0.0, min(1.0, temp / 900.0)) * 100))
            box.bar.setStyleSheet(
                f"QProgressBar {{ background-color: {T.BG_APP}; border: none; }}"
                f"QProgressBar::chunk {{ background-color: {color}; }}"
            )

        self.lbl_bias_v.setText(f"{bias * 100:.1f}" if bias > 0 else "--")


class GForceCard(BaseCard):
    """
    Diagrama de força G (G-G plot), como no i2.

    O ponto mostra a combinação lateral × longitudinal do instante e o rastro
    mostra os últimos segundos — dá para ver de imediato se o envelope de
    aderência está sendo usado por inteiro ou se as curvas estão "quadradas".
    """
    MAX_G = 3.0          # Escala do anel externo
    TRAIL_LEN = 120      # Amostras no rastro (~2 s a 60 Hz)

    def __init__(self):
        super(GForceCard, self).__init__(title="Força G")
        self._trail = collections.deque(maxlen=self.TRAIL_LEN)
        self._lat = 0.0
        self._lon = 0.0
        self._peak_lat = 0.0
        self._peak_lon = 0.0

        self.canvas = _GForceCanvas(self)
        self.body.addWidget(self.canvas, stretch=1)

        self.row_lat, self.lbl_lat_v = T.channel_row("Lateral", "+0.00", "g")
        self.row_lon, self.lbl_lon_v = T.channel_row("Longitud.", "+0.00", "g")
        self.row_peak, self.lbl_peak_v = T.channel_row("Pico", "0.0/0.0", "g")
        self.body.addWidget(self.row_lat)
        self.body.addWidget(self.row_lon)
        self.body.addWidget(self.row_peak)

    def update_g(self, lat: float, lon: float):
        self._lat, self._lon = lat, lon
        self._trail.append((lat, lon))
        self._peak_lat = max(self._peak_lat, abs(lat))
        self._peak_lon = max(self._peak_lon, abs(lon))

        self.lbl_lat_v.setText(f"{lat:+.2f}")
        self.lbl_lon_v.setText(f"{lon:+.2f}")
        self.lbl_peak_v.setText(f"{self._peak_lat:.1f}/{self._peak_lon:.1f}")
        self.canvas.update()


class _GForceCanvas(QWidget):
    """Área de desenho do GForceCard (separada por causa do paintEvent)."""

    def __init__(self, card: 'GForceCard'):
        super().__init__()
        self._card = card
        self.setMinimumSize(120, 120)
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, event):
        card = self._card
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        side = min(w, h) - 2
        cx, cy = w / 2.0, h / 2.0
        radius = side / 2.0

        # Fundo afundado quadrado (o i2 desenha o G-G dentro de uma caixa)
        painter.setPen(QPen(QColor(T.BORDER_SOFT), 1))
        painter.setBrush(QColor(T.BG_INSET))
        painter.drawRect(int(cx - radius), int(cy - radius), int(side), int(side))

        # Anéis de 1 g, 2 g, 3 g
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(T.GRID), 1))
        for step in range(1, int(card.MAX_G) + 1):
            r = radius * (step / card.MAX_G)
            painter.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

        # Eixos
        painter.drawLine(int(cx - radius), int(cy), int(cx + radius), int(cy))
        painter.drawLine(int(cx), int(cy - radius), int(cx), int(cy + radius))

        # Rótulo do anel externo
        painter.setPen(QPen(QColor(T.TXT_UNIT), 1))
        painter.setFont(QFont(T.FONT_MONO, 6))
        painter.drawText(int(cx + 3), int(cy - radius + 9), f"{card.MAX_G:.0f}g")

        def to_screen(lat, lon):
            # +lat = direita → x cresce | +lon = aceleração → y sobe na tela
            x = cx + (max(-card.MAX_G, min(card.MAX_G, lat)) / card.MAX_G) * radius
            y = cy - (max(-card.MAX_G, min(card.MAX_G, lon)) / card.MAX_G) * radius
            return x, y

        # Rastro (mais antigo = mais apagado)
        trail = list(card._trail)
        total = len(trail)
        painter.setPen(Qt.NoPen)
        for i, (lat, lon) in enumerate(trail):
            color = QColor(T.CH_GFORCE)
            color.setAlpha(int(18 + 170 * (i / max(1, total - 1))))
            painter.setBrush(color)
            x, y = to_screen(lat, lon)
            painter.drawEllipse(int(x - 1), int(y - 1), 3, 3)

        # Ponto atual
        x, y = to_screen(card._lat, card._lon)
        g_total = (card._lat ** 2 + card._lon ** 2) ** 0.5
        dot = T.BAD if g_total > 2.0 else (T.WARN if g_total > 1.2 else T.OK)
        painter.setPen(QPen(QColor(T.BG_APP), 1))
        painter.setBrush(QColor(dot))
        painter.drawEllipse(int(x - 4), int(y - 4), 9, 9)

        painter.end()


class LegendsRow(QWidget):
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._add_legend(layout, T.PURPLE, "recorde da sessão")
        self._add_legend(layout, T.OK, "mais rápido que sua melhor volta")
        self._add_legend(layout, T.BAD, "mais lento")
        layout.addStretch()

    def _add_legend(self, layout, color, text):
        box = QFrame()
        box.setFixedSize(8, 8)
        box.setStyleSheet(f"background-color: {color}; border: none;")
        lbl = QLabel(text)
        lbl.setFont(T.f_label(8))
        lbl.setStyleSheet(f"color: {T.TXT_UNIT};")
        layout.addWidget(box)
        layout.addWidget(lbl)
        layout.addSpacing(14)


class TimeAxisItem(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        strings = []
        for val in values:
            if val < 0:
                strings.append("")
                continue
            minutes = int(val // 60)
            seconds = int(val % 60)
            if minutes > 0:
                strings.append(f"{minutes}:{seconds:02d}")
            else:
                strings.append(f"{seconds}")
        return strings


class CustomPlot(pg.PlotWidget):
    """
    Gráfico de canal no estilo i2.

    Diferenças em relação a um plot solto:
      * Gutter do eixo Y de largura FIXA, para todos os gráficos da pilha
        ficarem alinhados verticalmente na mesma coluna.
      * Eixo X só é exibido no último gráfico da pilha (`show_x=True`) —
        no i2 a escala de tempo/distância aparece uma única vez, embaixo.
      * Nome do canal e valor ao vivo desenhados no canto superior esquerdo,
        sobre a grade, em vez de um título centralizado.
    """
    Y_GUTTER = 52

    def __init__(self, title, color=None, show_x=False, unit=""):
        super(CustomPlot, self).__init__(
            axisItems={'bottom': TimeAxisItem(orientation='bottom')})

        self._unit = unit
        self.setBackground(T.BG_PANEL)
        self.showGrid(x=True, y=True, alpha=0.28)
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.setMenuEnabled(False)
        self.setStyleSheet(f"border: 1px solid {T.BORDER};")

        for ax in ('left', 'bottom'):
            axis = self.getAxis(ax)
            axis.setPen(pg.mkPen(T.BORDER))
            axis.setTextPen(pg.mkPen(T.TXT_UNIT))
            axis.setStyle(tickFont=QFont(T.FONT_MONO, 7), tickLength=-3)

        # Coluna do eixo Y com largura fixa: alinha a pilha inteira
        self.getAxis('left').setWidth(self.Y_GUTTER)
        self.showAxis('bottom', show_x)
        # Sem eixo X, o pyqtgraph corta o rótulo do tick mais baixo do eixo Y;
        # a margem inferior devolve o espaço para ele.
        self.getPlotItem().layout.setContentsMargins(0, 4, 6, 0 if show_x else 7)

        # Cabeçalho sobreposto: NOME DO CANAL  +  valor ao vivo
        self.hdr_name = QLabel(title.upper(), self)
        self.hdr_name.setFont(T.f_title(7))
        self.hdr_name.setStyleSheet(
            f"color: {T.TXT_TITLE}; background: transparent; border: none;")
        self.hdr_name.adjustSize()

        self.hdr_value = QLabel("--", self)
        self.hdr_value.setFont(QFont(T.FONT_MONO, 10, QFont.Bold))
        self.hdr_value.setStyleSheet(
            f"color: {color or T.TXT_VALUE}; background: transparent; border: none;")
        self.hdr_value.adjustSize()

        self._reposition_header()

    def _reposition_header(self):
        # O pyqtgraph dispara resizeEvent durante o próprio __init__, antes de
        # os rótulos existirem — daí o guard.
        if "hdr_name" not in self.__dict__:
            return
        x = self.Y_GUTTER + 8
        self.hdr_name.move(x, 4)
        self.hdr_value.move(x + self.hdr_name.width() + 10, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_header()

    def set_live_value(self, text: str):
        """Atualiza o valor ao vivo mostrado no cabeçalho do gráfico."""
        self.hdr_value.setText(f"{text} {self._unit}".strip())
        self.hdr_value.adjustSize()


class LapHistoryTable(QTableWidget):
    def __init__(self):
        super(LapHistoryTable, self).__init__(0, 6)
        self.setHorizontalHeaderLabels(["VOLTA", "S1", "S2", "S3", "TEMPO", "Δ BEST"])
        self._best_row = -1
        self.setStyleSheet(f"""
            QTableWidget {{
                background-color: {T.BG_PANEL};
                color: {T.TXT_VALUE};
                gridline-color: {T.BORDER_SOFT};
                border: 1px solid {T.BORDER};
                border-radius: 0px;
                font-family: "{T.FONT_MONO}";
                font-size: 9pt;
            }}
            QTableWidget::item {{ padding: 2px 5px; }}
            QHeaderView::section {{
                background-color: {T.BG_HEADER};
                color: {T.TXT_TITLE};
                padding: 3px;
                border: none;
                border-bottom: 1px solid {T.BORDER};
                font-family: "{T.FONT_UI}";
                font-size: 8pt;
                font-weight: bold;
            }}
        """)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(22)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setSelectionMode(QTableWidget.NoSelection)
        self.setShowGrid(True)

    def highlight_best_lap(self, best_row: int, prev_best_row: int = -1):
        """Destaca a volta mais rápida e limpa o destaque anterior."""
        if prev_best_row >= 0 and prev_best_row != best_row:
            for col in range(self.columnCount()):
                item = self.item(prev_best_row, col)
                if item:
                    item.setBackground(QColor(T.BG_PANEL))
                    item.setForeground(QColor(T.TXT_VALUE))
        if best_row >= 0:
            for col in range(self.columnCount()):
                item = self.item(best_row, col)
                if item:
                    item.setBackground(QColor("#12241a"))
                    item.setForeground(QColor(T.OK))
