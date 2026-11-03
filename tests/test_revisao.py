from acidmom import revisao


def match(text, word, suggestion, issue="misspelling"):
    return {"offset": text.index(word), "length": len(word),
            "replacements": [{"value": suggestion}, {"value": "outra"}],
            "rule": {"id": "MORFOLOGIK_RULE_PT_BR", "issueType": issue}}


def test_corrige_palavra_errada():
    text = "O prático caiu e o comandante corrigo a rota."
    fixed, changes = revisao.apply_fixes(text, [match(text, "corrigo", "corrijo")])
    assert fixed == "O prático caiu e o comandante corrijo a rota."
    assert changes == [("corrigo", "corrijo")]


def test_nao_mexe_em_nomes_nem_em_sugestoes_duvidosas():
    text = "O graneleiro Xin Hai Tong encalhou em Paranaguá, deixano óleo."
    matches = [
        match(text, "Xin", "Sim"),                    # nome do navio (maiúscula)
        match(text, "Paranaguá", "Paranaguá"),        # lugar (maiúscula)
        match(text, "deixano", "deixando"),           # erro de verdade
        match(text, "óleo", "óleo", issue="style"),   # não é erro de grafia
    ]
    fixed, changes = revisao.apply_fixes(text, matches)
    assert fixed == "O graneleiro Xin Hai Tong encalhou em Paranaguá, deixando óleo."
    assert changes == [("deixano", "deixando")]


def test_sugestao_muito_diferente_fica_de_fora():
    text = "Houve abalroamento no canal."
    fixed, changes = revisao.apply_fixes(text, [match(text, "abalroamento", "a")])
    assert fixed == text and changes == []


def test_falha_do_servico_nao_trava(monkeypatch):
    def boom(text):
        raise OSError("sem rede")
    monkeypatch.setattr(revisao, "check", boom)
    assert revisao.revisar("Navio encalhou.") == "Navio encalhou."


def test_revisar_aplica_correcao(monkeypatch):
    text = "Vou ver e corrigo depois."
    monkeypatch.setattr(revisao, "check", lambda t: [match(t, "corrigo", "corrijo")])
    assert revisao.revisar(text) == "Vou ver e corrijo depois."
