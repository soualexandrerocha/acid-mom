"""Região marítima de cada acidente, para contar os acidentes por região.

Usa as coordenadas quando existem (caixas de latitude/longitude, das mais específicas
para as mais amplas). Sem coordenadas, procura palavras do local e do país, em vários
idiomas. Quando nada bate, o acidente fica em "Local não informado".
As caixas são aproximadas: servem para contar, não para traçar fronteiras.
"""

import re
import sqlite3
import unicodedata

SEM_LOCAL = "Local não informado"

# (região, [(lat_min, lat_max, lon_min, lon_max), ...]) em ordem de prioridade.
CAIXAS = [
    ("Golfo Pérsico, Ormuz e Golfo de Omã", [(22, 31, 47, 60.5)]),
    ("Mar Vermelho e Golfo de Áden", [(12, 30, 32, 44), (10, 16, 43, 51.5)]),
    ("Mar Negro e estreitos turcos", [(39.8, 47.5, 26, 42)]),
    ("Mar Báltico", [(53.8, 66, 10, 30.5)]),
    ("Mar do Norte e Canal da Mancha", [(48.5, 62, -5.5, 10.5)]),
    ("Mar Mediterrâneo", [(30, 42, -5.6, 3), (30, 46, 3, 36.5)]),
    ("Golfo do México e Caribe", [(8, 31, -98, -60)]),
    ("Ásia Oriental e Sudeste Asiático", [(-11, 50, 95, 150)]),
    ("Oceano Índico", [(-45, 30, 20, 95)]),
    ("Atlântico Sul", [(-60, 0, -70, 20)]),
    ("Atlântico Norte", [(9, 66, -82, 20), (0, 9, -77, 20)]),
    ("Ártico", [(66, 90, -180, 180)]),
    ("Oceano Austral", [(-90, -60, -180, 180)]),
]
PACIFICO = "Oceano Pacífico"  # o que sobra entre 60°S e 66°N

# Palavras (sem acento, minúsculas) que apontam a região quando não há coordenadas.
# Países que dão para mais de um mar (Rússia, Alemanha, Noruega...) ficam de fora de propósito.
PALAVRAS = [
    ("Golfo Pérsico, Ormuz e Golfo de Omã",
     r"hormuz|ormuz|\boman\b|umman|persian gulf|arabian gulf|golfo persico|\biran|\bira\b|iraq|iraque|"
     r"\buae\b|emirates|emirados|kuwait|qatar|catar|bahrain|barein"),
    ("Mar Vermelho e Golfo de Áden", r"red sea|mar vermelho|mar rojo|\baden\b|yemen|iemen|djibouti|eritrea"),
    ("Mar Negro e estreitos turcos",
     r"black sea|mar negro|karadeniz|mer noire|schwarzes meer|bulgaria|romania|roumanie|romenia|"
     r"ukrain|ucrania|marmara|bosphorus|bosporus|bosforo|dardanel|canakkale|istanbul"),
    ("Mar Báltico", r"baltic|baltico|ostsee|baltique"),
    ("Mar do Norte e Canal da Mancha", r"north sea|mar do norte|nordsee|english channel|canal da mancha"),
    ("Mar Mediterrâneo",
     r"mediterr|akdeniz|cyprus|chipre|kibris|chypre|zypern|malta|aegean|ege denizi|mar egeu|adriatic|"
     r"greece|grecia|italy|italia|libya|libia|lebanon|libano|israel|egypt|egito"),
    ("Golfo do México e Caribe",
     r"gulf of mexico|golfo do mexico|golfo de mexico|caribbean|caribe|mississippi|texas|louisiana|"
     r"curacao|curazao|venezuela|jamaica|\bcuba\b|haiti|puerto rico|porto rico"),
    ("Ásia Oriental e Sudeste Asiático",
     r"south china sea|mar da china|china|japan|japao|korea|coreia|vietnam|vietna|philippin|filipinas|"
     r"indonesia|malaysia|malasia|singapore|singapura|cambodia|camboja|thailand|tailandia|taiwan"),
    ("Oceano Índico",
     r"indian ocean|oceano indico|india|sri lanka|bangladesh|pakistan|arabian sea|mar arabico|"
     r"tanzania|kenya|quenia|mozambique|mocambique|somalia|madagascar|mauritius"),
    ("Atlântico Sul", r"south atlantic|atlantico sul|atlantico sur|gabon|gabao|angola|namibia|brasil|brazil|argentin|uruguai|uruguay"),
    ("Atlântico Norte", r"north atlantic|atlantico norte|atlantic|atlantico|canary|canarias|azores|acores|biscay|biscaia"),
    ("Oceano Pacífico", r"pacific|pacifico|ecuador|equador|peru|chile|new zealand|nova zelandia"),
]
_PALAVRAS = [(regiao, re.compile(padrao)) for regiao, padrao in PALAVRAS]


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))


def regiao_por_coordenadas(lat: float, lon: float) -> str:
    lon = ((lon + 180) % 360) - 180
    for regiao, caixas in CAIXAS:
        if any(a <= lat <= b and c <= lon <= d for a, b, c, d in caixas):
            return regiao
    return PACIFICO


def regiao(lat, lon, local: str | None = None, pais: str | None = None) -> str:
    if lat is not None and lon is not None:
        return regiao_por_coordenadas(lat, lon)
    texto = _sem_acento(" ".join(filter(None, [local, pais])))
    for nome, padrao in _PALAVRAS:
        if padrao.search(texto):
            return nome
    return SEM_LOCAL


def contar(acidentes: list[dict]) -> list[dict]:
    """Conta por região. Cada acidente precisa de "region" e "severity".

    Volta do maior para o menor, com "Local não informado" sempre no fim.
    """
    contagem: dict[str, dict] = {}
    for a in acidentes:
        linha = contagem.setdefault(a["region"], {"region": a["region"], "total": 0, "very_serious": 0})
        linha["total"] += 1
        linha["very_serious"] += a["severity"] == "very_serious"
    return sorted(contagem.values(), key=lambda r: (r["region"] == SEM_LOCAL, -r["total"], r["region"]))


def regiao_da_linha(row: sqlite3.Row) -> str:
    return regiao(row["lat"], row["lon"], row["location"], row["country"])
