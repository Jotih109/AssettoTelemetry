"""
providers/assettocorsa.py
=========================
Provider de telemetria para o Assetto Corsa 1 (AC / Content Manager).

Diferente do Automobilista 2 (que transmite pacotes UDP), o Assetto Corsa 1
expõe a telemetria em **memória compartilhada** do Windows (Shared Memory).
Não é preciso configurar porta, IP nem nada no jogo — basta o AC estar rodando.

Três blocos são publicados pelo jogo:
    Local\\acpmf_physics   — atualizado a ~333 Hz (dados de física)
    Local\\acpmf_graphics  — atualizado a ~60 Hz  (tempos, setores, sessão)
    Local\\acpmf_static    — escrito uma vez por sessão (carro, pista, limites)

Referência oficial do layout:
    Assetto Corsa SDK — "Shared Memory Reference"
    (SPageFilePhysics / SPageFileGraphic / SPageFileStatic)

IMPORTANTE: os structs abaixo seguem o layout do **AC1**. O ACC (Competizione)
usa os mesmos nomes de arquivo mas com campos extras/diferentes — este provider
foi escrito para o AC1.
"""

import ctypes
import ctypes.wintypes as wintypes
import time
from typing import Optional

from core.models import TelemetryState
from providers.base import TelemetryProvider


# ---------------------------------------------------------------------------
# Constantes de configuração
# ---------------------------------------------------------------------------

# Nomes dos blocos de memória compartilhada do AC
MMAP_PHYSICS  = "acpmf_physics"
MMAP_GRAPHICS = "acpmf_graphics"
MMAP_STATIC   = "acpmf_static"

# Segundos sem o packetId mudar até considerar o jogo desconectado/pausado
STALE_TIMEOUT = 2.0
# Segundos desconectado até tentar remapear a memória (AC reiniciado)
REMAP_TIMEOUT = 5.0

# O AC1 publica `steerAngle` normalizado (-1.0 .. 1.0), não em graus.
# Multiplicamos por metade do steer lock típico para exibir em graus.
# Ajuste se você roda um volante com lock diferente (ex.: F1 ~ 180, GT ~ 240).
STEER_LOCK_DEG = 240.0

# AC_STATUS
AC_OFF    = 0
AC_REPLAY = 1
AC_LIVE   = 2
AC_PAUSE  = 3

# AC_SESSION_TYPE
SESSION_NAMES = {
    -1: "Desconhecida", 0: "Practice", 1: "Qualify", 2: "Race",
    3: "Hotlap", 4: "Time Attack", 5: "Drift", 6: "Drag",
}

# AC_FLAG_TYPE
FLAG_NAMES = {
    0: "", 1: "AZUL", 2: "AMARELA", 3: "PRETA",
    4: "BRANCA", 5: "XADREZ", 6: "PENALIDADE",
}


# ---------------------------------------------------------------------------
# Structs da memória compartilhada (layout AC1)
# ---------------------------------------------------------------------------

