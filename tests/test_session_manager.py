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
from core.session_manager import (
    SessionManager, _read_json_safe, _write_json_atomic, _read_ideal_ghost,
    IDEAL_GHOST_FILENAME, IDEAL_GHOST_LEGACY_FILENAME,
)

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
    # Volta inteira (0..6930 m dos 7000 m da pista), mas sem os canais que só
    # existem nas versões novas: car_x/car_z, steer e delta.
    _n_legacy = 40
    legacy = {
        "metadata": {"track": "Spa", "car": "Test Car", "lap_time_str": "1:29.500",
                     "sector_times_ms": [30000, 30000, 29500], "timestamp": ""},
        "telemetry": {
            "times": [i * 2.25 for i in range(_n_legacy)],
            "distance": [i * 177.7 for i in range(_n_legacy)],
            "speed": [10.0, 20.0, 30.0] + [100.0] * (_n_legacy - 3),
            "gas": [0.9] * _n_legacy, "brake": [0.1] * _n_legacy,
            "sector": [0] * _n_legacy, "rpm": [6000] * _n_legacy,
        },
    }
    _write_json_atomic(best_path, legacy)
    sm2 = SessionManager(data_dir=data_dir)
    loaded = sm2.auto_load_ghosts(_state())
    check("ghost antigo (sem canais novos) é carregado", loaded)
    tele = sm2.best_lap_ghost["telemetry"]
    check("ghost antigo mantém os canais que existiam",
          tele["speed"][:3] == [10.0, 20.0, 30.0])
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
    record = sm3.save_lap(final)
    check("save_lap devolve o registro do catálogo", record is not None,
          str(record))
    saved = [f for f in os.listdir(os.path.join(folder, "laps"))] \
        if os.path.isdir(os.path.join(folder, "laps")) else []
    check("save_lap grava o arquivo da volta em laps/", len(saved) >= 1, str(saved[:2]))
    check("a volta é gravada comprimida",
          bool(saved) and all(f.endswith(".json.gz") for f in saved), str(saved[:2]))
    reread = sm3.telemetry_for(sm3.completed_laps[-1]) if sm3.completed_laps else None
    check("volta salva relê pelo catálogo", bool(reread))
    check("volta salva tem car_x/car_z",
          bool(reread) and len(reread["car_x"]) == n_times,
          f"{len(reread['car_x']) if reread else 0} != {n_times}")
    check("o índice do catálogo foi escrito",
          os.path.exists(os.path.join(folder, "index.json")))
    check("completed_laps não carrega telemetria embutida",
          bool(sm3.completed_laps) and "telemetry" not in sm3.completed_laps[-1])

    # --- 6. Setor fechado alimenta a volta ideal ----------------------------
    ideal = _read_ideal_ghost(folder)
    check("volta ideal foi criada ao fechar o setor 1",
          ideal is not None and ideal["metadata"]["sector_times_ms"][0] > 0,
          str(ideal["metadata"]["sector_times_ms"]) if ideal else "arquivo ausente")
    check("a volta ideal também é gravada comprimida",
          os.path.exists(os.path.join(folder, IDEAL_GHOST_FILENAME)))

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

    # --- 8. Cruzamento da linha de chegada ----------------------------------
    # O AC dá DOIS sinais de fim de volta em quadros diferentes: o cronômetro
    # da volta zera na hora e o iLastTime (tempo oficial) só aparece alguns
    # quadros depois. Fechar a volta nos dois criava uma segunda "volta" de
    # meia dúzia de pontos que entrava no histórico, virava ghost de
    # referência e jogava o Delta Geral para dezenas de segundos.
    TRACK_M = 7000.0
    LAP_MS = 89745

    def _lap_frames(sm, lap_number, last_time, from_pct=0, to_pct=100, lap_ms=LAP_MS):
        """Roda os quadros de uma volta, de from_pct% a to_pct% da pista."""
        for i in range(from_pct, to_pct):
            norm = i / 100.0
            ms = int(norm * lap_ms)
            sm.process_state(_state(
                current_time=f"{ms // 60000}:{(ms % 60000) // 1000:02d}.{ms % 1000:03d}",
                last_time=last_time, best_time=last_time,
                lap_number=lap_number, distance_traveled=norm * TRACK_M,
                track_position=norm, speed_kmh=150.0 + norm * 100,
                sector_index=0 if norm < 0.33 else (1 if norm < 0.66 else 2),
                gas=0.8, brake=0.0, fuel=60.0, g_lat=1.2,
                car_x=norm * 1000, car_z=norm * 500,
            ))

    def _lap_frames_off_track(sm, lap_number, last_time, tyres_out=4,
                              in_pit=False, from_pct=0, to_pct=100,
                              lap_ms=LAP_MS):
        """Igual a _lap_frames, mas com rodas fora da pista no meio da volta."""
        for i in range(from_pct, to_pct):
            norm = i / 100.0
            ms = int(norm * lap_ms)
            sm.process_state(_state(
                current_time=f"{ms // 60000}:{(ms % 60000) // 1000:02d}.{ms % 1000:03d}",
                last_time=last_time, best_time=last_time,
                lap_number=lap_number, distance_traveled=norm * TRACK_M,
                track_position=norm, speed_kmh=150.0 + norm * 100,
                sector_index=0 if norm < 0.33 else (1 if norm < 0.66 else 2),
                gas=0.8, brake=0.0, fuel=60.0, g_lat=1.2,
                car_x=norm * 1000, car_z=norm * 500,
                # Corta a pista só no meio da volta
                tyres_out=tyres_out if 40 <= i <= 45 else 0,
                in_pit=in_pit, in_pit_lane=in_pit,
            ))

    def _cross_line(sm, new_lap_number, prev_time, new_time, delay_frames=3):
        """Cruza a linha: o tempo oficial só chega depois de `delay_frames`."""
        for i in range(8):
            norm = i * 0.005
            lt = new_time if i >= delay_frames else prev_time
            ms = int(norm * LAP_MS)
            sm.process_state(_state(
                current_time=f"0:{ms // 1000:02d}.{ms % 1000:03d}",
                last_time=lt, best_time=lt,
                lap_number=new_lap_number, distance_traveled=norm * TRACK_M,
                track_position=norm, speed_kmh=150.0,
                sector_index=0, gas=0.8, brake=0.0, fuel=59.0, g_lat=0.1,
                car_x=norm * 1000, car_z=norm * 500,
            ))

    sm5 = SessionManager(data_dir=os.path.join(work, "cross"))
    _lap_frames(sm5, 1, "")                       # volta 1 inteira
    _cross_line(sm5, 2, "", "1:29.745")

    check("cruzar a linha fecha a volta UMA vez",
          len(sm5.completed_laps) == 1,
          f"{len(sm5.completed_laps)} voltas em completed_laps")
    check("cruzar a linha cria UMA linha no histórico",
          len(sm5.historic_laps) == 1,
          str([(h['lap_number'], h['total_time']) for h in sm5.historic_laps]))
    if sm5.completed_laps:
        lap = sm5.completed_laps[0]
        check("a volta fechada recebe o tempo oficial do jogo",
              lap["lap_time_str"] == "1:29.745", lap["lap_time_str"])
        check("a volta fechada recebe o número dela, não o da volta nova",
              lap["lap_number"] == 1, str(lap["lap_number"]))
        lap_tele = sm5.telemetry_for(lap)
        check("a volta fechada guarda a telemetria inteira",
              len(lap_tele.get("times", [])) == 100,
              f"{len(lap_tele.get('times', []))} pontos")
        d = lap_tele.get("distance", [])
        check("distância da volta salva é monotônica (nada da volta seguinte)",
              all(d[i] <= d[i + 1] for i in range(len(d) - 1)))
    check("a volta nova não perde os quadros do começo",
          len(sm5.current_lap_data["times"]) == 8,
          f"{len(sm5.current_lap_data['times'])} pontos")

    # Volta inteira PODE virar referência
    check("volta inteira vira referência",
          len(sm5.session_best_lap_ghost["telemetry"]["times"]) == 100,
          f"{len(sm5.session_best_lap_ghost['telemetry']['times'])} pontos no ghost")

    # E o delta contra ela fica coerente (mesma volta => ~0)
    st_delta = _state(current_time="0:44.872", last_time="1:29.745",
                      distance_traveled=TRACK_M * 0.5, track_position=0.5,
                      lap_number=2, sector_index=1)
    sm5.process_state(st_delta, reference_ghost=sm5.session_best_lap_ghost)
    check("delta contra volta inteira fica coerente",
          abs(st_delta.delta_time) < 1.0, f"{st_delta.delta_time:+.3f}s")

    # --- 8b. Deltas de setor no estado --------------------------------------
    # O engenheiro de pista lê s1/s2/s3_delta do estado. S1 e S2 fecham DENTRO
    # da volta; o S3 só existe depois do tempo oficial chegar — quando a volta
    # nova já começou e os tempos correntes foram zerados. Sem tratar esse
    # caso, o S3 fica estruturalmente em 0.0 e o aviso nunca sai.
    sm_sec = SessionManager(data_dir=os.path.join(work, "setores"))
    _lap_frames(sm_sec, 1, "")
    _cross_line(sm_sec, 2, "", "1:29.745")

    # Referência um pouco mais lenta em cada setor: os três deltas ficam < 0
    ref_ms = [t + 200 for t in sm_sec.last_completed_sector_times]
    ghost = {"metadata": {"lap_time_str": "1:30.345", "sector_times_ms": ref_ms},
             "telemetry": sm_sec.session_best_lap_ghost["telemetry"]}

    st_sec = _state(current_time="0:10.000", last_time="1:29.745",
                    distance_traveled=TRACK_M * 0.1, track_position=0.1,
                    lap_number=2, sector_index=0)
    sm_sec.process_state(st_sec, reference_ghost=ghost)

    check("S3 do estado sai do último fechamento (não fica preso em zero)",
          st_sec.s3_delta < 0.0,
          f"s1={st_sec.s1_delta:+.3f} s2={st_sec.s2_delta:+.3f} "
          f"s3={st_sec.s3_delta:+.3f}")
    check("S3 do estado bate com o tempo medido",
          abs(st_sec.s3_delta - (sm_sec.last_completed_sector_times[2]
                                 - ref_ms[2]) / 1000.0) < 0.002,
          f"{st_sec.s3_delta:+.3f}s")

    # --- 9. Volta parcial não pode virar referência -------------------------
    # App aberto no MEIO de uma volta: a telemetria começa em 24% da pista.
    # Ela tem tempo de volta válido (o jogo informa), mas não serve de
    # referência — o delta é interpolado por distância e faltaria o começo.
    sm6 = SessionManager(data_dir=os.path.join(work, "partial"))
    _lap_frames(sm6, 104, "1:29.812", from_pct=24)
    _cross_line(sm6, 105, "1:29.812", "1:29.745")

    check("volta parcial é salva no histórico",
          len(sm6.completed_laps) == 1, f"{len(sm6.completed_laps)}")
    check("volta parcial NÃO vira referência de sessão",
          sm6.session_best_lap_ghost["telemetry"]["times"] == [],
          f"{len(sm6.session_best_lap_ghost['telemetry']['times'])} pontos")
    check("volta parcial NÃO vira Personal Best",
          sm6.best_lap_ghost["telemetry"]["times"] == [],
          f"{len(sm6.best_lap_ghost['telemetry']['times'])} pontos")
    check("volta parcial é marcada como incompleta no arquivo",
          sm6.completed_laps[0]["metadata"].get("full_lap") is False)

    # --- 10. Delta não extrapola fora da referência -------------------------
    # Com uma referência que só cobre 1681..6934 m, pedir o delta a 1085 m
    # (antes do primeiro ponto dela) precisa devolver 0, não a diferença
    # contra o primeiro ponto — era daí que saía o "-51 s" na tela.
    partial_ghost = {
        "metadata": {"lap_time_str": "1:29.745"},
        "telemetry": {
            "times": [21.5 + i * 0.9 for i in range(76)],
            "distance": [1681.0 + i * 70.0 for i in range(76)],
        },
    }
    st_out = _state(current_time="0:13.898", last_time="1:29.745",
                    distance_traveled=1085.0, track_position=0.155,
                    lap_number=105, sector_index=0)
    sm7 = SessionManager(data_dir=os.path.join(work, "extrap"))
    sm7.process_state(st_out, reference_ghost=partial_ghost)
    check("delta fora da faixa da referência fica em zero",
          st_out.delta_time == 0.0, f"{st_out.delta_time:+.3f}s")

    st_in = _state(current_time="0:44.872", last_time="1:29.745",
                   distance_traveled=3500.0, track_position=0.5,
                   lap_number=105, sector_index=1)
    sm7.process_state(st_in, reference_ghost=partial_ghost)
    check("delta dentro da faixa da referência é calculado",
          st_in.delta_time != 0.0, f"{st_in.delta_time:+.3f}s")

    # --- 10b. PB parcial gravado em disco é recusado e se cura --------------
    # Versões anteriores gravavam como Personal Best a volta parcial de quem
    # abriu o app no meio da volta. Esse arquivo produzia delta sem sentido em
    # toda sessão seguinte, e nada o substituía.
    heal_dir = os.path.join(work, "heal")
    heal_folder = os.path.join(heal_dir, "Spa", "Test Car")
    os.makedirs(heal_folder, exist_ok=True)
    _write_json_atomic(os.path.join(heal_folder, "best_lap_ghost.json"), {
        "metadata": {"track": "Spa", "car": "Test Car", "lap_time_str": "1:29.745",
                     "sector_times_ms": [30000, 30000, 29745]},
        "telemetry": {
            "times": [21.5 + i * 0.9 for i in range(76)],
            "distance": [1681.0 + i * 70.0 for i in range(76)],   # começa em 24%
            "speed": [180.0] * 76, "gas": [0.9] * 76, "brake": [0.0] * 76,
            "sector": [0] * 76, "rpm": [6000] * 76,
        },
    })
    sm9 = SessionManager(data_dir=heal_dir)
    sm9.auto_load_ghosts(_state())
    check("PB parcial em disco é recusado na leitura",
          sm9.best_lap_ghost["telemetry"]["times"] == [],
          f"{len(sm9.best_lap_ghost['telemetry']['times'])} pontos")

    # E a próxima volta completa assume o posto, mesmo sem o jogo anunciar
    # um novo recorde (best_time igual do começo ao fim)
    _lap_frames(sm9, 1, "1:29.745")
    _cross_line(sm9, 2, "1:29.745", "1:29.900")
    check("próxima volta completa vira o novo Personal Best",
          len(sm9.best_lap_ghost["telemetry"]["times"]) == 100,
          f"{len(sm9.best_lap_ghost['telemetry']['times'])} pontos")
    # O Personal Best deixou de ser um arquivo sobrescrito: agora é a volta
    # mais rápida DO CATÁLOGO. Bater o recorde não apaga mais o anterior.
    pb_rec = sm9.library.personal_best("Spa", "Test Car")
    check("o Personal Best passa a ser derivado do catálogo",
          pb_rec is not None and pb_rec.full_lap and pb_rec.lap_time_str == "1:29.900",
          str(pb_rec.lap_time_str if pb_rec else None))
    check("o best_lap_ghost.json antigo foi importado e saiu do caminho",
          not os.path.exists(os.path.join(heal_folder, "best_lap_ghost.json"))
          and os.path.exists(os.path.join(heal_folder, "best_lap_ghost.json.imported")))
    # A volta parcial importada continua no catálogo (fixada), mas não pode
    # ser Personal Best: `personal_best` exige volta inteira.
    all_recs = sm9.library.records("Spa", "Test Car")
    check("a volta parcial importada fica no catálogo sem virar referência",
          any(r.pinned and not r.full_lap for r in all_recs),
          str([(r.lap_time_str, r.full_lap, r.pinned) for r in all_recs]))

    # --- 10a. A volta ideal não pode ser o arquivo mais gordo da pasta ------
    # Ela é sintética e fica fora do catálogo (ninguém a deu), então escapava
    # do arredondamento. Com a telemetria crua, o ideal_lap_ghost.json ficava
    # DEZ VEZES maior que a volta comprimida que o originou.
    sm_ideal = SessionManager(data_dir=os.path.join(work, "ideal"))
    _lap_frames(sm_ideal, 1, "")
    _cross_line(sm_ideal, 2, "", "1:30.000")
    ideal_folder = os.path.join(work, "ideal", "Spa", "Test Car")
    ideal_file = os.path.join(ideal_folder, IDEAL_GHOST_FILENAME)
    laps_dir = os.path.join(ideal_folder, "laps")
    check("a volta ideal foi gravada", os.path.exists(ideal_file))
    if os.path.exists(ideal_file) and os.path.isdir(laps_dir):
        ideal_size = os.path.getsize(ideal_file)
        lap_size = sum(os.path.getsize(os.path.join(laps_dir, f))
                       for f in os.listdir(laps_dir))
        # Ela tem o tamanho de uma volta inteira, e agora é gravada como uma
        check("a volta ideal não é maior que a volta real",
              ideal_size <= lap_size * 2,
              f"ideal={ideal_size}B  volta={lap_size}B")
        ideal_json = _read_ideal_ghost(ideal_folder)
        speeds = ideal_json["telemetry"]["speed"] if ideal_json else []
        check("os canais da volta ideal saem arredondados",
              bool(speeds) and all(round(v, 2) == v for v in speeds),
              str(speeds[:3]))

    # A volta ideal do formato antigo é lida e migrada na primeira gravação
    migra_dir = os.path.join(work, "migra")
    migra_folder = os.path.join(migra_dir, "Spa", "Test Car")
    os.makedirs(migra_folder, exist_ok=True)
    _write_json_atomic(os.path.join(migra_folder, IDEAL_GHOST_LEGACY_FILENAME), {
        "metadata": {"track": "Spa", "car": "Test Car",
                     "sector_times_ms": [40000, 40000, 40000], "lap_time_str": ""},
        "telemetry": {"times": [0.0, 1.0], "distance": [0.0, 10.0],
                      "speed": [100.0, 110.0], "gas": [1.0, 1.0],
                      "brake": [0.0, 0.0], "sector": [0, 0], "rpm": [6000, 6100]},
    })
    sm_migra = SessionManager(data_dir=migra_dir)
    sm_migra.auto_load_ghosts(_state())
    check("volta ideal do formato antigo é lida",
          sm_migra.ideal_lap_ghost["metadata"]["sector_times_ms"] == [40000, 40000, 40000],
          str(sm_migra.ideal_lap_ghost["metadata"]["sector_times_ms"]))
    check("o tempo total da volta ideal antiga é recalculado na leitura",
          sm_migra.ideal_lap_ghost["metadata"]["lap_time_str"] == "2:00.000",
          sm_migra.ideal_lap_ghost["metadata"]["lap_time_str"])
    # Bater um setor grava no formato novo e tira o antigo do caminho
    _lap_frames(sm_migra, 1, "")
    _cross_line(sm_migra, 2, "", "1:30.000")
    check("gravar a volta ideal migra para o formato comprimido",
          os.path.exists(os.path.join(migra_folder, IDEAL_GHOST_FILENAME))
          and not os.path.exists(os.path.join(migra_folder, IDEAL_GHOST_LEGACY_FILENAME)))

    # --- 10b. Volta suja: gravada e listada, mas nunca vira referência ------
    # O AC entrega `numberOfTyresOut`; três rodas fora é corte de pista. Antes
    # nada disso era gravado, e uma volta cortando a grama virava Personal Best
    # — a referência que o piloto passava a perseguir era um tempo que ele não
    # conseguiria repetir limpo.
    sm10 = SessionManager(data_dir=os.path.join(work, "sujas"))
    _lap_frames_off_track(sm10, 1, "")
    _cross_line(sm10, 2, "", "1:25.000")

    check("volta suja é gravada e entra no histórico",
          len(sm10.completed_laps) == 1 and len(sm10.historic_laps) == 1,
          f"completed={len(sm10.completed_laps)} historic={len(sm10.historic_laps)}")
    if sm10.completed_laps:
        check("volta suja é marcada como inválida",
              sm10.completed_laps[0]["valid"] is False)
        check("o histórico também sabe que a volta é suja",
              sm10.historic_laps[0].get("valid") is False)
        check("a marca de suja vai para o catálogo",
              sm10.completed_laps[0]["record"].valid is False)
    check("volta suja NÃO vira referência de sessão, mesmo sendo a mais rápida",
          sm10.session_best_lap_ghost["telemetry"]["times"] == [],
          f"{len(sm10.session_best_lap_ghost['telemetry']['times'])} pontos")
    check("volta suja NÃO vira Personal Best",
          sm10.library.personal_best("Spa", "Test Car") is None)

    # A volta limpa seguinte assume, mesmo sendo MAIS LENTA que a suja
    _lap_frames(sm10, 2, "1:25.000")
    _cross_line(sm10, 3, "1:25.000", "1:31.000")
    check("a volta limpa seguinte vira referência, mesmo mais lenta",
          len(sm10.session_best_lap_ghost["telemetry"]["times"]) == 100,
          sm10.session_best_lap_ghost["metadata"].get("lap_time_str"))
    pb10 = sm10.library.personal_best("Spa", "Test Car")
    check("o Personal Best é a volta limpa, não a suja mais rápida",
          pb10 is not None and pb10.lap_time_str == "1:31.000",
          str(pb10.lap_time_str if pb10 else None))

    # No box e no pit lane as quatro rodas ficam "fora" o tempo todo: isso não
    # pode invalidar a volta de saída de box por um motivo errado.
    sm11 = SessionManager(data_dir=os.path.join(work, "boxes"))
    _lap_frames_off_track(sm11, 1, "", tyres_out=4, in_pit=True)
    _cross_line(sm11, 2, "", "1:33.000")
    check("rodas fora dentro do box não invalidam a volta",
          bool(sm11.completed_laps) and sm11.completed_laps[0]["valid"] is True,
          str(sm11.completed_laps[0]["valid"] if sm11.completed_laps else None))

    # Duas rodas na zebra continuam sendo volta limpa
    sm12 = SessionManager(data_dir=os.path.join(work, "zebra"))
    _lap_frames_off_track(sm12, 1, "", tyres_out=2)
    _cross_line(sm12, 2, "", "1:29.000")
    check("duas rodas na zebra não invalidam a volta",
          bool(sm12.completed_laps) and sm12.completed_laps[0]["valid"] is True)

    # --- 11. Volta feita ANTES do app abrir não é volta concluída -----------
    # O jogo já entrega um iLastTime da volta anterior no primeiro quadro; ler
    # isso como "acabei de fechar uma volta" criava uma linha fantasma.
    sm8 = SessionManager(data_dir=os.path.join(work, "startup"))
    _lap_frames(sm8, 42, "1:31.200", from_pct=40, to_pct=60)
    check("tempo de volta anterior ao app não cria volta fantasma",
          len(sm8.completed_laps) == 0 and len(sm8.historic_laps) == 0,
          f"completed={len(sm8.completed_laps)} historic={len(sm8.historic_laps)}")

finally:
    shutil.rmtree(work, ignore_errors=True)

print()
fails = [r for r in results if not r[1]]
for name, ok, detail in results:
    print(f"  [{'OK ' if ok else 'ERRO'}] {name}" + (f"   ({detail})" if detail else ""))
print(f"\n=== {len(results) - len(fails)}/{len(results)} verificacoes passaram ===")
sys.exit(1 if fails else 0)
