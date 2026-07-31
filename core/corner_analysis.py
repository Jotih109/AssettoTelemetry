"""
core/corner_analysis.py — Análise Curva a Curva (Turn-by-Turn), estilo MoTeC i2
==============================================================================
Esta camada é puramente analítica: recebe os arrays de telemetria já gravados
(o mesmo dicionário que o SessionManager mantém em `current_lap_data` e que os
ghosts guardam em `telemetry`) e devolve, para cada curva da pista, as métricas
que um engenheiro de pista olha primeiro:

    * Ponto de frenagem  — metro em que o freio sai de ~0% e passa de 10%
    * Velocidade mínima  — o V_min do ápice (e onde ele aconteceu)
    * Ponto de retomada  — metro em que o acelerador volta a 100%
    * Delta da curva     — tempo ganho/perdido SÓ naquele trecho

Nada aqui depende de PyQt nem do provider: dá para rodar em teste puro.

Mapeamento das curvas
---------------------
Cada pista tem um arquivo JSON em `track_maps/`, nomeado pelo slug da pista:

    track_maps/autodromo_jose_carlos_pace.json      (mapeamento manual)
    track_maps/autodromo_jose_carlos_pace.auto.json  (detectado automaticamente)

O manual sempre vence. Formato (limites em posição relativa 0.0–1.0 OU em
metros, os dois são aceitos — veja `parse_corner_map`):

    {
      "track": "Autodromo Jose Carlos Pace",
      "track_length": 4309.0,
      "corners": [
        {"name": "S do Senna", "start": 0.150, "end": 0.225, "direction": "L"},
        {"name": "Descida do Lago", "start_m": 2150, "end_m": 2480}
      ]
    }

Quando a pista ainda não tem mapeamento, `detect_corners` faz o fallback:
marca como curva todo trecho em que |G lateral| passa de 0.4 g. O resultado é
gravado como `*.auto.json` para que a numeração das curvas não mude de volta
para volta — e para que você possa renomear/ajustar o arquivo e promovê-lo a
mapeamento manual.
"""

import dataclasses
import json
import math
import os
import re
from typing import List, Optional

from core.paths import get_app_dir

# ---------------------------------------------------------------------------
# Parâmetros da análise
# ---------------------------------------------------------------------------

#: |G lateral| a partir do qual o trecho é considerado curva (fallback automático).
G_LAT_CORNER_THRESHOLD = 0.4
#: Histerese: a curva só termina quando o G lateral cai abaixo deste valor.
#: Sem histerese, uma curva de raio variável viraria três curvas separadas.
G_LAT_RELEASE_THRESHOLD = 0.25
#: Curvas mais curtas que isso são ruído (zigue-zague em reta, correção de volante).
MIN_CORNER_LENGTH_M = 25.0
#: Dois trechos separados por menos que isso são a mesma curva (esses, chicanes).
MERGE_GAP_M = 40.0

#: Freio acima disso conta como "está freando".
BRAKE_ON_THRESHOLD = 0.10
#: E abaixo disso conta como "pé fora do freio" (o "0%" da definição).
BRAKE_OFF_THRESHOLD = 0.02
#: Acelerador a partir disso conta como retomada plena ("100%").
THROTTLE_FULL_THRESHOLD = 0.98

#: Quantos metros ANTES do início da curva procurar o ponto de frenagem.
#: A freada acontece na reta, não dentro da curva.
BRAKE_LOOKBACK_M = 300.0
#: Quantos metros DEPOIS do fim da curva procurar a retomada plena.
THROTTLE_LOOKAHEAD_M = 250.0

