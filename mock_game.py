"""
mock_game.py — Simulador de Telemetria do Assetto Corsa (Shared Memory)
=======================================================================
Gera telemetria gravando diretamente na memória compartilhada do Windows
(Local\\acpmf_physics, Local\\acpmf_graphics, Local\\acpmf_static)
usando as estruturas nativas do Assetto Corsa 1.

COMO USAR:
  1. Terminal A: python mock_game.py
  2. Terminal B: python main.pyw (com MOCK_MODE = False)
"""

import time
import mmap
import ctypes

from providers.assettocorsa import SPageFilePhysics, SPageFileGraphic, SPageFileStatic
from providers.mock import MockTelemetryProvider

def run_mock_ac():
    print("=" * 60)
    print("  AC Mock Telemetry | Interlagos | Porsche 992 GT3 Cup")
    print("=" * 60)
    print("  Inicializando memória compartilhada do Windows...")

    # Criação dos mapeamentos de memória (Windows) - igual ao Assetto Corsa real
    shm_p = mmap.mmap(-1, ctypes.sizeof(SPageFilePhysics), "Local\\acpmf_physics")
    shm_g = mmap.mmap(-1, ctypes.sizeof(SPageFileGraphic), "Local\\acpmf_graphics")
    shm_s = mmap.mmap(-1, ctypes.sizeof(SPageFileStatic), "Local\\acpmf_static")

    # Vinculando as structs aos buffers de memória para escrita direta
    physics = SPageFilePhysics.from_buffer(shm_p)
    graphics = SPageFileGraphic.from_buffer(shm_g)
    static = SPageFileStatic.from_buffer(shm_s)

    # 1. Preenche bloco estático (Lido 1 vez pelo dashboard)
    static.smVersion = "1.7"
    static.acVersion = "1.16.3"
    static.carModel = "Porsche 992 GT3 Cup"
    static.track = "Interlagos"
    static.playerName = "Mock Player"
    static.maxRpm = 8500
    static.maxFuel = 80.0
    static.trackSPlineLength = 4309.0
    
    print("  Memória mapeada com sucesso.")
    print("  Gerando telemetria a 60 Hz... (Pressione Ctrl+C para sair)")
    print("=" * 60)

    # Aproveitamos a lógica avançada que já existe no MockTelemetryProvider
    # para gerar os dados e apenas "injetamos" na memória compartilhada.
    provider = MockTelemetryProvider()
    provider.connect()
    
    packet_id = 0
    
    try:
        while True:
            state = provider.get_state()
            packet_id += 1
            
            # --- Atualiza a struct de Física ---
            physics.packetId = packet_id
            physics.gas = state.gas
            physics.brake = state.brake
            physics.fuel = state.fuel
            physics.gear = state.gear
            physics.rpms = state.rpm
            physics.steerAngle = state.steer_angle / 360.0
            physics.speedKmh = state.speed_kmh
            physics.accG[0] = state.g_lat
            physics.accG[1] = state.g_vert
            physics.accG[2] = state.g_lon
            
            for i in range(4):
                physics.tyreCoreTemperature[i] = state.tyre_temp[i]
                physics.wheelsPressure[i] = state.tyre_pressure[i]
                physics.tyreWear[i] = state.tyre_wear[i]
                physics.suspensionTravel[i] = state.suspension_travel[i]
                physics.brakeTemp[i] = state.brake_temp[i]
                physics.tyreTempI[i] = state.tyre_temp_inner[i]
                physics.tyreTempM[i] = state.tyre_temp_middle[i]
                physics.tyreTempO[i] = state.tyre_temp_outer[i]
            
            # --- Atualiza a struct de Gráficos (Sessão/Tempos) ---
            graphics.packetId = packet_id
            graphics.status = 2  # AC_LIVE
            graphics.session = 0 # Practice
            graphics.currentTime = state.current_time
            graphics.lastTime = state.last_time
            graphics.bestTime = state.best_time
            graphics.distanceTraveled = state.distance_traveled
            graphics.currentSectorIndex = state.sector_index
            graphics.completedLaps = state.completed_laps
            graphics.isInPit = 1 if state.in_pit else 0
            graphics.isInPitLane = 1 if state.in_pit_lane else 0
            graphics.normalizedCarPosition = state.track_position
            
            # Feedback na tela
            if packet_id % 60 == 0:
                print(f"  Volta: {state.lap_number:02d} | Tempo: {state.current_time} | Vel: {int(state.speed_kmh):03d} km/h", end="\r")

            time.sleep(1/60.0)

    except KeyboardInterrupt:
        print("\n\n  Mock encerrado.")
    finally:
        shm_p.close()
        shm_g.close()
        shm_s.close()


if __name__ == "__main__":
    run_mock_ac()