class SPageFilePhysics(ctypes.Structure):
    """Bloco acpmf_physics — física do carro, atualizado a cada tick."""
    _pack_ = 4
    _fields_ = [
        ("packetId",             ctypes.c_int32),
        ("gas",                  ctypes.c_float),       # 0..1
        ("brake",                ctypes.c_float),       # 0..1
        ("fuel",                 ctypes.c_float),       # litros
        ("gear",                 ctypes.c_int32),       # 0=Ré, 1=N, 2=1ª ...
        ("rpms",                 ctypes.c_int32),
        ("steerAngle",           ctypes.c_float),       # normalizado -1..1
        ("speedKmh",             ctypes.c_float),
        ("velocity",             ctypes.c_float * 3),
        ("accG",                 ctypes.c_float * 3),   # [lat, vert, lon] em G
        ("wheelSlip",            ctypes.c_float * 4),
        ("wheelLoad",            ctypes.c_float * 4),   # não usado no AC1
        ("wheelsPressure",       ctypes.c_float * 4),   # PSI
        ("wheelAngularSpeed",    ctypes.c_float * 4),   # rad/s
        ("tyreWear",             ctypes.c_float * 4),   # 100 = novo
        ("tyreDirtyLevel",       ctypes.c_float * 4),   # 0..5
        ("tyreCoreTemperature",  ctypes.c_float * 4),   # °C
        ("camberRAD",            ctypes.c_float * 4),
        ("suspensionTravel",     ctypes.c_float * 4),   # metros
        ("drs",                  ctypes.c_float),
        ("tc",                   ctypes.c_float),       # >0 = TC atuando
        ("heading",              ctypes.c_float),
        ("pitch",                ctypes.c_float),
        ("roll",                 ctypes.c_float),
        ("cgHeight",             ctypes.c_float),
        ("carDamage",            ctypes.c_float * 5),   # F, R, L, Ri, total
        ("numberOfTyresOut",     ctypes.c_int32),
        ("pitLimiterOn",         ctypes.c_int32),
        ("abs",                  ctypes.c_float),       # >0 = ABS atuando
        ("kersCharge",           ctypes.c_float),
        ("kersInput",            ctypes.c_float),
        ("autoShifterOn",        ctypes.c_int32),
        ("rideHeight",           ctypes.c_float * 2),
        ("turboBoost",           ctypes.c_float),       # bar
        ("ballast",              ctypes.c_float),
        ("airDensity",           ctypes.c_float),
        ("airTemp",              ctypes.c_float),       # °C
        ("roadTemp",             ctypes.c_float),       # °C
        ("localAngularVel",      ctypes.c_float * 3),
        ("finalFF",              ctypes.c_float),       # force feedback 0..1
        ("performanceMeter",     ctypes.c_float),       # delta vs melhor volta (s)
        ("engineBrake",          ctypes.c_int32),
        ("ersRecoveryLevel",     ctypes.c_int32),
        ("ersPowerLevel",        ctypes.c_int32),
        ("ersHeatCharging",      ctypes.c_int32),
        ("ersIsCharging",        ctypes.c_int32),
        ("kersCurrentKJ",        ctypes.c_float),
        ("drsAvailable",         ctypes.c_int32),
        ("drsEnabled",           ctypes.c_int32),
        ("brakeTemp",            ctypes.c_float * 4),   # °C
        ("clutch",               ctypes.c_float),       # 1 = embreagem solta
        ("tyreTempI",            ctypes.c_float * 4),   # interno
        ("tyreTempM",            ctypes.c_float * 4),   # meio
        ("tyreTempO",            ctypes.c_float * 4),   # externo
        ("isAIControlled",       ctypes.c_int32),
        ("tyreContactPoint",     (ctypes.c_float * 3) * 4),
        ("tyreContactNormal",    (ctypes.c_float * 3) * 4),
        ("tyreContactHeading",   (ctypes.c_float * 3) * 4),
        ("brakeBias",            ctypes.c_float),       # 0..1 (fração dianteira)
        ("localVelocity",        ctypes.c_float * 3),
    ]


class SPageFileGraphic(ctypes.Structure):
    """Bloco acpmf_graphics — tempos, setores e estado da sessão."""
    _pack_ = 4
    _fields_ = [
        ("packetId",              ctypes.c_int32),
        ("status",                ctypes.c_int32),      # AC_STATUS
        ("session",               ctypes.c_int32),      # AC_SESSION_TYPE
        ("currentTime",           ctypes.c_wchar * 15),
        ("lastTime",              ctypes.c_wchar * 15),
        ("bestTime",              ctypes.c_wchar * 15),
        ("split",                 ctypes.c_wchar * 15),
        ("completedLaps",         ctypes.c_int32),
        ("position",              ctypes.c_int32),
        ("iCurrentTime",          ctypes.c_int32),      # ms
        ("iLastTime",             ctypes.c_int32),      # ms
        ("iBestTime",             ctypes.c_int32),      # ms
        ("sessionTimeLeft",       ctypes.c_float),      # ms
        ("distanceTraveled",      ctypes.c_float),      # metros na sessão
        ("isInPit",               ctypes.c_int32),
        ("currentSectorIndex",    ctypes.c_int32),      # 0, 1, 2
        ("lastSectorTime",        ctypes.c_int32),      # ms
        ("numberOfLaps",          ctypes.c_int32),
        ("tyreCompound",          ctypes.c_wchar * 33),
        ("replayTimeMultiplier",  ctypes.c_float),
        ("normalizedCarPosition", ctypes.c_float),      # 0..1 na volta
        ("carCoordinates",        ctypes.c_float * 3),  # x, y, z do jogador
        ("penaltyTime",           ctypes.c_float),
        ("flag",                  ctypes.c_int32),      # AC_FLAG_TYPE
        ("idealLineOn",           ctypes.c_int32),
        ("isInPitLane",           ctypes.c_int32),      # >= AC 1.5
        ("surfaceGrip",           ctypes.c_float),      # 0..1
        ("mandatoryPitDone",      ctypes.c_int32),      # >= AC 1.13
        ("windSpeed",             ctypes.c_float),      # >= AC 1.14
        ("windDirection",         ctypes.c_float),
    ]