CORNER_MAPS_DIRNAME = "track_maps"


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Corner:
    """Uma curva da pista, delimitada por posição relativa (0.0 a 1.0)."""
    index: int                 # 1-based, na ordem da volta
    name: str
    start: float               # posição relativa do início
    end: float                 # posição relativa do fim
    direction: str = ""        # "L" / "R" / "" (desconhecida)

    def start_m(self, track_length: float) -> float:
        return self.start * track_length

    def end_m(self, track_length: float) -> float:
        return self.end * track_length

    def length_m(self, track_length: float) -> float:
        return max(0.0, (self.end - self.start) * track_length)

    def to_dict(self) -> dict:
        d = {"name": self.name, "start": round(self.start, 5), "end": round(self.end, 5)}
        if self.direction:
            d["direction"] = self.direction
        return d


@dataclasses.dataclass
class CornerMap:
    """Conjunto de curvas de uma pista."""
    track: str
    track_length: float
    corners: List[Corner] = dataclasses.field(default_factory=list)
    #: "manual" (arquivo escrito à mão) ou "auto" (detectado pela Força G)
    source: str = "manual"

    def to_dict(self) -> dict:
        return {
            "track": self.track,
            "track_length": round(self.track_length, 1),
            "source": self.source,
            "corners": [c.to_dict() for c in self.corners],
        }


@dataclasses.dataclass
class CornerMetrics:
    """Métricas de UMA curva em UMA volta. Campos None = não foi possível medir."""
    corner: Corner
    braking_point_m: Optional[float] = None
    v_min: Optional[float] = None
    v_min_m: Optional[float] = None
    throttle_point_m: Optional[float] = None
    entry_time: Optional[float] = None
    exit_time: Optional[float] = None

    @property
    def section_time(self) -> Optional[float]:
        """Tempo gasto entre o início e o fim da curva, em segundos."""
        if self.entry_time is None or self.exit_time is None:
            return None
        dt = self.exit_time - self.entry_time
        return dt if dt > 0 else None

    @property
    def has_data(self) -> bool:
        return self.v_min is not None or self.section_time is not None


@dataclasses.dataclass
class CornerComparison:
    """Curva medida na volta analisada, lado a lado com a volta de referência."""
    corner: Corner
    lap: CornerMetrics
    ref: Optional[CornerMetrics] = None

    def _delta(self, attr: str) -> Optional[float]:
        a = getattr(self.lap, attr, None)
        b = getattr(self.ref, attr, None) if self.ref else None
        if a is None or b is None:
            return None
        return a - b

    @property
    def delta_braking_m(self) -> Optional[float]:
        """+ = freou mais tarde (mais fundo) que a referência."""
        return self._delta("braking_point_m")

    @property
    def delta_v_min(self) -> Optional[float]:
        """+ = passou mais rápido no ápice que a referência."""
        return self._delta("v_min")

    @property
    def delta_throttle_m(self) -> Optional[float]:
        """+ = retomou mais tarde que a referência (pior)."""
        return self._delta("throttle_point_m")

    @property
    def delta_time(self) -> Optional[float]:
        """+ = perdeu tempo nesta curva; - = ganhou. Em segundos."""
        a = self.lap.section_time
        b = self.ref.section_time if self.ref else None
        if a is None or b is None:
            return None
        return a - b


# ---------------------------------------------------------------------------
# Persistência dos mapas de curva
# ---------------------------------------------------------------------------

def corner_maps_dir() -> str:
    return get_app_dir(CORNER_MAPS_DIRNAME)


def track_slug(track_name: str) -> str:
    """Nome de pista → nome de arquivo estável e seguro."""
    slug = (track_name or "").strip().lower()
    slug = slug.replace("—", "-").replace("–", "-")
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_") or "unknown_track"


