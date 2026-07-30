"""
providers/mock.py — Provider de Telemetria Simulado (MOCK_MODE)
=================================================================
Gera um TelemetryState fictício, avançando em tempo real (baseado no
relógio, não em ticks fixos), para permitir testar toda a UI, o
SessionManager (setores, deltas, histórico de voltas, ghosts) e a
escala dinâmica dos gráficos sem precisar ter o Assetto Corsa instalado.

Implementa a mesma interface TelemetryProvider usada pelo
AssettoCorsaTelemetryProvider — basta trocar qual provider é injetado na
Engine (ver MOCK_MODE em main.pyw).
"""

import math
import random
import time

from core.models import TelemetryState
from providers.base import TelemetryProvider

TRACK_NAME = "Autodromo Jose Carlos Pace — Grand Prix (MOCK)"
CAR_NAME = "Porsche 992 GT3 Cup (MOCK)"
TRACK_LENGTH = 4309.0  # metros

BASE_S1_MS = 31500
BASE_S2_MS = 31500
BASE_S3_MS = 26500
BASE_LAP_MS = BASE_S1_MS + BASE_S2_MS + BASE_S3_MS

FUEL_CAPACITY = 80.0
FUEL_INITIAL = 60.0
FUEL_PER_LAP = 1.45

GEAR_RANGES = {
    2: (0, 78),
    3: (68, 118),
    4: (105, 163),
    5: (148, 213),
    6: (198, 258),
    7: (243, 315),
}


def _gear_for_speed(speed_kmh, braking=False):
    chosen = 2
    items = sorted(GEAR_RANGES.items(), key=lambda x: x[0], reverse=braking)
    for g, (vmin, vmax) in items:
        if vmin <= speed_kmh <= vmax:
            chosen = g
            break
    else:
        if speed_kmh > 260:
            chosen = 7
    vmin, vmax = GEAR_RANGES.get(chosen, (240, 310))
    ratio = max(0.0, min(1.0, (speed_kmh - vmin) / max(1, vmax - vmin)))
    rpm = 4200 + ratio * 4100 + random.randint(-50, 50)
    return chosen, rpm


def _track_profile(p):
    """Perfil de pista fictício com 8 zonas (retas e curvas)."""
    noise = random.gauss(0, 0.012)
    snoise = random.uniform(-1.2, 1.2)

    if p < 0.15:
        t = p / 0.15
        gas, brake = min(1.0, 0.9 + noise), 0.0
        speed = 130 + t * 150 + snoise
        steer = random.uniform(-0.01, 0.01)
    elif p < 0.22:
        t = (p - 0.15) / 0.07
        gas, brake = 0.0, min(1.0, t * 3.5 + noise)
        speed = 280 - t * 180 + snoise
        steer = math.sin(t * math.pi) * 0.45 + noise
    elif p < 0.38:
        t = (p - 0.22) / 0.16
        gas = max(0.05, 0.25 + t * 0.5 + noise)
        brake = max(0.0, 0.15 - t * 0.15 + noise)
        speed = 100 + t * 80 + snoise
        steer = math.sin(t * math.pi * 2) * 0.4 + noise
    elif p < 0.50:
        t = (p - 0.38) / 0.12
        gas, brake = min(1.0, 0.7 + t * 0.3 + noise), 0.0
        speed = 180 + t * 80 + snoise
        steer = random.uniform(-0.02, 0.02)
    elif p < 0.60:
        t = (p - 0.50) / 0.10
        gas, brake = 0.0, min(1.0, t * 2.5 + noise)
        speed = 260 - t * 150 + snoise
        steer = math.sin(t * math.pi) * 0.35 + noise
    elif p < 0.75:
        t = (p - 0.60) / 0.15
        gas = max(0.2, 0.4 + t * 0.6 + noise)
        brake = max(0.0, 0.1 - t * 0.1)
        speed = 110 + t * 130 + snoise
        steer = math.sin(t * math.pi * 1.5) * 0.3 + noise
    elif p < 0.88:
        t = (p - 0.75) / 0.13
        if t < 0.4:
            s = t / 0.4
            gas, brake = max(0.0, 0.1 + noise), max(0.0, 0.7 - s * 0.5 + noise)
            speed = 240 - s * 100 + snoise
        else:
            s = (t - 0.4) / 0.6
            gas, brake = max(0.1, 0.2 + s * 0.7 + noise), 0.0
            speed = 140 + s * 70 + snoise
        steer = math.sin(t * math.pi * 2) * 0.45 + noise
    else:
        t = (p - 0.88) / 0.12
        gas, brake = min(1.0, 0.6 + t * 0.4 + noise), max(0.0, 0.05 - t * 0.05)
        speed = 160 + t * 100 + snoise
        steer = random.uniform(-0.03, 0.03)

    return (
        max(0.0, min(1.0, gas)),
        max(0.0, min(1.0, brake)),
        max(20.0, min(310.0, speed)),
        steer,
    )


