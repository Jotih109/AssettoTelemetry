"""
providers/automobilista2.py
===========================
Provider de telemetria para o Automobilista 2 (AMS2).

O AMS2 transmite dados via UDP no formato do Project CARS 2 (pCars2).
Para ativar no jogo:
  Options → Gameplay → UDP Telemetry
  → IP: 127.0.0.1
  → Port: 5606

Referência do protocolo:
  https://github.com/loiste-interactive/pcars2-telemetry-streaming
  (SMS_UDP_Definitions.hpp — versão 9)
"""

import socket
import struct
import threading
from core.models import TelemetryState
from providers.base import TelemetryProvider


# ---------------------------------------------------------------------------
# Constantes do protocolo pCars2 / AMS2
# ---------------------------------------------------------------------------

UDP_HOST = "0.0.0.0"       # Escuta em todas as interfaces
UDP_PORT = 5606            # Porta padrão do AMS2 / pCars2
UDP_TIMEOUT = 2.0          # Segundos sem pacote até considerar desconectado
BUFFER_SIZE = 2048         # Tamanho máximo de um pacote UDP pCars2

# PacketType IDs (campo 'mPacketType' no header)
PKT_CAR_PHYSICS      = 0   # Telemetria de física do carro (roda a cada tick)
PKT_RACE_DEFINITION  = 1   # Info da sessão (pista, carro, clima)
PKT_PARTICIPANTS     = 2   # Lista de participantes
PKT_TIMINGS          = 3   # Tempos de volta
PKT_GAME_STATE       = 4   # Estado do jogo (menus, corrida, pit)
PKT_WEATHER_STATE    = 5   # Clima
PKT_VEHICLE_NAMES    = 6   # Nomes dos veículos
PKT_TIME_STATS       = 7   # Estatísticas de tempo por participante
PKT_PARTICIPANT_VEH  = 8   # Dados de veículo por participante

# Gear mapping do AMS2 (0=R, 1=N, 2=1ª, ...)
GEAR_REVERSE  = 0
GEAR_NEUTRAL  = 1

# Flags de CarFlags (campo mCarFlags do PKT_CAR_PHYSICS)
CAR_HEADLIGHT  = (1 << 0)
CAR_ENGINE_ON  = (1 << 1)
CAR_PIT_LIMITER = (1 << 2)
CAR_ABS        = (1 << 3)
CAR_TC         = (1 << 5)


# ---------------------------------------------------------------------------
# Parsing de structs do protocolo pCars2
# ---------------------------------------------------------------------------

# Header comum a todos os pacotes (12 bytes para Project CARS 2)
# < = little-endian | I = uint32 | B = uint8
HEADER_FMT = "<IIBBBB"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