def parse_corner_map(data: dict, track_length: float = 0.0) -> Optional[CornerMap]:
    """
    Converte o dicionário do JSON num CornerMap.

    Aceita limites em posição relativa (`start`/`end`, 0.0–1.0) ou em metros
    (`start_m`/`end_m`) — nesse caso precisa de um comprimento de pista, que
    vem do próprio arquivo ou do argumento. Curvas inválidas são descartadas
    em silêncio; um arquivo escrito à mão com uma linha torta não deve
    derrubar o dashboard.
    """
    if not isinstance(data, dict):
        return None

    length = float(data.get("track_length") or 0.0) or float(track_length or 0.0)
    corners: List[Corner] = []

    for raw in data.get("corners") or []:
        if not isinstance(raw, dict):
            continue
        start = raw.get("start")
        end = raw.get("end")
        if start is None or end is None:
            if length <= 0:
                continue
            start_m, end_m = raw.get("start_m"), raw.get("end_m")
            if start_m is None or end_m is None:
                continue
            try:
                start, end = float(start_m) / length, float(end_m) / length
            except (TypeError, ValueError):
                continue
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= start < end <= 1.0):
            continue
        idx = len(corners) + 1
        corners.append(Corner(
            index=idx,
            name=str(raw.get("name") or f"C{idx}"),
            start=start,
            end=end,
            direction=str(raw.get("direction") or "").upper()[:1],
        ))

    if not corners:
        return None

    corners.sort(key=lambda c: c.start)
    for i, c in enumerate(corners, start=1):
        c.index = i

    return CornerMap(
        track=str(data.get("track") or ""),
        track_length=length,
        corners=corners,
        source=str(data.get("source") or "manual"),
    )


def load_corner_map(track_name: str, track_length: float = 0.0) -> Optional[CornerMap]:
    """
    Carrega o mapeamento da pista. O manual tem prioridade sobre o automático.

    Retorna None quando a pista não tem nenhum dos dois — aí o chamador deve
    rodar `detect_corners` em cima de uma volta e gravar com `save_corner_map`.
    """
    slug = track_slug(track_name)
    possible_slugs = [slug]
    if "interlagos" in slug or "pace" in slug:
        for alias in ("interlagos", "ks_interlagos", "autodromo_jose_carlos_pace", "autodromo_jose_carlos_pace_grand_prix_mock"):
            if alias not in possible_slugs:
                possible_slugs.append(alias)

    base = corner_maps_dir()
    for s in possible_slugs:
        for filename, source in ((f"{s}.json", "manual"), (f"{s}.auto.json", "auto")):
            path = os.path.join(base, filename)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError, UnicodeDecodeError) as e:
                print(f"[CornerAnalysis] Mapa de curvas inválido, ignorando: {path} ({e})")
                continue
            cmap = parse_corner_map(data, track_length)
            if cmap:
                cmap.source = source
                if not cmap.track:
                    cmap.track = track_name
                return cmap
    return None


def save_corner_map(cmap: CornerMap, auto: bool = True) -> Optional[str]:
    """Grava o mapa em `track_maps/`. Retorna o caminho, ou None se falhar."""
    slug = track_slug(cmap.track)
    suffix = ".auto.json" if auto else ".json"
    path = os.path.join(corner_maps_dir(), f"{slug}{suffix}")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cmap.to_dict(), f, indent=2, ensure_ascii=False)
        return path
    except OSError as e:
        print(f"[CornerAnalysis] Falha ao salvar mapa de curvas {path}: {e}")
        return None


# ---------------------------------------------------------------------------
# Fallback: detecção automática por Força G lateral
# ---------------------------------------------------------------------------