def _generate_track_spline():
    """Gera um traçado suave de pista (estilo Interlagos) usando Catmull-Rom Spline"""
    keypoints = [
        (0.0, 0.0),            # Reta dos boxes
        (-100.0, 600.0),       # S do Senna
        (-250.0, 500.0),       # Curva 2
        (-150.0, 300.0),       # Curva do Sol
        (200.0, -400.0),       # Descida do Lago
        (400.0, -150.0),       # Ferradura
        (500.0, 50.0),         # Laranjinha
        (350.0, 350.0),        # Pinheirinho
        (150.0, 200.0),        # Bico de Pato
        (100.0, -50.0),        # Mergulho
        (20.0, -300.0),        # Junção
        (-40.0, -100.0),       # Subida dos boxes
    ]
    
    pts = [keypoints[-1]] + keypoints + [keypoints[0], keypoints[1]]
    track_path = []
    for i in range(1, len(pts) - 2):
        P0, P1, P2, P3 = pts[i-1], pts[i], pts[i+1], pts[i+2]
        num_points = 50 
        for t_step in range(num_points):
            t = t_step / num_points
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * ((2 * P1[0]) + (-P0[0] + P2[0]) * t + (2*P0[0] - 5*P1[0] + 4*P2[0] - P3[0]) * t2 + (-P0[0] + 3*P1[0] - 3*P2[0] + P3[0]) * t3)
            z = 0.5 * ((2 * P1[1]) + (-P0[1] + P2[1]) * t + (2*P0[1] - 5*P1[1] + 4*P2[1] - P3[1]) * t2 + (-P0[1] + 3*P1[1] - 3*P2[1] + P3[1]) * t3)
            track_path.append((x, z))
    return track_path

_MOCK_TRACK_PATH = _generate_track_spline()


def _ms_to_str(ms: int) -> str:
    if ms <= 0:
        return "--:--.---"
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{minutes}:{seconds:02d}.{millis:03d}"


