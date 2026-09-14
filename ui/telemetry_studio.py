"""
ui/telemetry_studio.py — Estação de Análise de Telemetria Ponto a Ponto (MoTeC Style)
=====================================================================================

Módulo central do `main2.pyw`. Permite inspecionar a telemetria gravada ponto a ponto
com traçado 2D interativo, heatmap de frenagem e aceleração, marcadores de curvas e
ápices, gráficos empilhados perfeitamente sincronizados, círculo de atrito G-G
(friction circle), HUD de telemetria e reprodução em replay (0.25x a 2x).
"""

from __future__ import annotations

import bisect
import math
import os
import sys
from typing import Dict, List, Optional, Tuple

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSplitter, QPushButton, QTreeWidget, QTreeWidgetItem, QFileDialog,
    QAbstractItemView, QComboBox, QMenu, QDialog, QTextEdit, QProgressBar,
    QSlider, QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QCheckBox,
    QButtonGroup, QRadioButton, QShortcut,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QPointF, QRectF, QLineF
from PyQt5.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QKeySequence, QLinearGradient,
    QPainterPath, QPolygonF,
)
import pyqtgraph as pg
import numpy as np

from core.lap_library import LapLibrary, LapRecord, RetentionPolicy
from core import corner_analysis as ca
from ui import theme as T


# ---------------------------------------------------------------------------
# Cores e Constantes
# ---------------------------------------------------------------------------
LAP_COLOR_MAIN = "#00e5ff"      # Ciano brilhante (Volta sob análise)
LAP_COLOR_REF  = "#ff9100"      # Âmbar/Laranja (Volta de referência)

COLOR_BRAKE_HARD = "#ff1744"    # Vermelho intenso (freio pesado > 60%)
COLOR_BRAKE_MED  = "#ff5252"    # Vermelho médio
COLOR_BRAKE_SOFT = "#ff9100"    # Laranja / Trail braking suave (5% a 25%)
COLOR_COAST      = "#607d8b"    # Cinza azulado neutro (sem pé no freio nem no acelerador)
COLOR_GAS_FULL   = "#00e676"    # Verde vibrante (acelerador pleno 100%)
COLOR_GAS_PART   = "#00b0ff"    # Azul claro (retomada parcial)


def _clamp(val: float, low: float, high: float) -> float:
    return max(low, min(high, val))


# ---------------------------------------------------------------------------
# Círculo de Atrito G-G (MoTeC Friction / Traction Circle)
# ---------------------------------------------------------------------------
class GGCircleWidget(QWidget):
    """
    Mostrador 2D das Forças G (Lateral vs Longitudinal).
    Exibe os círculos de 0.5G, 1.0G, 1.5G e 2.0G, o rastro recente e a posição
    instantânea do carro. Indispensável em telemetria real para avaliar trail
    braking e aproveitamento da aderência dos pneus.
    """

    def __init__(self, max_g: float = 2.0, parent=None):
        super().__init__(parent)
        self.max_g = max_g
        self.current_lat_g = 0.0
        self.current_lon_g = 0.0
        self.trail: List[Tuple[float, float]] = []
        self.setMinimumSize(130, 130)
        self.setStyleSheet(f"background-color: {T.BG_INSET}; border: 1px solid {T.BORDER};")

    def set_g_force(self, lat_g: float, lon_g: float, trail: Optional[List[Tuple[float, float]]] = None):
        self.current_lat_g = lat_g
        self.current_lon_g = lon_g
        if trail is not None:
            self.trail = trail
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        side = min(w, h) - 16
        if side <= 10:
            return

        cx = w / 2.0
        cy = h / 2.0
        radius = side / 2.0

        # Fundo do mostrador
        painter.fillRect(self.rect(), QColor(T.BG_INSET))

        # Círculos concêntricos de referência (G)
        pen_grid = QPen(QColor(T.GRID), 1, Qt.DashLine)
        painter.setPen(pen_grid)
        painter.setBrush(Qt.NoBrush)

        for g in (0.5, 1.0, 1.5, 2.0):
            if g > self.max_g:
                continue
            r = radius * (g / self.max_g)
            painter.drawEllipse(QPointF(cx, cy), r, r)

        # Eixos horizontal (Lat G) e vertical (Lon G)
        pen_axes = QPen(QColor(T.BORDER), 1)
        painter.setPen(pen_axes)
        painter.drawLine(QLineF(cx - radius, cy, cx + radius, cy))
        painter.drawLine(QLineF(cx, cy - radius, cx, cy + radius))

        # Rótulos dos eixos
        painter.setFont(QFont(T.FONT_UI, 7))
        painter.setPen(QColor(T.TXT_UNIT))
        painter.drawText(int(cx + radius - 18), int(cy - 3), "LAT")
        painter.drawText(int(cx + 3), int(cy - radius + 10), "+LON")
        painter.drawText(int(cx + 3), int(cy + radius - 3), "FREIO")

        # Rastro dos pontos recentes (trail)
        if self.trail:
            n = len(self.trail)
            for i, (lat, lon) in enumerate(self.trail):
                alpha = int(30 + 170 * (i / max(1, n - 1)))
                px = cx + (lat / self.max_g) * radius
                py = cy - (lon / self.max_g) * radius
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(0, 229, 255, alpha))
                painter.drawEllipse(QPointF(px, py), 2.0, 2.0)

        # Ponto atual instantâneo
        px = cx + (self.current_lat_g / self.max_g) * radius
        py = cy - (self.current_lon_g / self.max_g) * radius
        px = _clamp(px, cx - radius, cx + radius)
        py = _clamp(py, cy - radius, cy + radius)

        # Cor do ponto: frenagem (lon negativo) = vermelho
        if self.current_lon_g < -0.3:
            dot_color = QColor("#ff1744")
        elif self.current_lon_g > 0.3:
            dot_color = QColor("#00e676")
        else:
            dot_color = QColor("#00e5ff")

        # Halo
        painter.setPen(Qt.NoPen)
        halo_color = QColor(dot_color)
        halo_color.setAlpha(80)
        painter.setBrush(halo_color)
        painter.drawEllipse(QPointF(px, py), 7, 7)

        # Ponto central
        painter.setPen(QPen(QColor("#ffffff"), 1.5))
        painter.setBrush(dot_color)
        painter.drawEllipse(QPointF(px, py), 4, 4)

        # Rótulo de valores no canto
        painter.setFont(QFont(T.FONT_MONO, 8, QFont.Bold))
        painter.setPen(QColor(T.TXT_VALUE))
        tot_g = math.hypot(self.current_lat_g, self.current_lon_g)
        painter.drawText(8, 14, f"{tot_g:.2f} G")