def lateral_g_series(telemetry: dict) -> List[float]:
    """
    Série de |G lateral| ponto a ponto.

    Usa o canal `g_lat` quando a volta foi gravada com ele. Voltas antigas não
    têm esse canal — nesse caso o G lateral é reconstruído da geometria:
    a_lat = v² · κ, onde κ é a curvatura do traçado (car_x/car_z) obtida pelo
    círculo que passa por três pontos consecutivos.
    """
    g_lat = telemetry.get("g_lat") or []
    times = telemetry.get("times") or []
    if len(g_lat) >= len(times) > 0:
        return [abs(float(v)) for v in g_lat[:len(times)]]

    xs = telemetry.get("car_x") or []
    zs = telemetry.get("car_z") or []
    speeds = telemetry.get("speed") or []
    n = min(len(xs), len(zs), len(speeds))
    if n < 3:
        return [0.0] * len(times)

    out = [0.0] * n
    for i in range(1, n - 1):
        x0, z0 = xs[i - 1], zs[i - 1]
        x1, z1 = xs[i], zs[i]
        x2, z2 = xs[i + 1], zs[i + 1]
        # Área do triângulo (×2) e os três lados: κ = 4·Área / (a·b·c)
        cross = (x1 - x0) * (z2 - z0) - (z1 - z0) * (x2 - x0)
        a = math.hypot(x1 - x0, z1 - z0)
        b = math.hypot(x2 - x1, z2 - z1)
        c = math.hypot(x2 - x0, z2 - z0)
        denom = a * b * c
        if denom < 1e-6:
            continue
        curvature = abs(2.0 * cross) / denom
        v_ms = max(0.0, speeds[i]) / 3.6
        out[i] = (v_ms * v_ms * curvature) / 9.81
    if n >= 2:
        out[0], out[-1] = out[1], out[-2]

    # Suaviza: curvatura de três pontos é sensível ao ruído das coordenadas
    return _moving_average(out, 5)


def _moving_average(values: List[float], window: int) -> List[float]:
    if window <= 1 or len(values) < window:
        return list(values)
    half = window // 2
    out = []
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def detect_corners(telemetry: dict, track_length: float = 0.0,
                   threshold: float = G_LAT_CORNER_THRESHOLD,
                   release: float = G_LAT_RELEASE_THRESHOLD) -> List[Corner]:
    """
    Detecta curvas por Força G lateral, com histerese, filtro de comprimento
    mínimo e fusão de trechos vizinhos (esses e chicanes contam como uma curva).

    Retorna curvas nomeadas C1, C2, ... na ordem da volta.
    """
    distances = telemetry.get("distance") or []
    if len(distances) < 3:
        return []

    length = float(track_length or 0.0) or max(distances)
    if length <= 0:
        return []

    g_abs = _moving_average(lateral_g_series(telemetry), 15)
    n = min(len(g_abs), len(distances))
    if n < 3:
        return []

    # Sinal do G lateral (para descobrir a mão da curva) — só quando gravado
    g_signed = telemetry.get("g_lat") or []

    # Cada trecho guarda os limites com histerese (start/end) e também o
    # "núcleo" — a parte que realmente passou do limiar. O filtro de tamanho
    # olha o núcleo: a histerese estica o trecho de propósito, e um pico curto
    # de ruído esticado não pode passar por curva.
    spans = []   # (start_m, end_m, core_start_m, core_end_m, soma_do_g_com_sinal)
    open_start = None
    core_start = core_end = 0.0
    signed_sum = 0.0
    for i in range(n):
        if open_start is None:
            if g_abs[i] >= threshold:
                open_start = distances[i]
                core_start = core_end = distances[i]
                signed_sum = 0.0
        else:
            if i < len(g_signed):
                signed_sum += float(g_signed[i])
            if g_abs[i] >= threshold:
                core_end = distances[i]
            if g_abs[i] < release:
                spans.append((open_start, distances[i], core_start, core_end, signed_sum))
                open_start = None
    if open_start is not None:
        spans.append((open_start, distances[n - 1], core_start, core_end, signed_sum))

    # Funde trechos separados por menos de MERGE_GAP_M
    merged = []
    for start_m, end_m, c_start, c_end, sig in spans:
        if merged and start_m - merged[-1][1] <= MERGE_GAP_M:
            prev_start, _, prev_c_start, _, prev_sig = merged[-1]
            merged[-1] = (prev_start, end_m, prev_c_start, c_end, prev_sig + sig)
        else:
            merged.append((start_m, end_m, c_start, c_end, sig))

    corners = []
    for start_m, end_m, c_start, c_end, sig in merged:
        if c_end - c_start < MIN_CORNER_LENGTH_M:
            continue
        idx = len(corners) + 1
        direction = ""
        if abs(sig) > 1e-6:
            direction = "R" if sig > 0 else "L"
        corners.append(Corner(
            index=idx,
            name=f"C{idx}",
            start=max(0.0, min(1.0, start_m / length)),
            end=max(0.0, min(1.0, end_m / length)),
            direction=direction,
        ))
    return corners


