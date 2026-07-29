from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea, QSizePolicy
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from core.models import TelemetryState
from ui import theme as T
from ui.components import (
    GearCard, SpeedCard, RpmCard, CarDataCard,
    PedalsBarCard, SteeringWheelCard, TrackMapCard
)


class SidebarPanel(QWidget):
    """
    Coluna esquerda: o que o piloto lê de relance.

    Dimensionada para caber INTEIRA em uma janela de ~850 px de altura, sem
    rolagem — no i2 nada fica escondido atrás de uma barra de rolagem.
    """

    def __init__(self):
        super().__init__()
        self.setFixedWidth(240)
        self.setStyleSheet(f"background-color: {T.BG_APP}; border: none;")

        base_layout = QVBoxLayout(self)
        base_layout.setContentsMargins(0, 0, 0, 0)
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        
        scroll_widget = QWidget()
        scroll_widget.setStyleSheet("background: transparent;")
        main_layout = QVBoxLayout(scroll_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(4)
        
        scroll_area.setWidget(scroll_widget)
        base_layout.addWidget(scroll_area)

        # --- Cabeçalho: conexão + pista/carro ---
        header = QFrame()
        header.setStyleSheet(T.panel_qss())
        head_lay = QVBoxLayout(header)
        head_lay.setContentsMargins(4, 4, 4, 4)
        head_lay.setSpacing(2)

        conn_row = QHBoxLayout()
        conn_row.setSpacing(4)
        self._conn_dot = QLabel("■")
        self._conn_dot.setFont(QFont(T.FONT_MONO, 8))
        self._conn_dot.setStyleSheet(f"color: {T.BAD}; background: transparent; border: none;")
        self._conn_label = QLabel("AGUARDANDO O ASSETTO CORSA")
        self._conn_label.setFont(T.f_title(7))
        self._conn_label.setStyleSheet(
            f"color: {T.TXT_DIM}; background: transparent; border: none;")
        conn_row.addWidget(self._conn_dot)
        conn_row.addWidget(self._conn_label)
        conn_row.addStretch()
        head_lay.addLayout(conn_row)

        self._lbl_car = QLabel("--")
        self._lbl_car.setFont(QFont(T.FONT_UI, 9, QFont.DemiBold))
        self._lbl_car.setStyleSheet(
            f"color: {T.TXT_VALUE}; background: transparent; border: none;")
        self._lbl_car.setWordWrap(True)
        self._lbl_track = QLabel("--")
        self._lbl_track.setFont(T.f_label(8))
        self._lbl_track.setStyleSheet(
            f"color: {T.TXT_LABEL}; background: transparent; border: none;")
        self._lbl_track.setWordWrap(True)
        head_lay.addWidget(self._lbl_car)
        head_lay.addWidget(self._lbl_track)
        main_layout.addWidget(header)

        # Instantiate Components
        self.pedals_bar_card = PedalsBarCard()
        self.gear_card = GearCard()
        self.speed_card = SpeedCard()
        self.rpm_card = RpmCard()
        self.car_data_card = CarDataCard()
        self.steer_card = SteeringWheelCard()

        main_layout.addWidget(self.rpm_card, stretch=0)

        # Marcha + Velocidade empilhados, pedais ao lado
        gear_speed_col = QVBoxLayout()
        gear_speed_col.setSpacing(4)
        gear_speed_col.addWidget(self.gear_card)
        gear_speed_col.addWidget(self.speed_card)
        gear_speed_col.addWidget(self.steer_card)

        top_row = QHBoxLayout()
        top_row.setSpacing(4)
        top_row.addWidget(self.pedals_bar_card)
        top_row.addLayout(gear_speed_col, stretch=1)
        main_layout.addLayout(top_row, stretch=0)

        main_layout.addWidget(self.car_data_card, stretch=0)
        
        self.track_map_card = TrackMapCard()
        self.track_map_card.setMinimumHeight(180)
        self.track_map_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        main_layout.addWidget(self.track_map_card, stretch=1)

        # Removemos o addStretch() para que o TrackMapCard ocupe o espaço restante


    def update_panel(self, state: TelemetryState):
        if not state.is_connected:
            self._conn_dot.setStyleSheet(
                f"color: {T.BAD}; background: transparent; border: none;")
            self._conn_label.setText("AGUARDANDO O ASSETTO CORSA")
            return

        # Status conexão
        if state.is_paused:
            dot, txt = T.WARN, "PAUSADO"
        elif state.is_replay:
            dot, txt = T.CH_SPEED, "REPLAY"
        else:
            dot, txt = T.OK, "AO VIVO"
        self._conn_dot.setStyleSheet(f"color: {dot}; background: transparent; border: none;")
        self._conn_label.setText(txt)

        # Pista / Carro
        self._lbl_car.setText(state.car_name)
        self._lbl_track.setText(state.track_name)

        # Update Gear and Speed Cards (pass rpm for dynamic color)
        max_rpm = getattr(state, 'max_rpm', 8500.0)
        self.pedals_bar_card.update_pedals(state.gas, state.brake)
        self.gear_card.update_gear(state.gear, state.rpm, max_rpm)
        self.speed_card.update_speed(int(state.speed_kmh))

        # Update RPM Card
        self.rpm_card.update_rpm(state.rpm, max_rpm)

        # Update Car Data
        self.car_data_card.update_data(
            fuel=state.fuel,
            laps=state.fuel_laps_remaining,
            turbo=state.turbo_boost,
            steer=state.steer_angle,
            fuel_avg=getattr(state, '_fuel_avg', 0.0),
            fuel_capacity=state.fuel_capacity,
        )

        # Update Steering Wheel
        self.steer_card.update_steer(state.steer_angle)