class SPageFileStatic(ctypes.Structure):
    """Bloco acpmf_static — escrito uma vez ao entrar na sessão."""
    _pack_ = 4
    _fields_ = [
        ("smVersion",                ctypes.c_wchar * 15),
        ("acVersion",                ctypes.c_wchar * 15),
        ("numberOfSessions",         ctypes.c_int32),
        ("numCars",                  ctypes.c_int32),
        ("carModel",                 ctypes.c_wchar * 33),
        ("track",                    ctypes.c_wchar * 33),
        ("playerName",               ctypes.c_wchar * 33),
        ("playerSurname",            ctypes.c_wchar * 33),
        ("playerNick",               ctypes.c_wchar * 33),
        ("sectorCount",              ctypes.c_int32),
        ("maxTorque",                ctypes.c_float),
        ("maxPower",                 ctypes.c_float),
        ("maxRpm",                   ctypes.c_int32),
        ("maxFuel",                  ctypes.c_float),
        ("suspensionMaxTravel",      ctypes.c_float * 4),
        ("tyreRadius",               ctypes.c_float * 4),
        ("maxTurboBoost",            ctypes.c_float),
        ("deprecated_1",             ctypes.c_float),
        ("deprecated_2",             ctypes.c_float),
        ("penaltiesEnabled",         ctypes.c_int32),
        ("aidFuelRate",              ctypes.c_float),
        ("aidTireRate",              ctypes.c_float),
        ("aidMechanicalDamage",      ctypes.c_float),
        # ATENÇÃO: no SDK do AC estes campos são `bool` (1 byte cada).
        # Declará-los como int32 desalinharia TODOS os campos seguintes.
        ("aidAllowTyreBlankets",     ctypes.c_bool),
        ("aidStability",             ctypes.c_float),
        ("aidAutoClutch",            ctypes.c_int32),
        ("aidAutoBlip",              ctypes.c_int32),
        ("hasDRS",                   ctypes.c_bool),
        ("hasERS",                   ctypes.c_bool),
        ("hasKERS",                  ctypes.c_bool),
        ("kersMaxJ",                 ctypes.c_float),
        ("engineBrakeSettingsCount", ctypes.c_int32),
        ("ersPowerControllerCount",  ctypes.c_int32),
        ("trackSPlineLength",        ctypes.c_float),   # metros
        ("trackConfiguration",       ctypes.c_wchar * 33),
        ("ersMaxJ",                  ctypes.c_float),
        ("isTimedRace",              ctypes.c_bool),
        ("hasExtraLap",              ctypes.c_bool),
        ("carSkin",                  ctypes.c_wchar * 33),
        ("reversedGridPositions",    ctypes.c_int32),
        ("PitWindowStart",           ctypes.c_int32),
        ("PitWindowEnd",             ctypes.c_int32),
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ms_to_laptime_str(ms: int) -> str:
    """Converte milissegundos para 'm:ss.mmm'. Retorna placeholder se inválido."""
    # O AC usa 0 ou valores absurdos (ex.: 999999999) para "sem tempo"
    if ms <= 0 or ms >= 3_600_000:
        return "--:--.---"
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    millis  = ms % 1000
    return f"{minutes}:{seconds:02d}.{millis:03d}"


# Siglas que o .title() estragaria ('Gt3' -> 'GT3')
_ACRONYMS = {
    "gt", "gt1", "gt2", "gt3", "gt4", "gte", "gtr", "gto", "gt40",
    "rsr", "rs", "gtb", "gts", "sf", "bmw", "amg", "srt", "stw",
    "s1", "s2", "s3", "f1", "f2", "f3", "kr", "hp", "abt",
}


def _clean_ac_name(raw: str) -> str:
    """
    Normaliza nomes internos do AC para exibição.
    Ex.: 'ks_porsche_911_gt3_r' -> 'Porsche 911 GT3 R'
         'spa'                  -> 'Spa'
    Nomes que já vêm capitalizados (mods, layouts) são preservados.
    """
    if not raw:
        return ""
    txt = raw.replace("\x00", "").strip()
    if not txt:
        return ""
    # Só reformata o que vem em snake_case minúsculo (padrão dos conteúdos Kunos)
    if txt.islower():
        # 'ks_' é só o marcador de conteúdo oficial da Kunos — não é parte do nome
        if txt.startswith("ks_"):
            txt = txt[3:]
        words = txt.replace("_", " ").split()
        txt = " ".join(w.upper() if w in _ACRONYMS else w.capitalize() for w in words)
    return txt


# ---------------------------------------------------------------------------
# Acesso à memória compartilhada via API do Windows
# ---------------------------------------------------------------------------
# Usamos OpenFileMapping (e NÃO o módulo mmap do Python) de propósito:
# o mmap com fileno=-1 CRIA o bloco caso ele não exista, o que deixaria um
# bloco fantasma de tamanho fixo registrado no sistema — e isso pode atrapalhar
# o próprio Assetto Corsa quando ele tentar criar o dele depois.
# OpenFileMapping apenas ABRE um bloco existente: se o jogo não está rodando,
# a chamada falha e sabemos que não há telemetria.

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

FILE_MAP_READ = 0x0004

_kernel32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.OpenFileMappingW.restype = wintypes.HANDLE

_kernel32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                    wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
_kernel32.MapViewOfFile.restype = wintypes.LPVOID

_kernel32.UnmapViewOfFile.argtypes = [wintypes.LPCVOID]
_kernel32.UnmapViewOfFile.restype = wintypes.BOOL

_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


class SharedBlock:
    """
    Um bloco de memória compartilhada aberto em modo leitura.

    A view é mapeada inteira (tamanho 0 = "todo o bloco"), então não é preciso
    adivinhar o tamanho que a versão do jogo publicou. A leitura é feita com
    memmove para uma cópia local do struct — evita ler campos "rasgados"
    no meio de uma escrita do jogo.
    """

    def __init__(self, handle, address):
        self._handle = handle
        self._address = address

    @classmethod
    def open(cls, tag: str) -> Optional["SharedBlock"]:
        """Abre o bloco pelo nome. Retorna None se ele não existir (jogo fechado)."""
        for name in (f"Local\\{tag}", tag):
            handle = _kernel32.OpenFileMappingW(FILE_MAP_READ, False, name)
            if not handle:
                continue
            address = _kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, 0)
            if not address:
                _kernel32.CloseHandle(handle)
                continue
            return cls(handle, address)
        return None

    def read(self, struct_cls):
        """Copia o bloco para uma instância nova de struct_cls."""
        dest = struct_cls()
        ctypes.memmove(ctypes.byref(dest), self._address, ctypes.sizeof(struct_cls))
        return dest

    def close(self):
        if self._address:
            _kernel32.UnmapViewOfFile(self._address)
            self._address = None
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None