def build_auto_corner_map(track_name: str, telemetry: dict,
                          track_length: float = 0.0) -> Optional[CornerMap]:
    """Detecta as curvas de uma volta e devolve um CornerMap pronto para salvar."""
    distances = telemetry.get("distance") or []
    length = float(track_length or 0.0) or (max(distances) if distances else 0.0)
    corners = detect_corners(telemetry, length)
    if not corners:
        return None
    return CornerMap(track=track_name or "", track_length=length,
                     corners=corners, source="auto")


# ---------------------------------------------------------------------------
# Métricas por curva
# ---------------------------------------------------------------------------

def _interp_at(distances: List[float], values: List[float],
               target_m: float) -> Optional[float]:
    """Valor de um canal na distância pedida, interpolado linearmente."""
    n = min(len(distances), len(values))
    if n == 0:
        return None
    if target_m <= distances[0]:
        return float(values[0])
    if target_m >= distances[n - 1]:
        return float(values[n - 1])
    import bisect
    i = bisect.bisect_left(distances, target_m, 0, n)
    if i <= 0:
        return float(values[0])
    d0, d1 = distances[i - 1], distances[i]
    v0, v1 = values[i - 1], values[i]
    if d1 == d0:
        return float(v0)
    ratio = (target_m - d0) / (d1 - d0)
    return float(v0 + ratio * (v1 - v0))


def time_at_distance(telemetry: dict, target_m: float) -> Optional[float]:
    """
    Instante (s) em que a volta passou por uma distância (m).

    É o que traduz os limites de curva — definidos em distância — para o eixo
    de tempo dos gráficos.
    """
    return _interp_at(telemetry.get("distance") or [],
                      telemetry.get("times") or [], target_m)


def _index_range(distances: List[float], from_m: float, to_m: float) -> range:
    """Índices cuja distância cai em [from_m, to_m]."""
    import bisect
    lo = bisect.bisect_left(distances, from_m)
    hi = bisect.bisect_right(distances, to_m)
    return range(max(0, lo), min(len(distances), hi))