# ---------------------------------------------------------------------------
# HUD / Painel de Inspeção Ponto a Ponto
# ---------------------------------------------------------------------------
class PointInspectorWidget(QFrame):
    """
    Painel de telemetria ponto a ponto. Mostra velocímetro digital, barras de
    freio e acelerador, marcha, volante, RPM, delta e diagnóstico do trecho.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            PointInspectorWidget {{
                background-color: {T.BG_PANEL};
                border: 1px solid {T.BORDER};
            }}
        """)
        self.setFixedHeight(140)
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(10)

        # Bloco 1: Localização & Trecho
        b1 = QVBoxLayout()
        b1.setSpacing(2)
        lbl_pos_title = QLabel("LOCALIZAÇÃO")
        lbl_pos_title.setFont(T.f_title(8))
        lbl_pos_title.setStyleSheet(f"color: {T.TXT_TITLE};")
        b1.addWidget(lbl_pos_title)

        self.lbl_corner_name = QLabel("Reta Principal")
        self.lbl_corner_name.setFont(QFont(T.FONT_UI, 12, QFont.Bold))
        self.lbl_corner_name.setStyleSheet("color: #00e5ff;")
        b1.addWidget(self.lbl_corner_name)

        self.lbl_distance = QLabel("0 m · 0.0% da volta")
        self.lbl_distance.setFont(QFont(T.FONT_MONO, 10))
        self.lbl_distance.setStyleSheet(f"color: {T.TXT_VALUE};")
        b1.addWidget(self.lbl_distance)

        self.lbl_time = QLabel("Tempo: 0.000 s")
        self.lbl_time.setFont(QFont(T.FONT_MONO, 9))
        self.lbl_time.setStyleSheet(f"color: {T.TXT_UNIT};")
        b1.addWidget(self.lbl_time)
        root.addLayout(b1, stretch=2)

        root.addWidget(self._create_separator())

        # Bloco 2: Velocidade & Delta
        b2 = QVBoxLayout()
        b2.setSpacing(2)
        lbl_speed_title = QLabel("VELOCIDADE")
        lbl_speed_title.setFont(T.f_title(8))
        lbl_speed_title.setStyleSheet(f"color: {T.TXT_TITLE};")
        b2.addWidget(lbl_speed_title)

        self.lbl_speed = QLabel("0.0 km/h")
        self.lbl_speed.setFont(QFont(T.FONT_MONO, 20, QFont.Bold))
        self.lbl_speed.setStyleSheet("color: #ffffff;")
        b2.addWidget(self.lbl_speed)

        self.lbl_delta = QLabel("Delta: --")
        self.lbl_delta.setFont(QFont(T.FONT_MONO, 10, QFont.Bold))
        self.lbl_delta.setStyleSheet(f"color: {T.TXT_UNIT};")
        b2.addWidget(self.lbl_delta)
        root.addLayout(b2, stretch=2)

        root.addWidget(self._create_separator())

        # Bloco 3: Pedais (Acelerador & Freio lado a lado)
        b3 = QVBoxLayout()
        b3.setSpacing(3)
        lbl_pedals_title = QLabel("PEDAIS (ACEL / FREIO)")
        lbl_pedals_title.setFont(T.f_title(8))
        lbl_pedals_title.setStyleSheet(f"color: {T.TXT_TITLE};")
        b3.addWidget(lbl_pedals_title)

        # Acelerador
        row_gas = QHBoxLayout()
        row_gas.setSpacing(6)
        lbl_gas_tag = QLabel("GAS")
        lbl_gas_tag.setFont(QFont(T.FONT_MONO, 8, QFont.Bold))
        lbl_gas_tag.setStyleSheet("color: #00e676; width: 28px;")
        row_gas.addWidget(lbl_gas_tag)

        self.bar_gas = QProgressBar()
        self.bar_gas.setFixedHeight(12)
        self.bar_gas.setTextVisible(False)
        self.bar_gas.setRange(0, 100)
        self.bar_gas.setStyleSheet(f"""
            QProgressBar {{
                background-color: {T.BG_INSET}; border: 1px solid {T.BORDER}; border-radius: 0px;
            }}
            QProgressBar::chunk {{ background-color: #00e676; }}
        """)
        row_gas.addWidget(self.bar_gas, 1)

        self.lbl_gas_val = QLabel("0%")
        self.lbl_gas_val.setFont(QFont(T.FONT_MONO, 9, QFont.Bold))
        self.lbl_gas_val.setStyleSheet("color: #00e676; min-width: 40px;")
        self.lbl_gas_val.setAlignment(Qt.AlignRight)
        row_gas.addWidget(self.lbl_gas_val)
        b3.addLayout(row_gas)

        # Freio
        row_brake = QHBoxLayout()
        row_brake.setSpacing(6)
        lbl_brake_tag = QLabel("BRK")
        lbl_brake_tag.setFont(QFont(T.FONT_MONO, 8, QFont.Bold))
        lbl_brake_tag.setStyleSheet("color: #ff1744; width: 28px;")
        row_brake.addWidget(lbl_brake_tag)

        self.bar_brake = QProgressBar()
        self.bar_brake.setFixedHeight(12)
        self.bar_brake.setTextVisible(False)
        self.bar_brake.setRange(0, 100)
        self.bar_brake.setStyleSheet(f"""
            QProgressBar {{
                background-color: {T.BG_INSET}; border: 1px solid {T.BORDER}; border-radius: 0px;
            }}
            QProgressBar::chunk {{ background-color: #ff1744; }}
        """)
        row_brake.addWidget(self.bar_brake, 1)

        self.lbl_brake_val = QLabel("0%")
        self.lbl_brake_val.setFont(QFont(T.FONT_MONO, 9, QFont.Bold))
        self.lbl_brake_val.setStyleSheet("color: #ff1744; min-width: 40px;")
        self.lbl_brake_val.setAlignment(Qt.AlignRight)
        row_brake.addWidget(self.lbl_brake_val)
        b3.addLayout(row_brake)

        # Status de frenagem
        self.lbl_pedal_hint = QLabel("Pé no fundo")
        self.lbl_pedal_hint.setFont(QFont(T.FONT_UI, 9))
        self.lbl_pedal_hint.setStyleSheet(f"color: {T.TXT_UNIT};")
        b3.addWidget(self.lbl_pedal_hint)

        root.addLayout(b3, stretch=3)

        root.addWidget(self._create_separator())

        # Bloco 4: Volante, Marcha & RPM
        b4 = QVBoxLayout()
        b4.setSpacing(2)
        lbl_car_title = QLabel("MOTOR & VOLANTE")
        lbl_car_title.setFont(T.f_title(8))
        lbl_car_title.setStyleSheet(f"color: {T.TXT_TITLE};")
        b4.addWidget(lbl_car_title)

        row_car = QHBoxLayout()
        row_car.setSpacing(10)

        self.lbl_gear = QLabel("N")
        self.lbl_gear.setFont(QFont(T.FONT_MONO, 22, QFont.Bold))
        self.lbl_gear.setStyleSheet("color: #ffea00;")
        row_car.addWidget(self.lbl_gear)

        sub_car = QVBoxLayout()
        sub_car.setSpacing(1)
        self.lbl_rpm = QLabel("0 RPM")
        self.lbl_rpm.setFont(QFont(T.FONT_MONO, 11, QFont.Bold))
        self.lbl_rpm.setStyleSheet(f"color: {T.TXT_VALUE};")
        sub_car.addWidget(self.lbl_rpm)

        self.lbl_steer = QLabel("Volante: 0.0°")
        self.lbl_steer.setFont(QFont(T.FONT_MONO, 10))
        self.lbl_steer.setStyleSheet(f"color: {T.TXT_UNIT};")
        sub_car.addWidget(self.lbl_steer)
        row_car.addLayout(sub_car)

        b4.addLayout(row_car)

        self.lbl_lat_lon = QLabel("Lat: 0.00 G · Lon: 0.00 G")
        self.lbl_lat_lon.setFont(QFont(T.FONT_MONO, 9))
        self.lbl_lat_lon.setStyleSheet(f"color: {T.TXT_UNIT};")
        b4.addWidget(self.lbl_lat_lon)

        root.addLayout(b4, stretch=3)

        root.addWidget(self._create_separator())

        # Bloco 5: Diagrama G-G Círculo de Atrito
        self.gg_widget = GGCircleWidget(max_g=2.2)
        root.addWidget(self.gg_widget)

    def _create_separator(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"color: {T.BORDER}; background-color: {T.BORDER};")
        return sep

    def update_point(self, data: dict):
        dist = data.get("dist", 0.0)
        tot_dist = data.get("tot_dist", 1.0)
        prog = (dist / max(1.0, tot_dist)) * 100.0
        time_s = data.get("time", 0.0)
        corner_phrase = data.get("corner_phrase", "")

        self.lbl_corner_name.setText(corner_phrase or "Pista")
        self.lbl_distance.setText(f"{dist:.0f} m  ·  {prog:.1f}% da volta")
        self.lbl_time.setText(f"Tempo: {time_s:.3f} s")

        speed = data.get("speed", 0.0)
        self.lbl_speed.setText(f"{speed:.1f} km/h")

        delta = data.get("delta", None)
        if delta is not None:
            sign = "+" if delta >= 0 else ""
            color = "#ff3333" if delta > 0.01 else ("#00e676" if delta < -0.01 else T.TXT_VALUE)
            symbol = "▲" if delta > 0 else "▼"
            self.lbl_delta.setText(f"Delta: {sign}{delta:.3f} s {symbol}")
            self.lbl_delta.setStyleSheet(f"color: {color};")
        else:
            self.lbl_delta.setText("Delta: -- (solo)")
            self.lbl_delta.setStyleSheet(f"color: {T.TXT_UNIT};")

        gas = data.get("gas", 0.0)
        brake = data.get("brake", 0.0)
        gas_pct = int(_clamp(gas * 100.0, 0, 100))
        brake_pct = int(_clamp(brake * 100.0, 0, 100))

        self.bar_gas.setValue(gas_pct)
        self.bar_brake.setValue(brake_pct)
        self.lbl_gas_val.setText(f"{gas_pct}%")
        self.lbl_brake_val.setText(f"{brake_pct}%")

        if brake > 0.6:
            self.lbl_pedal_hint.setText("🛑 FRENAGEM FORTE")
            self.lbl_pedal_hint.setStyleSheet("color: #ff1744; font-weight: bold;")
        elif brake > 0.05:
            self.lbl_pedal_hint.setText("⚠️ TRAIL BRAKING")
            self.lbl_pedal_hint.setStyleSheet("color: #ff9100; font-weight: bold;")
        elif gas > 0.95:
            self.lbl_pedal_hint.setText("🟢 ACELERAÇÃO PLENA (100%)")
            self.lbl_pedal_hint.setStyleSheet("color: #00e676; font-weight: bold;")
        elif gas > 0.2:
            self.lbl_pedal_hint.setText("⚡ Retomada parcial")
            self.lbl_pedal_hint.setStyleSheet("color: #00b0ff;")
        else:
            self.lbl_pedal_hint.setText("Transição / Coasting")
            self.lbl_pedal_hint.setStyleSheet(f"color: {T.TXT_UNIT};")

        gear = data.get("gear", 0)
        gear_str = "R" if gear == -1 else ("N" if gear == 0 else f"{gear}ª")
        self.lbl_gear.setText(gear_str)
        rpm = data.get("rpm", 0)
        self.lbl_rpm.setText(f"{rpm} RPM")

        steer = data.get("steer", 0.0)
        arrow = "↰ " if steer < -1.0 else ("↱ " if steer > 1.0 else "")
        self.lbl_steer.setText(f"Volante: {arrow}{steer:.1f}°")

        lat_g = data.get("lat_g", 0.0)
        lon_g = data.get("lon_g", 0.0)
        self.lbl_lat_lon.setText(f"Lat: {lat_g:+.2f} G · Lon: {lon_g:+.2f} G")

        trail = data.get("trail_g", None)
        self.gg_widget.set_g_force(lat_g, lon_g, trail)


