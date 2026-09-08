"""
Mapeamento e conversão de canais de telemetria para o padrão MoTeC i2.

Mapeia a telemetria padronizada do ApexView (TelemetryState ou dict de volta do
catálogo lap_library) para os canais canônicos do MoTeC i2, garantindo
reamostragem limpa a 60 Hz.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np

from .ld_writer import ChannelData


TARGET_FREQ_HZ = 60.0


def _resample_linear(t_orig: np.ndarray, y_orig: np.ndarray, t_target: np.ndarray) -> np.ndarray:
    """Reamostra um canal para a grade temporal alvo via interpolação linear."""
    if len(t_orig) == 0:
        return np.zeros_like(t_target, dtype=np.float32)
    if len(t_orig) == 1:
        return np.full_like(t_target, y_orig[0], dtype=np.float32)
    return np.interp(t_target, t_orig, y_orig).astype(np.float32)


def _resample_step(t_orig: np.ndarray, y_orig: np.ndarray, t_target: np.ndarray) -> np.ndarray:
    """Reamostra canais discretos (ex.: marchas, setores) com arredondamento."""
    arr = _resample_linear(t_orig, y_orig, t_target)
    return np.round(arr).astype(np.float32)


def _extract_channel_series(
    telemetry: Dict[str, Any],
    keys: Sequence[str],
    n_samples: int,
    default_val: float = 0.0,
) -> np.ndarray:
    """Extrai uma série numérica procurando por chaves alternativas."""
    for key in keys:
        if key in telemetry and telemetry[key]:
            val = telemetry[key]
            if isinstance(val, (list, tuple, np.ndarray)) and len(val) > 0:
                try:
                    arr = np.array(val, dtype=np.float32)
                    if len(arr) == n_samples:
                        return arr
                    elif len(arr) > 0:
                        # Se tiver comprimento diferente, interpola para o comprimento base
                        orig_idx = np.linspace(0, 1, len(arr))
                        base_idx = np.linspace(0, 1, n_samples)
                        return np.interp(base_idx, orig_idx, arr).astype(np.float32)
                except Exception:
                    pass
    return np.full(n_samples, default_val, dtype=np.float32)


def _convert_ac_gear(gear_raw: np.ndarray) -> np.ndarray:
    """
    Converte marchas do Assetto Corsa para o padrão MoTeC.
    AC: 0 = Ré, 1 = Neutro, 2 = 1ª, 3 = 2ª, etc.
    MoTeC: -1 = Ré, 0 = Neutro, 1 = 1ª, 2 = 2ª, etc.
    """
    if len(gear_raw) == 0:
        return gear_raw
    out = np.zeros_like(gear_raw, dtype=np.float32)
    # Se já tem valores negativos, já está no formato padrão
    if np.any(gear_raw < 0):
        return np.round(gear_raw).astype(np.float32)
    # Se tiver valores >= 2 ou 1
    for i, g in enumerate(gear_raw):
        val = int(round(g))
        if val >= 2:
            out[i] = float(val - 1)
        elif val == 1:
            out[i] = 0.0
        elif val == 0:
            out[i] = -1.0
        else:
            out[i] = float(val)
    return out


def map_telemetry_to_motec_channels(
    telemetry: Dict[str, Any],
    freq: float = TARGET_FREQ_HZ,
) -> Tuple[List[ChannelData], float]:
    """
    Mapeia os dados brutos de telemetria em uma lista de ChannelData reamostrados a 60 Hz.

    :param telemetry: Dicionário contendo as séries temporais de canais.
    :param freq: Frequência alvo em Hz (padrão 60 Hz).
    :return: Tupla contendo (lista de ChannelData, duração total em segundos).
    """
    # 1. Obter e normalizar o tempo da volta
    times_raw = telemetry.get("times") or []
    if len(times_raw) < 2:
        # Se não houver série de tempo, cria uma a partir de 'distance' ou gera mock
        n_pts = max(len(telemetry.get("speed") or []), len(telemetry.get("distance") or []), 10)
        t_orig = np.linspace(0.0, float(n_pts) / freq, n_pts, dtype=np.float32)
    else:
        t_orig = np.array(times_raw, dtype=np.float32)
        t_orig = t_orig - t_orig[0]  # Garante início em 0.0

    total_duration_s = float(t_orig[-1]) if len(t_orig) > 1 else 1.0
    if total_duration_s <= 0.0:
        total_duration_s = 1.0

    # 2. Criar grade de tempo uniforme a 60 Hz
    num_target_samples = max(2, int(round(total_duration_s * freq)) + 1)
    t_target = np.linspace(0.0, total_duration_s, num_target_samples, dtype=np.float32)
    n_orig = len(t_orig)

    # 3. Extrair canais da telemetria de entrada
    speed_raw = _extract_channel_series(telemetry, ["speed", "speed_kmh"], n_orig, 0.0)
    gas_raw = _extract_channel_series(telemetry, ["gas", "throttle"], n_orig, 0.0)
    brake_raw = _extract_channel_series(telemetry, ["brake"], n_orig, 0.0)
    steer_raw = _extract_channel_series(telemetry, ["steer", "steer_angle"], n_orig, 0.0)
    gear_raw = _extract_channel_series(telemetry, ["gear"], n_orig, 1.0)
    rpm_raw = _extract_channel_series(telemetry, ["rpm"], n_orig, 0.0)
    glat_raw = _extract_channel_series(telemetry, ["g_lat", "lat_g"], n_orig, 0.0)
    glon_raw = _extract_channel_series(telemetry, ["g_lon", "g_long", "lon_g"], n_orig, 0.0)
    gvert_raw = _extract_channel_series(telemetry, ["g_vert", "vert_g"], n_orig, 1.0)
    abs_raw = _extract_channel_series(telemetry, ["abs_intervention", "abs_active"], n_orig, 0.0)
    tc_raw = _extract_channel_series(telemetry, ["tc_intervention", "tc_active"], n_orig, 0.0)
    dist_raw = _extract_channel_series(telemetry, ["distance", "distance_traveled"], n_orig, 0.0)

    # Normalização de pedais (se vier 0.0-1.0, converte para 0-100%)
    if np.max(gas_raw) <= 1.05 and np.max(gas_raw) > 0.0:
        gas_raw = gas_raw * 100.0
    if np.max(brake_raw) <= 1.05 and np.max(brake_raw) > 0.0:
        brake_raw = brake_raw * 100.0

    # Normalização de ABS / TC (se vier 0.0-1.0, converte para 0-100%)
    if np.max(abs_raw) <= 1.05 and np.max(abs_raw) > 0.0:
        abs_raw = abs_raw * 100.0
    if np.max(tc_raw) <= 1.05 and np.max(tc_raw) > 0.0:
        tc_raw = tc_raw * 100.0

    # Converter marcha do Assetto Corsa
    gear_raw = _convert_ac_gear(gear_raw)

    # Reamostrar canais contínuos
    speed_60 = _resample_linear(t_orig, speed_raw, t_target)
    gas_60 = _resample_linear(t_orig, gas_raw, t_target)
    brake_60 = _resample_linear(t_orig, brake_raw, t_target)
    steer_60 = _resample_linear(t_orig, steer_raw, t_target)
    rpm_60 = _resample_linear(t_orig, rpm_raw, t_target)
    glat_60 = _resample_linear(t_orig, glat_raw, t_target)
    dist_60 = _resample_linear(t_orig, dist_raw, t_target)
    gear_60 = _resample_step(t_orig, gear_raw, t_target)
    abs_60 = _resample_linear(t_orig, abs_raw, t_target)
    tc_60 = _resample_linear(t_orig, tc_raw, t_target)

    # Força G Longitudinal (se não fornecida ou zerada, calcula via aceleração da velocidade)
    if np.all(glon_raw == 0.0) and len(speed_60) > 1:
        # dv/dt em m/s^2 dividido por 9.80665
        v_ms = speed_60 / 3.6
        dt = 1.0 / freq
        accel = np.gradient(v_ms, dt)
        glon_60 = (accel / 9.80665).astype(np.float32)
    else:
        glon_60 = _resample_linear(t_orig, glon_raw, t_target)

    gvert_60 = _resample_linear(t_orig, gvert_raw, t_target)

    # 4. Pneus: Pressões e Temperaturas
    # Podem vir como arrays individuais, listas de 4 rodas, ou padrão
    tyre_press_fl = _extract_tyre_wheel(telemetry, "tyre_pressure", "tyre_press_fl", 0, n_orig, 26.5)
    tyre_press_fr = _extract_tyre_wheel(telemetry, "tyre_pressure", "tyre_press_fr", 1, n_orig, 26.5)
    tyre_press_rl = _extract_tyre_wheel(telemetry, "tyre_pressure", "tyre_press_rl", 2, n_orig, 25.5)
    tyre_press_rr = _extract_tyre_wheel(telemetry, "tyre_pressure", "tyre_press_rr", 3, n_orig, 25.5)

    tyre_temp_fl = _extract_tyre_wheel(telemetry, "tyre_temp", "tyre_temp_fl", 0, n_orig, 85.0)
    tyre_temp_fr = _extract_tyre_wheel(telemetry, "tyre_temp", "tyre_temp_fr", 1, n_orig, 85.0)
    tyre_temp_rl = _extract_tyre_wheel(telemetry, "tyre_temp", "tyre_temp_rl", 2, n_orig, 83.0)
    tyre_temp_rr = _extract_tyre_wheel(telemetry, "tyre_temp", "tyre_temp_rr", 3, n_orig, 83.0)

    p_fl_60 = _resample_linear(t_orig, tyre_press_fl, t_target)
    p_fr_60 = _resample_linear(t_orig, tyre_press_fr, t_target)
    p_rl_60 = _resample_linear(t_orig, tyre_press_rl, t_target)
    p_rr_60 = _resample_linear(t_orig, tyre_press_rr, t_target)

    t_fl_60 = _resample_linear(t_orig, tyre_temp_fl, t_target)
    t_fr_60 = _resample_linear(t_orig, tyre_temp_fr, t_target)
    t_rl_60 = _resample_linear(t_orig, tyre_temp_rl, t_target)
    t_rr_60 = _resample_linear(t_orig, tyre_temp_rr, t_target)

    # 5. Montar lista estruturada de canais MoTeC
    channels: List[ChannelData] = [
        ChannelData("Speed", "Speed", "km/h", speed_60, int(freq)),
        ChannelData("Throttle", "Thr", "%", gas_60, int(freq)),
        ChannelData("Brake", "Brk", "%", brake_60, int(freq)),
        ChannelData("Steer", "Str", "deg", steer_60, int(freq)),
        ChannelData("Gear", "Gear", "", gear_60, int(freq)),
        ChannelData("RPM", "RPM", "rpm", rpm_60, int(freq)),
        ChannelData("G_Lat", "GLat", "G", glat_60, int(freq)),
        ChannelData("G_Long", "GLong", "G", glon_60, int(freq)),
        ChannelData("G_Vert", "GVert", "G", gvert_60, int(freq)),
        ChannelData("ABS_Active", "ABS", "%", abs_60, int(freq)),
        ChannelData("TC_Active", "TC", "%", tc_60, int(freq)),
        ChannelData("Tyre_Press_FL", "P_FL", "psi", p_fl_60, int(freq)),
        ChannelData("Tyre_Press_FR", "P_FR", "psi", p_fr_60, int(freq)),
        ChannelData("Tyre_Press_RL", "P_RL", "psi", p_rl_60, int(freq)),
        ChannelData("Tyre_Press_RR", "P_RR", "psi", p_rr_60, int(freq)),
        ChannelData("Tyre_Temp_FL", "T_FL", "C", t_fl_60, int(freq)),
        ChannelData("Tyre_Temp_FR", "T_FR", "C", t_fr_60, int(freq)),
        ChannelData("Tyre_Temp_RL", "T_RL", "C", t_rl_60, int(freq)),
        ChannelData("Tyre_Temp_RR", "T_RR", "C", t_rr_60, int(freq)),
        ChannelData("Distance", "Dist", "m", dist_60, int(freq)),
        ChannelData("Lap_Time", "Time", "s", t_target, int(freq)),
    ]

    # Canais de coordenadas X e Z (se presentes)
    if "car_x" in telemetry and telemetry["car_x"]:
        car_x = _extract_channel_series(telemetry, ["car_x"], n_orig, 0.0)
        channels.append(ChannelData("Car_X", "CarX", "m", _resample_linear(t_orig, car_x, t_target), int(freq)))
    if "car_z" in telemetry and telemetry["car_z"]:
        car_z = _extract_channel_series(telemetry, ["car_z"], n_orig, 0.0)
        channels.append(ChannelData("Car_Z", "CarZ", "m", _resample_linear(t_orig, car_z, t_target), int(freq)))

    return channels, total_duration_s


def _extract_tyre_wheel(
    telemetry: Dict[str, Any],
    group_key: str,
    wheel_key: str,
    wheel_idx: int,
    n_samples: int,
    default_val: float,
) -> np.ndarray:
    """Extrai canal de uma roda específica [0=FL, 1=FR, 2=RL, 3=RR]."""
    if wheel_key in telemetry and telemetry[wheel_key]:
        return _extract_channel_series(telemetry, [wheel_key], n_samples, default_val)
    if group_key in telemetry and telemetry[group_key]:
        val = telemetry[group_key]
        if isinstance(val, (list, tuple)) and len(val) > 0:
            # Caso 1: lista de 4 floats (valor constante da volta)
            if len(val) == 4 and isinstance(val[wheel_idx], (int, float)):
                return np.full(n_samples, float(val[wheel_idx]), dtype=np.float32)
            # Caso 2: lista de listas ou array 2D
            if len(val) == n_samples and isinstance(val[0], (list, tuple)) and len(val[0]) > wheel_idx:
                return np.array([row[wheel_idx] for row in val], dtype=np.float32)
    return np.full(n_samples, default_val, dtype=np.float32)
