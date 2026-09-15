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

Quando a pista ainda não tem mapeamento, `detect_corners` faz o fallback: acha
as curvas pelo G lateral, em quatro passos (histerese, fusão, corte na troca
de mão, corte no vale). O resultado é gravado como `*.auto.json` para que a
numeração das curvas não mude de volta para volta — e para que você possa
renomear/ajustar o arquivo e promovê-lo a mapeamento manual.

Onde, em palavras
-----------------
`corner_at` traduz uma metragem da volta em `(curva, fase)`, e `where_phrase`
transforma isso na frase que o engenheiro fala: *"na freada da Curva 7"*,
*"na saída do Pinheirinho"*. As FASES são o que torna um recado acionável —
uma freada acontece na reta ANTES da curva, e um localizador que só reconheça
o interior da curva devolve "não sei" justamente para o evento que o piloto
mais precisa situar.
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
#: Dois trechos separados por menos que isso são a mesma curva — mas SÓ se
#: forem para o mesmo lado (ver `detect_corners`).
MERGE_GAP_M = 40.0

#: Base, em metros, sobre a qual a curvatura do traçado é medida quando o G
#: lateral tem de ser reconstruído da geometria. Ver `signed_lateral_g_series`.
CURVATURE_SPACING_M = 12.0
#: Teto do G lateral reconstruído. Acima disto é ruído numérico, não medida.
MAX_PLAUSIBLE_G = 5.0

#: Dentro de um trecho contínuo de curva, um vale de |G| que caia abaixo desta
#: fração do menor dos dois picos vizinhos separa duas curvas. É o que divide
#: uma sequência ligada — Beausset/Bendor, o esse do Senna — em curvas
#: distintas sem depender de o G chegar a zero entre elas, o que numa
#: sequência rápida nunca acontece.
VALLEY_RATIO = 0.72
#: E o vale só conta se os dois lados tiverem pelo menos este tamanho, senão
#: uma oscilação no meio de uma curva longa a partiria em duas.
MIN_SPLIT_LENGTH_M = 30.0

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

#: Quantas voltas LIMPAS confirmam o mapa de uma pista.
#:
#: Com uma volta só não existe consenso: o que aquela volta mostrar é lei,
#: inclusive um corte de pista. Com três, uma curva precisa aparecer em duas
#: para entrar — um corte aparece numa volta e é rejeitado, e uma curva de
#: verdade aparece em todas e fica.
CONSENSUS_LAPS = 3
#: Resolução da votação, em metros. Fina o bastante para não borrar limites de
#: curva (uma curva tem 80 m ou mais) e grossa o bastante para a votação de
#: uma pista de 7 km custar mil e poucas posições.
CONSENSUS_STEP_M = 5.0

#: Versão do detector automático. Sobe sempre que o algoritmo passa a achar
#: curvas diferentes das que achava antes.
#:
#: É o que faz uma correção do detector CHEGAR em quem já usou o app: um
#: `*.auto.json` gravado por uma versão anterior é tratado como provisório e
#: refeito na primeira volta inteira. Sem isso o arquivo antigo ficaria para
#: sempre — ele existe e cobre a volta toda, então nada pediria para refazê-lo,
#: e a pista continuaria com as três curvas gigantes que o detector antigo
#: entregava.
#:
#: 1 -> detecção original (histerese + fusão)
#: 2 -> corte na troca de mão e no vale; curvatura medida sobre base de 12 m
#: 3 -> mapa por consenso de voltas limpas (corte de pista deixa de entrar)
DETECTOR_VERSION = 3

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
    #: Fração da volta coberta pela telemetria de onde as curvas foram
    #: detectadas. Um mapa tirado de meia volta só conhece metade das curvas —
    #: ele serve por enquanto, mas precisa ser refeito quando aparecer uma
    #: volta inteira.
    coverage: float = 1.0
    #: Versão do detector que gerou este mapa. Só vale para `source == "auto"`;
    #: um mapa escrito à mão não tem detector nenhum por trás.
    detector_version: int = DETECTOR_VERSION
    #: De quantas voltas limpas este mapa saiu. Enquanto for menos que
    #: CONSENSUS_LAPS o mapa já serve, mas continua sendo refeito conforme
    #: voltas limpas novas aparecem.
    laps_used: int = CONSENSUS_LAPS

    @property
    def is_provisional(self) -> bool:
        """
        Este mapa automático precisa ser refeito?

        Dois motivos: ele saiu de uma volta INCOMPLETA (e por isso só conhece
        as curvas de um pedaço da pista), ou saiu de uma versão ANTERIOR do
        detector (e por isso pode estar simplesmente errado). Mapa manual
        nunca é provisório — foi você que escreveu.
        """
        if self.source != "auto":
            return False
        return (self.coverage < 0.95
                or self.detector_version < DETECTOR_VERSION
                or self.laps_used < CONSENSUS_LAPS)

    def to_dict(self) -> dict:
        return {
            "track": self.track,
            "track_length": round(self.track_length, 1),
            "source": self.source,
            "coverage": round(self.coverage, 4),
            "detector_version": self.detector_version,
            "laps_used": self.laps_used,
            "corners": [c.to_dict() for c in self.corners],
        }