class MockTelemetryProvider(TelemetryProvider):
    """
    Simulador interno de telemetria — não abre socket algum, roda 100% em
    memória. A cada get_state() avança a simulação com base no tempo real
    decorrido (time.perf_counter), então funciona corretamente independente
    da frequência com que a Engine chama get_state().
    """

    def __init__(self):
        self._connected = False
        self._last_tick = None
        self._start_time = time.perf_counter()

        self._lap_ms = 0.0
        self._last_lap_ms = 0.0
        self._best_lap_ms = 0
        self._lap_number = 1

        self._s1_locked_ms = 0
        self._s2_locked_ms = 0
        self._sector_index = 0  # 0=S1, 1=S2, 2=S3

        self._fuel = FUEL_INITIAL

        self._lap_target = BASE_LAP_MS + random.randint(-300, 400)
        self._s1_end = BASE_S1_MS + random.randint(-200, 200)
        self._s2_end = BASE_S1_MS + BASE_S2_MS + random.randint(-200, 200)
        self._ref_best_ms = BASE_LAP_MS

        self._current_sector_times = [0, 0, 0]

    # -----------------------------------------------------------------------
    # Interface TelemetryProvider
    # -----------------------------------------------------------------------

    def connect(self) -> bool:
        self._connected = True
        if self._last_tick is None:
            self._last_tick = time.perf_counter()
        return True

    def close(self):
        self._connected = False

    def get_state(self) -> TelemetryState:
        state = TelemetryState(is_connected=True)

        now = time.perf_counter()
        dt_ms = (max(0.0, now - self._last_tick) * 1000.0) if self._last_tick else 0.0
        self._last_tick = now

        self._lap_ms += dt_ms
        progress = min(1.0, self._lap_ms / self._lap_target)
        distance = progress * TRACK_LENGTH

        # --- Fechamento de setores ---
        if self._sector_index == 0 and self._lap_ms >= self._s1_end:
            self._s1_locked_ms = self._lap_ms
            self._current_sector_times[0] = self._s1_locked_ms
            self._sector_index = 1
        elif self._sector_index == 1 and self._lap_ms >= self._s2_end:
            self._s2_locked_ms = self._lap_ms - self._s1_locked_ms
            self._current_sector_times[1] = self._s2_locked_ms
            self._sector_index = 2

        # --- Fim de volta ---
        if self._lap_ms >= self._lap_target:
            self._last_lap_ms = self._lap_ms
            s3_ms = self._last_lap_ms - self._s1_locked_ms - self._s2_locked_ms
            self._current_sector_times[2] = s3_ms

            if self._best_lap_ms == 0 or self._last_lap_ms < self._best_lap_ms:
                self._best_lap_ms = self._last_lap_ms
                self._ref_best_ms = self._best_lap_ms

            self._fuel = max(0.0, self._fuel - FUEL_PER_LAP + random.uniform(-0.05, 0.05))

            self._lap_number += 1
            self._lap_ms = 0
            self._sector_index = 0
            self._s1_locked_ms = 0
            self._s2_locked_ms = 0
            self._current_sector_times = [0, 0, 0]
            self._lap_target = BASE_LAP_MS + random.randint(-300, 400)
            self._s1_end = BASE_S1_MS + random.randint(-150, 150)
            self._s2_end = BASE_S1_MS + BASE_S2_MS + random.randint(-150, 150)
            progress = 0.0
            distance = 0.0

        # --- Física / entradas do piloto ---
        gas, brake, speed_kmh, steer = _track_profile(progress)
        gear, rpm = _gear_for_speed(speed_kmh, braking=(brake > 0.2))

        state.car_name = CAR_NAME
        state.track_name = TRACK_NAME
        state.gas = gas
        state.brake = brake
        state.clutch = 0.0
        state.steer_angle = steer * 90.0
        state.speed_kmh = speed_kmh
        state.rpm = int(rpm)
        state.max_rpm = 8500.0
        state.gear = gear
        state.turbo_boost = max(0.0, gas * 1.4 + random.uniform(-0.05, 0.05))

        state.current_time = _ms_to_str(int(self._lap_ms))
        state.last_time = _ms_to_str(int(self._last_lap_ms))
        state.best_time = _ms_to_str(int(self._best_lap_ms))
        state.sector_index = self._sector_index
        state.lap_number = self._lap_number

        state.track_position = progress
        state.distance_traveled = distance
        state.track_length = TRACK_LENGTH
        
        path_idx = int(progress * len(_MOCK_TRACK_PATH)) % len(_MOCK_TRACK_PATH)
        state.car_x = _MOCK_TRACK_PATH[path_idx][0]
        state.car_y = 0.0
        state.car_z = _MOCK_TRACK_PATH[path_idx][1]

        # Delta em tempo real vs melhor volta (mesma lógica do SessionManager,
        # mantida aqui apenas para preencher o campo cru vindo do "jogo")
        ref_time_s = (progress * self._ref_best_ms) / 1000.0
        curr_s = self._lap_ms / 1000.0
        state.delta_time = round(curr_s - ref_time_s, 3) if self._ref_best_ms > 0 else 0.0

        # Eletrônica — no AC estes valores são a intensidade da intervenção
        state.has_abs = True
        state.has_tc = True
        state.has_drs = True
        state.abs_intervention = max(0.0, (brake - 0.85) / 0.15) if brake > 0.85 else 0.0
        state.tc_intervention = max(0.0, (gas - 0.90) / 0.10) if (gas > 0.90 and gear <= 3) else 0.0
        state.abs_active = state.abs_intervention > 0.02
        state.tc_active = state.tc_intervention > 0.02
        state.pit_limiter = False
        state.brake_bias = 0.615
        state.ffb_level = min(1.0, abs(steer) * 0.8 + brake * 0.3)

        # Força G aproximada a partir do volante e dos pedais
        state.g_lat = steer * 2.2
        state.g_lon = (gas * 1.1) - (brake * 2.3)
        state.g_vert = 1.0 + math.sin(now * 5) * 0.1

        state.ambient_temp = 26.0
        state.track_temp = 34.0
        state.rain_density = 0.0
        state.track_wetness = 0.0
        state.surface_grip = 0.982
        state.wind_speed = 3.5
        state.wind_direction = 205.0
        state.car_damage = 0.0

        # Sessão
        state.session_type = "Practice"
        state.session_time_left = max(0.0, 1800.0 - (now - self._start_time))
        state.race_position = 3
        state.total_laps = 0
        state.completed_laps = max(0, self._lap_number - 1)
        state.in_pit = False
        state.in_pit_lane = False
        state.flag = ""

        state.fuel = self._fuel
        state.fuel_capacity = FUEL_CAPACITY
        state.tyre_compound = "medium"

        base_temp = 78.0 + brake * 16.0 + gas * 5.0
        state.tyre_temp = [base_temp + i * 0.5 + random.uniform(-0.3, 0.3) for i in range(4)]
        state.tyre_pressure = [28.0 + brake * 0.9 + random.uniform(-0.1, 0.1) for _ in range(4)]
        state.tyre_wear = [max(0.0, 100.0 - self._lap_number * 0.6) for _ in range(4)]
        state.tyre_slip = [abs(brake - gas) * 0.1 for _ in range(4)]
        state.suspension_travel = [5.0 + math.sin(now * 3 + i) * 0.3 for i in range(4)]

        # Temperaturas Interna / Meio / Externa (o pneu de fora da curva
        # esquenta mais na parte interna da banda)
        lean = abs(steer) * 6.0
        state.tyre_temp_inner  = [t + lean for t in state.tyre_temp]
        state.tyre_temp_middle = [t for t in state.tyre_temp]
        state.tyre_temp_outer  = [t - lean * 0.6 for t in state.tyre_temp]

        # Freios: dianteiros trabalham mais que traseiros
        brake_base = 180.0 + brake * 520.0
        state.brake_temp = [brake_base, brake_base * 0.97,
                            brake_base * 0.72, brake_base * 0.70]

        return state