# Struct do pacote de física do carro (PKT_CAR_PHYSICS = 0)
# Campos selecionados — apenas o que é necessário para o dashboard
# Baseado em SMS_UDP_Definitions.hpp revisão 9
CAR_PHYSICS_FMT = (
    "<"
    "I"       # dummy to preserve indices
    "f"       # mViewedParticipantIndex (float, cast para int)
    # --- sBuildingBlocksData (float*3 = posição, float*3 = velocidade linear, etc.)
    "fff"     # mWorldPosition[3]
    "fff"     # mVelocity[3]
    "fff"     # mLocalVelocity[3]
    "fff"     # mAngularVelocity[3]
    "fff"     # mLocalAngularVelocity[3]
    "fff"     # mAngularAcceleration[3]
    # --- sCarPhysicsData
    "f"       # mSpeed (m/s)
    "f"       # mEngineSpeed (rpm rad/s — dividir por 2π para RPM)
    "f"       # mEngineTorque (Nm)
    # Flags, marchas, entradas
    "H"       # mCarFlags
    "b"       # mCurrentGear (assinado: -1=R, 0=N, 1..n=marcha)
    "b"       # mLastOpponentCollisionIndex
    "f"       # mLastOpponentCollisionMagnitude
    "f"       # mTurboBoostPressure (bar)
    "f"       # mFullThrottle
    "f"       # mThrottle
    "f"       # mBrake
    "f"       # mClutch
    "f"       # mSteering
    "f"       # mFuelLevel (0..1 fração)
    "f"       # mFuelCapacity (litros)
    "f"       # mSpeed_dup (duplicado)
    "H"       # mCarFlags_dup
    "b"       # mFuelPerLap
    "b"       # mLastOpponentCollisionIndex_dup
    "f"       # mLastOpponentCollisionMagnitude_dup
    "f"       # mBoostAmount
    "f"       # mOrientation_x
    "f"       # mOrientation_y
    "f"       # mOrientation_z
    "f"       # mLocalVelocity_dup_x
    "f"       # mLocalVelocity_dup_y
    "f"       # mLocalVelocity_dup_z
    # Pneus — 4 rodas: FL, FR, RL, RR
    "ffff"    # mSuspensionTravel[4] (m)
    "ffff"    # mSuspensionVelocity[4]
    "HHHH"    # mAirPressure[4] (PSI * 100 → dividir por 100)
    "ffff"    # mEngineSpeed_dup[4] (ignorar)
    "ffff"    # mWheelLocalPositionY[4]
    "ffff"    # mRideHeight[4]
    "ffff"    # mWheelVelocity[4] (m/s)
    "ffff"    # mWheelSpeed[4] (rad/s)
    "ffff"    # mTyreSlip[4]
    "ffff"    # mTyreGrip[4]
    "ffff"    # mTyreHeightAboveGround[4]
    "ffff"    # mTyreLateralStiffnessFactor[4]
    "ffff"    # mTyreWear[4] (0..1)
    "ffff"    # mBrakeDamage[4]
    "ffff"    # mSuspensionDamage[4]
    "hhhh"    # mTyreTreadTemp[4] (K * 10 → dividir por 10 e subtrair 273.15)
    "hhhh"    # mTyreLayerTemp[4]
    "hhhh"    # mTyreCarcassTemp[4]
    "hhhh"    # mTyreRimTemp[4]
    "hhhh"    # mTyreInternalAirTemp[4]
    "ffff"    # mWheelLocalPositionY_dup[4]
    "ffff"    # mRideHeight_dup[4]
    "fff"     # mLinearForce[3] (N)
    "I"       # mBrakeBias
)
CAR_PHYSICS_SIZE = struct.calcsize(CAR_PHYSICS_FMT)

# Struct do pacote de definição de corrida (PKT_RACE_DEFINITION = 1)
# Lido a partir de HEADER_SIZE (12 bytes) — sem dummy
RACE_DEFINITION_FMT = (
    "<"
    "I"       # mClassification
    "fff"     # mTrackCenter[3]
    "H"       # mPushToPassWaitTimeTotal
    "H"       # mPushToPassWaitTimeRemaining
    "B"       # mGameSessionState
    "B"       # mNumParticipants
    "B"       # mNumActiveParticipants
    "f"       # mEventTimeRemaining
    "f"       # mSplitTimeAhead
    "f"       # mSplitTimeBehind
    "f"       # mSplitTime
    "f"       # mEventTimeRemaining2
    "f"       # mLapTimeDifference (s)
    "f"       # mTimestampMS
    "128s"    # mTrackLocation (string, null-terminated)
    "64s"     # mTrackVariation (string, null-terminated)
    "64s"     # mTranslatedTrackVariation (string, null-terminated)
    "h"       # mLapsTimeInEvent (número de voltas — negativo = tempo)
    "b"       # mEnforcedPitStopLap
)
RACE_DEFINITION_SIZE = struct.calcsize(RACE_DEFINITION_FMT)

# Struct de informações de cada participante
PARTICIPANT_INFO_FMT = (
    "<"
    "hhhh"   # mWorldPosition[4] (comprimido — ignorar aqui)
    "hhhh"   # mCurrentLapDistance[4] (comprimido)
    "B"      # mRacePosition
    "B"      # mLapsCompleted
    "B"      # mCurrentLap
    "B"      # mSector
    "h"      # mLastSectorTime (ms)
    "H"      # mCurrentSectorTime (ms)
)
PARTICIPANT_INFO_SIZE = struct.calcsize(PARTICIPANT_INFO_FMT)

