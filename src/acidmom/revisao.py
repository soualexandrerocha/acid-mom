"""Revisão ortográfica dos resumos em português, com o LanguageTool.

O Claude às vezes erra a grafia de uma palavra do português seguindo a regra geral onde a
língua tem exceção (ex.: "corrigo" em vez de "corrijo"). O LanguageTool aponta esses erros.

Usa o serviço gratuito na internet (api.languagetool.org), que aceita 20 pedidos por minuto.
Os resumos já são públicos no mapa, então mandar o texto para lá não expõe nada.

Só corrige sozinho o que é seguro: palavra que não existe no dicionário, escrita toda em
minúsculas (nome de navio, porto ou país fica como está) e com uma sugestão parecida.
Se o serviço falhar, o resumo segue sem revisão; nunca trava a rodada.
"""

import logging
import time

import httpx

log = logging.getLogger(__name__)

URL = "https://api.languagetool.org/v2/check"
# Limite do serviço gratuito: 20 pedidos por minuto.
MIN_INTERVAL = 3.5
TIMEOUT = 20
_last_request = 0.0


def _close_enough(word: str, suggestion: str) -> bool:
    """Sugestão parecida: mesma letra inicial e no máximo 2 letras de diferença no tamanho."""
    return bool(suggestion) and suggestion[0].lower() == word[0].lower() and abs(len(suggestion) - len(word)) <= 2


def apply_fixes(text: str, matches: list[dict]) -> tuple[str, list[tuple[str, str]]]:
    """Aplica as correções seguras. Devolve o texto novo e a lista (errado, certo)."""
    changes = []
    for m in sorted(matches, key=lambda m: m["offset"], reverse=True):
        if m.get("rule", {}).get("issueType") != "misspelling":
            continue
        start, end = m["offset"], m["offset"] + m["length"]
        word = text[start:end]
        if not word.isalpha() or not word.islower():
            continue
        replacements = [r.get("value", "") for r in m.get("replacements", [])]
        if not replacements or not _close_enough(word, replacements[0]) or " " in replacements[0]:
            continue
        text = text[:start] + replacements[0] + text[end:]
        changes.append((word, replacements[0]))
    return text, changes[::-1]


def check(text: str) -> list[dict]:
    global _last_request
    time.sleep(max(0.0, _last_request + MIN_INTERVAL - time.monotonic()))
    resp = httpx.post(URL, data={"text": text, "language": "pt-BR"}, timeout=TIMEOUT)
    _last_request = time.monotonic()
    resp.raise_for_status()
    return resp.json().get("matches", [])


def revisar(text: str) -> str:
    if not text:
        return text
    try:
        matches = check(text)
    except Exception as exc:  # noqa: BLE001 - sem revisão é melhor que sem resumo
        log.warning("Revisão ortográfica falhou, resumo segue como está: %s", exc)
        return text
    fixed, changes = apply_fixes(text, matches)
    for wrong, right in changes:
        log.info("Ortografia: %r -> %r", wrong, right)
    return fixed
