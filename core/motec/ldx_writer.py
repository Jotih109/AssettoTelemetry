"""
Gerador de arquivo de marcas MoTeC i2 (.ldx).

Cria o arquivo XML acompanhante (.ldx) com os marcadores de beacon de volta
e setores para segmentação e identificação automática no MoTeC i2 Pro.
"""

from __future__ import annotations

import xml.dom.minidom as minidom
from typing import List, Optional


class MotecLDXWriter:
    """Gerador de arquivo .ldx em formato XML para o MoTeC i2 Pro."""

    @staticmethod
    def write(
        filepath: str,
        lap_time_s: float,
        lap_number: int = 1,
        sector_times_s: Optional[List[float]] = None,
    ) -> None:
        """
        Escreve o arquivo XML .ldx com as marcas da volta.

        :param filepath: Caminho do arquivo destino (.ldx).
        :param lap_time_s: Tempo total da volta em segundos.
        :param lap_number: Número da volta.
        :param sector_times_s: Lista de tempos dos setores em segundos (se disponível).
        """
        doc = minidom.Document()

        ldx = doc.createElement("LDXFile")
        ldx.setAttribute("locale", "English_United Kingdom.1252")
        ldx.setAttribute("DefaultLocale", "C")
        ldx.setAttribute("Version", "1.6")
        doc.appendChild(ldx)

        layers = doc.createElement("Layers")
        ldx.appendChild(layers)

        layer = doc.createElement("Layer")
        layers.appendChild(layer)

        markerblock = doc.createElement("MarkerBlock")
        layer.appendChild(markerblock)

        # Grupo de Beacons (Voltas)
        markergroup = doc.createElement("MarkerGroup")
        markergroup.setAttribute("Name", "Beacons")
        markergroup.setAttribute("Index", "0")
        markerblock.appendChild(markergroup)

        # Beacon de fechamento da volta (tempo em microssegundos)
        lap_time_us = lap_time_s * 1_000_000.0
        marker = doc.createElement("Marker")
        marker.setAttribute("Version", "100")
        marker.setAttribute("ClassName", "BCN")
        marker.setAttribute("Name", f"Manual.{lap_number}")
        marker.setAttribute("Flags", "77")
        marker.setAttribute("Time", f"{lap_time_us:.2f}")
        markergroup.appendChild(marker)

        # Se houver setores, adiciona grupo de setores
        if sector_times_s and len(sector_times_s) > 1:
            sec_group = doc.createElement("MarkerGroup")
            sec_group.setAttribute("Name", "Sectors")
            sec_group.setAttribute("Index", str(len(sector_times_s) - 1))
            markerblock.appendChild(sec_group)

            accum_time = 0.0
            for idx, s_time in enumerate(sector_times_s):
                accum_time += s_time
                sec_marker = doc.createElement("Marker")
                sec_marker.setAttribute("Version", "100")
                sec_marker.setAttribute("ClassName", "BCN")
                sec_marker.setAttribute("Name", f"Sector {idx + 1}")
                sec_marker.setAttribute("Flags", "77")
                sec_marker.setAttribute("Time", f"{accum_time * 1_000_000.0:.2f}")
                sec_group.appendChild(sec_marker)

        # Detalhes e resumo da volta
        details = doc.createElement("Details")
        layers.appendChild(details)

        totallaps = doc.createElement("String")
        totallaps.setAttribute("Id", "Total Laps")
        totallaps.setAttribute("Value", "1")
        details.appendChild(totallaps)

        if lap_time_s > 0:
            minutes = int(lap_time_s // 60)
            seconds = lap_time_s - (minutes * 60)
            formatted_time = f"{minutes:02d}:{seconds:06.3f}"

            ft = doc.createElement("String")
            ft.setAttribute("Id", "Fastest Time")
            ft.setAttribute("Value", formatted_time)
            details.appendChild(ft)

            fl = doc.createElement("String")
            fl.setAttribute("Id", "Fastest Lap")
            fl.setAttribute("Value", str(lap_number))
            details.appendChild(fl)

        xml_str = doc.toprettyxml(indent="  ", encoding="utf-8")
        with open(filepath, "wb") as f:
            f.write(xml_str)
