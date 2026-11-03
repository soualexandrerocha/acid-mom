import pytest

from acidmom import revisao


@pytest.fixture(autouse=True)
def sem_languagetool(monkeypatch):
    """Os testes não acessam a internet: o LanguageTool responde "nenhum erro"."""
    monkeypatch.setattr(revisao, "check", lambda text: [])