# Struct do pacote de timings (PKT_TIMINGS = 3)
# Lido a partir de HEADER_SIZE (12 bytes) — sem dummy
TIMINGS_FMT_PREFIX = (
    "<"
    "b"      # mNumParticipants
    "f"      # mParticipantsChangedTimestamp
    "f"      # mEventTimeRemaining
    "f"      # mSplitTimeAhead
    "f"      # mSplitTimeBehind
    "f"      # mSplitTime (delta)
)
TIMINGS_PREFIX_SIZE = struct.calcsize(TIMINGS_FMT_PREFIX)

# Por participante no pacote de timings
TIMING_PARTICIPANT_FMT = (
    "<"
    "I"      # mLastLapTime (ms)
    "I"      # mCurrentTime (ms)
    "I"      # mBestSector1Time (ms)
    "I"      # mBestSector2Time (ms)
    "I"      # mBestSector3Time (ms)
    "I"      # mBestLapTime (ms)
    "I"      # mLastSector1Time (ms)
    "I"      # mLastSector2Time (ms)
    "I"      # mLastSector3Time (ms)
    "I"      # mInvalidatedLap
    "f"      # mLapDistance
    "f"      # mTotalDistance
    "f"      # mTrackLength
    "B"      # mLapInvalidated
    "B"      # mLastLapWasInvalid
    "B"      # mCurrentLapIsValid
)
TIMING_PARTICIPANT_SIZE = struct.calcsize(TIMING_PARTICIPANT_FMT)

# Struct do pacote de nomes de veículo (PKT_VEHICLE_NAMES = 6)
VEHICLE_NAME_FMT = "<64s64s"
VEHICLE_NAME_SIZE = struct.calcsize(VEHICLE_NAME_FMT)


def _kelvin_tenths_to_celsius(k10: int) -> float:
    """Converte temperatura do protocolo (Kelvin * 10) para Celsius."""
    return (k10 / 10.0) - 273.15


def _ms_to_laptime_str(ms: int) -> str:
    """Converte milissegundos para string legível 'm:ss.mmm'."""
    if ms <= 0:
        return "--:--.---"
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    millis  = ms % 1000
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def _decode_cstr(data: bytes) -> str:
    """Decodifica uma string C (null-terminated) de bytes."""
    try:
        end = data.index(b'\x00')
        return data[:end].decode('utf-8', errors='replace').strip()
    except (ValueError, UnicodeDecodeError):
        return data.decode('utf-8', errors='replace').strip('\x00').strip()


# ---------------------------------------------------------------------------
# Provider Principal
# ---------------------------------------------------------------------------

