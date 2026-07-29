"""
main.pyw — Ponto de entrada do Dashboard de Telemetria do Assetto Corsa
=======================================================================
Fluxo de inicialização:
  1. AssettoCorsaTelemetryProvider — Abre a memória compartilhada do AC
  2. TelemetryEngine               — Thread a 60 Hz que chama get_state() e emite sinais Qt
  3. DashboardMainWindow           — Interface gráfica que reage aos sinais da Engine

NÃO É PRECISO CONFIGURAR NADA NO JOGO.
O Assetto Corsa 1 publica a telemetria automaticamente em memória
compartilhada (acpmf_physics / acpmf_graphics / acpmf_static). Basta abrir
o jogo e entrar na pista — o dashboard conecta sozinho e reconecta sozinho
se você sair para o menu ou fechar/reabrir o jogo.
"""

import sys
import traceback
from PyQt5.QtWidgets import QApplication

from providers.assettocorsa import AssettoCorsaTelemetryProvider
from providers.mock import MockTelemetryProvider
from core.engine import TelemetryEngine
from ui.main_window import DashboardMainWindow


def _install_crash_guard():
    """
    Rede de segurança contra travamento no meio da sessão.

    O PyQt5 aborta o processo quando uma exceção escapa de um slot — ou seja,
    um único quadro de telemetria estranho fecharia o dashboard enquanto você
    está na pista. Com um excepthook próprio, o erro é registrado no console e
    o app continua rodando.
    """
    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        print("[!] Erro não tratado (o dashboard continua rodando):")
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = hook

# --------------------------------------------------------------------------
# MOCK_MODE
# --------------------------------------------------------------------------
# True  -> usa o MockTelemetryProvider (simulador interno, sem o jogo aberto).
#          Ideal para testar a interface e a lógica de setores/deltas/gráficos
#          numa máquina onde o Assetto Corsa não está instalado.
# False -> usa o AssettoCorsaTelemetryProvider real (memória compartilhada).
MOCK_MODE = False


def main():
    _install_crash_guard()
    app = QApplication(sys.argv)

    if MOCK_MODE:
        print("[*] MOCK_MODE ativo — usando simulador interno de telemetria (sem o AC).")
        provider = MockTelemetryProvider()
    else:
        print("[*] Aguardando o Assetto Corsa... (nada a configurar no jogo)")
        provider = AssettoCorsaTelemetryProvider()

    # Injeta o provider na Engine central (60 Hz)
    engine = TelemetryEngine(provider=provider, hz=60)

    # Passa a Engine para a Interface Gráfica
    window = DashboardMainWindow(engine)
    window.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