# ---------------------------------------------------------------------------
# Widget de Traçado Interativo da Pista (Track Map Pro)
# ---------------------------------------------------------------------------
class TrackMapProWidget(QWidget):
    """
    Traçado 2D interativo profissional:
      - Renderização com Antialiasing de alta velocidade
      - Modos de visualização de Heatmap (Frenagem/Aceleração, Velocidade, Marcha, Delta)
      - Marcadores no traçado: Início de Frenagem (🔴 km/h), Ápice (🟡 V_min), Retomada (🟢)
      - Snap com mouse: passar o mouse ou clicar atrai para o ponto exato da volta
      - Cursor do carro animado com direção/orientação
      - Carro fantasma (ghost car) para comparação direta de onde cada um freou
      - Zoom e Pan com botões ou roda do mouse
    """

    sig_point_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 400)
        self.setMouseTracking(True)
        self.setStyleSheet(f"background-color: {T.BG_PANEL}; border: 1px solid {T.BORDER};")

        # Dados da volta principal
        self.cx: np.ndarray = np.array([])
        self.cz: np.ndarray = np.array([])
        self.telemetry: dict = {}
        self.total_points: int = 0

        # Dados da volta de referência
        self.ref_cx: np.ndarray = np.array([])
        self.ref_cz: np.ndarray = np.array([])
        self.ref_telemetry: dict = {}

        # Mapeamento de curvas e métricas de frenagem
        self.corner_map: Optional[ca.CornerMap] = None
        self.corner_metrics: List[ca.CornerMetrics] = []

        # Estado da visualização
        self.color_mode = "brake_throttle"
        self.show_markers = True
        self.show_corner_labels = True

        # Índice selecionado atualmente
        self.selected_index: int = 0
        self.ref_selected_index: int = 0

        # Câmera / Transformação (World -> Screen)
        self.scale: float = 1.0
        self.offset_x: float = 0.0
        self.offset_y: float = 0.0
        self._dragging = False
        self._drag_start = QPointF()

        # Cache de caminhos para desenho ultra-rápido (60 FPS)
        self._cached_segments: List[Tuple[QPainterPath, QColor, float]] = []
        self._cached_ref_path: Optional[QPainterPath] = None
        self._bounds = QRectF(0, 0, 100, 100)

    # -- Carregamento de dados ----------------------------------------------

    def set_lap_data(self, telemetry: dict, corner_map: Optional[ca.CornerMap] = None,
                     corner_metrics: Optional[List[ca.CornerMetrics]] = None):
        self.telemetry = telemetry or {}
        self.corner_map = corner_map
        self.corner_metrics = corner_metrics or []

        raw_x = self.telemetry.get("car_x") or []
        raw_z = self.telemetry.get("car_z") or []
        n = min(len(raw_x), len(raw_z))
        if n >= 2:
            self.cx = np.array(raw_x[:n], dtype=np.float32)
            self.cz = np.array(raw_z[:n], dtype=np.float32)
            self.total_points = n
        else:
            self.cx = np.array([])
            self.cz = np.array([])
            self.total_points = 0

        self.selected_index = 0
        self._update_bounds()
        self.rebuild_visuals()
        self.reset_view()

    def set_reference_lap_data(self, ref_telemetry: Optional[dict]):
        self.ref_telemetry = ref_telemetry or {}
        raw_x = self.ref_telemetry.get("car_x") or []
        raw_z = self.ref_telemetry.get("car_z") or []
        n = min(len(raw_x), len(raw_z))
        if n >= 2:
            self.ref_cx = np.array(raw_x[:n], dtype=np.float32)
            self.ref_cz = np.array(raw_z[:n], dtype=np.float32)
        else:
            self.ref_cx = np.array([])
            self.ref_cz = np.array([])

        self.rebuild_visuals()
        self.update()

    def set_color_mode(self, mode: str):
        self.color_mode = mode
        self.rebuild_visuals()
        self.update()

    def set_show_markers(self, show: bool):
        self.show_markers = show
        self.update()

    def set_show_corner_labels(self, show: bool):
        self.show_corner_labels = show
        self.update()

    def set_selected_index(self, index: int, ref_index: int = 0):
        if self.total_points > 0:
            self.selected_index = max(0, min(self.total_points - 1, index))
            self.ref_selected_index = max(0, min(len(self.ref_cx) - 1, ref_index)) if len(self.ref_cx) > 0 else 0
            self.update()

    # -- Cache de Geometria e Heatmap ---------------------------------------

    def _update_bounds(self):
        if self.total_points < 2:
            self._bounds = QRectF(-100, -100, 200, 200)
            return
        min_x, max_x = float(np.min(self.cx)), float(np.max(self.cx))
        min_z, max_z = float(np.min(self.cz)), float(np.max(self.cz))
        pad_x = max(10.0, (max_x - min_x) * 0.08)
        pad_z = max(10.0, (max_z - min_z) * 0.08)
        self._bounds = QRectF(min_x - pad_x, min_z - pad_z,
                              (max_x - min_x) + 2 * pad_x,
                              (max_z - min_z) + 2 * pad_z)

    def rebuild_visuals(self):
        self._cached_segments = []
        self._cached_ref_path = None

        if self.total_points < 2:
            return

        # 1. Volta de referência
        if len(self.ref_cx) >= 2:
            ref_path = QPainterPath()
            ref_path.moveTo(float(self.ref_cx[0]), float(self.ref_cz[0]))
            for i in range(1, len(self.ref_cx)):
                ref_path.lineTo(float(self.ref_cx[i]), float(self.ref_cz[i]))
            self._cached_ref_path = ref_path

        # 2. Segmentos da volta principal
        brakes = self.telemetry.get("brake") or [0.0] * self.total_points
        gases = self.telemetry.get("gas") or [0.0] * self.total_points
        speeds = self.telemetry.get("speed") or [100.0] * self.total_points
        gears = self.telemetry.get("gear") or [3] * self.total_points
        deltas = self.telemetry.get("delta") or [0.0] * self.total_points

        min_spd = float(np.min(speeds)) if speeds else 60.0
        max_spd = float(np.max(speeds)) if speeds else 250.0
        spd_range = max(1.0, max_spd - min_spd)

        current_color = None
        current_width = 3.0
        current_path: Optional[QPainterPath] = None

        for i in range(self.total_points - 1):
            p0 = QPointF(float(self.cx[i]), float(self.cz[i]))
            p1 = QPointF(float(self.cx[i + 1]), float(self.cz[i + 1]))

            if self.color_mode == "brake_throttle":
                brk = brakes[i] if i < len(brakes) else 0.0
                gas = gases[i] if i < len(gases) else 0.0

                if brk > 0.60:
                    seg_color = QColor(COLOR_BRAKE_HARD)
                    width = 4.0
                elif brk > 0.20:
                    seg_color = QColor(COLOR_BRAKE_MED)
                    width = 3.5
                elif brk > 0.04:
                    seg_color = QColor(COLOR_BRAKE_SOFT)
                    width = 3.0
                elif gas > 0.85:
                    seg_color = QColor(COLOR_GAS_FULL)
                    width = 3.0
                elif gas > 0.15:
                    seg_color = QColor(COLOR_GAS_PART)
                    width = 2.5
                else:
                    seg_color = QColor(COLOR_COAST)
                    width = 2.0

            elif self.color_mode == "speed":
                spd = speeds[i] if i < len(speeds) else min_spd
                ratio = _clamp((spd - min_spd) / spd_range, 0.0, 1.0)
                hue = int(240 - ratio * 240)
                seg_color = QColor.fromHsv(hue, 220, 255)
                width = 3.0

            elif self.color_mode == "gear":
                g = int(gears[i]) if i < len(gears) else 3
                gear_colors = {
                    1: "#e040fb", 2: "#2979ff", 3: "#00e5ff",
                    4: "#00e676", 5: "#ffea00", 6: "#ff9100", 7: "#ff1744"
                }
                seg_color = QColor(gear_colors.get(g, "#00e5ff"))
                width = 3.0

            elif self.color_mode == "delta":
                d = deltas[i] if i < len(deltas) else 0.0
                if d < -0.05:
                    seg_color = QColor("#00e676")
                elif d > 0.05:
                    seg_color = QColor("#ff1744")
                else:
                    seg_color = QColor("#00e5ff")
                width = 3.0
            else:
                seg_color = QColor(LAP_COLOR_MAIN)
                width = 3.0

            if current_path is None:
                current_path = QPainterPath()
                current_path.moveTo(p0)
                current_path.lineTo(p1)
                current_color = seg_color
                current_width = width
            elif current_color == seg_color and current_width == width:
                current_path.lineTo(p1)
            else:
                self._cached_segments.append((current_path, current_color, current_width))
                current_path = QPainterPath()
                current_path.moveTo(p0)
                current_path.lineTo(p1)
                current_color = seg_color
                current_width = width

        if current_path is not None and current_color is not None:
            self._cached_segments.append((current_path, current_color, current_width))

    # -- Câmera e Transformação de Coordenadas ------------------------------

    def reset_view(self):
        w = self.width()
        h = self.height()
        if w <= 10 or h <= 10 or self._bounds.width() <= 0:
            return

        scale_x = w / self._bounds.width()
        scale_y = h / self._bounds.height()
        self.scale = min(scale_x, scale_y) * 0.90

        center_x = self._bounds.center().x()
        center_y = self._bounds.center().y()
        self.offset_x = (w / 2.0) - (center_x * self.scale)
        self.offset_y = (h / 2.0) + (center_y * self.scale)
        self.update()

    def world_to_screen(self, wx: float, wz: float) -> QPointF:
        sx = wx * self.scale + self.offset_x
        sy = self.offset_y - (wz * self.scale)
        return QPointF(sx, sy)

    def screen_to_world(self, sx: float, sy: float) -> Tuple[float, float]:
        wx = (sx - self.offset_x) / self.scale
        wz = (self.offset_y - sy) / self.scale
        return wx, wz

    # -- Eventos de Mouse ---------------------------------------------------

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else (1.0 / 1.15)
        m_pos = event.pos()
        wx, wz = self.screen_to_world(m_pos.x(), m_pos.y())

        self.scale *= factor
        self.offset_x = m_pos.x() - wx * self.scale
        self.offset_y = m_pos.y() + wz * self.scale
        self.update()

    def mousePressEvent(self, event):
        if event.button() in (Qt.RightButton, Qt.MiddleButton):
            self._dragging = True
            self._drag_start = event.pos()
        elif event.button() == Qt.LeftButton:
            self._snap_to_mouse(event.pos())

    def mouseMoveEvent(self, event):
        if self._dragging:
            delta = event.pos() - self._drag_start
            self._drag_start = event.pos()
            self.offset_x += delta.x()
            self.offset_y += delta.y()
            self.update()
        elif event.buttons() & Qt.LeftButton:
            self._snap_to_mouse(event.pos())

    def mouseReleaseEvent(self, event):
        if event.button() in (Qt.RightButton, Qt.MiddleButton):
            self._dragging = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.scale == 1.0:
            self.reset_view()

    def _snap_to_mouse(self, mouse_pos: QPointF):
        if self.total_points < 2:
            return
        wx, wz = self.screen_to_world(mouse_pos.x(), mouse_pos.y())

        dx = self.cx - wx
        dz = self.cz - wz
        dist_sq = dx * dx + dz * dz
        best_idx = int(np.argmin(dist_sq))

        screen_pt = self.world_to_screen(float(self.cx[best_idx]), float(self.cz[best_idx]))
        px_dist = math.hypot(screen_pt.x() - mouse_pos.x(), screen_pt.y() - mouse_pos.y())

        if px_dist < 80.0:
            self.sig_point_selected.emit(best_idx)

    # -- Desenho Principal (Paint) ------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        painter.fillRect(self.rect(), QColor(T.BG_PANEL))

        if self.total_points < 2:
            painter.setFont(QFont(T.FONT_UI, 11))
            painter.setPen(QColor(T.TXT_UNIT))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Nenhuma telemetria carregada.\nSelecione uma volta à esquerda.")
            return

        painter.save()
        painter.translate(self.offset_x, self.offset_y)
        painter.scale(self.scale, -self.scale)

        # 1. Volta de Referência
        if self._cached_ref_path is not None:
            pen_ref = QPen(QColor(LAP_COLOR_REF), 4.0 / self.scale)
            pen_ref.setStyle(Qt.DashLine)
            painter.setPen(pen_ref)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self._cached_ref_path)

        # 2. Segmentos da Volta Principal
        painter.setBrush(Qt.NoBrush)
        for path, color, width in self._cached_segments:
            pen = QPen(color, width / self.scale)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(path)

        painter.restore()

        # 3. Marcadores de Frenagem e Curvas
        if self.show_markers:
            self._draw_markers(painter)

        # 4. Marcador do Ghost da Referência
        if len(self.ref_cx) > 0 and self.ref_selected_index < len(self.ref_cx):
            ref_pt = self.world_to_screen(float(self.ref_cx[self.ref_selected_index]),
                                          float(self.ref_cz[self.ref_selected_index]))
            self._draw_car_dot(painter, ref_pt, QColor(LAP_COLOR_REF), "REF", radius=6)

        # 5. Marcador do Carro Principal
        if 0 <= self.selected_index < self.total_points:
            car_pt = self.world_to_screen(float(self.cx[self.selected_index]),
                                          float(self.cz[self.selected_index]))

            next_idx = min(self.total_points - 1, self.selected_index + 1)
            prev_idx = max(0, self.selected_index - 1)
            dx = float(self.cx[next_idx] - self.cx[prev_idx])
            dz = float(self.cz[next_idx] - self.cz[prev_idx])
            heading = math.atan2(-dz, dx) if (dx != 0 or dz != 0) else 0.0

            self._draw_car_dot(painter, car_pt, QColor(LAP_COLOR_MAIN), "VOCÊ",
                               radius=8, heading=heading, show_heading=True)

        # 6. Legenda
        self._draw_overlay_legend(painter)

    def _draw_car_dot(self, painter: QPainter, pt: QPointF, color: QColor, label: str,
                      radius: float = 8, heading: float = 0.0, show_heading: bool = False):
        halo = QColor(color)
        halo.setAlpha(60)
        painter.setPen(Qt.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(pt, radius + 8, radius + 8)

        halo.setAlpha(120)
        painter.setBrush(halo)
        painter.drawEllipse(pt, radius + 4, radius + 4)

        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.setBrush(color)
        painter.drawEllipse(pt, radius, radius)

        if show_heading:
            arrow_len = radius + 12
            ax = pt.x() + arrow_len * math.cos(heading)
            ay = pt.y() + arrow_len * math.sin(heading)
            painter.setPen(QPen(QColor("#ffffff"), 2.5))
            painter.drawLine(pt, QPointF(ax, ay))

        painter.setFont(QFont(T.FONT_UI, 8, QFont.Bold))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(pt.x() - 30, pt.y() - radius - 16, 60, 14), Qt.AlignCenter, label)

    def _draw_markers(self, painter: QPainter):
        if not self.corner_metrics or not self.corner_map:
            return

        distances = self.telemetry.get("distance") or []
        speeds = self.telemetry.get("speed") or []
        if len(distances) != self.total_points:
            return

        painter.setFont(QFont(T.FONT_UI, 7, QFont.Bold))

        for m in self.corner_metrics:
            # Ponto de Frenagem
            if m.braking_point_m is not None:
                idx = bisect.bisect_left(distances, m.braking_point_m)
                if 0 <= idx < self.total_points:
                    pt = self.world_to_screen(float(self.cx[idx]), float(self.cz[idx]))
                    painter.setPen(QPen(QColor("#ffffff"), 1))
                    painter.setBrush(QColor("#ff1744"))
                    painter.drawEllipse(pt, 5, 5)

                    init_spd = speeds[idx] if idx < len(speeds) else 0.0
                    speed_str = f"🛑 {init_spd:.0f} km/h" if init_spd > 0 else "🛑 Freio"
                    painter.setPen(QColor("#ff80ab"))
                    painter.drawText(int(pt.x() + 7), int(pt.y() - 4), speed_str)

            # Ápice
            if m.v_min_m is not None and m.v_min is not None:
                idx = bisect.bisect_left(distances, m.v_min_m)
                if 0 <= idx < self.total_points:
                    pt = self.world_to_screen(float(self.cx[idx]), float(self.cz[idx]))
                    painter.setPen(QPen(QColor("#ffffff"), 1))
                    painter.setBrush(QColor("#ffd600"))
                    painter.drawEllipse(pt, 4, 4)

                    painter.setPen(QColor("#ffff8d"))
                    painter.drawText(int(pt.x() + 6), int(pt.y() + 12), f"★ {m.v_min:.0f}")

            # Nome da curva
            if self.show_corner_labels and m.corner:
                c_start_m = m.corner.start_m(self.corner_map.track_length)
                idx = bisect.bisect_left(distances, c_start_m)
                if 0 <= idx < self.total_points:
                    pt = self.world_to_screen(float(self.cx[idx]), float(self.cz[idx]))
                    painter.setPen(QColor("#80d8ff"))
                    painter.drawText(int(pt.x() - 20), int(pt.y() - 14), m.corner.name)

    def _draw_overlay_legend(self, painter: QPainter):
        painter.setFont(QFont(T.FONT_UI, 8))
        modes_info = {
            "brake_throttle": "Modo: Frenagem (Vermelho) & Acelerador (Verde)",
            "speed": "Modo: Heatmap de Velocidade (Azul = Lento, Vermelho = Rápido)",
            "gear": "Modo: Marchas (1ª a 7ª)",
            "delta": "Modo: Delta vs Referência (Verde = Ganhando, Vermelho = Perdendo)",
            "single": "Modo: Traçado Limpo",
        }
        mode_text = modes_info.get(self.color_mode, "")
        painter.setPen(QColor(T.TXT_TITLE))
        painter.drawText(12, 20, mode_text)

        painter.setFont(QFont(T.FONT_UI, 7))
        painter.setPen(QColor(T.TXT_UNIT))
        painter.drawText(12, 34, "💡 Dica: Clique ou passe o mouse em qualquer parte do traçado para inspecionar.")


