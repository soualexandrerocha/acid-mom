from acidmom import regioes
from acidmom.regioes import regiao


def test_coordenadas():
    assert regiao(26.6, 56.3) == "Golfo Pérsico, Ormuz e Golfo de Omã"
    assert regiao(43.5, 34.0) == "Mar Negro e estreitos turcos"
    assert regiao(53.54, 9.97) == "Mar do Norte e Canal da Mancha"  # Hamburgo
    assert regiao(54.71, 20.51) == "Mar Báltico"  # Kaliningrado
    assert regiao(34.9, 31.5) == "Mar Mediterrâneo"
    assert regiao(43.28, -2.14) == "Atlântico Norte"  # golfo da Biscaia, não Mediterrâneo
    assert regiao(-2.19, -79.89) == "Oceano Pacífico"  # Guayaquil
    assert regiao(29.08, -94.27) == "Golfo do México e Caribe"
    assert regiao(12.0, 113.0) == "Ásia Oriental e Sudeste Asiático"


def test_texto_sem_coordenadas_em_varios_idiomas():
    assert regiao(None, None, "Umman açıkları", None) == "Golfo Pérsico, Ormuz e Golfo de Omã"
    assert regiao(None, None, "Large de la Roumanie", "Roumanie") == "Mar Negro e estreitos turcos"
    assert regiao(None, None, "ao largo de Chipre", "Chipre") == "Mar Mediterrâneo"
    assert regiao(None, None, "Costa russa do Mar Báltico", "Rússia") == "Mar Báltico"
    # País que dá para mais de um mar não decide sozinho.
    assert regiao(None, None, "porto alemão (não especificado)", "Alemanha") == regioes.SEM_LOCAL
    assert regiao(None, None, None, None) == regioes.SEM_LOCAL


def test_contar_poe_sem_local_no_fim():
    linhas = regioes.contar([
        {"region": regioes.SEM_LOCAL, "severity": "serious"},
        {"region": regioes.SEM_LOCAL, "severity": "serious"},
        {"region": "Mar Báltico", "severity": "very_serious"},
    ])
    assert [l["region"] for l in linhas] == ["Mar Báltico", regioes.SEM_LOCAL]
    assert linhas[0]["very_serious"] == 1
