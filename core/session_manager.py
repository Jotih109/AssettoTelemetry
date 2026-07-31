import json
import os
import copy
from datetime import datetime
from core.models import TelemetryState


# ---------------------------------------------------------------------------
# Persistência resiliente
# ---------------------------------------------------------------------------

def _write_json_atomic(path: str, data: dict) -> bool:
    """
    Grava JSON de forma atômica: escreve num arquivo temporário e só então
    substitui o definitivo (os.replace é atômico no Windows e no POSIX).

    Sem isso, fechar o jogo ou o app no meio de uma gravação deixa o arquivo
    truncado — e um ghost truncado derrubava o dashboard ao entrar na pista.
    """
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        return True
    except OSError as e:
        print(f"[SessionManager] Falha ao salvar {os.path.basename(path)}: {e}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return False


def _read_json_safe(path: str):
    """
    Lê um JSON tolerando arquivo corrompido/truncado.

    Retorna None em caso de falha (o chamador decide o fallback) e renomeia
    o arquivo problemático para *.corrupt, para não tentar lê-lo de novo em
    cada volta nem perder o material caso você queira investigar.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        print(f"[SessionManager] Arquivo inválido, ignorando: {path} ({e})")
        try:
            os.replace(path, f"{path}.corrupt")
        except OSError:
            pass
        return None


def parse_lap_time_ms(t_str: str) -> int:
    """
    Converte tempo de volta em milissegundos. Aceita dois formatos:
        "m:ss.mmm"  — o que os providers enviam (ex: "1:23.456")
        "m:ss:mmm"  — formato legado, com três dois-pontos
    Devolve 0 para vazio, "--:--.---" ou qualquer coisa que não dê para ler.
    """
    try:
        if not t_str or t_str.startswith("-"):
            return 0
        if "." in t_str:
            min_sec, millis = t_str.rsplit(".", 1)
            parts = min_sec.split(":")
            minutes = int(parts[0]) if len(parts) >= 2 else 0
            seconds = int(parts[-1])
            return (minutes * 60 * 1000) + (seconds * 1000) + int(millis)
        parts = t_str.split(":")
        if len(parts) == 3:
            return (int(parts[0]) * 60 * 1000) + (int(parts[1]) * 1000) + int(parts[2])
    except (ValueError, IndexError):
        pass
    return 0


from core.paths import get_app_dir

#: Quantos quadros esperar pelo tempo oficial da volta depois de cruzar a linha.
#: O AC zera o cronômetro da volta na hora, mas só publica o iLastTime alguns
#: quadros depois — a volta que fechou só pode ser finalizada com ele em mão.
#: 45 quadros ≈ 0,75 s a 60 Hz.
LAP_TIME_WAIT_FRAMES = 45

#: Uma volta só entra no histórico e só pode virar referência se tiver pelo
#: menos isto de telemetria. Sem esse piso, o punhado de quadros gravado num
#: teleporte/saída de box virava "melhor volta" e detonava o delta.
MIN_LAP_POINTS = 20
MIN_LAP_SPAN_S = 5.0


def _is_plausible_lap(lap_data: dict) -> bool:
    """Volta com telemetria suficiente para entrar no histórico."""
    times = lap_data.get("times") or []
    if len(times) < MIN_LAP_POINTS:
        return False
    return (max(times) - min(times)) >= MIN_LAP_SPAN_S


def covers_full_lap(lap_data: dict, track_length: float = 0.0) -> bool:
    """
    A telemetria vai da linha de chegada até a linha de chegada?

    Só uma volta inteira pode servir de referência: o delta é calculado
    interpolando o tempo do fantasma NA MESMA DISTÂNCIA, e uma volta gravada
    pela metade (app aberto no meio da volta, saída de box) não tem o que
    comparar no trecho que falta.
    """
    distances = lap_data.get("distance") or []
    if len(distances) < MIN_LAP_POINTS:
        return False
    first, last = distances[0], distances[-1]
    if track_length and track_length > 0:
        return first <= track_length * 0.05 and last >= track_length * 0.95
    # Sem o comprimento da pista, o melhor palpite é o próprio traçado:
    # começou perto do zero e andou bastante
    return first <= max(50.0, last * 0.05) and last > 500.0


class SessionManager:
    """
    Gerencia a sessão atual, mantendo as arrays da volta atual e da volta ideal (Theoretical Best).
    Faz o fatiamento e costura (splicing) de setores em tempo real.
    """
    def __init__(self, data_dir=None):
        self.data_dir = data_dir if data_dir else get_app_dir("telemetry_data")
            
        self.historic_laps = []
        self.completed_laps = []
        self.reset_current_lap()
        
        self.best_lap_ghost = self._empty_ghost()
        self.session_best_lap_ghost = self._empty_ghost()
        self.ideal_lap_ghost = self._empty_ghost()
        
        self._last_time = ""
        self._best_time = ""
        self._last_sector_index = 0
        # Volta que cruzou a linha e está esperando o tempo oficial do jogo
        self._pending_lap = None
        self._last_lap_number = 0
        self._seen_first_frame = False
        self._lap_start_time_ms = 0
        self._current_sector_0_ms = 0
        self._current_sector_1_ms = 0
        self._fuel_at_lap_start = -1.0
        self._fuel_consumption_history: list = []  # L per lap
        self.avg_fuel_per_lap: float = 0.0
        # Snapshot of the LAST COMPLETED lap — never wiped by reset
        self.last_completed_sector_times = [0, 0, 0]
        self.last_completed_lap_time_str = ""
        self.current_reference_sector_ms = [0, 0, 0]
        
    def _empty_ghost(self):
        return {
            "metadata": {
                "track": "", "car": "", "lap_time_str": "", 
                "sector_times_ms": [0, 0, 0], "timestamp": ""
            },
            "telemetry": {
                "times": [], "distance": [], "speed": [], "gas": [], "brake": [], "sector": [], "rpm": [], "steer": [], "delta": [], "car_x": [], "car_z": [], "abs_intervention": [], "tc_intervention": [], "g_lat": []
            }
        }
        
    def reset_current_lap(self):
        self.current_lap_data = {
            "times": [], "distance": [], "speed": [], "gas": [], "brake": [], "sector": [], "rpm": [], "steer": [], "delta": [], "car_x": [], "car_z": [], "abs_intervention": [], "tc_intervention": [], "g_lat": []
        }
        self.current_sector_times = [0, 0, 0]

    # Nomes que indicam que o bloco estático do jogo ainda não foi lido
    _UNKNOWN_NAMES = {"", "unknown", "unknowntrack", "unknowncar",
                      "unknown track", "unknown car"}

    def _clean_folder_names(self, track, car):
        invalid_chars = '<>:"/\\|?*'
        for c in invalid_chars:
            track = track.replace(c, '')
            car = car.replace(c, '')
        return track if track else "UnknownTrack", car if car else "UnknownCar"

    def _is_identified(self, track: str, car: str) -> bool:
        """
        True apenas quando pista E carro foram realmente identificados.

        Ao entrar na pista existe uma janela de alguns quadros em que o bloco
        estático do AC ainda não foi lido e o estado vem como "Unknown Track /
        Unknown Car". Salvar voltas nessa janela cria uma pasta lixo e, pior,
        faz o dashboard carregar esse ghost como "melhor volta" de qualquer
        sessão não identificada — misturando carros e pistas diferentes.
        """
        return (track.strip().lower() not in self._UNKNOWN_NAMES
                and car.strip().lower() not in self._UNKNOWN_NAMES)

    def process_state(self, state: TelemetryState, reference_ghost: dict = None):
        """
        Injeta o estado atual e gerencia os ciclos da volta e setores.

        reference_ghost: ghost (best_lap_ghost / session_best_lap_ghost / ideal_lap_ghost)
        escolhido pela UI para servir de base ao Delta Geral e aos deltas de setor.
        Se None, usa o session_best_lap_ghost (comportamento padrão).
        """
        if reference_ghost is None:
            reference_ghost = self.session_best_lap_ghost

        # Exposto para a UI calcular os deltas de setor sem duplicar a lógica de seleção
        self.current_reference_sector_ms = reference_ghost.get("metadata", {}).get("sector_times_ms", [0, 0, 0])

        parse_time_to_ms = parse_lap_time_ms

        # Primeiro quadro da sessão: absorve o que o jogo já traz sem tratar
        # como volta concluída. Sem isso, o tempo de uma volta feita ANTES do
        # app abrir era lido como "acabei de fechar uma volta" e virava uma
        # linha fantasma no histórico.
        if not self._seen_first_frame:
            self._seen_first_frame = True
            self._last_time = state.last_time
            self._best_time = state.best_time
            self._last_lap_number = state.lap_number
            self._last_sector_index = state.sector_index

        time_sec = parse_time_to_ms(state.current_time) / 1000.0
        time_ms = int(time_sec * 1000)
        
        # --- Live Delta Calculation ---
        best_ghost_t = reference_ghost.get("telemetry", {})
        best_times = best_ghost_t.get("times", [])
        best_distances = best_ghost_t.get("distance", [])
        
        # O delta só existe onde a referência TEM dado. Fora da faixa de
        # distância que ela cobre, extrapolar produzia números absurdos
        # (dezenas de segundos): bastava a referência ser uma volta parcial —
        # o app aberto no meio de uma volta, por exemplo — para o delta virar
        # "-51 s" no começo da volta seguinte.
        ref_covers_here = (
            bool(best_distances)
            and best_distances[0] <= state.distance_traveled <= best_distances[-1]
        )

        if (best_times and best_distances and len(best_times) == len(best_distances)
                and state.distance_traveled > 0 and time_sec > 0 and ref_covers_here):
            import bisect
            idx = bisect.bisect_left(best_distances, state.distance_traveled)

            if idx == 0:
                ref_time = best_times[0]
            elif idx >= len(best_distances):
                ref_time = best_times[-1]
            else:
                d0 = best_distances[idx-1]
                d1 = best_distances[idx]
                t0 = best_times[idx-1]
                t1 = best_times[idx]
                
                if d1 == d0:
                    ref_time = t0
                else:
                    ratio = (state.distance_traveled - d0) / (d1 - d0)
                    ref_time = t0 + ratio * (t1 - t0)
            
            # Subtrai o tempo atual pelo tempo do fantasma NA MESMA DISTÂNCIA
            state.delta_time = round(time_sec - ref_time, 3)
        
        # 1. Checa mudança de setor
        if state.sector_index != self._last_sector_index:
            closed_sector = self._last_sector_index
            if closed_sector == 0:
                self._current_sector_0_ms = time_ms
                self.current_sector_times[0] = self._current_sector_0_ms
            elif closed_sector == 1:
                self._current_sector_1_ms = time_ms - self._current_sector_0_ms
                self.current_sector_times[1] = self._current_sector_1_ms
                
            self._update_ideal_lap(state, closed_sector, self.current_sector_times[closed_sector])
            self._last_sector_index = state.sector_index
        
        # 2. Checa Fim da Volta
        #
        # Um cruzamento de linha no AC dá DOIS sinais em quadros diferentes: o
        # cronômetro da volta zera imediatamente e o iLastTime (tempo oficial)
        # só aparece algumas dezenas de milissegundos depois. Tratar os dois
        # como fim de volta fechava a volta duas vezes — a segunda com meia
        # dúzia de pontos, que entrava no histórico e virava ghost de
        # referência, jogando o delta para dezenas de segundos.
        #
        # Por isso o fechamento é em duas etapas: no primeiro sinal a volta é
        # separada e a nova começa limpa; a finalização (S3, tempo, gravação)
        # espera o tempo oficial chegar.
        _NO_TIME = {"", "--:--.---"}
        new_lap_time = (state.last_time not in _NO_TIME
                        and state.last_time != self._last_time)

        lap_restarted = False
        if len(self.current_lap_data["times"]) > 0:
            if time_sec < self.current_lap_data["times"][-1] - 1.0:
                lap_restarted = True

        lap_number_advanced = (state.lap_number > 0 and self._last_lap_number > 0
                               and state.lap_number != self._last_lap_number)

        crossed_line = lap_restarted or lap_number_advanced or new_lap_time
        if crossed_line and self._pending_lap is None:
            self._begin_lap_close(state)

        if self._pending_lap is not None:
            self._pending_lap["frames"] += 1
            if new_lap_time:
                # Tempo oficial chegou: finaliza com o número certo
                self._finish_lap_close(state, state.last_time)
            elif self._pending_lap["frames"] >= LAP_TIME_WAIT_FRAMES:
                # O jogo não publicou nada; finaliza com o que existe para não
                # perder a telemetria da volta
                self._finish_lap_close(state, state.last_time)
        elif self._fuel_at_lap_start < 0 and state.fuel > 0:
            # Initialize on first valid frame
            self._fuel_at_lap_start = state.fuel

        self._last_lap_number = state.lap_number

        # Grava dados da telemetria da volta atual
        self.current_lap_data["times"].append(time_sec)
        self.current_lap_data["distance"].append(state.distance_traveled)
        self.current_lap_data["speed"].append(state.speed_kmh)
        self.current_lap_data["gas"].append(state.gas)
        self.current_lap_data["brake"].append(state.brake)
        self.current_lap_data["sector"].append(state.sector_index)
        self.current_lap_data["rpm"].append(state.rpm)
        self.current_lap_data["steer"].append(state.steer_angle)
        self.current_lap_data["delta"].append(state.delta_time)
        self.current_lap_data["car_x"].append(state.car_x)
        self.current_lap_data["car_z"].append(state.car_z)
        self.current_lap_data["abs_intervention"].append(state.abs_intervention)
        self.current_lap_data["tc_intervention"].append(state.tc_intervention)
        # Canal usado pela Análise Curva a Curva para detectar curvas quando a
        # pista ainda não tem mapeamento manual (|G lat| > 0.4g).
        self.current_lap_data["g_lat"].append(state.g_lat)

    def _begin_lap_close(self, state: TelemetryState):
        """
        Primeira etapa do fim de volta: separa a volta que fechou e começa a
        nova do zero AGORA, para que nenhum ponto da volta nova seja gravado
        na antiga (e vice-versa).
        """
        # --- Consumo de combustível da volta que fechou ---
        if self._fuel_at_lap_start >= 0 and state.fuel >= 0:
            consumed = self._fuel_at_lap_start - state.fuel
            if 0.0 < consumed < 10.0:   # sanity check
                self._fuel_consumption_history.append(consumed)
                self._fuel_consumption_history = self._fuel_consumption_history[-5:]
                self.avg_fuel_per_lap = (sum(self._fuel_consumption_history)
                                         / len(self._fuel_consumption_history))

        self._pending_lap = {
            "data": self.current_lap_data,
            "sector_times": list(self.current_sector_times),
            "sector_0_ms": self._current_sector_0_ms,
            "sector_1_ms": self._current_sector_1_ms,
            # O número da volta que fechou é o do quadro ANTERIOR: neste o jogo
            # já está contando a volta nova
            "lap_number": self._last_lap_number or getattr(state, "lap_number", 0),
            "frames": 0,
        }

        self.reset_current_lap()
        self._last_sector_index = state.sector_index
        self._current_sector_0_ms = 0
        self._current_sector_1_ms = 0
        self._fuel_at_lap_start = state.fuel

    def _finish_lap_close(self, state: TelemetryState, lap_time_str: str):
        """
        Segunda etapa: com o tempo oficial em mão, fecha o S3, alimenta a volta
        ideal, grava a volta e atualiza os ghosts.
        """
        pending = self._pending_lap
        self._pending_lap = None
        if pending is None:
            return

        lap_data = pending["data"]
        sector_times = list(pending["sector_times"])

        lap_ms = parse_lap_time_ms(lap_time_str)
        if pending["sector_0_ms"] > 0 and pending["sector_1_ms"] > 0 and lap_ms > 0:
            sector_times[2] = lap_ms - pending["sector_0_ms"] - pending["sector_1_ms"]

        # Snapshot para a UI ler depois
        self.last_completed_sector_times = list(sector_times)
        self.last_completed_lap_time_str = lap_time_str

        self._update_ideal_lap(state, 2, sector_times[2], lap_data=lap_data)

        self.save_lap(state, lap_time_str=lap_time_str, lap_data=lap_data,
                      sector_times=sector_times, lap_number=pending["lap_number"])

        if lap_time_str not in ("", "--:--.---"):
            self._last_time = lap_time_str

    def _update_ideal_lap(self, state: TelemetryState, closed_sector: int,
                          new_sector_time_ms: int, lap_data: dict = None):
        if closed_sector < 0 or closed_sector > 2 or new_sector_time_ms <= 0:
            return
            
        track, car = self._clean_folder_names(state.track_name.strip(), state.car_name.strip())
        if not self._is_identified(track, car):
            return
        folder_path = os.path.join(self.data_dir, track, car)
        ideal_path = os.path.join(folder_path, "ideal_lap_ghost.json")
        
        ideal_data = self._empty_ghost()
        if os.path.exists(ideal_path):
            loaded = _read_json_safe(ideal_path)
            if loaded is not None and "metadata" in loaded and "telemetry" in loaded:
                ideal_data = loaded

        ideal_sector_times = ideal_data["metadata"].get("sector_times_ms", [0, 0, 0])
        best_recorded_time = ideal_sector_times[closed_sector]
        
        # Se for o primeiro registro ou se o novo tempo for menor (mais rápido)
        if best_recorded_time == 0 or new_sector_time_ms < best_recorded_time:
            print(f"NOVO THEORETICAL BEST para Setor {closed_sector}: {new_sector_time_ms}ms")
            ideal_sector_times[closed_sector] = new_sector_time_ms
            ideal_data["metadata"]["sector_times_ms"] = ideal_sector_times
            ideal_data["metadata"]["track"] = track
            ideal_data["metadata"]["car"] = car
            ideal_data["metadata"]["timestamp"] = datetime.now().isoformat()
            
            # Calcula o tempo total da volta ideal (soma dos melhores setores)
            total_ideal_ms = sum(ideal_sector_times)
            if total_ideal_ms > 0:
                m = int(total_ideal_ms // 60000)
                s = int((total_ideal_ms % 60000) // 1000)
                ms = int(total_ideal_ms % 1000)
                ideal_data["metadata"]["lap_time_str"] = f"{m}:{s:02d}.{ms:03d}"
            
            # SPLICING (Costura) da Telemetria
            # Manter os pontos que NÃO são do closed_sector
            new_telemetry = {"times": [], "distance": [], "speed": [], "gas": [], "brake": [], "sector": [], "rpm": [], "steer": [], "delta": [], "car_x": [], "car_z": [], "g_lat": []}
            
            # Copia os dados do ideal antigo que pertencem aos outros setores
            old_t = ideal_data["telemetry"]
            for i in range(len(old_t.get("times", []))):
                if old_t["sector"][i] != closed_sector:
                    new_telemetry["times"].append(old_t["times"][i])
                    new_telemetry["distance"].append(old_t.get("distance", [0.0]*len(old_t["times"]))[i])
                    new_telemetry["speed"].append(old_t["speed"][i])
                    new_telemetry["gas"].append(old_t["gas"][i])
                    new_telemetry["brake"].append(old_t["brake"][i])
                    new_telemetry["sector"].append(old_t["sector"][i])
                    new_telemetry["rpm"].append(old_t.get("rpm", [0]*len(old_t["times"]))[i])
                    new_telemetry["steer"].append(old_t.get("steer", [0.0]*len(old_t["times"]))[i])
                    new_telemetry["delta"].append(old_t.get("delta", [0.0]*len(old_t["times"]))[i])
                    new_telemetry["car_x"].append(old_t.get("car_x", [0.0]*len(old_t["times"]))[i])
                    new_telemetry["car_z"].append(old_t.get("car_z", [0.0]*len(old_t["times"]))[i])
                    new_telemetry["g_lat"].append(old_t.get("g_lat", [0.0]*len(old_t["times"]))[i])

            # Injeta os dados da volta que fechou o setor. No fim da volta essa
            # não é mais a volta atual (que já começou limpa), e sim a que
            # acabou de ser separada — por isso `lap_data`.
            curr_t = lap_data if lap_data is not None else self.current_lap_data
            for i in range(len(curr_t["times"])):
                if curr_t["sector"][i] == closed_sector:
                    new_telemetry["times"].append(curr_t["times"][i])
                    new_telemetry["distance"].append(curr_t["distance"][i])
                    new_telemetry["speed"].append(curr_t["speed"][i])
                    new_telemetry["gas"].append(curr_t["gas"][i])
                    new_telemetry["brake"].append(curr_t["brake"][i])
                    new_telemetry["sector"].append(curr_t["sector"][i])
                    new_telemetry["rpm"].append(curr_t["rpm"][i])
                    new_telemetry["steer"].append(curr_t["steer"][i])
                    new_telemetry["delta"].append(curr_t["delta"][i])
                    new_telemetry["car_x"].append(curr_t["car_x"][i])
                    new_telemetry["car_z"].append(curr_t["car_z"][i])
                    new_telemetry["g_lat"].append(curr_t["g_lat"][i])

            # Reordenar por tempo (times)
            if len(new_telemetry["times"]) > 0:
                sorted_indices = sorted(range(len(new_telemetry["times"])), key=lambda k: new_telemetry["times"][k])
                for key in new_telemetry.keys():
                    new_telemetry[key] = [new_telemetry[key][i] for i in sorted_indices]
                    
            ideal_data["telemetry"] = new_telemetry
            self.ideal_lap_ghost = ideal_data
            
            os.makedirs(folder_path, exist_ok=True)
            _write_json_atomic(ideal_path, ideal_data)
 
    def save_lap(self, state: TelemetryState, manual=False, lap_time_str: str = None,
                 lap_data: dict = None, sector_times: list = None,
                 lap_number: int = None):
        """
        Grava uma volta e atualiza histórico e ghosts.

        Os parâmetros opcionais existem para o fim de volta em duas etapas: a
        volta que fechou não é mais `current_lap_data` quando o tempo oficial
        chega, e o número dela é o do quadro anterior ao cruzamento.
        """
        if lap_data is None:
            lap_data = self.current_lap_data
        if sector_times is None:
            sector_times = self.current_sector_times
        if len(lap_data["times"]) == 0: return
        track, car = self._clean_folder_names(state.track_name.strip(), state.car_name.strip())
        if not self._is_identified(track, car):
            print("[SessionManager] Volta descartada: pista/carro ainda não identificados "
                  f"({track} / {car}).")
            return

        plausible = _is_plausible_lap(lap_data)
        if not plausible:
            # Um punhado de quadros não é uma volta: não entra no histórico nem
            # pode virar referência (era isso que estourava o delta). A gravação
            # em disco continua, para não perder nada que possa ser útil.
            print(f"[SessionManager] Volta curta demais para o histórico "
                  f"({len(lap_data['times'])} pontos): fica só no arquivo.")

        folder_path = os.path.join(self.data_dir, track, car)
        os.makedirs(folder_path, exist_ok=True)

        if lap_time_str is None:
            lap_time_str = state.last_time if not manual else state.current_time
        now = datetime.now()
        safe_lap_time = lap_time_str.replace(":", "-")
        filename = f"{now.strftime('%Y-%m-%d_%H-%M')}_{safe_lap_time}.json"

        full_lap = covers_full_lap(lap_data, getattr(state, "track_length", 0.0))

        data_to_save = {
            "metadata": {
                "track": track, "car": car,
                "lap_time_str": lap_time_str,
                "sector_times_ms": list(sector_times),
                "timestamp": now.isoformat(),
                "manual_save": manual,
                # Marca se a telemetria cobre a volta inteira — quem lê o
                # arquivo depois sabe se pode usá-la como referência
                "full_lap": full_lap,
            },
            "telemetry": lap_data
        }

        _write_json_atomic(os.path.join(folder_path, filename), data_to_save)
            
        def ms_to_str(ms):
            if ms <= 0: return "--:--"
            m = int(ms / 60000)
            s = int((ms % 60000) / 1000)
            mls = int(ms % 1000)
            if m > 0:
                return f"{m}:{s:02d}.{mls:03d}"
            return f"{s}.{mls:03d}"
            
        if not plausible:
            return

        if lap_number is None:
            lap_number = getattr(state, "lap_number", len(self.historic_laps) + 1)

        self.historic_laps.append({
            "lap_number": lap_number,
            "s1": ms_to_str(sector_times[0]),
            "s2": ms_to_str(sector_times[1]),
            "s3": ms_to_str(sector_times[2]),
            "total_time": lap_time_str
        })

        self.completed_laps.append({
            "lap_number": lap_number,
            "lap_time_str": lap_time_str,
            "metadata": copy.deepcopy(data_to_save["metadata"]),
            "telemetry": copy.deepcopy(data_to_save["telemetry"])
        })

        # Salva o Session Best na memória. Tempo ilegível conta como "infinito",
        # para nunca ganhar a comparação.
        current_lap_ms = parse_lap_time_ms(lap_time_str) or 9999999
        session_best_str = self.session_best_lap_ghost["metadata"].get("lap_time_str", "")
        session_best_ms = (parse_lap_time_ms(session_best_str) or 9999999) if session_best_str else 9999999

        # Validar Best Lap: mais de 30 segundos (evita lapsos/saídas de box) E
        # telemetria da volta inteira (senão o delta não tem com o que comparar).
        is_real_lap_time = current_lap_ms > 30000 and full_lap
        if is_real_lap_time and current_lap_ms < session_best_ms:
            # Novo session best
            self.session_best_lap_ghost = copy.deepcopy(data_to_save)

        # O ghost de Personal Best sobrescreve um arquivo em disco: só uma volta
        # com tempo de volta de verdade e telemetria inteira pode fazer isso.
        # `pb_missing` é a auto-cura: quando não há PB em disco — ou quando o
        # que havia foi recusado na leitura por estar incompleto — a próxima
        # volta boa assume o posto, sem precisar bater o recorde do jogo.
        pb_missing = not self.best_lap_ghost.get("telemetry", {}).get("times")
        if is_real_lap_time and (state.best_time != self._best_time or manual or pb_missing):
            self._best_time = state.best_time
            self.best_lap_ghost = copy.deepcopy(data_to_save)
            _write_json_atomic(os.path.join(folder_path, "best_lap_ghost.json"), data_to_save)
 
    def auto_load_ghosts(self, state: TelemetryState):
        """ Carrega tanto o Best quanto o Ideal """
        track, car = self._clean_folder_names(state.track_name.strip(), state.car_name.strip())
        if not self._is_identified(track, car):
            return False
        folder_path = os.path.join(self.data_dir, track, car)

        best_path = os.path.join(folder_path, "best_lap_ghost.json")
        ideal_path = os.path.join(folder_path, "ideal_lap_ghost.json")
        
        loaded = False
        best_data = _read_json_safe(best_path) if os.path.exists(best_path) else None
        # Um PB gravado por versão anterior pode conter uma volta pela metade
        # (o app tinha sido aberto no meio de uma volta). Como referência ela
        # não serve: recusar aqui é o que faz o app se curar sozinho na próxima
        # volta boa, em vez de mostrar delta sem sentido para sempre.
        if (best_data is not None and "telemetry" in best_data
                and best_data["telemetry"].get("distance")
                and not covers_full_lap(best_data["telemetry"],
                                        getattr(state, "track_length", 0.0))):
            print("[SessionManager] Personal Best em disco cobre só parte da volta: "
                  "descartado (a próxima volta completa toma o lugar).")
            best_data = None
        if best_data is not None and "telemetry" in best_data:
            self.best_lap_ghost = best_data
            loaded = True
        else:
            self.best_lap_ghost = self._empty_ghost()

        ideal_data = _read_json_safe(ideal_path) if os.path.exists(ideal_path) else None
        if ideal_data is not None and "telemetry" in ideal_data:
            self.ideal_lap_ghost = ideal_data
            meta = self.ideal_lap_ghost.get("metadata", {})
            if not meta.get("lap_time_str") and "sector_times_ms" in meta:
                st = meta["sector_times_ms"]
                tot = sum(st)
                if tot > 0:
                    m = int(tot // 60000)
                    s = int((tot % 60000) // 1000)
                    ms = int(tot % 1000)
                    meta["lap_time_str"] = f"{m}:{s:02d}.{ms:03d}"
            loaded = True
        else:
            self.ideal_lap_ghost = self._empty_ghost()

        if os.path.exists(folder_path):
            saved_files = sorted([f for f in os.listdir(folder_path) if f.endswith(".json") and f not in ("best_lap_ghost.json", "ideal_lap_ghost.json")])
            for fname in saved_files:
                fpath = os.path.join(folder_path, fname)
                ldata = _read_json_safe(fpath)
                if ldata and "telemetry" in ldata and len(ldata["telemetry"].get("times", [])) > 0:
                    meta = ldata.get("metadata", {})
                    lap_t_str = meta.get("lap_time_str", "--:--.---")
                    already_present = any(
                        c.get("lap_time_str") == lap_t_str and c.get("telemetry", {}).get("times") == ldata["telemetry"].get("times")
                        for c in self.completed_laps
                    )
                    if not already_present:
                        self.completed_laps.append({
                            "lap_number": len(self.completed_laps) + 1,
                            "lap_time_str": lap_t_str,
                            "metadata": meta,
                            "telemetry": ldata["telemetry"]
                        })

        return loaded
