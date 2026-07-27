"""
tests/test_assettocorsa_provider.py — Teste do provider do Assetto Corsa
=======================================================================
Faz o papel do jogo: cria os blocos de memória compartilhada com os nomes
que o AC usa, escreve valores conhecidos e verifica se o provider os traduz
corretamente para o TelemetryState. Valida os structs, os offsets de cada
campo e todas as conversões de unidade.

Rode com o Assetto Corsa FECHADO (senão os blocos reais entram no caminho):

    python tests/test_assettocorsa_provider.py

Sai com código 0 se tudo passar, 1 se alguma verificação falhar.
"""
import ctypes, mmap, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.assettocorsa import (
    AssettoCorsaTelemetryProvider, SPageFilePhysics, SPageFileGraphic, SPageFileStatic,
)

# --- "jogo" escreve os blocos --------------------------------------------------
maps = {}
for tag, cls in (("acpmf_physics", SPageFilePhysics),
                 ("acpmf_graphics", SPageFileGraphic),
                 ("acpmf_static", SPageFileStatic)):
    mm = mmap.mmap(-1, ctypes.sizeof(cls), tagname=f"Local\\{tag}")
    maps[tag] = (mm, cls)

p = SPageFilePhysics()
p.packetId = 1
p.gas, p.brake, p.clutch = 0.85, 0.10, 1.0     # clutch=1.0 -> pedal solto
p.fuel = 42.5
p.gear = 5                                      # 4ª marcha
p.rpms = 7200
p.steerAngle = -0.35
p.speedKmh = 187.4
p.accG[0], p.accG[1], p.accG[2] = 1.8, 1.02, -0.9
for i in range(4):
    p.wheelsPressure[i] = 27.0 + i * 0.5
    p.tyreWear[i] = 96.0 - i
    p.tyreCoreTemperature[i] = 88.0 + i
    p.tyreTempI[i], p.tyreTempM[i], p.tyreTempO[i] = 95.0, 90.0, 82.0
    p.suspensionTravel[i] = 0.032
    p.brakeTemp[i] = 480.0 - i * 40
    p.wheelSlip[i] = 0.12
p.tc, p.abs = 0.30, 0.0
p.pitLimiterOn = 0
p.turboBoost = 0.92
p.airTemp, p.roadTemp = 24.5, 33.2
p.carDamage[0], p.carDamage[4] = 3.0, 11.0
p.brakeBias = 0.63
p.finalFF = 0.97
p.drsAvailable, p.drsEnabled = 1, 1
p.kersCharge = 0.4

g = SPageFileGraphic()
g.packetId = 1
g.status = 2                     # AC_LIVE
g.session = 0                    # Practice
g.completedLaps = 4
g.position = 2
g.iCurrentTime = 63_450
g.iLastTime = 92_180
g.iBestTime = 91_005
g.sessionTimeLeft = 1_234_000.0  # ms
g.currentSectorIndex = 1
g.lastSectorTime = 30_120
g.numberOfLaps = 10
g.tyreCompound = "Semislicks (SM)"
g.normalizedCarPosition = 0.42
g.isInPit, g.isInPitLane = 0, 0
g.surfaceGrip = 0.978
g.windSpeed, g.windDirection = 4.0, 205.0
g.flag = 2                       # amarela
g.carCoordinates[0] = 120.5

s = SPageFileStatic()
s.smVersion, s.acVersion = "1.7", "1.16.3"
s.carModel = "ks_porsche_911_gt3_r"
s.track = "spa"
s.trackConfiguration = ""
s.playerName, s.playerSurname = "Joao", "Lamim"
s.sectorCount = 3
s.maxRpm = 9000
s.maxFuel = 120.0
s.maxTurboBoost = 1.2
s.trackSPlineLength = 7004.0
s.hasDRS, s.hasKERS = True, True

for tag, obj in (("acpmf_physics", p), ("acpmf_graphics", g), ("acpmf_static", s)):
    mm, cls = maps[tag]
    mm.seek(0)
    mm.write(bytes(obj))

# --- o dashboard lê ----------------------------------------------------------
prov = AssettoCorsaTelemetryProvider()
assert prov.connect(), "connect() falhou com os blocos criados"

st = prov.get_state()
# packetId precisa mudar para o provider considerar "fresco"
p.packetId = 2
maps["acpmf_physics"][0].seek(0); maps["acpmf_physics"][0].write(bytes(p))
st = prov.get_state()

