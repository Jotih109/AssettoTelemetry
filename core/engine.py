"""
core/engine.py — Motor Central de Telemetria
=============================================
Roda em uma QThread separada para não bloquear a interface gráfica.
A cada ciclo (padrão: 60 Hz) chama provider.get_state(), aplica
cálculos de engenharia e emite o sinal on_update para a UI.
"""

import time
import traceback
from PyQt5.QtCore import QThread, pyqtSignal
from providers.base import TelemetryProvider
from core.models import TelemetryState


def _next_deadline(prev_deadline: float, period: float, now: float):
    """
    Próximo instante-alvo do laço de 60 Hz, e quanto falta dormir até lá.

    Separada do laço para poder testar a compensação de deriva sem depender
    de tempo real — um relógio falso (a lista de `now` que o teste passa)
    basta, e não há sleep nenhum no caminho do teste.

    Devolve `(proximo_deadline, atraso)`:
      * `atraso > 0`  — ainda falta esse tanto até o próximo quadro; dorme.
      * `atraso <= 0` — o ciclo já está atrasado. Não tenta recuperar dormindo
        negativo, e o deadline REANCORA em `now`: um atraso de 2s (troca de
        sessão, erro, o que for) não vira uma rajada de dezenas de quadros
        idênticos tentando "descontar" o tempo perdido de uma vez.
    """
    proximo = prev_deadline + period
    atraso = proximo - now
    if atraso <= 0:
        return now, 0.0
    return proximo, atraso


class TelemetryEngine(QThread):
    """
    Motor central da telemetria (agnóstico de simulador).

    Responsabilidades:
    - Chamar provider.connect() e re-tentar automaticamente em caso de falha
    - Invocar provider.get_state() a cada ciclo para obter o TelemetryState
    - Aplicar cálculos derivados (consumo de combustível, estimativas)
    - Emitir o sinal on_update para a interface gráfica consumir
    """

    # Sinal Qt que envia o estado empacotado para a Interface
    on_update = pyqtSignal(TelemetryState)

    def __init__(self, provider: TelemetryProvider, hz: int = 60):
        super().__init__()
        self.provider = provider
        self.hz = hz
        self._running = False

    def run(self):
        self._running = True
        sleep_time = 1.0 / self.hz
        # Relógio de DEADLINE ABSOLUTO, não "durma sleep_time a cada volta":
        # connect() + get_state() + os cálculos consomem parte do orçamento
        # do quadro, e dormir sleep_time inteiro DEPOIS soma os dois tempos —
        # medido: 60 Hz pedidos entregavam ~56 Hz de verdade (6,7% de
        # déficit). A matemática da compensação está em `_next_deadline`,
        # testável sem tempo real; aqui só se chama ela.
        next_tick = time.perf_counter()

        while self._running:
            try:
                # 1. Conecta ou re-conecta ao provider
                if not self.provider.connect():
                    self.on_update.emit(TelemetryState(is_connected=False))
                    time.sleep(1.0)
                    next_tick = time.perf_counter()
                    continue

                # 2. Lê os dados mais recentes do simulador
                state = self.provider.get_state()

                # 3. Cálculos derivados de engenharia
                # NOTA: fuel_avg_consumption e fuel_laps_remaining são calculados
                # dinamicamente pelo SessionManager (média real das últimas 5 voltas)
                # e injetados pela main_window antes de enviar para a sidebar.
                # Não sobrescrever aqui com valor fixo.

                # 4. Emite o estado para a interface gráfica
                self.on_update.emit(state)

            except Exception:
                # Imprime o traceback completo sem fechar a thread
                print("[Engine] ERRO no ciclo de telemetria:")
                traceback.print_exc()
                time.sleep(0.5)  # Pausa antes de tentar novamente
                next_tick = time.perf_counter()

            next_tick, atraso = _next_deadline(next_tick, sleep_time, time.perf_counter())
            if atraso > 0:
                time.sleep(atraso)

        self.provider.close()

    def stop(self):
        """Para a thread de forma segura, aguardando o ciclo atual terminar."""
        self._running = False
        self.wait()