def analyze_corner(telemetry: dict, corner: Corner, track_length: float = 0.0,
                   search_from_m: float = None,
                   search_to_m: float = None) -> CornerMetrics:
    """
    Calcula as quatro métricas da curva sobre os arrays de uma volta.

    `search_from_m` / `search_to_m` limitam as janelas de busca do ponto de
    frenagem e do ponto de retomada — normalmente o fim da curva anterior e o
    início da próxima. Sem esses limites, duas curvas em sequência acabariam
    apontando a MESMA freada (a janela de uma invade o trecho da outra).
    `analyze_lap` e `compare_laps` já passam isso pela lista de curvas.
    """
    distances = telemetry.get("distance") or []
    times = telemetry.get("times") or []
    speeds = telemetry.get("speed") or []
    brakes = telemetry.get("brake") or []
    gases = telemetry.get("gas") or []

    metrics = CornerMetrics(corner=corner)
    if len(distances) < 2:
        return metrics

    length = float(track_length or 0.0) or max(distances)
    if length <= 0:
        return metrics

    start_m = corner.start_m(length)
    end_m = corner.end_m(length)

    metrics.entry_time = _interp_at(distances, times, start_m)
    metrics.exit_time = _interp_at(distances, times, end_m)

    # --- Velocidade mínima (ápice) ---
    apex_idx = None
    for i in _index_range(distances, start_m, end_m):
        if i >= len(speeds):
            break
        if metrics.v_min is None or speeds[i] < metrics.v_min:
            metrics.v_min = float(speeds[i])
            metrics.v_min_m = float(distances[i])
            apex_idx = i

    # --- Ponto de frenagem: primeiro cruzamento 0% -> >10% antes do ápice ---
    # A freada acontece na reta anterior, por isso a janela começa atrás da
    # entrada da curva. Exigir que os pontos anteriores estejam com o pé fora
    # do freio evita marcar o meio de uma freada longa como "o ponto".
    brake_from = max(0.0, start_m - BRAKE_LOOKBACK_M)
    if search_from_m is not None:
        brake_from = max(brake_from, float(search_from_m))
    brake_to = metrics.v_min_m if metrics.v_min_m is not None else end_m
    for i in _index_range(distances, brake_from, brake_to):
        if i >= len(brakes) or i == 0:
            continue
        if brakes[i] > BRAKE_ON_THRESHOLD and brakes[i - 1] <= BRAKE_OFF_THRESHOLD:
            metrics.braking_point_m = float(distances[i])
            break

    # --- Ponto de retomada: acelerador de volta a 100% saindo da curva ---
    throttle_from = metrics.v_min_m if metrics.v_min_m is not None else start_m
    throttle_to = end_m + THROTTLE_LOOKAHEAD_M
    if search_to_m is not None:
        throttle_to = min(throttle_to, float(search_to_m))
    for i in _index_range(distances, throttle_from, throttle_to):
        if i >= len(gases):
            break
        if gases[i] >= THROTTLE_FULL_THRESHOLD:
            metrics.throttle_point_m = float(distances[i])
            break

    if apex_idx is None:
        metrics.v_min = None
        metrics.v_min_m = None

    return metrics


def _search_bounds(corners: List[Corner], i: int, track_length: float):
    """
    Até onde a busca da curva `i` pode ir para trás e para frente: o fim da
    curva anterior e o início da próxima. É o que impede duas curvas seguidas
    de reivindicarem a mesma freada ou a mesma retomada.
    """
    from_m = corners[i - 1].end_m(track_length) if i > 0 else None
    to_m = corners[i + 1].start_m(track_length) if i + 1 < len(corners) else None
    return from_m, to_m


def analyze_lap(telemetry: dict, corners: List[Corner],
                track_length: float = 0.0) -> List[CornerMetrics]:
    """Roda `analyze_corner` para todas as curvas da pista."""
    length = float(track_length or 0.0) or max(telemetry.get("distance") or [0.0])
    out = []
    for i, corner in enumerate(corners):
        from_m, to_m = _search_bounds(corners, i, length)
        out.append(analyze_corner(telemetry, corner, length, from_m, to_m))
    return out


def compare_laps(lap_telemetry: dict, ref_telemetry: dict, corners: List[Corner],
                 track_length: float = 0.0) -> List[CornerComparison]:
    """
    Compara a volta analisada com a de referência, curva por curva.

    `ref_telemetry` pode ser vazio: nesse caso as métricas da volta são
    calculadas normalmente e os deltas ficam None (a UI mostra "--").
    """
    ref_has_data = bool((ref_telemetry or {}).get("distance"))
    length = float(track_length or 0.0) or max(lap_telemetry.get("distance") or [0.0])
    out = []
    for i, corner in enumerate(corners):
        from_m, to_m = _search_bounds(corners, i, length)
        lap_m = analyze_corner(lap_telemetry, corner, length, from_m, to_m)
        ref_m = (analyze_corner(ref_telemetry, corner, length, from_m, to_m)
                 if ref_has_data else None)
        out.append(CornerComparison(corner=corner, lap=lap_m, ref=ref_m))
    return out


def worst_corner(comparisons: List[CornerComparison]) -> Optional[CornerComparison]:
    """A curva onde mais tempo foi perdido — o primeiro lugar para trabalhar."""
    losses = [c for c in comparisons if c.delta_time is not None and c.delta_time > 0]
    if not losses:
        return None
    return max(losses, key=lambda c: c.delta_time)
