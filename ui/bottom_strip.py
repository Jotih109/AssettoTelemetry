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

from PyQt5.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QSizePolicy
from core.models import TelemetryState
from ui.components import BrakesCard, TireCard, SessionCard, AssistsCard
from ui import theme as T


class BottomStrip(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background: transparent;")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Usando um único painel integrado sem título
        self.panel = T.Panel(body_margins=(4, 4, 4, 4))
        
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)

        self.session_card = SessionCard()
        self.assists_card = AssistsCard()
        self.brakes_card = BrakesCard()
        self.tire_card = TireCard()

        # Remove bordas individuais de cada card para unificar no painel principal
        cards = (self.session_card, self.assists_card, self.brakes_card, self.tire_card)
                 
        for card in cards:
            card.setStyleSheet(card.styleSheet().replace(f"border: 1px solid {T.BORDER};", "border: none;"))
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            
        grid.addWidget(self.session_card, 0, 0)
        grid.addWidget(self.assists_card, 0, 1)
        grid.addWidget(self.brakes_card, 1, 0)
        grid.addWidget(self.tire_card, 1, 1)

        self.panel.body.addLayout(grid)
        main_layout.addWidget(self.panel)

    def update_strip(self, state: TelemetryState):
        if not state.is_connected:
            return

        # Sessão e Eletrônica (movidos da sidebar)
        self.session_card.update_session(state)
        self.assists_card.update_electronics(state)

        # Temperatura dos freios + distribuição de frenagem
        self.brakes_card.update_brakes(state.brake_temp, state.brake_bias)

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
