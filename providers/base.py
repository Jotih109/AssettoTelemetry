from abc import ABC, abstractmethod
from core.models import TelemetryState

class TelemetryProvider(ABC):
    """
    Interface base para provedores de telemetria.

    Define o contrato que qualquer provider de simulador deve implementar,
    garantindo que a Engine e a Interface Gráfica sejam completamente
    independentes do protocolo de comunicação do jogo.

    Implementações disponíveis:
        AssettoCorsaTelemetryProvider — Assetto Corsa 1 via memória compartilhada
        AMS2TelemetryProvider         — Automobilista 2 via UDP (protocolo pCars2)
        MockTelemetryProvider         — simulador interno, para testes
    """
    
    @abstractmethod
    def connect(self) -> bool:
        """Tenta estabelecer a conexão com a telemetria do simulador."""
        pass
        
    @abstractmethod
    def get_state(self) -> TelemetryState:
        """Lê os dados mais recentes e retorna um TelemetryState preenchido."""
        pass
        
    @abstractmethod
    def close(self):
        """Fecha conexões, libera memória, limpa buffers."""
        pass
