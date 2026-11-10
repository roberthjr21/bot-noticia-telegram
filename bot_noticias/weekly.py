"""Selección equilibrada de noticias almacenadas durante la última semana."""

from __future__ import annotations
from collections import defaultdict


def choose_weekly(rows: list[dict], limit: int = 7) -> list[dict]:
    """Selecciona por temas para evitar que una sola categoría domine el resumen."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["topic"]].append(row)
    for group in groups.values():
        group.sort(key=lambda item: item["published_at"], reverse=True)
    chosen: list[dict] = []
    while len(chosen) < limit:
        progress = False
        for topic in sorted(groups):
            if groups[topic]:
                chosen.append(groups[topic].pop(0))
                progress = True
                if len(chosen) == limit:
                    break
        if not progress:
            break
    return chosen