# ---------------------------------------------------------------------------
# Provider Principal
# ---------------------------------------------------------------------------

class AssettoCorsaTelemetryProvider(TelemetryProvider):
    """
    Provider de telemetria para o Assetto Corsa 1 via memória compartilhada.

    Não requer configuração no jogo — basta o AC estar aberto. O provider
    detecta automaticamente quando o jogo é fechado/reiniciado e remapeia
    a memória sozinho.

    Uso:
        provider = AssettoCorsaTelemetryProvider()
        engine = TelemetryEngine(provider=provider, hz=60)
    """

    def __init__(self):
        self._mm_physics: Optional[SharedBlock] = None
        self._mm_graphics: Optional[SharedBlock] = None
        self._mm_static: Optional[SharedBlock] = None

        # Detecção de "jogo parado": o packetId para de mudar
        self._last_packet_id = -1
        self._last_packet_change = 0.0
        self._is_connected = False

        # Cache do bloco estático (só muda ao trocar de sessão)
        self._static: Optional[SPageFileStatic] = None
        self._static_signature = ""

    # -----------------------------------------------------------------------
    # Interface TelemetryProvider
    # -----------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Abre os três blocos de memória compartilhada do AC.
        Retorna False enquanto o jogo não estiver rodando — a Engine
        simplesmente tenta de novo no próximo ciclo.
        """
        if self._mm_physics is not None and self._mm_graphics is not None:
            return True

        self._mm_physics  = SharedBlock.open(MMAP_PHYSICS)
        self._mm_graphics = SharedBlock.open(MMAP_GRAPHICS)
        self._mm_static   = SharedBlock.open(MMAP_STATIC)

        if self._mm_physics is None or self._mm_graphics is None:
            # Silencioso e barato: só significa "AC ainda não está aberto"
            self._release()
            return False

        print("[AC] Memória compartilhada do Assetto Corsa conectada.")
        return True

    def get_state(self) -> TelemetryState:
        """Lê os blocos de memória e devolve um TelemetryState preenchido."""
        state = TelemetryState(is_connected=False)

        if self._mm_physics is None or self._mm_graphics is None:
            return state

        try:
            physics  = self._mm_physics.read(SPageFilePhysics)
            graphics = self._mm_graphics.read(SPageFileGraphic)
        except Exception:
            # Memória sumiu (jogo fechado) — força remapeamento no próximo ciclo
            self._release()
            return state

        # --- Detecção de conexão -------------------------------------------
        now = time.monotonic()
        if physics.packetId != self._last_packet_id:
            self._last_packet_id = physics.packetId
            self._last_packet_change = now

        fresh = (now - self._last_packet_change) < STALE_TIMEOUT
        in_session = graphics.status in (AC_LIVE, AC_PAUSE, AC_REPLAY)
        self._is_connected = bool(fresh and in_session and physics.packetId > 0)

        if not self._is_connected:
            # Jogo fechado há um tempo: solta a memória para pegar a nova instância
            if (now - self._last_packet_change) > REMAP_TIMEOUT:
                self._release()
            return state

        state.is_connected = True

        # --- Bloco estático (carro, pista, limites) -------------------------
        static = self._read_static()
        self._fill_static(state, static)

        # --- Física ---------------------------------------------------------
        self._fill_physics(state, physics, static)

        # --- Gráficos / sessão ----------------------------------------------
        self._fill_graphics(state, graphics)

        return state

    def close(self):
        """Libera os mapeamentos de memória."""
        self._release()
        print("[AC] Memória compartilhada liberada.")

    # -----------------------------------------------------------------------
    # Leitura de memória
    # -----------------------------------------------------------------------

    def _read_static(self) -> Optional[SPageFileStatic]:
        """Lê o bloco estático, reaproveitando o cache enquanto a sessão não troca."""
        if self._mm_static is None:
            return self._static

        try:
            static = self._mm_static.read(SPageFileStatic)
        except Exception:
            return self._static

        signature = f"{static.carModel}|{static.track}|{static.trackConfiguration}"
        if signature != self._static_signature:
            self._static_signature = signature
            self._static = static
            print(f"[AC] Sessão: {static.track} / {static.carModel} "
                  f"(SM v{static.smVersion}, AC v{static.acVersion})")
        return self._static

    def _release(self):
        """Fecha os mapeamentos e zera o estado de conexão."""
        for attr in ("_mm_physics", "_mm_graphics", "_mm_static"):
            mm = getattr(self, attr, None)
            if mm is not None:
                try:
                    mm.close()
                except Exception:
                    pass
            setattr(self, attr, None)

        self._is_connected = False
        self._last_packet_id = -1
        self._last_packet_change = 0.0

    # -----------------------------------------------------------------------
    # Conversão para TelemetryState
    # -----------------------------------------------------------------------

    def _fill_static(self, state: TelemetryState, static: Optional[SPageFileStatic]):
        """Nome do carro/pista, RPM máximo, capacidade de tanque, comprimento da pista."""
        if static is None:
            return

        car = _clean_ac_name(static.carModel)
        state.car_name = car or "Unknown Car"

        track = _clean_ac_name(static.track)
        layout = _clean_ac_name(static.trackConfiguration)
        if track and layout and layout.lower() != track.lower():
            state.track_name = f"{track} — {layout}"
        else:
            state.track_name = track or "Unknown Track"

        if static.maxRpm > 0:
            state.max_rpm = float(static.maxRpm)
        if static.maxFuel > 0:
            state.fuel_capacity = float(static.maxFuel)
        if static.trackSPlineLength > 100:
            state.track_length = float(static.trackSPlineLength)
        if static.sectorCount > 0:
            state.sector_count = int(static.sectorCount)

        state.player_name = f"{static.playerName} {static.playerSurname}".strip()
        state.has_drs  = bool(static.hasDRS)
        state.has_ers  = bool(static.hasERS)
        state.has_kers = bool(static.hasKERS)
        state.has_abs  = True
        state.has_tc   = True

    def _fill_physics(self, state: TelemetryState, p: SPageFilePhysics,
                      static: Optional[SPageFileStatic]):
        """Pedais, motor, pneus, freios, G-force, danos, clima."""
        # --- Entradas do piloto ---
        state.gas   = max(0.0, min(1.0, p.gas))
        state.brake = max(0.0, min(1.0, p.brake))
        # No AC, clutch=1.0 significa embreagem SOLTA (engatada).
        # Invertemos para a convenção "0 = solto, 1 = pisado" usada na UI.
        state.clutch = max(0.0, min(1.0, 1.0 - p.clutch))
        # steerAngle é normalizado (-1..1) — convertemos para graus
        state.steer_angle = p.steerAngle * STEER_LOCK_DEG
        state.steer_norm = max(-1.0, min(1.0, p.steerAngle))

        # --- Motor / transmissão ---
        state.speed_kmh = max(0.0, p.speedKmh)
        state.rpm = int(max(0, p.rpms))
        if state.max_rpm <= 0:
            state.max_rpm = float(max(state.rpm + 1000, 8000))
        # O AC já usa 0=Ré, 1=Neutro, 2=1ª — mesma convenção do TelemetryState
        state.gear = int(p.gear)
        state.turbo_boost = max(0.0, p.turboBoost)
        if static is not None and static.maxTurboBoost > 0:
            state.turbo_boost_max = float(static.maxTurboBoost)

        # --- Combustível ---
        state.fuel = max(0.0, p.fuel)

        # --- Eletrônica ---
        # No AC, `abs` e `tc` são a INTENSIDADE da intervenção (0 = não atuando)
        state.abs_active = p.abs > 0.02
        state.tc_active  = p.tc > 0.02
        state.abs_intervention = max(0.0, min(1.0, p.abs))
        state.tc_intervention  = max(0.0, min(1.0, p.tc))
        state.pit_limiter = bool(p.pitLimiterOn)
        state.drs_available = bool(p.drsAvailable)
        state.drs_active    = bool(p.drsEnabled) or p.drs > 0.5
        state.kers_charge   = max(0.0, min(1.0, p.kersCharge))
        state.ers_recovery_level = int(p.ersRecoveryLevel)
        state.engine_brake = int(p.engineBrake)
        state.brake_bias = max(0.0, min(1.0, p.brakeBias))
        state.auto_shifter = bool(p.autoShifterOn)

        # --- G-force: accG do AC é [lateral, vertical, longitudinal] ---
        state.g_lat  = p.accG[0]
        state.g_vert = p.accG[1]
        state.g_lon  = p.accG[2]

        # --- Força no volante (útil para detectar clipping de FFB) ---
        state.ffb_level = max(0.0, min(1.0, abs(p.finalFF)))

        # --- Pneus [FL, FR, RL, RR] ---
        state.tyre_temp     = [float(v) for v in p.tyreCoreTemperature]
        state.tyre_pressure = [float(v) for v in p.wheelsPressure]
        state.tyre_wear     = [max(0.0, min(100.0, float(v))) for v in p.tyreWear]
        state.tyre_slip     = [float(v) for v in p.wheelSlip]
        state.tyre_dirt     = [float(v) for v in p.tyreDirtyLevel]
        # Suspensão em metros -> milímetros (mesma unidade do provider do AMS2)
        state.suspension_travel = [float(v) * 1000.0 for v in p.suspensionTravel]

        # Temperaturas Interna / Meio / Externa — essenciais para acertar câmber
        state.tyre_temp_inner  = [float(v) for v in p.tyreTempI]
        state.tyre_temp_middle = [float(v) for v in p.tyreTempM]
        state.tyre_temp_outer  = [float(v) for v in p.tyreTempO]

        # --- Freios ---
        state.brake_temp = [float(v) for v in p.brakeTemp]

        # --- Clima (AC1 não tem clima dinâmico: só temperaturas) ---
        state.ambient_temp = float(p.airTemp)
        state.track_temp   = float(p.roadTemp)

        # --- Danos: carDamage[0..3] = frente/trás/esq/dir, [4] = total ---
        damage = [max(0.0, float(v)) for v in p.carDamage]
        state.car_damage_parts = damage[:4]
        state.car_damage = min(100.0, max(damage))

        state.tyres_out = int(p.numberOfTyresOut)

    def _fill_graphics(self, state: TelemetryState, g: SPageFileGraphic):
        """Tempos de volta, setores, posição na pista, sessão, grip e vento."""
        # --- Tempos (usamos os inteiros em ms, mais confiáveis que as strings) ---
        state.current_time = _ms_to_laptime_str(g.iCurrentTime)
        state.last_time    = _ms_to_laptime_str(g.iLastTime)
        state.best_time    = _ms_to_laptime_str(g.iBestTime)

        # --- Setores ---
        state.sector_index = max(0, min(2, int(g.currentSectorIndex)))
        state.last_sector_time = max(0, int(g.lastSectorTime))

        # --- Volta e posição na pista ---
        state.lap_number = int(g.completedLaps) + 1
        state.completed_laps = int(g.completedLaps)
        state.race_position = int(g.position)

        norm = g.normalizedCarPosition
        if 0.0 <= norm <= 1.0:
            state.track_position = float(norm)
            # distance_traveled = metros DENTRO da volta atual
            # (o session_manager usa isso para alinhar o delta com o fantasma)
            state.distance_traveled = float(norm) * state.track_length

        # --- Sessão ---
        state.session_type = SESSION_NAMES.get(int(g.session), "Desconhecida")
        state.session_time_left = max(0.0, g.sessionTimeLeft / 1000.0)
        state.total_laps = int(g.numberOfLaps)
        state.is_paused = (g.status == AC_PAUSE)
        state.is_replay = (g.status == AC_REPLAY)

        # --- Pit ---
        state.in_pit = bool(g.isInPit)
        state.in_pit_lane = bool(g.isInPitLane)
        state.mandatory_pit_done = bool(g.mandatoryPitDone)
        state.penalty_time = max(0.0, float(g.penaltyTime))

        # --- Bandeiras ---
        state.flag = FLAG_NAMES.get(int(g.flag), "")

        # --- Pneus e pista ---
        compound = (g.tyreCompound or "").replace("\x00", "").strip()
        state.tyre_compound = compound
        # surfaceGrip: 1.0 = pista totalmente "gomada"; abaixo disso, verde/suja
        grip = float(g.surfaceGrip)
        state.surface_grip = grip if 0.0 < grip <= 1.0 else 1.0
        # AC1 não tem chuva — deixamos os campos herdados em 0 e usamos
        # track_wetness apenas como "sujeira" derivada do grip.
        state.rain_density = 0.0
        state.track_wetness = 0.0

        # --- Vento ---
        state.wind_speed = max(0.0, float(g.windSpeed))
        state.wind_direction = float(g.windDirection)

        # --- Coordenadas do carro (para o mapa da pista) ---
        state.car_x = float(g.carCoordinates[0])
        state.car_y = float(g.carCoordinates[1])
        state.car_z = float(g.carCoordinates[2])


# Alias curto, no mesmo estilo do provider do AMS2
ACTelemetryProvider = AssettoCorsaTelemetryProvider
