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
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView, QWidget, QComboBox, QSizePolicy,
    QSpacerItem, QPushButton, QListWidget, QListWidgetItem
)
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QFont, QColor, QPainter, QPen, QPolygonF
import math
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
        lbl.setFont(T.f_title(5))
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
        lbl_cap = QLabel("RPM")
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
    """Combustível, bateria ERS/Híbrida, autonomia e turbo — em linhas de canal.

    O ângulo do volante é mostrado pelo widget de volante da sidebar, não aqui.
    """
    def __init__(self):
        super(CarDataCard, self).__init__(title="Carro")

        self.row_fuel, self.lbl_fuel_v = T.channel_row("Combustível", "0.0", "L")
        self.bar_fuel = QProgressBar()
        self.bar_fuel.setFixedHeight(3)
        self.bar_fuel.setRange(0, 100)
        self.bar_fuel.setTextVisible(False)
        self._set_fuel_bar(T.OK)

        self.row_energy, self.lbl_energy_v = T.channel_row("Energia ERS", "--", "%")
        self.bar_energy = QProgressBar()
        self.bar_energy.setFixedHeight(3)
        self.bar_energy.setRange(0, 100)
        self.bar_energy.setTextVisible(False)
        self._set_energy_bar("#00e5ff")

        self.row_laps, self.lbl_laps_v = T.channel_row("Voltas est.", "0.0", "")
        self.row_avg,  self.lbl_avg_v  = T.channel_row("Consumo", "--", "L/v")
        self.row_turbo, self.lbl_turbo_v = T.channel_row("Turbo", "0.00", "bar")

        self.body.addWidget(self.row_fuel)
        self.body.addWidget(self.bar_fuel)
        self.body.addWidget(self.row_energy)
        self.body.addWidget(self.bar_energy)
        for row in (self.row_laps, self.row_avg, self.row_turbo):
            self.body.addWidget(row)

        # Aliases antigos (compatibilidade)
        self.lbl_fuel_avg = self.lbl_avg_v

    def _set_fuel_bar(self, color):
        self.bar_fuel.setStyleSheet(
            f"QProgressBar {{ background-color: {T.BG_INSET};"
            f"border: 1px solid {T.BORDER_SOFT}; border-radius: 0px; }}"
            f"QProgressBar::chunk {{ background-color: {color}; }}"
        )

    def _set_energy_bar(self, color):
        self.bar_energy.setStyleSheet(
            f"QProgressBar {{ background-color: {T.BG_INSET};"
            f"border: 1px solid {T.BORDER_SOFT}; border-radius: 0px; }}"
            f"QProgressBar::chunk {{ background-color: {color}; }}"
        )

    def update_data(self, fuel: float, laps: float, turbo: float, steer: float,
                    fuel_avg: float = 0.0, fuel_capacity: float = 0.0,
                    has_kers: bool = False, kers_charge: float = 0.0):
        self.lbl_fuel_v.setText(f"{fuel:.1f}")
        self.lbl_laps_v.setText(f"{laps:.1f}")
        self.lbl_turbo_v.setText(f"{turbo:.2f}")
        self.lbl_avg_v.setText(f"{fuel_avg:.2f}" if fuel_avg > 0 else "--")

        if fuel_capacity > 0:
            pct = max(0.0, min(1.0, fuel / fuel_capacity))
            color = T.BAD if pct < 0.10 else (T.WARN if pct < 0.25 else T.OK)
            self.bar_fuel.setValue(int(pct * 100))
            self._set_fuel_bar(color)
            self.lbl_fuel_v.setStyleSheet(
                f"color: {color}; background: transparent; border: none;")
            self.row_fuel.lbl_name.setText(f"Combustível {fuel_capacity:.0f}L")

        # Exibe nível de carga do ERS / Bateria Híbrida quando disponível (F1 / Hypercar / LMH / LMDh)
        if has_kers or kers_charge > 0.0:
            self.row_energy.setVisible(True)
            self.bar_energy.setVisible(True)
            e_pct = max(0.0, min(1.0, kers_charge)) * 100.0
            self.lbl_energy_v.setText(f"{e_pct:.0f}")
            e_color = T.BAD if e_pct < 15.0 else (T.WARN if e_pct < 30.0 else "#00e5ff")
            self.bar_energy.setValue(int(e_pct))
            self._set_energy_bar(e_color)
            self.lbl_energy_v.setStyleSheet(
                f"color: {e_color}; background: transparent; border: none;")
        else:
            self.row_energy.setVisible(False)
            self.bar_energy.setVisible(False)


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
        vbox.setContentsMargins(5, 5, 5, 5)
        vbox.setSpacing(1)

        head = QHBoxLayout()
        head.setSpacing(4)
        lbl_title = QLabel(label)
        lbl_title.setFont(T.f_title(7))
        lbl_title.setStyleSheet(f"color: {T.TXT_TITLE}; background: transparent; border: none;")
        lbl_temp = QLabel("--")
        lbl_temp.setFont(QFont(T.FONT_MONO, 10, QFont.Bold))
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
        lbl_psi.setFont(QFont(T.FONT_MONO, 7))
        lbl_psi.setStyleSheet(f"color: {T.TXT_LABEL}; background: transparent; border: none;")
        lbl_wear = QLabel("--%")
        lbl_wear.setFont(QFont(T.FONT_MONO, 7))
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
    """Indicador retangular de estado (I/0 equipado/ativo na extrema esquerda)."""
    COLORS = {
        "ABS":  "#FFEA00",  # Amarelo vibrante na intervenção do ABS
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
        self.pill = QLabel()
        self.pill.setFont(QFont(T.FONT_MONO, 11, QFont.Bold))
        self.pill.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.pill.setFixedHeight(34)
        self.set_state(is_equipped=True, is_active=False)
        # O mínimo sai do próprio texto: com 60 px fixos, os rótulos longos
        # apareciam cortados ("I AB", "I KER") quando o rodapé apertava.
        # `set_state` só troca I por 0, então a largura não muda depois.
        self.pill.setMinimumWidth(max(60, self.pill.sizeHint().width()))
        layout.addWidget(self.pill)

    def set_state(self, is_equipped: bool = True, is_active: bool = False, color: str = ""):
        val_str = "I" if is_equipped else "0"
        self.pill.setText(f" {val_str}  {self._label}")
        if is_active:
            bg = color or self.COLORS.get(self._label, T.OK)
            self.pill.setStyleSheet(
                f"color: #0d0f11; background-color: {bg};"
                f"border: 1px solid {bg}; border-radius: 0px; padding-left: 6px;"
            )
        elif is_equipped:
            self.pill.setStyleSheet(
                f"color: {T.TXT_VALUE}; background-color: {T.BG_INSET};"
                f"border: 1px solid {T.BORDER_SOFT}; border-radius: 0px; padding-left: 6px;"
            )
        else:
            self.pill.setStyleSheet(
                f"color: {T.TXT_DIM}; background-color: {T.BG_INSET};"
                f"border: 1px solid {T.BORDER_SOFT}; border-radius: 0px; padding-left: 6px;"
            )

    def set_active(self, active: bool, color: str = ""):
        self.set_state(is_equipped=True, is_active=active, color=color)


class AssistsCard(BaseCard):
    """
    Eletrônica do carro.

    No Assetto Corsa o ABS e o TC vêm como INTENSIDADE da intervenção, então
    as barras mostram o quanto cada sistema está atuando naquele instante.
    """
    def __init__(self):
        super(AssistsCard, self).__init__(title="Eletrônica", margins=(4, 4, 4, 4), spacing=2)
        self.body.setAlignment(Qt.AlignTop)

        leds = QGridLayout()
        leds.setSpacing(4)
        self.led_abs = AssistLED("ABS")
        self.led_tc = AssistLED("TC")
        self.led_pit = AssistLED("PIT")
        self.led_drs = AssistLED("DRS")
        self.led_kers = AssistLED("KERS")
        self.led_box = AssistLED("BOX")
        for i, led in enumerate((self.led_abs, self.led_tc, 
                                 self.led_drs, self.led_kers, 
                                 self.led_pit, self.led_box)):
            leds.addWidget(led, i // 2, i % 2)
        self.body.addLayout(leds)

        self.bar_abs = self._make_bar(AssistLED.COLORS["ABS"])
        self.bar_tc = self._make_bar(AssistLED.COLORS["TC"])
        self.body.addWidget(self.bar_abs)
        self.body.addWidget(self.bar_tc)

        self.row_ffb, self.lbl_ffb_v = T.channel_row("FFB", "0", "%", value_size=13, label_size=10)
        self.body.addWidget(self.row_ffb)
        # Alias antigo
        self.lbl_ffb = self.lbl_ffb_v

        # Espaçador vertical para empurrar o conteúdo para cima, alinhando ao topo
        self.body.addSpacerItem(QSpacerItem(20, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

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
        has_abs  = getattr(state, 'has_abs', True)
        has_tc   = getattr(state, 'has_tc', True)
        has_drs  = getattr(state, 'has_drs', False)
        has_kers = getattr(state, 'has_kers', False) or getattr(state, 'has_ers', False)

        abs_interv = getattr(state, 'abs_intervention', 0.0)
        abs_intervening = (abs_interv > 0.02) or getattr(state, 'abs_active', False)

        tc_interv = getattr(state, 'tc_intervention', 0.0)
        tc_intervening = (tc_interv > 0.02) or getattr(state, 'tc_active', False)

        self.led_abs.set_state(is_equipped=has_abs, is_active=abs_intervening, color="#FFEA00")
        self.led_tc.set_state(is_equipped=has_tc, is_active=tc_intervening, color=T.CH_SPEED)
        self.led_pit.set_state(is_equipped=True, is_active=state.pit_limiter, color=T.BAD)
        self.led_drs.set_state(is_equipped=has_drs, is_active=state.drs_active, color=T.OK)
        self.led_kers.set_state(is_equipped=has_kers, is_active=(state.kers_charge > 0.01), color=T.PURPLE)
        self.led_box.set_state(is_equipped=True, is_active=(state.in_pit_lane or state.in_pit), color="#ff8800")

        self.bar_abs.setValue(int(state.abs_intervention * 100))
        self.bar_tc.setValue(int(state.tc_intervention * 100))

        ffb_pct = state.ffb_level * 100
        self.lbl_ffb_v.setText(f"{ffb_pct:.0f}")
        if ffb_pct > 95:
            color = T.BAD
            self.row_ffb.lbl_name.setText("FFB CLIP")
        else:
            color = T.WARN if ffb_pct > 80 else T.TXT_VALUE
            self.row_ffb.lbl_name.setText("FFB")
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


class LapSelectorCard(QWidget):
    """Seletor e navegador da volta a ser exibida nos gráficos."""
    def __init__(self):
        super(LapSelectorCard, self).__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        lbl_title = QLabel("VOLTA")
        lbl_title.setFont(T.f_title(10))
        lbl_title.setStyleSheet(f"color: {T.TXT_TITLE}; background: transparent;")

        btn_style = f"""
            QPushButton {{
                background-color: {T.BG_INSET};
                color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER};
                border-radius: 0px;
                padding: 4px 8px;
                font-family: "{T.FONT_UI}";
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {T.BG_HEADER}; color: #ffffff; }}
            QPushButton:disabled {{ color: #555555; background-color: {T.BG_INSET}; border-color: {T.BORDER_SOFT}; }}
        """

        self.btn_prev = QPushButton("◄")
        self.btn_prev.setFont(T.f_title(9))
        self.btn_prev.setCursor(Qt.PointingHandCursor)
        self.btn_prev.setStyleSheet(btn_style)
        # A lista vai da volta mais recente para a mais antiga, então subir
        # nela (◄) é ir para uma volta MAIS recente
        self.btn_prev.setToolTip("Volta mais recente")

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
        self.combo.addItem("Volta Atual (Ao Vivo)")

        self.btn_next = QPushButton("►")
        self.btn_next.setFont(T.f_title(9))
        self.btn_next.setCursor(Qt.PointingHandCursor)
        self.btn_next.setStyleSheet(btn_style)
        self.btn_next.setToolTip("Volta mais antiga")

        self.btn_prev.clicked.connect(self._on_prev_clicked)
        self.btn_next.clicked.connect(self._on_next_clicked)
        self.combo.currentIndexChanged.connect(self._update_nav_buttons)

        layout.addWidget(lbl_title, alignment=Qt.AlignVCenter)
        layout.addWidget(self.btn_prev, alignment=Qt.AlignVCenter)
        layout.addWidget(self.combo, alignment=Qt.AlignVCenter)
        layout.addWidget(self.btn_next, alignment=Qt.AlignVCenter)

        self._update_nav_buttons()

    def _on_prev_clicked(self):
        idx = self.combo.currentIndex()
        if idx > 0:
            self.combo.setCurrentIndex(idx - 1)

    def _on_next_clicked(self):
        idx = self.combo.currentIndex()
        if idx < self.combo.count() - 1:
            self.combo.setCurrentIndex(idx + 1)

    def _update_nav_buttons(self):
        idx = self.combo.currentIndex()
        count = self.combo.count()
        self.btn_prev.setEnabled(idx > 0)
        self.btn_next.setEnabled(idx < count - 1)


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
        super(WeatherCard, self).__init__(title="Pista", spacing=6)

        self.row_amb, self.lbl_amb_v = T.channel_row("Ar", "--", "°C", value_size=11, label_size=10)
        self.row_trk, self.lbl_trk_v = T.channel_row("Asfalto", "--", "°C", value_size=11, label_size=10)
        self.row_grip, self.lbl_grip_v = T.channel_row("Grip", "--", "%", value_size=11, label_size=10)
        self.row_wind, self.lbl_wind_v = T.channel_row("Vento", "--", "km/h", value_size=11, label_size=10)
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


class SteeringWheelWidget(QWidget):
    """Visualizador gráfico do volante."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(60, 60)
        self._angle = 0.0

    def set_angle(self, angle: float):
        self._angle = angle
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w = self.width()
        h = self.height()
        side = min(w, h)
        painter.translate(w / 2, h / 2)
        
        color = QColor(T.CH_STEER)
        
        pen_circle = QPen(color, max(4, int(side * 0.05)), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        pen_spoke = QPen(color, max(5, int(side * 0.08)), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        
        radius = side / 2.0 - pen_circle.width()
        
        painter.setPen(pen_circle)
        painter.drawEllipse(QPointF(0, 0), radius, radius)
        
        painter.save()
        painter.rotate(self._angle)
        
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        
        # Barra horizontal (raios laterais)
        painter.drawRect(int(-radius + 2), -5, int(radius * 2 - 4), 10)
        
        # Barra vertical (raio inferior)
        painter.drawRect(-5, 5, 10, int(radius - 5))
        
        painter.restore()


class SteeringWheelCard(BaseCard):
    """Cartão que contém o visualizador do volante e seu valor."""
    def __init__(self):
        super(SteeringWheelCard, self).__init__(title="", margins=(0, 5, 0, 5))
        
        self.wheel_widget = SteeringWheelWidget()
        self.wheel_widget.setFixedSize(80, 80)
        
        self.lbl_angle = QLabel("0.0°")
        self.lbl_angle.setFont(QFont(T.FONT_MONO, 12, QFont.Bold))
        self.lbl_angle.setAlignment(Qt.AlignCenter)
        self.lbl_angle.setStyleSheet(f"color: {T.CH_STEER}; background: transparent; border: none;")
        
        lay = QVBoxLayout()
        lay.setAlignment(Qt.AlignCenter)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self.wheel_widget, alignment=Qt.AlignCenter)
        lay.addWidget(self.lbl_angle, alignment=Qt.AlignCenter)
        
        self.body.addLayout(lay)
        
    def update_steer(self, steer_angle: float):
        self.wheel_widget.set_angle(steer_angle)
        self.lbl_angle.setText(f"{steer_angle:.1f}°")


class SessionCard(BaseCard):
    """Tipo de sessão, posição, voltas, tempo restante, composto e danos."""
    def __init__(self):
        super(SessionCard, self).__init__(title="Sessão", spacing=6)

        self.row_pos, self.lbl_pos_v = T.channel_row("Posição", "--", "", value_size=11, label_size=10)
        self.row_laps, self.lbl_laps_v = T.channel_row("Volta", "--", "", value_size=11, label_size=10)
        self.row_left, self.lbl_left_v = T.channel_row("Restante", "--", "", value_size=11, label_size=10)
        # "Comp." em vez de "Composto": com o nome inteiro, o rótulo era
        # cortado ("Compostc") assim que o valor era um composto de nome longo
        self.row_comp, self.lbl_comp_v = T.channel_row("Comp.", "--", "", value_size=11, label_size=10)
        self.row_dmg, self.lbl_dmg_v = T.channel_row("Danos", "0", "%", value_size=11, label_size=10)
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

        # O AC manda nomes longos ("Semislick (SM)"), que estouravam a linha e
        # cobriam o rótulo. Quando o nome traz o código entre parênteses, é ele
        # que aparece — é como o composto é chamado no box.
        compound = (state.tyre_compound or "").strip()
        if compound.endswith(")") and "(" in compound:
            compound = compound[compound.rfind("(") + 1:-1].strip() or compound
        self.lbl_comp_v.setText(compound or "--")

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

        # "Bias" em vez de "Brake Bias": o painel já se chama FREIOS, e o nome
        # inteiro empurrava o valor contra a borda do card
        self.row_bias, self.lbl_bias_v = T.channel_row("Bias", "--", "%", value_size=14, label_size=11)
        self.body.addWidget(self.row_bias)
        self.body.addStretch()
        # Alias antigo
        self.lbl_bias = self.lbl_bias_v

    def _make_brake(self, label):
        box = QFrame()
        box.setStyleSheet(T.inset_qss())
        vbox = QVBoxLayout(box)
        # 6 px em vez de 8: temperatura de disco tem 3 dígitos ("180") e, com
        # a margem larga, o primeiro dígito era cortado em janela estreita
        vbox.setContentsMargins(6, 6, 6, 6)
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
        super(GForceCard, self).__init__(title="Força G", margins=(6, 6, 6, 6))
        self._trail = collections.deque(maxlen=self.TRAIL_LEN)
        self._lat = 0.0
        self._lon = 0.0
        self._peak_lat = 0.0
        self._peak_lon = 0.0

        self.canvas = _GForceCanvas(self)
        self.body.addWidget(self.canvas, stretch=1)

        self.row_lat, self.lbl_lat_v = T.channel_row("Lat", "+0.00", "g")
        self.row_lon, self.lbl_lon_v = T.channel_row("Lon", "+0.00", "g")
        self.row_peak, self.lbl_peak_v = T.channel_row("Pico", "0.0/0.0", "g")
        
        # Alinhamento e largura mínima para evitar encavalamento com 'g'
        for row, lbl in ((self.row_lat, self.lbl_lat_v), 
                         (self.row_lon, self.lbl_lon_v), 
                         (self.row_peak, self.lbl_peak_v)):
            lbl.setMinimumWidth(45)
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        
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
        self.showGrid(x=False, y=True, alpha=0.20)
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

    def set_live_value(self, text: str, is_html: bool = False):
        """Atualiza o valor ao vivo mostrado no cabeçalho do gráfico."""
        if is_html:
            self.hdr_value.setText(f"{text}&nbsp;<span style='color:{T.TXT_UNIT};'>{self._unit}</span>")
        else:
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
            QTableWidget::item {{ padding: 2px 3px; }}
            QHeaderView::section {{
                background-color: {T.BG_HEADER};
                color: {T.TXT_TITLE};
                padding: 1px;
                border: none;
                border-bottom: 1px solid {T.BORDER};
                font-family: "{T.FONT_UI}";
                font-size: 8pt;
                font-weight: bold;
            }}
        """)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        # TEMPO e Δ BEST dividem a sobra: são as colunas que o piloto lê de
        # longe, e as de setor já se ajustam ao conteúdo
        self.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        # 20 px em vez de 24: cabem 7 voltas na faixa de 180 px do rodapé,
        # em vez de 5, sem apertar a fonte de 9 pt
        self.verticalHeader().setDefaultSectionSize(20)
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

class EngineerPanel(BaseCard):
    """
    Painel do Engenheiro de Pista.

    Uma lista de recados, o mais recente no topo, com a cor indicando a
    urgência. O seletor de modo decide QUANDO o engenheiro fala:

        Fim de volta — o balanço da volta que acabou (padrão)
        Ao vivo      — avisos com o carro na pista (roda travando, pneu, bandeira)
        Sob demanda  — só quando você clica em ANALISAR

    O botão de voz liga/desliga a fala sem apagar o texto.
    """

    MODE_LAP = 0
    MODE_LIVE = 1
    MODE_MANUAL = 2

    MAX_MESSAGES = 40

    SEVERITY_COLORS = {
        "critico": T.BAD,
        "atencao": T.WARN,
        "info": T.TXT_LABEL,
    }
    SEVERITY_MARKS = {"critico": "!!", "atencao": "! ", "info": "  "}

    def __init__(self):
        super(EngineerPanel, self).__init__(title="Engenheiro", margins=(4, 4, 4, 4),
                                            spacing=3)

        control_row = QHBoxLayout()
        control_row.setContentsMargins(0, 0, 0, 0)
        control_row.setSpacing(3)

        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Fim de volta", "Ao vivo", "Sob demanda"])
        self.combo_mode.setToolTip("Quando o engenheiro deve falar")
        self.combo_mode.setStyleSheet(f"""
            QComboBox {{
                background-color: {T.BG_INSET}; color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER}; border-radius: 0px;
                padding: 2px 6px; font-family: "{T.FONT_UI}"; font-size: 11px;
            }}
            QComboBox:hover {{ border: 1px solid #3d454c; }}
            QComboBox::drop-down {{ border: none; width: 14px; }}
            QComboBox QAbstractItemView {{
                background-color: {T.BG_PANEL}; color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER};
                selection-background-color: {T.BG_HEADER};
                font-size: 11px; outline: none;
            }}
        """)

        btn_style = f"""
            QPushButton {{
                background-color: {T.BG_INSET}; color: {T.TXT_LABEL};
                border: 1px solid {T.BORDER}; border-radius: 0px;
                padding: 2px 6px; font-family: "{T.FONT_UI}";
                font-size: 10px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {T.BG_HEADER}; color: {T.TXT_VALUE}; }}
            QPushButton:checked {{ color: {T.OK}; border: 1px solid {T.OK}; }}
            QPushButton:disabled {{ color: #4a5157; border-color: {T.BORDER_SOFT}; }}
        """

        self.btn_voice = QPushButton("VOZ")
        self.btn_voice.setCheckable(True)
        self.btn_voice.setChecked(True)
        self.btn_voice.setCursor(Qt.PointingHandCursor)
        self.btn_voice.setStyleSheet(btn_style)
        self.btn_voice.setToolTip("Liga/desliga a fala (o texto continua)")

        self.btn_analyze = QPushButton("ANALISAR")
        self.btn_analyze.setCursor(Qt.PointingHandCursor)
        self.btn_analyze.setStyleSheet(btn_style)
        self.btn_analyze.setToolTip("Analisa agora a volta exibida nos gráficos")

        control_row.addWidget(self.combo_mode, stretch=1)
        control_row.addWidget(self.btn_voice)
        control_row.addWidget(self.btn_analyze)
        self.body.addLayout(control_row)

        self.list_messages = QListWidget()
        self.list_messages.setWordWrap(True)
        self.list_messages.setSelectionMode(QListWidget.NoSelection)
        self.list_messages.setFocusPolicy(Qt.NoFocus)
        self.list_messages.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_messages.setStyleSheet(f"""
            QListWidget {{
                background-color: {T.BG_INSET};
                border: 1px solid {T.BORDER_SOFT}; border-radius: 0px;
                font-family: "{T.FONT_UI}"; font-size: 11px;
                outline: none;
            }}
            QListWidget::item {{ padding: 2px 3px; border: none; }}
        """)
        self.body.addWidget(self.list_messages, stretch=1)

        self._empty_hint()

    # -- estado -----------------------------------------------------------

    @property
    def mode(self) -> int:
        return self.combo_mode.currentIndex()

    @property
    def voice_on(self) -> bool:
        return self.btn_voice.isChecked()

    def _empty_hint(self):
        self.list_messages.clear()
        item = QListWidgetItem("Aguardando a primeira volta...")
        item.setForeground(QColor(T.TXT_DIM))
        self.list_messages.addItem(item)
        self._has_messages = False

    # -- API --------------------------------------------------------------

    def add_advice(self, advice, prefix: str = ""):
        """Insere um recado no topo da lista."""
        if not getattr(self, "_has_messages", False):
            self.list_messages.clear()
            self._has_messages = True

        mark = self.SEVERITY_MARKS.get(advice.severity, "  ")
        texto = f"{mark} {prefix}{advice.display}" if prefix else f"{mark} {advice.display}"
        item = QListWidgetItem(texto)
        item.setForeground(QColor(self.SEVERITY_COLORS.get(advice.severity, T.TXT_VALUE)))
        item.setToolTip(advice.display)
        self.list_messages.insertItem(0, item)

        while self.list_messages.count() > self.MAX_MESSAGES:
            self.list_messages.takeItem(self.list_messages.count() - 1)
        self.list_messages.scrollToTop()

    def add_separator(self, texto: str):
        """Cabeçalho de bloco, tipo 'VOLTA 12 — 1:29.305'."""
        if not getattr(self, "_has_messages", False):
            self.list_messages.clear()
            self._has_messages = True
        item = QListWidgetItem(texto.upper())
        item.setForeground(QColor(T.TXT_TITLE))
        f = item.font()
        f.setBold(True)
        item.setFont(f)
        self.list_messages.insertItem(0, item)
        self.list_messages.scrollToTop()

    def clear_messages(self):
        self._empty_hint()


class CornerAnalysisTable(QTableWidget):
    """
    Tabela Curva a Curva (Turn-by-Turn).

    Uma linha por curva da pista, exibindo a numeração da curva, o tempo
    gasto na curva e o delta de tempo contra a volta de referência.
    """

    HEADERS = ["CURVA", "TEMPO s", "Δ T s"]

    def __init__(self):
        super(CornerAnalysisTable, self).__init__(0, len(self.HEADERS))
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.setStyleSheet(f"""
            QTableWidget {{
                background-color: {T.BG_PANEL};
                color: {T.TXT_VALUE};
                gridline-color: {T.BORDER_SOFT};
                border: none;
                border-radius: 0px;
                font-family: "{T.FONT_MONO}";
                font-size: 9pt;
            }}
            QTableWidget::item {{ padding: 2px 5px; }}
            QHeaderView::section {{
                background-color: {T.BG_HEADER};
                color: {T.TXT_TITLE};
                padding: 1px;
                border: none;
                border-bottom: 1px solid {T.BORDER};
                font-family: "{T.FONT_UI}";
                font-size: 8pt;
                font-weight: bold;
            }}
        """)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        # Pistas de verdade têm 10-20 curvas: cada pixel de linha economizado
        # é uma curva a mais visível sem rolar
        self.verticalHeader().setDefaultSectionSize(19)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setSelectionMode(QTableWidget.NoSelection)
        self.setShowGrid(True)
        self._worst_row = -1

    # -- helpers de formatação -------------------------------------------

    # Sem o sufixo "s": o cabeçalho já diz que a coluna é tempo, e os dois
    # caracteres economizados são o que fazia o delta aparecer cortado
    # ("+0.2…") no painel estreito do rodapé.
    @staticmethod
    def _fmt_sec_time(value) -> str:
        return "--" if value is None else f"{value:.3f}"

    @staticmethod
    def _fmt_delta_t(value) -> str:
        return "--" if value is None else f"{value:+.3f}"

    def _set(self, row: int, col: int, text: str, color: str = None):
        item = self.item(row, col)
        if item is None:
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignCenter)
            self.setItem(row, col, item)
        elif item.text() != text:
            item.setText(text)
        item.setForeground(QColor(color or T.TXT_VALUE))

    def update_corners(self, comparisons: list):
        """
        Redesenha a tabela a partir de uma lista de
        `core.corner_analysis.CornerComparison`.
        """
        if self.rowCount() != len(comparisons):
            self.setRowCount(len(comparisons))
            self._worst_row = -1

        worst_row, worst_loss = -1, 0.0

        for row, cmp_ in enumerate(comparisons):
            corner = cmp_.corner
            label = f"C{corner.index}"
            self._set(row, 0, label, T.TXT_LABEL)

            # Tempo gasto na curva
            sec_time = cmp_.lap.section_time
            self._set(row, 1, self._fmt_sec_time(sec_time), T.TXT_VALUE)

            # Delta de tempo: negativo é ganho
            d_t = cmp_.delta_time
            self._set(row, 2, self._fmt_delta_t(d_t),
                      self._color_for(d_t, higher_is_better=False))

            if d_t is not None and d_t > worst_loss:
                worst_loss, worst_row = d_t, row

        self._highlight_worst(worst_row)

    @staticmethod
    def _color_for(value, higher_is_better: bool) -> str:
        """Verde quando o piloto está melhor que a referência, vermelho quando pior."""
        if value is None:
            return T.TXT_DIM
        if abs(value) < 1e-9:
            return T.TXT_DIM
        better = value > 0 if higher_is_better else value < 0
        return T.OK if better else T.BAD

    def _highlight_worst(self, worst_row: int):
        """Marca a curva onde mais tempo foi perdido — onde começar a trabalhar."""
        if self._worst_row == worst_row:
            return
        if self._worst_row >= 0:
            for col in range(self.columnCount()):
                item = self.item(self._worst_row, col)
                if item:
                    item.setBackground(QColor(T.BG_PANEL))
        if worst_row >= 0:
            for col in range(self.columnCount()):
                item = self.item(worst_row, col)
                if item:
                    item.setBackground(QColor("#2a1214"))
        self._worst_row = worst_row


class TrackMapWidget(QWidget):
    def __init__(self):
        super(TrackMapWidget, self).__init__()
        # --- Camada 1: traçado cinza permanente da pista (layout) ---
        self._bg_x = []
        self._bg_z = []
        # --- Camada 2: traçado do ghost (opcional, sobreposto ao cinza) ---
        self._ghost_x = []
        self._ghost_z = []
        self._ghost_gas = []
        self._ghost_brake = []
        # --- Camada 3: traçado ao vivo colorido ---
        self._live_x = []
        self._live_z = []
        self._live_gas = []
        self._live_brake = []
        # --- Marcador do carro ---
        self._marker_x = None
        self._marker_z = None
        self._marker_gas = 0.0
        self._marker_brake = 0.0

    def set_data(self, x, z, gas=None, brake=None):
        """Define o traçado do ghost de referência (camada 2, sobre o cinza).
        Se não houver base trace ainda, usa estes dados também como base cinza."""
        n = min(len(x or []), len(z or []))
        gn = min(n, len(gas or []), len(brake or [])) if gas and brake else 0

        self._ghost_x = list(x[:n]) if n else []
        self._ghost_z = list(z[:n]) if n else []
        self._ghost_gas = list(gas[:gn]) if gn else []
        self._ghost_brake = list(brake[:gn]) if gn else []

        # Se o traçado cinza ainda não foi definido por uma volta real, usa o ghost como base
        if len(self._bg_x) < 2 and n >= 2:
            self._bg_x = list(x[:n])
            self._bg_z = list(z[:n])

        self.update()

    def set_base_trace(self, x, z):
        """Fixa o traçado cinza permanente da pista com coordenadas reais.
        Chamado após a primeira volta completa. Uma vez definido, não é apagado
        por mudanças de ghost ou referência."""
        n = min(len(x or []), len(z or []))
        if n >= 2:
            self._bg_x = list(x[:n])
            self._bg_z = list(z[:n])
            self.update()

    def set_live_data(self, x, z, gas, brake):
        """Define o traçado ao vivo da volta atual (colorido)."""
        n = min(len(x or []), len(z or []), len(gas or []), len(brake or []))
        self._live_x = list(x[:n]) if n else []
        self._live_z = list(z[:n]) if n else []
        self._live_gas = list(gas[:n]) if n else []
        self._live_brake = list(brake[:n]) if n else []
        self.update()

    def set_marker(self, x, z, gas=0.0, brake=0.0):
        self._marker_x = x
        self._marker_z = z
        self._marker_gas = gas
        self._marker_brake = brake
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Determina a bounding box usando o melhor traçado disponível
        ref_x = self._bg_x if len(self._bg_x) >= 2 else (
            self._ghost_x if len(self._ghost_x) >= 2 else self._live_x)
        ref_z = self._bg_z if len(self._bg_z) >= 2 else (
            self._ghost_z if len(self._ghost_z) >= 2 else self._live_z)

        if not ref_x or len(ref_x) < 2:
            return

        min_x, max_x = min(ref_x), max(ref_x)
        min_z, max_z = min(ref_z), max(ref_z)

        w, h = self.width(), self.height()
        range_x = max(1.0, max_x - min_x)
        range_z = max(1.0, max_z - min_z)

        margin = 15
        avail_w = w - margin * 2
        avail_h = h - margin * 2

        scale = min(avail_w / range_x, avail_h / range_z)

        cx = (min_x + max_x) / 2
        cz = (min_z + max_z) / 2

        def to_screen(px, pz):
            sx = (px - cx) * scale + w / 2
            sy = -(pz - cz) * scale + h / 2
            return QPointF(sx, sy)

        # --- Camada 1: traçado cinza permanente (layout da pista) ---
        draw_bg_x = self._bg_x if len(self._bg_x) >= 2 else []
        draw_bg_z = self._bg_z if len(self._bg_z) >= 2 else []
        if draw_bg_x:
            poly_bg = QPolygonF([to_screen(px, pz) for px, pz in zip(draw_bg_x, draw_bg_z)])
            pen_bg = QPen(QColor("#555555"), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen_bg)
            painter.drawPolyline(poly_bg)

        # --- Camada 2: traçado do ghost (colorido, translúcido) ---
        if self._ghost_x and len(self._ghost_x) >= 2 and self._ghost_gas and self._ghost_brake:
            n_ghost = len(self._ghost_x)
            current_color = None
            current_poly = QPolygonF()

            for i in range(n_ghost):
                p = to_screen(self._ghost_x[i], self._ghost_z[i])
                g = self._ghost_gas[i] if i < len(self._ghost_gas) else 0.0
                b = self._ghost_brake[i] if i < len(self._ghost_brake) else 0.0

                if b > 0.1:
                    base_col = QColor(T.CH_BRAKE)
                elif g > 0.1:
                    base_col = QColor(T.CH_THROTTLE)
                else:
                    base_col = QColor("#FFEA00")

                col = QColor(base_col.red(), base_col.green(), base_col.blue(), 80)

                if col != current_color:
                    if len(current_poly) >= 2:
                        pen_ghost = QPen(current_color, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                        painter.setPen(pen_ghost)
                        painter.drawPolyline(current_poly)
                    last_pt = current_poly.last() if not current_poly.isEmpty() else None
                    current_poly = QPolygonF()
                    if last_pt:
                        current_poly.append(last_pt)
                    current_color = col

                current_poly.append(p)

            if len(current_poly) >= 2 and current_color is not None:
                pen_ghost = QPen(current_color, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                painter.setPen(pen_ghost)
                painter.drawPolyline(current_poly)

        # --- Camada 3: traçado ao vivo colorido ---
        if self._live_x and len(self._live_x) >= 2:
            n_live = len(self._live_x)
            current_color = None
            current_poly = QPolygonF()

            for i in range(n_live):
                p = to_screen(self._live_x[i], self._live_z[i])
                g = self._live_gas[i] if i < len(self._live_gas) else 0.0
                b = self._live_brake[i] if i < len(self._live_brake) else 0.0

                if b > 0.1:
                    col = QColor(T.CH_BRAKE)
                elif g > 0.1:
                    col = QColor(T.CH_THROTTLE)
                else:
                    col = QColor("#FFEA00")

                if col != current_color:
                    if len(current_poly) >= 2:
                        pen_live = QPen(current_color, 2.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                        painter.setPen(pen_live)
                        painter.drawPolyline(current_poly)
                    last_pt = current_poly.last() if not current_poly.isEmpty() else None
                    current_poly = QPolygonF()
                    if last_pt:
                        current_poly.append(last_pt)
                    current_color = col

                current_poly.append(p)

            if len(current_poly) >= 2 and current_color is not None:
                pen_live = QPen(current_color, 2.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                painter.setPen(pen_live)
                painter.drawPolyline(current_poly)

        # --- Camada 4: marcador do carro (bolinha) ---
        if self._marker_x is not None and self._marker_z is not None:
            mp = to_screen(self._marker_x, self._marker_z)

            g = getattr(self, '_marker_gas', 0.0)
            b = getattr(self, '_marker_brake', 0.0)

            if b > 0.1:
                marker_color = QColor(T.CH_BRAKE)
            elif g > 0.1:
                marker_color = QColor(T.CH_THROTTLE)
            elif g <= 0.1 and b <= 0.1:
                marker_color = QColor("#FFEA00")
            else:
                marker_color = QColor("#0000FF")

            painter.setPen(Qt.NoPen)
            painter.setBrush(marker_color)
            painter.drawEllipse(mp, 5, 5)


class TrackMapCard(BaseCard):
    def __init__(self):
        super(TrackMapCard, self).__init__(title="MAPA DA PISTA", margins=(0, 5, 0, 5))
        self.map_widget = TrackMapWidget()
        self.map_widget.setMinimumSize(180, 180)
        self.map_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.body.addWidget(self.map_widget)
