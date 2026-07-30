import os
import json
from datetime import datetime
from core.paths import get_app_dir

class TelemetryStorageManager:
    """
    Gerencia a persistência e carregamento sob demanda (lazy loading)
    dos dados de telemetria em formato JSON leve.
    """
    def __init__(self, base_dir=None):
        self.base_dir = base_dir if base_dir else get_app_dir("telemetry_sessions")
            
        self.current_session_dir = None
        self.summary_path = None
        self.session_metadata = {}
        
    def start_new_session(self, track_name, car_model):
        """Inicializa uma nova sessão gerando o timestamp e criando as pastas"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_name = f"sessao_{timestamp}"
        
        self.current_session_dir = os.path.join(self.base_dir, session_name)
        os.makedirs(self.current_session_dir, exist_ok=True)
        os.makedirs(os.path.join(self.current_session_dir, "laps"), exist_ok=True)
        
        self.summary_path = os.path.join(self.current_session_dir, "session_summary.json")
        
        self.session_metadata = {
            "track_name": track_name,
            "car_model": car_model,
            "date": datetime.now().isoformat(),
            "fastest_lap_index": -1,
            "best_lap_time_seconds": float('inf'),
            "laps": []
        }
        
        self._write_summary()
        
    def _write_summary(self):
        if self.summary_path:
            with open(self.summary_path, 'w', encoding='utf-8') as f:
                json.dump(self.session_metadata, f, indent=4)
                
    def save_lap(self, lap_number, lap_time_str, lap_time_seconds, is_valid, telemetry_data):
        """Salva a telemetria da volta e atualiza o resumo"""
        if not self.current_session_dir:
            return
            
        safe_time_str = lap_time_str.replace(":", "m").replace(".", "s")
        file_name = f"lap_{lap_number:02d}_{safe_time_str}.json"
        rel_path = f"laps/{file_name}"
        abs_path = os.path.join(self.current_session_dir, rel_path)
        
        # Otimiza float rounding 
        optimized_data = {}
        for key, arr in telemetry_data.items():
            if isinstance(arr, list):
                if key in ['gear', 'rpm']: 
                    optimized_data[key] = [int(v) for v in arr]
                else:
                    optimized_data[key] = [round(float(v), 3) for v in arr]
            else:
                optimized_data[key] = arr
                
        # Adiciona metadados no arquivo da volta
        lap_payload = {
            "metadata": {
                "lap_number": lap_number,
                "lap_time": lap_time_str,
                "car_model": self.session_metadata.get("car_model"),
                "track_name": self.session_metadata.get("track_name")
            },
            "telemetry": optimized_data
        }
        
        with open(abs_path, 'w', encoding='utf-8') as f:
            # Usando separators=(',', ':') para remover espaços em branco e deixar o arquivo mais leve
            json.dump(lap_payload, f, separators=(',', ':'))
            
        # Atualiza o Summary
        lap_summary = {
            "lap_number": lap_number,
            "lap_time": lap_time_str,
            "lap_time_seconds": lap_time_seconds,
            "is_valid": is_valid,
            "file_path": rel_path
        }
        self.session_metadata["laps"].append(lap_summary)
        
        # Verifica se é a melhor volta (desconsiderando laps inválidas ou voltas muito curtas)
        if lap_time_seconds > 10 and lap_time_seconds < self.session_metadata["best_lap_time_seconds"]:
            self.session_metadata["best_lap_time_seconds"] = lap_time_seconds
            self.session_metadata["fastest_lap_index"] = len(self.session_metadata["laps"]) - 1
            
        self._write_summary()
        
    @staticmethod
    def load_sessions(base_dir="telemetry_sessions"):
        """Retorna uma lista de sessões com seus metadados (apenas o summary)"""
        sessions = []
        if not os.path.exists(base_dir):
            return sessions
            
        for folder in sorted(os.listdir(base_dir), reverse=True):
            summary_path = os.path.join(base_dir, folder, "session_summary.json")
            if os.path.exists(summary_path):
                with open(summary_path, 'r', encoding='utf-8') as f:
                    try:
                        data = json.load(f)
                        data['session_dir'] = folder
                        sessions.append(data)
                    except json.JSONDecodeError:
                        pass
        return sessions
        
    @staticmethod
    def load_lap_data(base_dir, session_dir, lap_file_path):
        """Carrega a telemetria pontual (Lazy Load)"""
        abs_path = os.path.join(base_dir, session_dir, lap_file_path)
        if os.path.exists(abs_path):
            with open(abs_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
