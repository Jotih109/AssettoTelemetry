"""
ui/bottom_strip.py — Faixa de análise ao lado do histórico de voltas
====================================================================
A sidebar concentra o que o piloto olha de relance (marcha, RPM, pedais,
combustível, eletrônica). Os painéis de análise, que pedem mais espaço
horizontal, ficam nesta faixa embaixo dos gráficos:

    Força G (G-G plot)  |  Freios  |  Pista  |  Pneus

Cada painel tem largura fixa e altura elástica, para que todos comecem e
terminem na mesma linha — a folga que sobrar vai para o histórico de voltas.
"""

from PyQt5.QtWidgets import QWidget, QHBoxLayout, QSizePolicy
from core.models import TelemetryState
from ui.components import GForceCard, BrakesCard, WeatherCard, TireCard


class BottomStrip(QWidget):
    # Largura de cada painel, na ordem em que aparecem
    WIDTHS = {"gforce": 175, "brakes": 235, "weather": 170, "tires": 290}

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background: transparent;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.gforce_card = GForceCard()
        self.brakes_card = BrakesCard()
        self.weather_card = WeatherCard()
        self.tire_card = TireCard()

        for key, card in (("gforce", self.gforce_card),
                          ("brakes", self.brakes_card),
                          ("weather", self.weather_card),
                          ("tires", self.tire_card)):
            card.setFixedWidth(self.WIDTHS[key])
            # Altura elástica: todos os painéis preenchem a altura da faixa
            card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            layout.addWidget(card)

    def update_strip(self, state: TelemetryState):
        if not state.is_connected:
            return

        # Força G lateral x longitudinal (com rastro dos últimos instantes)
        self.gforce_card.update_g(state.g_lat, state.g_lon)

        # Temperatura dos freios + distribuição de frenagem
        self.brakes_card.update_brakes(state.brake_temp, state.brake_bias)

        # AC1 não tem chuva: mostramos aderência da pista e vento
        self.weather_card.update_weather(
            ambient=state.ambient_temp,
            track=state.track_temp,
            grip=state.surface_grip,
            wind_speed=state.wind_speed,
            wind_dir=state.wind_direction,
        )

        # Pneus: temperatura do núcleo, pressão, desgaste e I/M/E da banda
        for i, box in enumerate((self.tire_card.t_fl, self.tire_card.t_fr,
                                 self.tire_card.t_rl, self.tire_card.t_rr)):
            self.tire_card.update_tire(
                box,
                state.tyre_temp[i], state.tyre_pressure[i], state.tyre_wear[i],
                t_inner=state.tyre_temp_inner[i],
                t_middle=state.tyre_temp_middle[i],
                t_outer=state.tyre_temp_outer[i],
            )