checks = [
    ("is_connected", st.is_connected, True),
    ("car_name", st.car_name, "Porsche 911 GT3 R"),
    ("track_name", st.track_name, "Spa"),
    ("max_rpm", st.max_rpm, 9000.0),
    ("track_length", st.track_length, 7004.0),
    ("fuel_capacity", st.fuel_capacity, 120.0),
    ("gas", round(st.gas, 2), 0.85),
    ("brake", round(st.brake, 2), 0.10),
    ("clutch (invertido)", round(st.clutch, 2), 0.0),
    ("gear", st.gear, 5),
    ("rpm", st.rpm, 7200),
    ("speed_kmh", round(st.speed_kmh, 1), 187.4),
    ("fuel", round(st.fuel, 1), 42.5),
    ("turbo_boost", round(st.turbo_boost, 2), 0.92),
    ("turbo_boost_max", round(st.turbo_boost_max, 1), 1.2),
    ("g_lat", round(st.g_lat, 1), 1.8),
    ("g_lon", round(st.g_lon, 1), -0.9),
    ("tc_active", st.tc_active, True),
    ("abs_active", st.abs_active, False),
    ("tc_intervention", round(st.tc_intervention, 2), 0.30),
    ("brake_bias", round(st.brake_bias, 2), 0.63),
    ("ffb_level", round(st.ffb_level, 2), 0.97),
    ("drs_active", st.drs_active, True),
    ("tyre_temp[0]", round(st.tyre_temp[0], 1), 88.0),
    ("tyre_pressure[3]", round(st.tyre_pressure[3], 1), 28.5),
    ("tyre_wear[1]", round(st.tyre_wear[1], 1), 95.0),
    ("tyre_temp_inner[0]", round(st.tyre_temp_inner[0], 1), 95.0),
    ("tyre_temp_outer[0]", round(st.tyre_temp_outer[0], 1), 82.0),
    ("suspension_travel[0] mm", round(st.suspension_travel[0], 1), 32.0),
    ("brake_temp[0]", round(st.brake_temp[0], 1), 480.0),
    ("ambient_temp", round(st.ambient_temp, 1), 24.5),
    ("track_temp", round(st.track_temp, 1), 33.2),
    ("car_damage", round(st.car_damage, 1), 11.0),
    ("current_time", st.current_time, "1:03.450"),
    ("last_time", st.last_time, "1:32.180"),
    ("best_time", st.best_time, "1:31.005"),
    ("sector_index", st.sector_index, 1),
    ("last_sector_time", st.last_sector_time, 30120),
    ("lap_number", st.lap_number, 5),
    ("race_position", st.race_position, 2),
    ("track_position", round(st.track_position, 2), 0.42),
    ("distance_traveled", round(st.distance_traveled, 0), 2942.0),
    ("session_type", st.session_type, "Practice"),
    ("session_time_left", round(st.session_time_left, 0), 1234.0),
    ("total_laps", st.total_laps, 10),
    ("tyre_compound", st.tyre_compound, "Semislicks (SM)"),
    ("surface_grip", round(st.surface_grip, 3), 0.978),
    ("wind_speed", round(st.wind_speed, 1), 4.0),
    ("flag", st.flag, "AMARELA"),
    ("in_pit", st.in_pit, False),
    ("car_x", round(st.car_x, 1), 120.5),
    ("player_name", st.player_name, "Joao Lamim"),
    ("has_drs", st.has_drs, True),
    ("sector_count", st.sector_count, 3),
]

fails = [(n, got, exp) for n, got, exp in checks if got != exp]
for n, got, exp in checks:
    mark = "OK " if (n, got, exp) not in fails else "ERRO"
    print(f"  [{mark}] {n:26s} = {got!r}" + ("" if (n, got, exp) not in fails else f"  (esperado {exp!r})"))

# Desconexão: packetId congelado deve virar is_connected=False depois do timeout
prov._last_packet_change -= 3.0
st2 = prov.get_state()
print(f"\n  [{'OK ' if not st2.is_connected else 'ERRO'}] desconexao por packetId congelado -> is_connected={st2.is_connected}")

prov.close()
for mm, _ in maps.values():
    mm.close()

print(f"\n=== {len(checks) - len(fails)}/{len(checks)} verificacoes passaram ===")
sys.exit(1 if fails else 0)
