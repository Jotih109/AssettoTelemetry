"""
core/corner_bests.py — A melhor passagem de cada curva, guardada
=================================================================

O coach ([core/live_coach.py](core/live_coach.py)) mira, em cada curva, na
melhor passagem que ele conhece daquele trecho. O problema é que ele aprendia
isso do zero a cada sessão: na primeira volta do Treino 2 ele não sabia nada,
mesmo depois de um Treino 1 inteiro te observando — e precisava de duas voltas
só para reconstruir o que já tinha aprendido na véspera.

Este módulo guarda esse conhecimento entre sessões, num arquivo por
Pista/Carro. É pouca coisa (um punhado de números por curva), então ele
convive com o catálogo de voltas sem pesar:

    telemetry_data/<Pista>/<Carro>/corner_bests.json

Duas maneiras de encher esse arquivo:

* **Ao vivo**, volta a volta: quando você bate a sua melhor passagem numa
  curva, o número novo é gravado. É o caminho normal.
* **De uma vez** (`bootstrap`), varrendo as melhores voltas do catálogo. Serve
  para quem já tinha voltas gravadas antes desta versão — ou para quem apagou
  o arquivo. Custa ~110 ms para cinco voltas e acontece uma vez só.

**A assinatura do mapa de curvas é gravada junto**, e o arquivo é descartado se
ela mudar. Um mapa detectado automaticamente pode ser refeito com outro número
de curvas — e aí a "curva 5" de ontem não é a curva 5 de hoje. Comparar os
dois seria pior que não comparar nada.
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from core import corner_analysis as ca
from core.lap_library import LapLibrary, LapRecord, read_json_file, write_bytes_atomic

#: Versão do formato. Arquivo de outra versão é descartado e refeito.
SCHEMA_VERSION = 1

FILENAME = "corner_bests.json"

#: Quantas voltas do catálogo varrer no `bootstrap`. As mais rápidas primeiro:
#: a melhor passagem numa curva quase sempre está numa das melhores voltas, e
#: varrer o catálogo inteiro custaria segundos ao entrar na pista.
BOOTSTRAP_LAPS = 6


@dataclasses.dataclass
class CornerBest:
    """A melhor passagem conhecida numa curva, e de onde ela veio."""
    index: int
    name: str = ""
    section_s: float = 0.0
    braking_point_m: Optional[float] = None
    v_min: Optional[float] = None
    v_min_m: Optional[float] = None
    throttle_point_m: Optional[float] = None
    #: De que volta do catálogo veio (para o piloto poder ir olhar)
    lap_id: str = ""
    lap_time_str: str = ""
    timestamp: str = ""

    def to_metrics(self, corner: "ca.Corner") -> "ca.CornerMetrics":
        """
        Reconstrói as medidas no formato que a análise usa.

        `entry_time`/`exit_time` viram 0 e `section_s`: o coach só olha a
        DURAÇÃO do trecho, nunca o instante absoluto — que não faria sentido
        mesmo, vindo de outra volta.
        """
        m = ca.CornerMetrics(corner=corner)
        m.entry_time = 0.0
        m.exit_time = self.section_s
        m.braking_point_m = self.braking_point_m
        m.v_min = self.v_min
        m.v_min_m = self.v_min_m
        m.throttle_point_m = self.throttle_point_m
        return m

    @classmethod
    def from_metrics(cls, metrics: "ca.CornerMetrics", *, lap_id: str = "",
                     lap_time_str: str = "",
                     timestamp: str = "") -> Optional["CornerBest"]:
        secao = metrics.section_time
        if secao is None or secao <= 0:
            return None
        corner = metrics.corner
        return cls(
            index=corner.index,
            name=corner.name or f"Curva {corner.index}",
            section_s=round(float(secao), 4),
            braking_point_m=_round_or_none(metrics.braking_point_m, 1),
            v_min=_round_or_none(metrics.v_min, 2),
            v_min_m=_round_or_none(metrics.v_min_m, 1),
            throttle_point_m=_round_or_none(metrics.throttle_point_m, 1),
            lap_id=lap_id, lap_time_str=lap_time_str,
            timestamp=timestamp or datetime.now().isoformat(timespec="seconds"),
        )

    @classmethod
    def from_dict(cls, data: dict) -> Optional["CornerBest"]:
        known = set(cls.__dataclass_fields__)
        limpo = {k: v for k, v in data.items() if k in known}
        if "index" not in limpo:
            return None
        try:
            return cls(**limpo)
        except (TypeError, ValueError):
            return None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _round_or_none(value, nd):
    return None if value is None else round(float(value), nd)


def map_signature(corners: List["ca.Corner"], source: str = "") -> str:
    """
    Identidade do mapa de curvas em uso.

    Se ela mudar, o que foi aprendido não vale mais: a curva 5 de um mapa
    detectado automaticamente pode virar a curva 4 no próximo, e o coach
    passaria a cobrar a freada da curva errada.
    """
    partes = [source or "?", str(len(corners or []))]
    for c in (corners or []):
        partes.append(f"{c.index}:{c.start:.4f}-{c.end:.4f}")
    return "|".join(partes)


class CornerBestStore:
    """
    Lê e grava as melhores passagens por Pista/Carro.

    Sem estado próprio além de um cache do que foi lido: quem manda é o
    arquivo, e o coach recebe uma cópia para trabalhar.
    """

    def __init__(self, library: LapLibrary):
        self.library = library

    def path_for(self, track: str, car: str) -> str:
        return os.path.join(self.library.folder_for(track, car), FILENAME)

    # -- leitura -------------------------------------------------------------

    def load(self, track: str, car: str,
             signature: str) -> Tuple[Dict[int, CornerBest], List[str]]:
        """
        `(melhores_por_curva, ids_das_voltas_já_vistas)`.

        Devolve vazio quando o arquivo não existe, é de outra versão ou foi
        gravado com outro mapa de curvas.
        """
        path = self.path_for(track, car)
        if not os.path.exists(path):
            return {}, []
        data = read_json_file(path)
        if not isinstance(data, dict):
            return {}, []
        if data.get("schema") != SCHEMA_VERSION:
            print("[CornerBests] Formato antigo: as melhores passagens serão refeitas.")
            return {}, []
        if data.get("map_signature") != signature:
            print("[CornerBests] O mapa de curvas mudou: as melhores passagens "
                  "de antes não valem mais para as curvas de agora.")
            return {}, []

        bests: Dict[int, CornerBest] = {}
        for raw in data.get("corners", []):
            if not isinstance(raw, dict):
                continue
            best = CornerBest.from_dict(raw)
            if best is not None and best.section_s > 0:
                bests[best.index] = best
        vistas = [x for x in data.get("seen_laps", []) if isinstance(x, str)]
        return bests, vistas

    # -- escrita -------------------------------------------------------------

    def save(self, track: str, car: str, signature: str,
             bests: Dict[int, CornerBest], seen_laps: List[str]) -> bool:
        payload = {
            "schema": SCHEMA_VERSION,
            "track": track,
            "car": car,
            "map_signature": signature,
            "updated": datetime.now().isoformat(timespec="seconds"),
            # A lista é limitada: ela existe só para o bootstrap não reanalisar
            # a mesma volta, e um catálogo grande não precisa virar um arquivo
            # grande por causa disso.
            "seen_laps": list(seen_laps)[-400:],
            "corners": [b.to_dict() for b in sorted(bests.values(),
                                                    key=lambda x: x.index)],
        }
        blob = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
        return write_bytes_atomic(self.path_for(track, car), blob)

    # -- primeira carga a partir do catálogo ---------------------------------

    def bootstrap(self, track: str, car: str, corners: List["ca.Corner"],
                  track_length: float, *,
                  known: Dict[int, CornerBest] = None,
                  seen_laps: List[str] = None,
                  max_laps: int = BOOTSTRAP_LAPS) -> Tuple[Dict[int, CornerBest],
                                                           List[str], int]:
        """
        Varre as voltas mais rápidas do catálogo procurando passagens melhores.

        Devolve `(melhores, vistas, quantas_voltas_foram_lidas)`. Só analisa
        volta que ainda não foi vista, então rodar isto de novo depois de uma
        sessão custa quase nada.
        """
        bests = dict(known or {})
        vistas = list(seen_laps or [])
        ja_vista = set(vistas)

        if not corners or track_length <= 0:
            return bests, vistas, 0

        candidatas = [r for r in self.library.records(track, car,
                                                      only_full=True,
                                                      only_valid=True)
                      if r.lap_id not in ja_vista]
        candidatas.sort(key=lambda r: r.lap_time_ms)
        candidatas = candidatas[:max_laps]

        lidas = 0
        for rec in candidatas:
            telemetry = self.library.load_telemetry(track, car, rec)
            if not telemetry:
                continue
            lidas += 1
            vistas.append(rec.lap_id)
            self.absorb_lap(bests, telemetry, corners, track_length, rec=rec)
        return bests, vistas, lidas

    @staticmethod
    def absorb_lap(bests: Dict[int, CornerBest], telemetry: dict,
                   corners: List["ca.Corner"], track_length: float,
                   rec: LapRecord = None) -> int:
        """
        Guarda as passagens desta volta que forem melhores que as conhecidas.
        Devolve quantas curvas melhoraram.
        """
        melhorou = 0
        for metrics in ca.analyze_lap(telemetry, corners, track_length):
            novo = CornerBest.from_metrics(
                metrics,
                lap_id=rec.lap_id if rec else "",
                lap_time_str=rec.lap_time_str if rec else "",
                timestamp=rec.timestamp if rec else "")
            if novo is None:
                continue
            atual = bests.get(novo.index)
            if atual is None or novo.section_s < atual.section_s:
                bests[novo.index] = novo
                melhorou += 1
        return melhorou
