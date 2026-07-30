import dataclasses
from typing import List

@dataclasses.dataclass
class TelemetryState:
    """
    Representação padronizada do estado atual do carro e da sessão.

    O AssettoCorsaTelemetryProvider lê a memória compartilhada do
    Assetto Corsa 1 (acpmf_physics / acpmf_graphics / acpmf_static) e mapeia
    todos os valores para este dataclass. A Interface Gráfica e a Engine de
    análises consomem apenas este formato, sendo completamente independentes
    de como o simulador entrega os dados.

    Providers disponíveis:
        AssettoCorsaTelemetryProvider — AC1 via memória compartilhada
        AMS2TelemetryProvider         — Automobilista 2 via UDP (pCars2)
        MockTelemetryProvider         — simulador interno para testes
    """

    # --- Status da Conexão ---
    is_connected: bool = False
    car_name: str = "Unknown Car"
    track_name: str = "Unknown Track"

    # --- Entradas do Piloto (0.0 a 1.0) ---
    gas: float = 0.0
    brake: float = 0.0
    clutch: float = 0.0
    steer_angle: float = 0.0  # Graus
    steer_norm: float = 0.0   # -1.0 a 1.0 (bruto do jogo)

    # --- Motor e Transmissão ---
    speed_kmh: float = 0.0
    rpm: int = 0
    max_rpm: float = 8500.0  # RPM Máximo (ex: 8500 no Porsche Cup)
    gear: int = 0            # 0=Ré, 1=Neutro, 2=1ª, 3=2ª ...
    turbo_boost: float = 0.0 # bar
    turbo_boost_max: float = 0.0  # bar (limite do carro, vem do bloco estático)

    # --- Tempos e Posição ---
    current_time: str = ""   # Tempo da volta atual (m:ss.mmm)
    last_time: str = ""      # Tempo da última volta
    best_time: str = ""      # Melhor volta pessoal na sessão
    sector_index: int = 0    # Setor atual (0, 1 ou 2)
    last_sector_time: int = 0  # Tempo do último setor (ms)
    s1_time: str = "--:--"    # Tempo formatado do Setor 1
    s2_time: str = "--:--"    # Tempo formatado do Setor 2
    s3_time: str = "--:--"    # Tempo formatado do Setor 3

    # Personal Best sectors (real)
    pb_s1: str = "--:--"
    pb_s2: str = "--:--"
    pb_s3: str = "--:--"

    s1_delta: float = 0.0    # Delta S1 vs referência
    s2_delta: float = 0.0    # Delta S2 vs referência
    s3_delta: float = 0.0    # Delta S3 vs referência
    lap_number: int = 0      # Número da volta atual
    delta_time: float = 0.0  # Segundos vs referência (+pior / -melhor)
    track_position: float = 0.0  # Progresso na pista (0.0 a 1.0)
    distance_traveled: float = 0.0  # Metros percorridos
    track_length: float = 4309.0  # Comprimento da pista em metros

    sector_count: int = 3    # Número de setores da pista (do bloco estático)

    # --- Eletrônica e Sistemas ---
    has_abs: bool = True
    has_tc: bool = True
    abs_active: bool = False
    tc_active: bool = False
    pit_limiter: bool = False
    # No AC estes valores são a INTENSIDADE da intervenção (0.0 a 1.0),
    # não apenas ligado/desligado — ótimo para ver onde o TC está te travando.
    abs_intervention: float = 0.0
    tc_intervention: float = 0.0
    brake_bias: float = 0.0      # 0.0 a 1.0 (fração no eixo dianteiro)
    engine_brake: int = 0        # Ajuste de freio-motor (carros que suportam)
    auto_shifter: bool = False

    # --- DRS / ERS / KERS ---
    has_drs: bool = False
    has_ers: bool = False
    has_kers: bool = False
    drs_available: bool = False
    drs_active: bool = False
    kers_charge: float = 0.0     # 0.0 a 1.0
    ers_recovery_level: int = 0

    # --- Força G (accG do AC) ---
    g_lat: float = 0.0    # Lateral (+direita)
    g_lon: float = 0.0    # Longitudinal (+aceleração)
    g_vert: float = 0.0   # Vertical

    # --- Force Feedback (útil para detectar clipping) ---
    ffb_level: float = 0.0  # 0.0 a 1.0 — acima de ~0.95 há clipping

    # --- Condições da Pista ---
    ambient_temp: float = 25.0
    track_temp: float = 30.0
    rain_density: float = 0.0    # 0.0 (seco) a 1.0 (tempestade) — AC1 não tem chuva
    track_wetness: float = 0.0   # 0.0 (seco) a 1.0 (molhado) — AC1 não tem chuva
    surface_grip: float = 1.0    # 0.0 a 1.0 — pista "verde" x pista gomada
    wind_speed: float = 0.0      # m/s
    wind_direction: float = 0.0  # Graus

    # --- Sessão ---
    session_type: str = ""        # Practice / Qualify / Race / Hotlap ...
    session_time_left: float = 0.0  # Segundos restantes
    total_laps: int = 0           # Voltas totais da corrida (0 = por tempo)
    completed_laps: int = 0
    race_position: int = 0
    flag: str = ""                # AZUL / AMARELA / XADREZ ...
    penalty_time: float = 0.0     # Segundos de penalidade pendentes
    in_pit: bool = False          # Parado no box
    in_pit_lane: bool = False     # Dentro do pit lane
    mandatory_pit_done: bool = False
    is_paused: bool = False
    is_replay: bool = False
    player_name: str = ""

    # --- Danos ---
    car_damage: float = 0.0  # 0 a 100% (pior componente)
    # [frente, trás, esquerda, direita]
    car_damage_parts: List[float] = dataclasses.field(default_factory=lambda: [0.0] * 4)
    tyres_out: int = 0       # Rodas fora da pista (corta-caminho)

    # --- Coordenadas do carro (para o mapa da pista) ---
    car_x: float = 0.0
    car_y: float = 0.0
    car_z: float = 0.0

    # --- Combustível e Pneus Gerais ---
    fuel: float = 0.0               # Litros restantes
    fuel_capacity: float = 0.0      # Litros do tanque cheio
    fuel_avg_consumption: float = 0.0  # Litros por volta (calculado pela engine)
    fuel_laps_remaining: float = 0.0   # Voltas estimadas com o combustível atual
    tyre_compound: str = ""

    # --- Dinâmica 4 Rodas [FL, FR, RL, RR] ---
    tyre_temp: List[float] = dataclasses.field(default_factory=lambda: [80.0] * 4)
    tyre_pressure: List[float] = dataclasses.field(default_factory=lambda: [25.0] * 4)
    tyre_wear: List[float] = dataclasses.field(default_factory=lambda: [100.0] * 4)
    tyre_slip: List[float] = dataclasses.field(default_factory=lambda: [0.0] * 4)
    tyre_dirt: List[float] = dataclasses.field(default_factory=lambda: [0.0] * 4)
    suspension_travel: List[float] = dataclasses.field(default_factory=lambda: [0.0] * 4)
    brake_temp: List[float] = dataclasses.field(default_factory=lambda: [0.0] * 4)

    # Temperaturas Interna / Meio / Externa da banda de rodagem — o jeito
    # certo de acertar câmber e pressão no AC.
    tyre_temp_inner: List[float] = dataclasses.field(default_factory=lambda: [80.0] * 4)
    tyre_temp_middle: List[float] = dataclasses.field(default_factory=lambda: [80.0] * 4)
    tyre_temp_outer: List[float] = dataclasses.field(default_factory=lambda: [80.0] * 4)