# ---------------------------------------------------------------------------
# Controlador de Reprodução (Replay Scrubber)
# ---------------------------------------------------------------------------
class PlaybackController(QFrame):
    """
    Controla a reprodução ponto a ponto da volta (Play, Pause, Scrubbing, Velocidade).
    """

    sig_seek = pyqtSignal(int)
    sig_toggle_play = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            PlaybackController {{
                background-color: {T.BG_INSET};
                border: 1px solid {T.BORDER};
            }}
        """)
        self.setFixedHeight(48)
        self.is_playing = False
        self.speed_factor = 1.0
        self.total_points = 100
        self.current_index = 0

        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._on_tick)
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        self.btn_play = QPushButton("▶ REPLAY")
        self.btn_play.setFixedWidth(85)
        self.btn_play.setCursor(Qt.PointingHandCursor)
        self.btn_play.setFont(QFont(T.FONT_UI, 9, QFont.Bold))
        self.btn_play.setStyleSheet(f"""
            QPushButton {{
                background-color: #00e5ff; color: #000000;
                border: none; padding: 5px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #80d8ff; }}
        """)
        self.btn_play.clicked.connect(self.toggle_play)
        layout.addWidget(self.btn_play)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(0)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background: {T.BG_PANEL}; height: 6px; border: 1px solid {T.BORDER};
            }}
            QSlider::sub-page:horizontal {{ background: #00e5ff; }}
            QSlider::handle:horizontal {{
                background: #ffffff; width: 14px; margin-top: -5px; margin-bottom: -5px;
                border: 1px solid #000000;
            }}
        """)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.valueChanged.connect(self._on_slider_value_changed)
        layout.addWidget(self.slider, 1)

        self.lbl_step = QLabel("0 / 0")
        self.lbl_step.setFont(QFont(T.FONT_MONO, 9))
        self.lbl_step.setStyleSheet(f"color: {T.TXT_VALUE}; min-width: 90px;")
        layout.addWidget(self.lbl_step)

        lbl_vel = QLabel("Vel:")
        lbl_vel.setFont(QFont(T.FONT_UI, 8))
        lbl_vel.setStyleSheet(f"color: {T.TXT_UNIT};")
        layout.addWidget(lbl_vel)

        self.combo_speed = QComboBox()
        self.combo_speed.addItem("0.25x", 0.25)
        self.combo_speed.addItem("0.5x", 0.5)
        self.combo_speed.addItem("1.0x", 1.0)
        self.combo_speed.addItem("2.0x", 2.0)
        self.combo_speed.setCurrentIndex(2)
        self.combo_speed.currentIndexChanged.connect(self._on_speed_changed)
        self.combo_speed.setStyleSheet(f"""
            QComboBox {{
                background-color: {T.BG_PANEL}; color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER}; padding: 2px 6px; font-size: 11px;
            }}
        """)
        layout.addWidget(self.combo_speed)

    def set_total_points(self, n: int):
        self.total_points = max(1, n)
        self.slider.blockSignals(True)
        self.slider.setRange(0, self.total_points - 1)
        self.slider.blockSignals(False)
        self.update_display(0)

    def update_display(self, index: int):
        self.current_index = index
        self.slider.blockSignals(True)
        self.slider.setValue(index)
        self.slider.blockSignals(False)
        self.lbl_step.setText(f"{index + 1} / {self.total_points}")

    def toggle_play(self):
        self.is_playing = not self.is_playing
        if self.is_playing:
            self.btn_play.setText("⏸ PAUSAR")
            self.btn_play.setStyleSheet(f"""
                QPushButton {{
                    background-color: #ff1744; color: #ffffff;
                    border: none; padding: 5px; font-weight: bold;
                }}
                QPushButton:hover {{ background-color: #ff5252; }}
            """)
            self._timer.start()
        else:
            self.btn_play.setText("▶ REPLAY")
            self.btn_play.setStyleSheet(f"""
                QPushButton {{
                    background-color: #00e5ff; color: #000000;
                    border: none; padding: 5px; font-weight: bold;
                }}
                QPushButton:hover {{ background-color: #80d8ff; }}
            """)
            self._timer.stop()
        self.sig_toggle_play.emit()

    def pause(self):
        if self.is_playing:
            self.toggle_play()

    def _on_speed_changed(self):
        self.speed_factor = float(self.combo_speed.currentData())

    def _on_slider_moved(self, val):
        self.current_index = val
        self.update_display(val)
        self.sig_seek.emit(val)

    def _on_slider_value_changed(self, val):
        if not self.is_playing:
            self.current_index = val
            self.update_display(val)
            self.sig_seek.emit(val)

    def _on_tick(self):
        step = max(1, int(1.5 * self.speed_factor))
        next_idx = self.current_index + step
        if next_idx >= self.total_points:
            next_idx = 0
        self.current_index = next_idx
        self.update_display(next_idx)
        self.sig_seek.emit(next_idx)


# ---------------------------------------------------------------------------
# Tabela de Análise Curva a Curva
# ---------------------------------------------------------------------------
class CornerTableWidget(QTableWidget):
    """Tabela Turn-by-Turn com início de frenagem, V_min e delta."""

    sig_corner_clicked = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(6)
        self.setHorizontalHeaderLabels([
            "Curva", "Frenagem", "V_min Ápice", "Retomada", "Tempo", "Delta"
        ])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setStyleSheet(f"""
            QTableWidget {{
                background-color: {T.BG_INSET}; color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER}; font-size: 11px;
            }}
            QHeaderView::section {{
                background-color: {T.BG_HEADER}; color: {T.TXT_TITLE};
                border: none; padding: 4px; font-size: 11px;
            }}
            QTableWidget::item:selected {{
                background-color: {T.BG_HEADER}; color: #00e5ff;
            }}
        """)
        self.itemClicked.connect(self._on_item_clicked)

    def populate(self, metrics: List[ca.CornerMetrics],
                 comparisons: Optional[List[ca.CornerComparison]] = None,
                 speeds: Optional[List[float]] = None,
                 distances: Optional[List[float]] = None):
        self.setRowCount(0)
        comp_dict = {c.corner.index: c for c in (comparisons or [])}

        for m in metrics:
            row = self.rowCount()
            self.insertRow(row)

            c_name = m.corner.name if m.corner else f"Curva {row + 1}"
            
            init_spd_str = ""
            if m.braking_point_m is not None and distances and speeds:
                idx = bisect.bisect_left(distances, m.braking_point_m)
                if 0 <= idx < len(speeds):
                    init_spd_str = f" ({speeds[idx]:.0f} km/h)"
            
            brk_str = f"{m.braking_point_m:.0f} m{init_spd_str}" if m.braking_point_m is not None else "--"
            vmin_str = f"{m.v_min:.1f} km/h" if m.v_min is not None else "--"
            gas_str = f"{m.throttle_point_m:.0f} m" if m.throttle_point_m is not None else "--"
            time_str = f"{m.section_time:.3f} s" if m.section_time is not None else "--"

            delta_str = "--"
            delta_color = QColor(T.TXT_UNIT)
            if m.corner and m.corner.index in comp_dict:
                cp = comp_dict[m.corner.index]
                if cp.delta_time is not None:
                    d = cp.delta_time
                    delta_str = f"{d:+.3f} s"
                    delta_color = QColor("#ff1744") if d > 0.01 else (QColor("#00e676") if d < -0.01 else QColor(T.TXT_VALUE))

            item_name = QTableWidgetItem(c_name)
            dist_m = m.braking_point_m if m.braking_point_m is not None else (m.corner.start_m(1.0) if m.corner else 0.0)
            item_name.setData(Qt.UserRole, dist_m)

            item_brk = QTableWidgetItem(brk_str)
            item_vmin = QTableWidgetItem(vmin_str)
            item_gas = QTableWidgetItem(gas_str)
            item_time = QTableWidgetItem(time_str)
            item_delta = QTableWidgetItem(delta_str)
            item_delta.setForeground(delta_color)

            for col, it in enumerate([item_name, item_brk, item_vmin, item_gas, item_time, item_delta]):
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.setItem(row, col, it)

    def _on_item_clicked(self, item):
        row = item.row()
        name_item = self.item(row, 0)
        if name_item:
            dist_m = name_item.data(Qt.UserRole)
            if dist_m is not None:
                self.sig_corner_clicked.emit(float(dist_m))


# ---------------------------------------------------------------------------
# Janela Principal do Estúdio de Telemetria (TelemetryStudioWindow)
# ---------------------------------------------------------------------------
class TelemetryStudioWindow(QMainWindow):
    """
    Janela completa da Estação de Trabalho de Telemetria (MoTeC Style Telemetry Studio).
    Integra catálogo, mapa interativo de frenagem, gráficos empilhados, HUD e replay.
    """

    def __init__(self, library: LapLibrary):
        super().__init__()
        self.library = library

        self.current_track: str = ""
        self.current_car: str = ""
        self.current_rec: Optional[LapRecord] = None
        self.current_tel: dict = {}

        self.ref_rec: Optional[LapRecord] = None
        self.ref_tel: dict = {}

        self.corner_map: Optional[ca.CornerMap] = None
        self.corner_metrics: List[ca.CornerMetrics] = []
        self.corner_comparisons: List[ca.CornerComparison] = []

        self.setWindowTitle("ApexView — Telemetria Ponto a Ponto (MoTeC Telemetry Studio)")
        self.resize(1500, 920)
        self.setStyleSheet(T.app_qss())

        self._build_ui()
        self.reload_catalog()

        if self.tree.topLevelItemCount() == 0:
            self.load_demo_session()

    # -- Construção da Interface --------------------------------------------

    def _build_ui(self):
        pg.setConfigOption('background', T.BG_PANEL)
        pg.setConfigOption('foreground', T.TXT_UNIT)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_workspace())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 1180])
        root.addWidget(splitter)

        self.shortcut_space = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.shortcut_space.activated.connect(self.playback.toggle_play)

    def _build_sidebar(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QLabel("VOLTAS GRAVADAS")
        title.setFont(T.f_title(9))
        title.setStyleSheet(f"color: {T.TXT_TITLE};")
        layout.addWidget(title)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Volta", "Tempo", "Data"])
        self.tree.setColumnWidth(0, 150)
        self.tree.setColumnWidth(1, 75)
        self.tree.setStyleSheet(f"""
            QTreeWidget {{
                background-color: {T.BG_INSET}; color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER}; font-size: 11px;
            }}
            QTreeWidget::item:selected {{ background-color: {T.BG_HEADER}; color: #00e5ff; }}
            QHeaderView::section {{
                background-color: {T.BG_HEADER}; color: {T.TXT_TITLE};
                border: none; padding: 4px; font-size: 11px;
            }}
        """)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        layout.addWidget(self.tree, 1)

        lbl_ref = QLabel("VOLTA DE COMPARAÇÃO (DELTA):")
        lbl_ref.setFont(T.f_title(8))
        lbl_ref.setStyleSheet(f"color: {T.TXT_TITLE};")
        layout.addWidget(lbl_ref)

        self.combo_ref = QComboBox()
        self.combo_ref.setStyleSheet(f"""
            QComboBox {{
                background-color: {T.BG_INSET}; color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER}; padding: 4px; font-size: 11px;
            }}
        """)
        self.combo_ref.currentIndexChanged.connect(self._on_ref_combo_changed)
        layout.addWidget(self.combo_ref)

        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(4)

        btn_demo = QPushButton("⚡ CARREGAR VOLTA DEMO (MOCK)")
        btn_demo.setCursor(Qt.PointingHandCursor)
        btn_demo.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.BG_HEADER}; color: #00e5ff;
                border: 1px solid #00e5ff; padding: 6px; font-weight: bold; font-size: 11px;
            }}
            QPushButton:hover {{ background-color: #00e5ff; color: #000; }}
        """)
        btn_demo.clicked.connect(self.load_demo_session)
        btn_layout.addWidget(btn_demo)

        row_actions = QHBoxLayout()
        for text, slot in (("ATUALIZAR", self.reload_catalog),
                           ("RELATÓRIO", self._on_export_report),
                           ("MoTeC", self._on_export_motec)):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    background-color: {T.BG_INSET}; color: {T.TXT_VALUE};
                    border: 1px solid {T.BORDER}; padding: 4px; font-size: 10px; font-weight: bold;
                }}
                QPushButton:hover {{ background-color: {T.BG_HEADER}; }}
            """)
            b.clicked.connect(slot)
            row_actions.addWidget(b)
        btn_layout.addLayout(row_actions)

        layout.addLayout(btn_layout)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet(f"color: {T.TXT_UNIT}; font-size: 10px;")
        layout.addWidget(self.lbl_status)

        return panel

    def _build_workspace(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        top_bar = QHBoxLayout()
        self.lbl_session_title = QLabel("Selecione uma volta para iniciar a telemetria ponto a ponto")
        self.lbl_session_title.setFont(QFont(T.FONT_UI, 11, QFont.Bold))
        self.lbl_session_title.setStyleSheet("color: #ffffff;")
        top_bar.addWidget(self.lbl_session_title, 1)

        top_bar.addWidget(QLabel("Traçado:"))
        self.combo_heat = QComboBox()
        self.combo_heat.addItem("🛑 Freio & Acelerador", "brake_throttle")
        self.combo_heat.addItem("⚡ Heatmap Velocidade", "speed")
        self.combo_heat.addItem("🔢 Marchas", "gear")
        self.combo_heat.addItem("▲ Delta vs Referência", "delta")
        self.combo_heat.addItem("Linha Simples", "single")
        self.combo_heat.currentIndexChanged.connect(self._on_heat_mode_changed)
        self.combo_heat.setStyleSheet(f"""
            QComboBox {{
                background-color: {T.BG_INSET}; color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER}; padding: 3px 8px; font-size: 11px;
            }}
        """)
        top_bar.addWidget(self.combo_heat)

        btn_reset_cam = QPushButton("⛶ Centralizar Pista")
        btn_reset_cam.setCursor(Qt.PointingHandCursor)
        btn_reset_cam.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.BG_INSET}; color: {T.TXT_VALUE};
                border: 1px solid {T.BORDER}; padding: 4px 8px; font-size: 11px;
            }}
            QPushButton:hover {{ background-color: {T.BG_HEADER}; }}
        """)
        btn_reset_cam.clicked.connect(lambda: self.track_map.reset_view())
        top_bar.addWidget(btn_reset_cam)

        layout.addLayout(top_bar)

        center_split = QSplitter(Qt.Horizontal)

        # 1. Mapa Interativo
        self.track_map = TrackMapProWidget()
        self.track_map.sig_point_selected.connect(self._on_point_seek)
        center_split.addWidget(self.track_map)

        # 2. Gráficos Empilhados Sincronizados
        center_split.addWidget(self._build_charts())
        center_split.setStretchFactor(0, 1)
        center_split.setStretchFactor(1, 1)
        center_split.setSizes([600, 580])
        layout.addWidget(center_split, 1)

        bottom_box = QHBoxLayout()
        bottom_box.setSpacing(4)

        self.hud = PointInspectorWidget()
        bottom_box.addWidget(self.hud, 2)

        self.corner_table = CornerTableWidget()
        self.corner_table.sig_corner_clicked.connect(self._on_corner_clicked)
        self.corner_table.setFixedWidth(440)
        bottom_box.addWidget(self.corner_table, 1)

        layout.addLayout(bottom_box)

        self.playback = PlaybackController()
        self.playback.sig_seek.connect(self._on_point_seek)
        layout.addWidget(self.playback)

        return panel

    def _build_charts(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.plots = {}
        self.cursors = {}
        first_plot = None

        chart_specs = [
            ("speed", "Velocidade (km/h)"),
            ("pedals", "Gas (V) / Freio (R)"),
            ("gear", "Marcha"),
            ("steer", "Volante (°)"),
            ("delta", "Delta (s)"),
        ]

        for key, title in chart_specs:
            p = pg.PlotWidget()
            p.showGrid(x=True, y=True, alpha=0.15)
            p.setMenuEnabled(False)
            p.getAxis('left').setWidth(52)
            p.setLabel('left', title)

            if first_plot is None:
                first_plot = p
            else:
                p.setXLink(first_plot)

            cursor = pg.InfiniteLine(angle=90, movable=False,
                                     pen=pg.mkPen("#ffffff", width=1.2))
            p.addItem(cursor)
            self.cursors[key] = cursor
            self.plots[key] = p
            p.scene().sigMouseMoved.connect(self._make_chart_mouse_handler(p))
            layout.addWidget(p)

        return host

    # -- Sincronização e Movimentação Ponto a Ponto -------------------------

    def _on_point_seek(self, index: int):
        if not self.current_tel:
            return

        n = self.track_map.total_points
        if n < 2:
            return
        idx = max(0, min(n - 1, index))

        distances = self.current_tel.get("distance") or []
        cur_dist = distances[idx] if idx < len(distances) else 0.0

        ref_idx = 0
        ref_dists = self.ref_tel.get("distance") or []
        if len(ref_dists) >= 2:
            ref_idx = min(bisect.bisect_left(ref_dists, cur_dist), len(ref_dists) - 1)

        self.track_map.set_selected_index(idx, ref_idx)

        x_val = cur_dist
        for c in self.cursors.values():
            c.setValue(x_val)

        speeds = self.current_tel.get("speed") or []
        gases = self.current_tel.get("gas") or []
        brakes = self.current_tel.get("brake") or []
        gears = self.current_tel.get("gear") or []
        rpms = self.current_tel.get("rpm") or []
        steers = self.current_tel.get("steer") or []
        times = self.current_tel.get("times") or []
        deltas = self.current_tel.get("delta") or []
        lat_gs = self.current_tel.get("g_lat") or []
        lon_gs = self.current_tel.get("g_lon") or []

        tot_dist = distances[-1] if distances else 1.0
        time_s = times[idx] if idx < len(times) else 0.0
        spd = speeds[idx] if idx < len(speeds) else 0.0
        gas = gases[idx] if idx < len(gases) else 0.0
        brk = brakes[idx] if idx < len(brakes) else 0.0
        gear = gears[idx] if idx < len(gears) else 0
        rpm = rpms[idx] if idx < len(rpms) else 0
        steer = steers[idx] if idx < len(steers) else 0.0
        delta = deltas[idx] if idx < len(deltas) else None
        lat_g = lat_gs[idx] if idx < len(lat_gs) else 0.0
        lon_g = lon_gs[idx] if idx < len(lon_gs) else 0.0

        trail_start = max(0, idx - 25)
        trail = [(lat_gs[i], lon_gs[i]) for i in range(trail_start, idx + 1)
                 if i < len(lat_gs) and i < len(lon_gs)]

        corner_phrase = ""
        if self.corner_map and self.corner_map.corners:
            c, p = ca.corner_at(self.corner_map.corners, self.corner_map.track_length, cur_dist)
            corner_phrase = ca.where_phrase(c, p)
            if not corner_phrase and c is not None:
                corner_phrase = c.name
        if not corner_phrase:
            corner_phrase = "Reta"

        hud_data = {
            "dist": cur_dist,
            "tot_dist": tot_dist,
            "time": time_s,
            "speed": spd,
            "gas": gas,
            "brake": brk,
            "gear": gear,
            "rpm": rpm,
            "steer": steer,
            "delta": delta,
            "lat_g": lat_g,
            "lon_g": lon_g,
            "trail_g": trail,
            "corner_phrase": corner_phrase,
        }
        self.hud.update_point(hud_data)
        self.playback.update_display(idx)

    def _make_chart_mouse_handler(self, plot):
        def handler(pos):
            if not self.current_tel:
                return
            if not plot.sceneBoundingRect().contains(pos):
                return
            x = plot.getPlotItem().vb.mapSceneToView(pos).x()
            distances = self.current_tel.get("distance") or []
            if len(distances) < 2:
                return
            idx = min(bisect.bisect_left(distances, x), len(distances) - 1)
            self._on_point_seek(idx)
        return handler

    def _on_corner_clicked(self, dist_m: float):
        distances = self.current_tel.get("distance") or []
        if len(distances) >= 2:
            idx = min(bisect.bisect_left(distances, dist_m), len(distances) - 1)
            self._on_point_seek(idx)

    # -- Redesenho dos Gráficos ---------------------------------------------

    def redraw_charts(self):
        for p in self.plots.values():
            p.clear()
        for key, c in self.cursors.items():
            self.plots[key].addItem(c)

        if not self.current_tel:
            return

        x = self.current_tel.get("distance") or []
        n = len(x)
        if n < 2:
            return

        # 1. Velocidade
        spd = self.current_tel.get("speed") or []
        self.plots["speed"].plot(x[:len(spd)], spd[:n], pen=pg.mkPen(LAP_COLOR_MAIN, width=1.5))

        if self.ref_tel:
            ref_x = self.ref_tel.get("distance") or []
            ref_spd = self.ref_tel.get("speed") or []
            if len(ref_x) >= 2:
                self.plots["speed"].plot(ref_x[:len(ref_spd)], ref_spd,
                                         pen=pg.mkPen(LAP_COLOR_REF, width=1.2, style=Qt.DashLine))

        # 2. Pedais (Acelerador Verde + Freio Vermelho)
        gas = [g * 100.0 for g in (self.current_tel.get("gas") or [])]
        brk = [b * 100.0 for b in (self.current_tel.get("brake") or [])]
        self.plots["pedals"].plot(x[:len(gas)], gas[:n], pen=pg.mkPen("#00e676", width=1.5))
        self.plots["pedals"].plot(x[:len(brk)], brk[:n], pen=pg.mkPen("#ff1744", width=1.5))

        if self.ref_tel:
            ref_gas = [g * 100.0 for g in (self.ref_tel.get("gas") or [])]
            ref_brk = [b * 100.0 for b in (self.ref_tel.get("brake") or [])]
            ref_x = self.ref_tel.get("distance") or []
            if len(ref_x) >= 2:
                self.plots["pedals"].plot(ref_x[:len(ref_gas)], ref_gas,
                                          pen=pg.mkPen("#81c784", width=1, style=Qt.DotLine))
                self.plots["pedals"].plot(ref_x[:len(ref_brk)], ref_brk,
                                          pen=pg.mkPen("#e57373", width=1, style=Qt.DotLine))

        # 3. Marcha
        gear = self.current_tel.get("gear") or []
        self.plots["gear"].plot(x[:len(gear)], gear[:n], pen=pg.mkPen("#ffea00", width=1.5))

        # 4. Volante
        steer = self.current_tel.get("steer") or []
        self.plots["steer"].plot(x[:len(steer)], steer[:n], pen=pg.mkPen("#ffd23f", width=1.5))
        self.plots["steer"].addLine(y=0, pen=pg.mkPen("#555555", style=Qt.DashLine))

        # 5. Delta de Tempo
        plot_delta = self.plots["delta"]
        if self.ref_tel:
            deltas = self.current_tel.get("delta") or []
            if len(deltas) >= 2:
                plot_delta.plot(x[:len(deltas)], deltas[:n], pen=pg.mkPen(LAP_COLOR_MAIN, width=1.5))
        plot_delta.addLine(y=0, pen=pg.mkPen("#666666", style=Qt.DashLine))

    # -- Seleção de Voltas e Dados ------------------------------------------

    def reload_catalog(self):
        self.tree.blockSignals(True)
        self.tree.clear()

        total_laps = 0
        for combo in self.library.catalog():
            track, car = combo["track"], combo["car"]
            total_laps += combo["laps"]
            best = combo["best"]

            top = QTreeWidgetItem([f"{track} — {car}",
                                   best.lap_time_str if best else "",
                                   f"{combo['laps']} voltas"])
            top.setForeground(0, QColor(T.TXT_TITLE))
            self.tree.addTopLevelItem(top)

            for session in self.library.sessions(track, car):
                node = QTreeWidgetItem([
                    f"Sessão {session['date_str'] or session['session_id']}",
                    session["best"].lap_time_str if session["best"] else "",
                    f"{len(session['laps'])} voltas"])
                node.setForeground(0, QColor(T.TXT_UNIT))
                top.addChild(node)

                for rec in session["laps"]:
                    leaf = QTreeWidgetItem([rec.label(with_date=False),
                                            rec.lap_time_str, rec.date_str])
                    leaf.setData(0, Qt.UserRole, (track, car, rec.lap_id))
                    if not rec.valid:
                        leaf.setForeground(0, QColor("#c98a00"))
                    if best is not None and rec.lap_id == best.lap_id:
                        leaf.setForeground(1, QColor("#00e676"))
                    node.addChild(leaf)

        self.tree.expandToDepth(0)
        self.tree.blockSignals(False)

        if total_laps:
            self.lbl_status.setText(f"{total_laps} voltas disponíveis no catálogo.")
        else:
            self.lbl_status.setText("Catálogo vazio. Use o botão Demo para testar.")

    def _on_tree_selection_changed(self):
        items = self.tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.UserRole)
        if not data:
            return
        track, car, lap_id = data
        rec = self.library.find(track, car, lap_id)
        if rec is None:
            return

        self.load_lap(track, car, rec)

    def _get_or_detect_corner_map(self, track_name: str, telemetry: dict) -> Optional[ca.CornerMap]:
        distances = telemetry.get("distance") or []
        track_len = float(distances[-1]) if distances else 4309.0
        cmap = ca.load_corner_map(track_name, track_len)
        if cmap is None:
            cmap = ca.build_auto_corner_map(track_name, telemetry, track_len)
        return cmap

    def load_lap(self, track: str, car: str, rec: LapRecord):
        self.current_track = track
        self.current_car = car
        self.current_rec = rec
        self.current_tel = self.library.load_telemetry(track, car, rec) or {}

        if not self.current_tel:
            self.lbl_status.setText("Falha ao abrir telemetria desta volta.")
            return

        distances = self.current_tel.get("distance") or []
        self.corner_map = self._get_or_detect_corner_map(track, self.current_tel)

        if self.corner_map and self.corner_map.corners:
            self.corner_metrics = ca.analyze_lap(self.current_tel, self.corner_map.corners, self.corner_map.track_length)
        else:
            self.corner_metrics = []

        self._populate_ref_combos()
        self.track_map.set_lap_data(self.current_tel, self.corner_map, self.corner_metrics)
        self.corner_table.populate(self.corner_metrics, self.corner_comparisons,
                                   speeds=self.current_tel.get("speed"), distances=distances)
        self.playback.set_total_points(self.track_map.total_points)

        self.lbl_session_title.setText(
            f"{track}  ·  {car}  ·  Volta {rec.lap_number} ({rec.lap_time_str})")
        self.redraw_charts()
        self._on_point_seek(0)

    def _populate_ref_combos(self):
        self.combo_ref.blockSignals(True)
        self.combo_ref.clear()
        self.combo_ref.addItem("Sem Referência (Análise Solo)", None)

        best = self.library.best_lap(self.current_track, self.current_car)
        if best and self.current_rec and best.lap_id != self.current_rec.lap_id:
            self.combo_ref.addItem(f"★ Melhor Volta ({best.lap_time_str})", best)

        all_laps = self.library.records(self.current_track, self.current_car)
        for r in all_laps:
            if self.current_rec and r.lap_id == self.current_rec.lap_id:
                continue
            if best and r.lap_id == best.lap_id:
                continue
            self.combo_ref.addItem(f"Volta {r.lap_number} ({r.lap_time_str}) - {r.date_str}", r)

        self.combo_ref.blockSignals(False)

        if self.combo_ref.count() > 1:
            self.combo_ref.setCurrentIndex(1)
            self._on_ref_combo_changed()

    def _on_ref_combo_changed(self):
        ref_rec = self.combo_ref.currentData()
        if ref_rec is not None:
            self.ref_rec = ref_rec
            self.ref_tel = self.library.load_telemetry(self.current_track, self.current_car, ref_rec) or {}
            self._compute_deltas()
            if self.corner_map and self.ref_tel and self.corner_map.corners:
                self.corner_comparisons = ca.compare_laps(
                    self.current_tel, self.ref_tel, self.corner_map.corners, self.corner_map.track_length)
        else:
            self.ref_rec = None
            self.ref_tel = {}
            self.corner_comparisons = []
            if "delta" in self.current_tel:
                del self.current_tel["delta"]

        self.track_map.set_reference_lap_data(self.ref_tel)
        self.corner_table.populate(self.corner_metrics, self.corner_comparisons,
                                   speeds=self.current_tel.get("speed"), distances=self.current_tel.get("distance"))
        self.redraw_charts()
        self._on_point_seek(self.playback.current_index)

    def _compute_deltas(self):
        if not self.current_tel or not self.ref_tel:
            return
        cur_dists = self.current_tel.get("distance") or []
        cur_times = self.current_tel.get("times") or []
        ref_dists = self.ref_tel.get("distance") or []
        ref_times = self.ref_tel.get("times") or []

        if len(cur_dists) < 2 or len(ref_dists) < 2:
            return

        deltas = []
        for i, d in enumerate(cur_dists):
            if d < ref_dists[0] or d > ref_dists[-1]:
                deltas.append(0.0)
                continue
            j = bisect.bisect_left(ref_dists, d)
            if j <= 0:
                ref_t = ref_times[0]
            elif j >= len(ref_dists):
                ref_t = ref_times[-1]
            else:
                d0, d1 = ref_dists[j - 1], ref_dists[j]
                t0, t1 = ref_times[j - 1], ref_times[j]
                ratio = (d - d0) / (d1 - d0) if d1 != d0 else 0.0
                ref_t = t0 + ratio * (t1 - t0)
            deltas.append(cur_times[i] - ref_t)

        self.current_tel["delta"] = deltas

    def _on_heat_mode_changed(self):
        mode = self.combo_heat.currentData()
        self.track_map.set_color_mode(mode)

    # -- Sessão de Demonstração (Mock Mode) ---------------------------------

    def load_demo_session(self):
        from providers.mock import (
            TRACK_NAME, CAR_NAME, TRACK_LENGTH, _MOCK_TRACK_PATH,
            _track_profile, _gear_for_speed
        )

        tel = {
            "times": [], "distance": [], "speed": [], "gas": [], "brake": [],
            "steer": [], "gear": [], "rpm": [], "car_x": [], "car_z": [],
            "g_lat": [], "g_lon": []
        }

        pts = len(_MOCK_TRACK_PATH)
        for i in range(pts):
            p = i / float(max(1, pts - 1))
            gas, brk, spd, steer = _track_profile(p)
            gear, rpm = _gear_for_speed(spd, braking=(brk > 0.1))
            x, z = _MOCK_TRACK_PATH[i]
            dist = p * TRACK_LENGTH
            t = p * 92.5

            lat_g = steer * 2.2
            lon_g = (1.1 if gas > 0.5 else 0.0) - (brk * 1.8)

            tel["times"].append(t)
            tel["distance"].append(dist)
            tel["speed"].append(spd)
            tel["gas"].append(gas)
            tel["brake"].append(brk)
            tel["steer"].append(steer * 45.0)
            tel["gear"].append(gear)
            tel["rpm"].append(int(rpm))
            tel["car_x"].append(x)
            tel["car_z"].append(z)
            tel["g_lat"].append(lat_g)
            tel["g_lon"].append(lon_g)

        demo_rec = LapRecord(
            lap_id="demo_01", track=TRACK_NAME, car=CAR_NAME,
            lap_number=1, lap_time_str="1:32.500", lap_time_ms=92500,
            valid=True, full_lap=True, points=pts
        )

        self.current_track = TRACK_NAME
        self.current_car = CAR_NAME
        self.current_rec = demo_rec
        self.current_tel = tel

        self.corner_map = self._get_or_detect_corner_map(TRACK_NAME, tel)
        if self.corner_map and self.corner_map.corners:
            self.corner_metrics = ca.analyze_lap(tel, self.corner_map.corners, self.corner_map.track_length)
        else:
            self.corner_metrics = []

        self.track_map.set_lap_data(tel, self.corner_map, self.corner_metrics)
        self.corner_table.populate(self.corner_metrics, speeds=tel.get("speed"), distances=tel.get("distance"))
        self.playback.set_total_points(self.track_map.total_points)

        self.lbl_session_title.setText(
            f"⚡ DEMO — {TRACK_NAME} · {CAR_NAME} · Volta 1 (1:32.500)")
        self.lbl_status.setText("Volta demo carregada com sucesso. Explore o traçado e os controles.")
        self.redraw_charts()
        self._on_point_seek(0)

    # -- Exportações --------------------------------------------------------

    def _on_export_report(self):
        if not self.current_rec:
            return
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "mapa_module", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mapa.pyw"))
        mapa_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mapa_mod)

        dlg = mapa_mod.LapReportDialog(self, self.library, self.current_track, self.current_car,
                                       self.current_rec, ref_rec=self.ref_rec)
        dlg.exec_()

    def _on_export_motec(self):
        if not self.current_rec:
            return
        sug = f"{self.current_track}_{self.current_car}_V{self.current_rec.lap_number}.ld".replace(" ", "_")
        path, _ = QFileDialog.getSaveFileName(self, "Exportar MoTeC", sug, "MoTeC i2 Log (*.ld)")
        if path:
            if not path.lower().endswith(".ld"):
                path += ".ld"
            if self.library.export_motec(self.current_track, self.current_car, self.current_rec, path):
                self.lbl_status.setText(f"Exportado MoTeC: {os.path.basename(path)}")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    library = LapLibrary(retention=RetentionPolicy(enabled=False))
    win = TelemetryStudioWindow(library)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
