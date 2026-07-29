"""
tests/test_session_manager.py — Persistência e resiliência do SessionManager
============================================================================
Cobre os casos que já quebraram o app na prática:

  * ghost corrompido/truncado em disco (o app abortava ao entrar na pista)
  * ghost antigo sem os canais novos (car_x/car_z/steer/delta)
  * gravação atômica: um arquivo maior sobrescrito por um menor não pode
    deixar sobra do conteúdo antigo

Roda sem interface gráfica e sem o jogo, num diretório temporário:

    python tests/test_session_manager.py
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import TelemetryState
from core.session_manager import SessionManager, _read_json_safe, _write_json_atomic

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def _state(**kw):
    st = TelemetryState(is_connected=True, track_name="Spa", car_name="Test Car")
    st.track_length = 7000.0
    for k, v in kw.items():
        setattr(st, k, v)
    return st


work = tempfile.mkdtemp(prefix="ac_telemetry_test_")
try:
    data_dir = os.path.join(work, "telemetry_data")
    folder = os.path.join(data_dir, "Spa", "Test Car")
    os.makedirs(folder, exist_ok=True)

    # --- 1. Gravação atômica -------------------------------------------------
    path = os.path.join(work, "atomic.json")
    _write_json_atomic(path, {"telemetry": {"times": list(range(5000))}})
    big_size = os.path.getsize(path)
    _write_json_atomic(path, {"telemetry": {"times": [1]}})
    small_size = os.path.getsize(path)
    check("gravação atômica encolhe o arquivo (sem sobra do antigo)",
          small_size < big_size, f"{big_size} -> {small_size} bytes")
    check("arquivo regravado continua JSON válido",
          json.load(open(path, encoding="utf-8"))["telemetry"]["times"] == [1])
    check("nenhum .tmp deixado para trás",
          not os.path.exists(path + ".tmp"))

    # --- 2. Leitura de arquivo corrompido -----------------------------------
    bad = os.path.join(work, "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write('{"metadata": {}, "telemetry": {"times": [1,2,3]}}LIXO_EXTRA')
    check("_read_json_safe devolve None em arquivo corrompido",
          _read_json_safe(bad) is None)
    check("arquivo corrompido é movido para .corrupt",
          os.path.exists(bad + ".corrupt") and not os.path.exists(bad))

    # --- 3. Ghost corrompido não derruba o auto_load_ghosts -----------------
    best_path = os.path.join(folder, "best_lap_ghost.json")
    ideal_path = os.path.join(folder, "ideal_lap_ghost.json")
    for p in (best_path, ideal_path):
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"metadata": {"lap_time_str": "1:29.5"}, "telemetry": {"times": [0.1')

    sm = SessionManager(data_dir=data_dir)
    st = _state()
    try:
        sm.auto_load_ghosts(st)
        ok = True
        err = ""
    except Exception as e:  # o comportamento antigo caía aqui
        ok, err = False, f"{type(e).__name__}: {e}"
    check("auto_load_ghosts sobrevive a ghost corrompido", ok, err)
    check("ghost corrompido cai no ghost vazio",
          sm.best_lap_ghost["telemetry"]["times"] == [])
    check("ghosts corrompidos foram isolados como .corrupt",
          os.path.exists(best_path + ".corrupt") and os.path.exists(ideal_path + ".corrupt"))

    # --- 4. Ghost antigo (sem car_x/car_z/steer/delta) ----------------------
    legacy = {
        "metadata": {"track": "Spa", "car": "Test Car", "lap_time_str": "1:29.500",
                     "sector_times_ms": [30000, 30000, 29500], "timestamp": ""},
        "telemetry": {
            "times": [0.0, 1.0, 2.0], "distance": [0.0, 50.0, 100.0],
            "speed": [10.0, 20.0, 30.0], "gas": [1.0, 1.0, 0.5],
            "brake": [0.0, 0.0, 0.5], "sector": [0, 0, 0], "rpm": [3000, 4000, 5000],
        },
    }
    _write_json_atomic(best_path, legacy)
    sm2 = SessionManager(data_dir=data_dir)
    loaded = sm2.auto_load_ghosts(_state())
    check("ghost antigo (sem canais novos) é carregado", loaded)
    tele = sm2.best_lap_ghost["telemetry"]
    check("ghost antigo mantém os canais que existiam",
          tele["speed"] == [10.0, 20.0, 30.0])
    check("canais novos ausentes não travam a leitura",
          tele.get("car_x", []) == [] and tele.get("steer", []) == [])

    # --- 5. Ciclo de volta completo: salvar e reler --------------------------
    sm3 = SessionManager(data_dir=data_dir)
    for i in range(30):
        s = _state(
            current_time=f"0:{i:02d}.000",
            distance_traveled=i * 200.0,
            speed_kmh=100.0 + i,
            gas=0.8, brake=0.1, rpm=6000 + i,
            steer_angle=5.0, sector_index=0 if i < 15 else 1,
            car_x=float(i), car_z=float(i * 2), fuel=50.0 - i * 0.05,
        )
        sm3.process_state(s)

    n_times = len(sm3.current_lap_data["times"])
    check("process_state grava todos os canais em lockstep",
          all(len(sm3.current_lap_data[k]) == n_times
              for k in ("distance", "speed", "gas", "brake", "sector",
                        "rpm", "steer", "delta", "car_x", "car_z")),
          f"times={n_times}")

    final = _state(last_time="1:29.500", current_time="1:29.500", best_time="1:29.500")
    sm3.save_lap(final)
    saved = [f for f in os.listdir(folder) if f.endswith(".json") and "ghost" not in f]
    check("save_lap grava o arquivo da volta", len(saved) >= 1, str(saved[:2]))
    reread = _read_json_safe(os.path.join(folder, saved[0]))
    check("volta salva relê como JSON válido", reread is not None)
    check("volta salva tem car_x/car_z",
          reread is not None and len(reread["telemetry"]["car_x"]) == n_times)

    # --- 6. Setor fechado alimenta a volta ideal ----------------------------
    ideal = _read_json_safe(os.path.join(folder, "ideal_lap_ghost.json"))
    check("volta ideal foi criada ao fechar o setor 1",
          ideal is not None and ideal["metadata"]["sector_times_ms"][0] > 0,
          str(ideal["metadata"]["sector_times_ms"]) if ideal else "arquivo ausente")

    # --- 7. Sessão não identificada não deve gravar nada --------------------
    # Ao entrar na pista existem alguns quadros em que o bloco estático do AC
    # ainda não foi lido; sem guarda, isso criava a pasta "Unknown Track /
    # Unknown Car" e o ghost dela era carregado em qualquer sessão anônima.
    sm4 = SessionManager(data_dir=data_dir)
    for i in range(20):
        anon = _state(track_name="Unknown Track", car_name="Unknown Car",
                      current_time=f"0:{i:02d}.000", distance_traveled=i * 200.0,
                      sector_index=0 if i < 10 else 1)
        sm4.process_state(anon)
    sm4.save_lap(_state(track_name="Unknown Track", car_name="Unknown Car",
                        last_time="1:29.000", current_time="1:29.000"))
    unknown_dir = os.path.join(data_dir, "Unknown Track")
    check("volta de sessão não identificada NÃO é salva",
          not os.path.exists(unknown_dir),
          "pasta criada" if os.path.exists(unknown_dir) else "")
    check("auto_load_ghosts recusa sessão não identificada",
          sm4.auto_load_ghosts(_state(track_name="Unknown Track",
                                      car_name="Unknown Car")) is False)

finally:
    shutil.rmtree(work, ignore_errors=True)

print()
fails = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"  [{'OK ' if ok else 'ERRO'}] {name}" + (f"   ({detail})" if detail else ""))
print(f"\n=== {len(results) - len(fails)}/{len(results)} verificacoes passaram ===")
sys.exit(1 if fails else 0)