@dataclasses.dataclass
class CornerMetrics:
    """Métricas de UMA curva em UMA volta. Campos None = não foi possível medir."""
    corner: Corner
    braking_point_m: Optional[float] = None
    braking_speed: Optional[float] = None
    v_min: Optional[float] = None
    v_min_m: Optional[float] = None
    throttle_point_m: Optional[float] = None
    throttle_pct: Optional[float] = None
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

    coverage = data.get("coverage")
    versao = data.get("detector_version")
    voltas = data.get("laps_used")
    return CornerMap(
        track=str(data.get("track") or ""),
        track_length=length,
        corners=corners,
        source=str(data.get("source") or "manual"),
        coverage=float(coverage) if isinstance(coverage, (int, float)) else 1.0,
        # Arquivo sem o campo é anterior ao versionamento: vale como versão 1,
        # que é justamente a que precisa ser refeita.
        detector_version=int(versao) if isinstance(versao, int) else 1,
        # Arquivo sem o campo saiu de uma volta só — que é o que o consenso
        # existe para corrigir.
        laps_used=int(voltas) if isinstance(voltas, int) else 1,
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
                # Mapa automático gravado antes de existir o campo `coverage`:
                # não há como saber de que parte da volta ele saiu, então vale
                # como provisório e é refeito na primeira volta inteira.
                if source == "auto" and "coverage" not in data:
                    cmap.coverage = 0.0
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

def signed_lateral_g_series(telemetry: dict) -> List[float]:
    """
    Série de G lateral COM SINAL (+ = um lado, - = o outro), ponto a ponto.

    O sinal é o que separa uma chicane de uma curva longa: `detect_corners`
    corta o trecho onde ele troca. Por isso a reconstrução geométrica também
    devolve sinal — antes só o canal `g_lat` tinha essa informação, e volta
    antiga saía com a mão da curva sempre em branco.

    Usa o canal `g_lat` quando a volta foi gravada com ele. Sem o canal, o G
    lateral vem da geometria do traçado (car_x/car_z): a_lat = v² · κ.

    A curvatura κ NÃO é medida entre amostras vizinhas. A 60 Hz e 100 km/h
    duas amostras ficam a 46 cm uma da outra, e as coordenadas são gravadas
    arredondadas ao centímetro: numa reta os três pontos são quase colineares,
    o ruído de arredondamento domina o cálculo e a curvatura estimada explode.
    Medindo sobre uma base fixa de CURVATURE_SPACING_M metros o ruído vira
    ~1 cm em 12 m — irrelevante — e a estimativa passa a valer também na reta.

    Em vez do círculo por três pontos, κ = dθ/ds: a variação de direção do
    traçado por metro percorrido. É a mesma grandeza, mas condicionada muito
    melhor — não tem denominador que tenda a zero quando os pontos se alinham.
    """
    times = telemetry.get("times") or []
    g_lat = telemetry.get("g_lat") or []
    if len(g_lat) >= len(times) > 0:
        return [float(v) for v in g_lat[:len(times)]]

    xs = telemetry.get("car_x") or []
    zs = telemetry.get("car_z") or []
    speeds = telemetry.get("speed") or []
    n = min(len(xs), len(zs), len(speeds))
    if n < 3:
        return [0.0] * len(times)

    # Comprimento de arco medido nas PRÓPRIAS coordenadas, não no canal
    # `distance` do jogo. A curvatura é dθ/ds ao longo desta polilinha: usar
    # uma escala de distância vinda de outra fonte mistura dois sistemas de
    # medida, e qualquer discordância entre eles (canal em outra escala, volta
    # com o traçado truncado) apareceria como curvatura errada por um fator
    # constante — G lateral fantasma numa reta, ou curva de verdade não vista.
    s = [0.0] * n
    for i in range(1, n):
        s[i] = s[i - 1] + math.hypot(xs[i] - xs[i - 1], zs[i] - zs[i - 1])

    out = [0.0] * n
    j_lo = 0
    j_hi = 0
    for i in range(n):
        # Índices a ~CURVATURE_SPACING_M metros para trás e para frente
        while j_lo < i and s[i] - s[j_lo] > CURVATURE_SPACING_M:
            j_lo += 1
        lo = j_lo if j_lo < i else max(0, i - 1)
        if j_hi < i:
            j_hi = i
        while j_hi < n - 1 and s[j_hi] - s[i] < CURVATURE_SPACING_M:
            j_hi += 1
        hi = j_hi if j_hi > i else min(n - 1, i + 1)
        if hi <= lo:
            continue

        # Direção do traçado antes e depois de i, medida por corda longa
        dx_in, dz_in = xs[i] - xs[lo], zs[i] - zs[lo]
        dx_out, dz_out = xs[hi] - xs[i], zs[hi] - zs[i]
        if (dx_in == 0.0 and dz_in == 0.0) or (dx_out == 0.0 and dz_out == 0.0):
            continue
        dtheta = math.atan2(dz_out, dx_out) - math.atan2(dz_in, dx_in)
        # Normaliza para (-pi, pi]: sem isto a passagem por ±180° viraria uma
        # curvatura gigante no meio de uma reta
        while dtheta > math.pi:
            dtheta -= 2.0 * math.pi
        while dtheta <= -math.pi:
            dtheta += 2.0 * math.pi

        # A distância entre os pontos médios das duas cordas
        ds = (s[hi] - s[lo]) / 2.0
        if ds < 1e-3:
            continue
        curvature = dtheta / ds
        v_ms = max(0.0, float(speeds[i])) / 3.6
        g = (v_ms * v_ms * curvature) / 9.81
        # Teto físico: nenhum GT3 faz 5 g lateral. Um resíduo numérico acima
        # disto é erro de medida, e deixá-lo passar contaminaria o limiar de
        # detecção da volta inteira.
        out[i] = max(-MAX_PLAUSIBLE_G, min(MAX_PLAUSIBLE_G, g))

    # Suaviza o valor COM SINAL. Suavizar o módulo (como antes) retifica o
    # ruído: a média de um ruído já positivo é um degrau positivo, e a volta
    # inteira aparecia acima do limiar de curva. Com sinal, o ruído cancela.
    return _moving_average(out, 5)


def lateral_g_series(telemetry: dict) -> List[float]:
    """Série de |G lateral| ponto a ponto (o módulo do sinal com sinal)."""
    return [abs(v) for v in signed_lateral_g_series(telemetry)]


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


def _core_range(g_abs: List[float], a: int, b: int,
                threshold: float) -> Optional[tuple]:
    """
    Sub-trecho de [a, b] que realmente passou do limiar.

    A histerese estica o trecho de propósito (a curva só "fecha" quando o G
    cai bem abaixo), então o filtro de tamanho mínimo tem de olhar o núcleo —
    senão um pico curto de ruído, esticado pela histerese, passa por curva.
    """
    primeiro = ultimo = None
    for i in range(a, b + 1):
        if g_abs[i] >= threshold:
            if primeiro is None:
                primeiro = i
            ultimo = i
    return (primeiro, ultimo) if primeiro is not None else None


def _side(g_signed: List[float], a: int, b: int) -> int:
    """Para que lado o trecho vira: +1, -1, ou 0 se não der para dizer."""
    soma = sum(g_signed[a:b + 1])
    if soma > 1e-6:
        return 1
    if soma < -1e-6:
        return -1
    return 0


def _split_by_side(g_signed: List[float], g_abs: List[float],
                   a: int, b: int, threshold: float) -> List[int]:
    """
    Pontos de corte onde o G lateral troca de LADO.

    Uma troca de mão nunca é a mesma curva: é uma chicane, um esse. Sem este
    corte os dois arcos viravam uma curva só — e com a mão errada, porque os
    sinais opostos se cancelavam na soma. O coach acabava usando um nome só
    ("Curva 3") para dois pontos de freada completamente diferentes.

    Só amostras acima do limiar votam: perto do zero o sinal oscila por
    ruído, e cortar ali partiria a reta em pedaços.
    """
    cortes = []
    lado = 0
    ultimo_forte = a
    for i in range(a, b + 1):
        if g_abs[i] < threshold:
            continue
        atual = 1 if g_signed[i] > 0 else -1
        if lado == 0:
            lado = atual
        elif atual != lado:
            # A fronteira é o |G| mínimo entre o último arco do lado antigo e
            # este: é ali que o carro passou reto de um lado para o outro.
            j = min(range(ultimo_forte, i + 1), key=lambda k: g_abs[k])
            cortes.append(j)
            lado = atual
        ultimo_forte = i
    return cortes


def _split_by_valley(g_abs: List[float], distances: List[float],
                     a: int, b: int, out: List[int]) -> None:
    """
    Corta o trecho no vale de |G| mais profundo, e repete nos dois pedaços.

    Numa sequência ligada — Beausset e Bendor, Pinheirinho e Bico de Pato — o
    G lateral nunca chega a zero entre as curvas, então a histerese sozinha
    entrega um bloco único de 400 m. Mas existe um VALE: o G alivia entre um
    ápice e o outro. Um vale que caia abaixo de VALLEY_RATIO do menor dos dois
    picos vizinhos é a fronteira entre duas curvas.

    Pega sempre o vale mais fundo primeiro e recorre, para que uma sequência
    de três curvas seja dividida nas duas fronteiras reais em vez de no
    primeiro respiro que aparecer.
    """
    if distances[b] - distances[a] < 2.0 * MIN_SPLIT_LENGTH_M:
        return
    # O corte tem de deixar os dois lados com tamanho de curva de verdade
    candidatos = [k for k in range(a, b + 1)
                  if distances[k] - distances[a] >= MIN_SPLIT_LENGTH_M
                  and distances[b] - distances[k] >= MIN_SPLIT_LENGTH_M]
    if not candidatos:
        return
    vale = min(candidatos, key=lambda k: g_abs[k])
    pico_esq = max(g_abs[a:vale + 1], default=0.0)
    pico_dir = max(g_abs[vale:b + 1], default=0.0)
    if g_abs[vale] > VALLEY_RATIO * min(pico_esq, pico_dir):
        return                                  # alívio raso: é uma curva só
    out.append(vale)
    _split_by_valley(g_abs, distances, a, vale, out)
    _split_by_valley(g_abs, distances, vale, b, out)


def detect_corners(telemetry: dict, track_length: float = 0.0,
                   threshold: float = G_LAT_CORNER_THRESHOLD,
                   release: float = G_LAT_RELEASE_THRESHOLD) -> List[Corner]:
    """
    Detecta as curvas da pista pelo G lateral.

    São quatro passos, e os dois últimos existem porque os dois primeiros,
    sozinhos, entregavam a pista inteira como três curvas gigantes:

      1. **Histerese** — o trecho abre quando |G| passa de `threshold` e só
         fecha quando cai abaixo de `release`. Sem isso uma curva de raio
         variável viraria três.
      2. **Fusão** de trechos vizinhos separados por menos de MERGE_GAP_M, e
         SÓ para o mesmo lado: dois arcos do mesmo sentido com um respiro no
         meio são uma curva; uma troca de mão nunca é.
      3. **Corte na troca de mão** — chicanes e esses viram curvas separadas,
         cada uma com a sua mão correta.
      4. **Corte no vale** — numa sequência ligada o G alivia entre os ápices
         sem nunca chegar a zero; esse alívio é a fronteira entre as curvas.

    Retorna curvas nomeadas "Curva 1", "Curva 2", ... na ordem da volta.
    """
    distances = telemetry.get("distance") or []
    if len(distances) < 3:
        return []

    length = float(track_length or 0.0) or max(distances)
    if length <= 0:
        return []

    # Com sinal: é o sinal que separa chicane de curva longa.
    g_signed = _moving_average(signed_lateral_g_series(telemetry), 15)
    n = min(len(g_signed), len(distances))
    if n < 3:
        return []
    g_signed = g_signed[:n]
    g_abs = [abs(v) for v in g_signed]

    # --- 1. Trechos com histerese, em índices ---
    spans = []
    aberto = None
    for i in range(n):
        if aberto is None:
            if g_abs[i] >= threshold:
                aberto = i
        elif g_abs[i] < release:
            spans.append((aberto, i))
            aberto = None
    if aberto is not None:
        spans.append((aberto, n - 1))

    # --- 2. Fusão de vizinhos, só do mesmo lado ---
    fundidos = []
    for a, b in spans:
        if fundidos:
            pa, pb = fundidos[-1]
            gap = distances[a] - distances[pb]
            mesmo_lado = _side(g_signed, pa, pb) == _side(g_signed, a, b)
            if gap <= MERGE_GAP_M and mesmo_lado:
                fundidos[-1] = (pa, b)
                continue
        fundidos.append((a, b))

    # --- 3 e 4. Cortes internos: troca de mão, depois vales ---
    pedacos = []
    for a, b in fundidos:
        limites = [a] + sorted(_split_by_side(g_signed, g_abs, a, b, threshold)) + [b]
        for k in range(len(limites) - 1):
            sub_ini, sub_fim = limites[k], limites[k + 1]
            vales = []
            _split_by_valley(g_abs, distances, sub_ini, sub_fim, vales)
            sub = [sub_ini] + sorted(vales) + [sub_fim]
            for m in range(len(sub) - 1):
                pedacos.append((sub[m], sub[m + 1]))

    # --- Monta as curvas ---
    corners = []
    for a, b in pedacos:
        nucleo = _core_range(g_abs, a, b, threshold)
        if nucleo is None:
            continue
        if distances[nucleo[1]] - distances[nucleo[0]] < MIN_CORNER_LENGTH_M:
            continue
        idx = len(corners) + 1
        lado = _side(g_signed, nucleo[0], nucleo[1])
        corners.append(Corner(
            index=idx,
            name=f"Curva {idx}",
            start=max(0.0, min(1.0, distances[a] / length)),
            end=max(0.0, min(1.0, distances[b] / length)),
            direction="R" if lado > 0 else ("L" if lado < 0 else ""),
        ))
    return corners


def lap_coverage(telemetry: dict, track_length: float = 0.0) -> float:
    """
    Que fração da volta a telemetria cobre (0.0 a 1.0).

    Uma volta gravada pela metade só revela metade das curvas — foi assim que
    um mapa automático de Spa acabou com curvas só até 54% da pista.
    """
    distances = telemetry.get("distance") or []
    if len(distances) < 2:
        return 0.0
    length = float(track_length or 0.0) or max(distances)
    if length <= 0:
        return 0.0
    return max(0.0, min(1.0, (distances[-1] - distances[0]) / length))


def _vote_corners(listas: List[List[Corner]], length: float) -> List[Corner]:
    """
    Funde as curvas achadas em várias voltas, mantendo só as que a MAIORIA
    confirma.

    A votação é por posição, não por curva: a pista é dividida em casas de
    CONSENSUS_STEP_M metros e cada volta vota "aqui é curva" nas casas que os
    seus limites cobrem. Onde a maioria concordar, é curva.

    Votar por posição em vez de casar curva com curva resolve de graça dois
    problemas que o casamento teria: os limites nunca saem idênticos em duas
    voltas (a fronteira passa a ser onde a maioria se forma), e não é preciso
    decidir qual curva de uma volta corresponde a qual da outra quando uma
    delas achou uma curva a mais.

    Uma curva que só uma volta viu — a geometria de um corte de pista, uma
    escapada — não alcança a maioria e cai. Uma curva de verdade que UMA volta
    deixou de ver, porque o piloto cortou justamente ali, continua com os
    votos das outras e fica.
    """
    n_casas = max(1, int(length / CONSENSUS_STEP_M))
    votos = [0] * n_casas
    lados = [0] * n_casas

    for curvas in listas:
        for c in curvas:
            i0 = max(0, min(n_casas - 1, int(c.start * n_casas)))
            i1 = max(0, min(n_casas, int(c.end * n_casas) + 1))
            lado = 1 if c.direction == "R" else (-1 if c.direction == "L" else 0)
            for i in range(i0, i1):
                votos[i] += 1
                lados[i] += lado

    maioria = len(listas) // 2 + 1
    corners: List[Corner] = []
    i = 0
    while i < n_casas:
        if votos[i] < maioria:
            i += 1
            continue
        inicio = i
        soma_lado = 0
        while i < n_casas and votos[i] >= maioria:
            soma_lado += lados[i]
            i += 1
        fim = i
        if (fim - inicio) * CONSENSUS_STEP_M < MIN_CORNER_LENGTH_M:
            continue
        idx = len(corners) + 1
        corners.append(Corner(
            index=idx,
            name=f"Curva {idx}",
            start=max(0.0, min(1.0, inicio / n_casas)),
            end=max(0.0, min(1.0, fim / n_casas)),
            direction="R" if soma_lado > 0 else ("L" if soma_lado < 0 else ""),
        ))
    return corners


def build_consensus_corner_map(track_name: str, telemetries: List[dict],
                               track_length: float = 0.0) -> Optional[CornerMap]:
    """
    Mapa de curvas a partir de VÁRIAS voltas limpas.

    Com uma volta só o resultado é o mesmo de `build_auto_corner_map` — e o
    mapa fica marcado como saído de uma volta (`laps_used=1`), o que o mantém
    provisório: ele já serve para a sessão de hoje, e é refeito quando a
    segunda e a terceira volta limpa aparecerem.

    Só voltas que cobrem a volta INTEIRA entram na votação. Meia volta só
    conhece metade das curvas, e o seu voto "não é curva" na outra metade
    derrubaria curvas de verdade.
    """
    length = float(track_length or 0.0)
    listas: List[List[Corner]] = []
    for tel in (telemetries or []):
        distancias = tel.get("distance") or []
        if not distancias:
            continue
        comprimento = length or max(distancias)
        if lap_coverage(tel, comprimento) < 0.95:
            continue
        curvas = detect_corners(tel, comprimento)
        if curvas:
            listas.append(curvas)
            length = length or comprimento

    if not listas or length <= 0:
        return None

    corners = listas[0] if len(listas) == 1 else _vote_corners(listas, length)
    if not corners:
        return None
    return CornerMap(track=track_name or "", track_length=length,
                     corners=corners, source="auto", coverage=1.0,
                     detector_version=DETECTOR_VERSION,
                     laps_used=len(listas))


def build_auto_corner_map(track_name: str, telemetry: dict,
                          track_length: float = 0.0) -> Optional[CornerMap]:
    """Detecta as curvas de uma volta e devolve um CornerMap pronto para salvar."""
    distances = telemetry.get("distance") or []
    length = float(track_length or 0.0) or (max(distances) if distances else 0.0)
    corners = detect_corners(telemetry, length)
    if not corners:
        return None
    return CornerMap(track=track_name or "", track_length=length,
                     corners=corners, source="auto",
                     coverage=lap_coverage(telemetry, length),
                     detector_version=DETECTOR_VERSION,
                     laps_used=1)


# ---------------------------------------------------------------------------
# Onde, em palavras
# ---------------------------------------------------------------------------

#: Metros ANTES do início da curva que ainda contam como "a freada dela".
#: A freada acontece na reta, nunca dentro da curva — um localizador que só
#: reconheça o interior devolve "não sei" justamente para o evento que o
#: piloto mais precisa situar (o ABS atuando, a roda travando).
LOCATE_APPROACH_M = 250.0
#: E metros DEPOIS do fim que contam como "a saída dela".
LOCATE_EXIT_M = 120.0

#: Como as curvas são CHAMADAS na fala e nos recados do painel.
#:
#: "numero" -> "Curva 7". "nome" -> o nome do mapa ("Ferradura"), caindo no
#: número quando a curva não tem nome.
#:
#: O padrão é o número. Um nome próprio obriga o piloto a traduzir ("qual era
#: a Ferradura mesmo?") no exato momento em que ele precisa agir, e a
#: numeração ainda casa com a tabela curva a curva e com o número desenhado na
#: faixa do gráfico — os três lugares passam a falar a mesma língua.
CORNER_LABEL_NUMBER = "numero"
CORNER_LABEL_NAME = "nome"
#: Estilo em uso. A interface ajusta a partir do config.json; o módulo não
#: importa a configuração de propósito, para continuar testável sozinho.
label_style = CORNER_LABEL_NUMBER

#: As três fases de uma curva, na ordem em que o piloto as vive.
PHASE_BRAKING = "freada"
PHASE_ENTRY = "entrada"
PHASE_APEX = "ápice"
PHASE_EXIT = "saída"


def corner_at(corners: List[Corner], track_length: float, meters: float,
              approach_m: float = LOCATE_APPROACH_M,
              exit_m: float = LOCATE_EXIT_M):
    """
    Em que curva — e em que FASE dela — cai uma metragem da volta.

    Devolve `(Corner, fase)`, ou `(None, "")` quando o ponto está numa reta
    longe de tudo. A fase é o que torna o recado acionável: "na freada da
    Curva 7" e "na saída da Curva 7" mandam o piloto mexer em coisas
    diferentes, e as duas coisas acontecem a mais de cem metros uma da outra.

    A janela de aproximação é generosa de propósito (250 m): é o trecho em que
    o piloto está freando para aquela curva, e é ali que aparecem o ABS e a
    roda travada.
    """
    if not corners or track_length <= 0:
        return None, ""

    d = float(meters)
    melhor = None
    for corner in corners:
        inicio = corner.start_m(track_length)
        fim = corner.end_m(track_length)
        if inicio <= d <= fim:
            # Dentro: a primeira terça parte ainda é entrada, a última é saída
            extensao = max(1.0, fim - inicio)
            frac = (d - inicio) / extensao
            fase = (PHASE_ENTRY if frac < 0.33
                    else PHASE_EXIT if frac > 0.75 else PHASE_APEX)
            return corner, fase
        if inicio - approach_m <= d < inicio:
            # Freando para esta curva. Pode haver outra candidata (uma curva
            # anterior cuja saída também alcança este ponto); a freada é mais
            # informativa, então ela ganha e a busca para aqui.
            return corner, PHASE_BRAKING
        if fim < d <= fim + exit_m and melhor is None:
            melhor = (corner, PHASE_EXIT)
    return melhor if melhor else (None, "")


def corner_label(corner: Optional[Corner]) -> str:
    """
    Como esta curva é chamada num recado — falado ou escrito.

    Respeita `label_style`. Com o padrão ("numero") devolve sempre
    "Curva 7", mesmo que a pista tenha mapeamento manual com nomes próprios;
    o nome continua em `corner.name`, para quem quiser mostrá-lo à parte.
    """
    if corner is None:
        return ""
    if label_style == CORNER_LABEL_NAME and corner.name:
        return corner.name
    return f"Curva {corner.index}"


def corner_detail_name(corner: Optional[Corner]) -> str:
    """
    O nome próprio da curva, quando ele acrescenta alguma coisa.

    Vazio quando a curva não tem nome, ou quando o nome já é o rótulo que está
    sendo dito — repetir "Curva 7" no detalhe de um recado que já diz
    "Curva 7" só ocupa espaço.
    """
    nome = (corner.name or "").strip() if corner is not None else ""
    return nome if nome and nome != corner_label(corner) else ""


def is_feminine(name: str) -> bool:
    """
    "na Ferradura" x "no Pinheirinho".

    Heurística boba de propósito: nome de curva termina em 'a' na maioria dos
    casos femininos, e errar o artigo numa frase falada custa menos que uma
    tabela de gêneros por pista.

    O teste é na PRIMEIRA palavra, não na última: as curvas detectadas
    automaticamente chamam-se "Curva 7", que termina em dígito — olhando só o
    fim, o engenheiro dizia "no Curva 7" em toda pista sem mapeamento manual.
    """
    palavras = (name or "").strip().split()
    if not palavras:
        return False
    return palavras[0].lower().rstrip(",").endswith("a")


def corner_article(name: str, preposition: str = "em") -> str:
    """Artigo contraído para o nome da curva: "na"/"no", "da"/"do"."""
    fem = is_feminine(name)
    if preposition == "de":
        return "da" if fem else "do"
    return "na" if fem else "no"


#: Como cada fase entra na frase. O ápice não tem prefixo: "na Ferradura" já
#: significa o miolo da curva, e "no ápice da Ferradura" só gasta sílabas.
_PHASE_PREFIX = {
    PHASE_BRAKING: "na freada",
    PHASE_ENTRY: "na entrada",
    PHASE_EXIT: "na saída",
}


def where_phrase(corner: Optional[Corner], phase: str = "") -> str:
    """
    O "onde" como o engenheiro fala: "na freada da Curva 7", "na saída do
    Pinheirinho", "na Ferradura".

    Sem curva devolve string vazia — e o chamador deve então dizer a frase
    SEM localização, em vez de inventar uma. Um lugar errado é pior que
    nenhum: o piloto vai trabalhar a curva errada.
    """
    if corner is None:
        return ""
    nome = corner_label(corner)
    prefixo = _PHASE_PREFIX.get(phase)
    if prefixo:
        return f"{prefixo} {corner_article(nome, 'de')} {nome}"
    return f"{corner_article(nome)} {nome}"


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
    Calcula as métricas da curva sobre os arrays de uma volta:
      * Ponto de início de frenagem (m) e velocidade na frenagem (km/h)
      * Velocidade mínima no ápice (km/h) e metro do ápice (m)
      * Ponto de retomada de aceleração (m) e porcentagem de acelerador (%)
      * Tempo do trecho da curva (s)
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

    if apex_idx is None:
        metrics.v_min = None
        metrics.v_min_m = None

    # --- Ponto de frenagem: primeiro cruzamento 0% -> >10% antes do ápice ---
    brake_from = max(0.0, start_m - BRAKE_LOOKBACK_M)
    if search_from_m is not None:
        brake_from = max(brake_from, float(search_from_m))
    brake_to = metrics.v_min_m if metrics.v_min_m is not None else end_m

    for i in _index_range(distances, brake_from, brake_to):
        if i >= len(brakes) or i == 0:
            continue
        if brakes[i] > BRAKE_ON_THRESHOLD:
            if brakes[i - 1] <= BRAKE_OFF_THRESHOLD or (brakes[i - 1] < BRAKE_ON_THRESHOLD and (i < 2 or brakes[i - 2] <= 0.05)):
                metrics.braking_point_m = float(distances[i])
                metrics.braking_speed = float(speeds[i]) if i < len(speeds) else None
                break

    # --- Ponto de retomada: acelerador de volta a 100% saindo da curva ---
    throttle_from = metrics.v_min_m if metrics.v_min_m is not None else start_m
    throttle_to = end_m + THROTTLE_LOOKAHEAD_M
    if search_to_m is not None:
        throttle_to = min(throttle_to, float(search_to_m))

    max_gas_seen = 0.0
    for i in _index_range(distances, throttle_from, throttle_to):
        if i >= len(gases):
            break
        g = float(gases[i])
        if g > max_gas_seen:
            max_gas_seen = g
        if gases[i] >= THROTTLE_FULL_THRESHOLD:
            metrics.throttle_point_m = float(distances[i])
            metrics.throttle_pct = 100.0
            break

    if metrics.throttle_point_m is None and max_gas_seen > 0.0:
        metrics.throttle_pct = float(max_gas_seen * 100.0)

    return metrics


def search_bounds(corners: List[Corner], i: int, track_length: float):
    """
    Até onde a busca da curva `i` pode ir para trás e para frente.
    Usa o ponto médio da curva vizinha para permitir janela de busca
    mesmo em curvas contíguas.
    """
    if i > 0:
        c_prev = corners[i - 1]
        from_m = (c_prev.start_m(track_length) + c_prev.end_m(track_length)) / 2.0
    else:
        from_m = None

    if i + 1 < len(corners):
        c_next = corners[i + 1]
        to_m = (c_next.start_m(track_length) + c_next.end_m(track_length)) / 2.0
    else:
        to_m = None

    return from_m, to_m


def analyze_lap(telemetry: dict, corners: List[Corner],
                track_length: float = 0.0) -> List[CornerMetrics]:
    """Roda `analyze_corner` para todas as curvas da pista."""
    length = float(track_length or 0.0) or max(telemetry.get("distance") or [0.0])
    out = []
    for i, corner in enumerate(corners):
        from_m, to_m = search_bounds(corners, i, length)
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
        from_m, to_m = search_bounds(corners, i, length)
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
