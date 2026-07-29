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


class SessionManager:
    """
    Gerencia a sessão atual, mantendo as arrays da volta atual e da volta ideal (Theoretical Best).
    Faz o fatiamento e costura (splicing) de setores em tempo real.
    """
    def __init__(self, data_dir="telemetry_data"):
        self.data_dir = data_dir
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
            
        self.historic_laps = []
        self.reset_current_lap()
        
        self.best_lap_ghost = self._empty_ghost()
        self.session_best_lap_ghost = self._empty_ghost()
        self.ideal_lap_ghost = self._empty_ghost()
        
        self._last_time = ""
        self._best_time = ""
        self._last_sector_index = 0
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
                "times": [], "distance": [], "speed": [], "gas": [], "brake": [], "sector": [], "rpm": [], "steer": [], "delta": [], "car_x": [], "car_z": []
            }
        }
        
    def reset_current_lap(self):
        self.current_lap_data = {
            "times": [], "distance": [], "speed": [], "gas": [], "brake": [], "sector": [], "rpm": [], "steer": [], "delta": [], "car_x": [], "car_z": []
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

        # Converte string de tempo para milissegundos
        # Aceita dois formatos:
        #   "m:ss.mmm"  — formato enviado pelos providers (ex: "1:23.456")
        #   "m:ss:mmm"  — formato legado com três dois-pontos
        def parse_time_to_ms(t_str):
            try:
                if not t_str or t_str.startswith("-"):
                    return 0
                # Formato "m:ss.mmm"
                if "." in t_str:
                    min_sec, millis = t_str.rsplit(".", 1)
                    parts = min_sec.split(":")
                    minutes = int(parts[0]) if len(parts) >= 2 else 0
                    seconds = int(parts[-1])
                    return (minutes * 60 * 1000) + (seconds * 1000) + int(millis)
                # Formato legado "m:ss:mmm"
                parts = t_str.split(":")
                if len(parts) == 3:
                    return (int(parts[0]) * 60 * 1000) + (int(parts[1]) * 1000) + int(parts[2])
            except Exception:
                pass
            return 0
            
        time_sec = parse_time_to_ms(state.current_time) / 1000.0
        time_ms = int(time_sec * 1000)
        
        # --- Live Delta Calculation ---
        best_ghost_t = reference_ghost.get("telemetry", {})
        best_times = best_ghost_t.get("times", [])
        best_distances = best_ghost_t.get("distance", [])
        
        if best_times and best_distances and len(best_times) == len(best_distances) and state.distance_traveled > 0 and time_sec > 0:
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
        lap_restarted = False
        if len(self.current_lap_data["times"]) > 0:
            if time_sec < self.current_lap_data["times"][-1] - 1.0:
                lap_restarted = True
                
        _NO_TIME = {"", "--:--.---"}
        if (state.last_time not in _NO_TIME and state.last_time != self._last_time) or lap_restarted:
            # --- Fuel Consumption ---
            if self._fuel_at_lap_start >= 0 and state.fuel >= 0:
                consumed = self._fuel_at_lap_start - state.fuel
                if 0.0 < consumed < 10.0:  # Sanity check: reasonable consumption
                    self._fuel_consumption_history.append(consumed)
                    # Keep only last 5 laps for rolling average
                    self._fuel_consumption_history = self._fuel_consumption_history[-5:]
                    self.avg_fuel_per_lap = sum(self._fuel_consumption_history) / len(self._fuel_consumption_history)
            
            # Fechou o S3
            last_lap_ms = parse_time_to_ms(state.last_time)
            if self._current_sector_0_ms > 0 and self._current_sector_1_ms > 0 and last_lap_ms > 0:
                self.current_sector_times[2] = last_lap_ms - self._current_sector_0_ms - self._current_sector_1_ms
            
            self._update_ideal_lap(state, 2, self.current_sector_times[2])

            # Salvar snapshot ANTES do reset (para a UI ler depois)
            self.last_completed_sector_times = list(self.current_sector_times)
            self.last_completed_lap_time_str = state.last_time

            # Salvar a volta completa
            self.save_lap(state)
            self.reset_current_lap()
            self._last_time = state.last_time
            self._last_sector_index = state.sector_index
            self._current_sector_0_ms = 0
            self._current_sector_1_ms = 0
            # Record fuel at start of NEW lap
            self._fuel_at_lap_start = state.fuel
        elif self._fuel_at_lap_start < 0 and state.fuel > 0:
            # Initialize on first valid frame
            self._fuel_at_lap_start = state.fuel
            
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

    def _update_ideal_lap(self, state: TelemetryState, closed_sector: int, new_sector_time_ms: int):
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
            new_telemetry = {"times": [], "distance": [], "speed": [], "gas": [], "brake": [], "sector": [], "rpm": [], "steer": [], "delta": [], "car_x": [], "car_z": []}
            
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
                    
            # Injeta os dados da volta ATUAL que pertencem ao closed_sector
            curr_t = self.current_lap_data
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
                    
            # Reordenar por tempo (times)
            if len(new_telemetry["times"]) > 0:
                sorted_indices = sorted(range(len(new_telemetry["times"])), key=lambda k: new_telemetry["times"][k])
                for key in new_telemetry.keys():
                    new_telemetry[key] = [new_telemetry[key][i] for i in sorted_indices]
                    
            ideal_data["telemetry"] = new_telemetry
            self.ideal_lap_ghost = ideal_data
            
            os.makedirs(folder_path, exist_ok=True)
            _write_json_atomic(ideal_path, ideal_data)
 
    def save_lap(self, state: TelemetryState, manual=False):
        if len(self.current_lap_data["times"]) == 0: return
        track, car = self._clean_folder_names(state.track_name.strip(), state.car_name.strip())
        if not self._is_identified(track, car):
            print("[SessionManager] Volta descartada: pista/carro ainda não identificados "
                  f"({track} / {car}).")
            return
        folder_path = os.path.join(self.data_dir, track, car)
        os.makedirs(folder_path, exist_ok=True)
        
        lap_time_str = state.last_time if not manual else state.current_time
        now = datetime.now()
        safe_lap_time = lap_time_str.replace(":", "-")
        filename = f"{now.strftime('%Y-%m-%d_%H-%M')}_{safe_lap_time}.json"
        
        data_to_save = {
            "metadata": {
                "track": track, "car": car,
                "lap_time_str": lap_time_str,
                "sector_times_ms": self.current_sector_times,
                "timestamp": now.isoformat(),
                "manual_save": manual
            },
            "telemetry": self.current_lap_data
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
            
        self.historic_laps.append({
            "lap_number": getattr(state, "lap_number", len(self.historic_laps) + 1),
            "s1": ms_to_str(self.current_sector_times[0]),
            "s2": ms_to_str(self.current_sector_times[1]),
            "s3": ms_to_str(self.current_sector_times[2]),
            "total_time": lap_time_str
        })
        
        # Salva o Session Best na memória
        def lap_time_to_ms(lap_str):
            try:
                parts = lap_str.split(":")
                if len(parts) == 2:
                    m = int(parts[0])
                    s_ms = parts[1].split(".")
                    s = int(s_ms[0])
                    ms = int(s_ms[1]) if len(s_ms) > 1 else 0
                    return m * 60000 + s * 1000 + ms
            except Exception:
                pass
            return 9999999
        
        current_lap_ms = lap_time_to_ms(lap_time_str)
        session_best_str = self.session_best_lap_ghost["metadata"].get("lap_time_str", "")
        session_best_ms = lap_time_to_ms(session_best_str) if session_best_str else 9999999
        
        # Validar Best Lap: deve ter mais de 30 segundos (30000ms) para evitar lapsos/saídas dos boxes.
        if 30000 < current_lap_ms < session_best_ms:
            # Novo session best
            self.session_best_lap_ghost = copy.deepcopy(data_to_save)
        
        if state.best_time != self._best_time or manual:
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
            
        return loaded