class AMS2TelemetryProvider(TelemetryProvider):
    """
    Provider de telemetria para o Automobilista 2 via UDP (protocolo pCars2).

    Roda um listener UDP em background que recebe e armazena os pacotes mais
    recentes. O método get_state() lê esses dados e os converte para um
    TelemetryState padronizado — sem bloquear a thread da engine.

    Configuração no AMS2:
        Options → Gameplay → UDP Telemetry
        → IP: 127.0.0.1  (ou o IP da máquina que roda o dashboard)
        → Port: 5606
    """

    def __init__(self, host: str = UDP_HOST, port: int = UDP_PORT):
        self._host = host
        self._port = port
        self._socket: socket.socket | None = None
        self._is_connected = False

        # Cache dos últimos pacotes recebidos (thread-safe via lock)
        self._lock = threading.Lock()
        self._last_car_physics: bytes | None = None
        self._last_timings: bytes | None = None
        self._last_race_def: bytes | None = None
        self._last_vehicle_name: bytes | None = None
        self._last_time_stats: bytes | None = None

        self._listener_thread: threading.Thread | None = None
        self._listening = False

    # -----------------------------------------------------------------------
    # Interface TelemetryProvider
    # -----------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Abre o socket UDP e inicia a thread de escuta em background.
        Retorna True se o socket já estava aberto ou foi aberto com sucesso.

        IMPORTANTE: usa self._socket (não self._is_connected) como guard,
        porque após um timeout o _is_connected vira False mas o socket
        ainda está aberto — tentar um segundo bind() causaria OSError.
        """
        if self._socket is not None:  # Socket já aberto — não re-bindar
            return True

        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._socket.settimeout(UDP_TIMEOUT)
            self._socket.bind((self._host, self._port))

            # Inicia thread de recepção em background
            self._listening = True
            self._listener_thread = threading.Thread(
                target=self._listener_loop,
                name="AMS2-UDP-Listener",
                daemon=True
            )
            self._listener_thread.start()

            print(f"[AMS2] Escutando UDP em {self._host}:{self._port}")
            return True

        except OSError as e:
            print(f"[AMS2] Falha ao abrir socket UDP: {e}")
            self._socket = None
            return False

    def get_state(self) -> TelemetryState:
        """
        Lê os dados mais recentes dos pacotes UDP e retorna um TelemetryState
        completamente preenchido. Não bloqueia — usa o cache da thread de escuta.
        """
        state = TelemetryState(is_connected=self._is_connected)

        if not self._is_connected:
            return state

        with self._lock:
            physics_data    = self._last_car_physics
            timings_data    = self._last_timings
            race_def_data   = self._last_race_def
            vehicle_data    = self._last_vehicle_name
            time_stats_data = self._last_time_stats

        # Sem nenhum pacote ainda: aguardando o AMS2 transmitir
        if physics_data is None:
            state.is_connected = False
            return state

        self._parse_car_physics(physics_data, state)

        if timings_data is not None:
            self._parse_timings(timings_data, state)

        if race_def_data is not None:
            self._parse_race_definition(race_def_data, state)

        if time_stats_data is not None:
            self._parse_time_stats(time_stats_data, state)

        if vehicle_data is not None:
            self._parse_vehicle_name(vehicle_data, state)

        return state

    def close(self):
        """Para a thread de escuta e fecha o socket UDP."""
        self._listening = False
        self._is_connected = False

        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

        if self._listener_thread and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=3.0)

        self._last_car_physics = None
        self._last_timings = None
        self._last_race_def = None
        self._last_vehicle_name = None
        self._last_time_stats = None
        print("[AMS2] Socket UDP fechado.")

    # -----------------------------------------------------------------------
    # Thread de Escuta UDP
    # -----------------------------------------------------------------------

    def _listener_loop(self):
        """
        Loop rodando em thread separada (daemon).
        Recebe pacotes UDP, identifica o tipo pelo header e armazena no cache.
        """
        while self._listening and self._socket:
            try:
                data, _ = self._socket.recvfrom(BUFFER_SIZE)
                if len(data) < HEADER_SIZE:
                    continue

                # mPacketType está no byte 10 no cabeçalho PCars2 (12 bytes)
                pkt_type = data[10]

                with self._lock:
                    if pkt_type == PKT_CAR_PHYSICS:
                        self._last_car_physics = data
                        self._is_connected = True   # Recebendo dados = conectado
                    elif pkt_type == PKT_TIMINGS:
                        self._last_timings = data
                    elif pkt_type == PKT_RACE_DEFINITION:
                        self._last_race_def = data
                    elif pkt_type == PKT_VEHICLE_NAMES:
                        self._last_vehicle_name = data
                    elif pkt_type == PKT_TIME_STATS:
                        self._last_time_stats = data

            except socket.timeout:
                # Timeout → jogo pausado ou fechado
                with self._lock:
                    self._is_connected = False
                continue
            except OSError:
                # Socket fechado externamente
                break

    # -----------------------------------------------------------------------
    # Parsing dos Pacotes
    # -----------------------------------------------------------------------

    def _parse_car_physics(self, data: bytes, state: TelemetryState):
        """
        Extrai dados de física do carro do pacote PKT_CAR_PHYSICS.
        Offsets ajustados para o protocolo UDP Nativo do Automobilista 2 (formato PCars2 UDP).
        """
        if len(data) < 400:
            return

        import struct

        # Lemos o viewedParticipantIndex (offset 12)
        v_idx = struct.unpack_from("<b", data, 12)[0]
        if v_idx >= 0:
            self._viewed_participant_index = v_idx

        # Velocidade: offset 36 (float) em m/s
        state.speed_kmh = struct.unpack_from("<f", data, 36)[0] * 3.6

        # RPM: offset 40 (uint16), MaxRPM: offset 42 (uint16)
        state.rpm = struct.unpack_from("<H", data, 40)[0]
        state.max_rpm = struct.unpack_from("<H", data, 42)[0]
        if state.max_rpm == 0:
            state.max_rpm = max(state.rpm + 1000, 8000)

        # Entradas (Acelerador, Freio, Embreagem)
        # No PCars2 UDP real, Gas é 13, Brake 14, SteerUnfilt 15, Clutch 16
        try:
            state.gas = max(0.0, min(1.0, data[13] / 255.0))
            state.brake = max(0.0, min(1.0, data[14] / 255.0))
            state.clutch = max(0.0, min(1.0, data[16] / 255.0))
        except:
            pass

        # Volante: offset 44 (int8, -127..127) - Filtado
        try:
            steer_raw = struct.unpack_from("<b", data, 44)[0]
            state.steer_angle = steer_raw / 127.0 * 90.0
        except:
            pass

        # Marcha: offset 45 (uint8)
        gear_raw = data[45] & 0x0F
        if gear_raw == 15:
            state.gear = 1  # Neutro
        elif gear_raw == 0:
            state.gear = 0  # Re
        else:
            state.gear = gear_raw + 1

        # Car Flags: offset 17 no jogo real (o mock usava 19, entao lemos 17 com fallback)
        car_flags = data[17] if data[17] != 0 else data[19]
        state.abs_active = bool(car_flags & CAR_ABS)
        state.tc_active = bool(car_flags & CAR_TC)
        state.pit_limiter = bool(car_flags & CAR_PIT_LIMITER)

        # Combustivel: Nivel(float) offset 32, Capacidade(uint8) offset 28 (mock usava 30)
        fuel_frac = struct.unpack_from("<f", data, 32)[0]
        fuel_frac = max(0.0, min(1.0, fuel_frac))
        fuel_cap = data[28] if data[28] < 250 and data[28] > 0 else data[30]
        fuel_cap = fuel_cap if fuel_cap < 250 and fuel_cap > 0 else 80
        state.fuel = fuel_frac * fuel_cap

        # Pneus
        # Suspensao offset 332
        if len(data) >= 332 + 16:
            sus = struct.unpack_from("<ffff", data, 332)
            state.suspension_travel = [x * 1000.0 for x in sus]

        # Pressao de Ar offset 352
        if len(data) >= 352 + 8:
            press = struct.unpack_from("<HHHH", data, 352)
            state.tyre_pressure = [x * 0.145038 for x in press]

        # Desgaste offset 200
        if len(data) >= 200 + 4:
            wear = struct.unpack_from("<BBBB", data, 200)
            state.tyre_wear = [(x / 255.0) * 100.0 for x in wear]

        # Temperatura (K*10) offset 216
        if len(data) >= 216 + 8:
            temps = struct.unpack_from("<HHHH", data, 216)
            state.tyre_temp = [x - 273.15 for x in temps]


    def _parse_timings(self, data: bytes, state: TelemetryState):
        """
        Extrai tempos de volta do pacote PKT_TIMINGS (tipo 3).
        Layout AMS2 UDP (Project Cars 2) - Array começa no offset 33.
        """
        try:
            if len(data) < 33:
                return

            PART_OFFSET = 33
            PART_SIZE = 32
            
            # Obtém o índice do jogador local lido em _parse_car_physics
            idx = getattr(self, "_viewed_participant_index", 0)
            
            b = PART_OFFSET + (idx * PART_SIZE)
            if b + PART_SIZE > len(data):
                return

            # Extrai delta do splitTime (offset 29 no pacote)
            split_time = struct.unpack_from("<f", data, 29)[0]
            if -100.0 < split_time < 100.0:
                state.delta_time = split_time

            # 12: sCurrentLapDistance (uint16)
            lap_dist = struct.unpack_from("<H", data, b + 12)[0]
            state.distance_traveled = float(lap_dist)
            if state.track_length > 0:
                state.track_position = min(1.0, float(lap_dist) / state.track_length)

            # 15: sSector (uint8)
            # PCars2 usa 0=S1, 1=S2, 2=S3
            sector_raw = data[b + 15] & 0x07
            state.sector_index = min(sector_raw, 2)

            # 21: sCurrentLap (uint8)
            laps_completed = data[b + 21] & 0x7F
            state.lap_number = int(laps_completed) + 1

            def _read_laptime(offset):
                secs = struct.unpack_from("<f", data, offset)[0]
                import math
                if math.isnan(secs) or secs <= 0 or secs > 3600:
                    return "--:--.---"
                total_ms = int(secs * 1000)
                mins  = total_ms // 60000
                s     = (total_ms % 60000) // 1000
                ms    = total_ms % 1000
                return f"{mins}:{s:02d}.{ms:03d}"

            # 22: sCurrentTime (float)
            curr_t  = _read_laptime(b + 22)
            if curr_t != "--:--.---":
                state.current_time = curr_t

        except Exception:
            pass

    def _parse_time_stats(self, data: bytes, state: TelemetryState):
        """
        Extrai tempos de volta (melhor/última) do pacote PKT_TIME_STATS (tipo 7).
        """
        try:
            if len(data) < 12:
                return
                
            PART_OFFSET = 12 + 4 # Header (12) + ParticipantsChangedTimestamp (4)
            PART_SIZE = 30 # sParticipantStatsInfo no PCars 2 tem 30 bytes
            
            idx = getattr(self, "_viewed_participant_index", 0)
            b = PART_OFFSET + (idx * PART_SIZE)
            
            if b + PART_SIZE > len(data):
                return
                
            def _read_laptime(offset):
                secs = struct.unpack_from("<f", data, offset)[0]
                import math
                if math.isnan(secs) or secs <= 0 or secs > 3600:
                    return "--:--.---"
                total_ms = int(secs * 1000)
                mins  = total_ms // 60000
                s     = (total_ms % 60000) // 1000
                ms    = total_ms % 1000
                return f"{mins}:{s:02d}.{ms:03d}"
                
            # 0: mFastestLapTime
            best_t = _read_laptime(b + 0)
            if best_t != "--:--.---":
                state.best_time = best_t
                
            # 4: mLastLapTime
            last_t = _read_laptime(b + 4)
            if last_t != "--:--.---":
                state.last_time = last_t
                
            # 8: mLastSectorTime
            last_sector_s = struct.unpack_from("<f", data, b + 8)[0]
            if 0 < last_sector_s < 600.0:
                state.last_sector_time = int(last_sector_s * 1000)
                
        except Exception:
            pass



    def _parse_race_definition(self, data: bytes, state: TelemetryState):
        """
        Extrai nome da pista, variação e condições climáticas
        do pacote PKT_RACE_DEFINITION.
        """
        try:
            if len(data) < HEADER_SIZE + RACE_DEFINITION_SIZE:
                return

            # Offset correto: HEADER_SIZE (12), não HEADER_SIZE - 4
            offset = HEADER_SIZE
            fields = struct.unpack_from(RACE_DEFINITION_FMT, data, offset)

            track_loc     = _decode_cstr(fields[16])
            track_variant = _decode_cstr(fields[17])

            if track_variant and track_variant.lower() != track_loc.lower():
                state.track_name = f"{track_loc} — {track_variant}"
            else:
                state.track_name = track_loc if track_loc else "Unknown Track"
        except Exception:
            pass

    def _parse_vehicle_name(self, data: bytes, state: TelemetryState):
        """
        Extrai o nome do veículo do pacote PKT_VEHICLE_NAMES.
        """
        # Pacote contém: header + nParticipants(1B) + array de nomes (64+64 bytes cada)
        name_offset = HEADER_SIZE + 1  # Pula o byte nParticipants
        if len(data) < name_offset + 64:
            return

        try:
            car_name_bytes = data[name_offset:name_offset + 64]
            state.car_name = _decode_cstr(car_name_bytes) or "Unknown Car"
        except Exception:
            pass
