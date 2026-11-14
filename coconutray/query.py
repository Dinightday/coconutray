"""Grounded query interpretation. Local rules always preserve explicit constraints."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
import re
import unicodedata


from coconutray.network_models import SearchIntent


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(text).lower()) if not unicodedata.combining(c))


def stem(word: str) -> str:
    if word.endswith("eis"):
        return word[:-3] + "el"
    if word.endswith("uis"):
        return word[:-3] + "ul"
    return word[:-1] if word.endswith("s") and word not in {"tenis", "lapis"} else word


COLORS = {"pret": "preto", "azul": "azul", "vermelh": "vermelho", "branc": "branco",
          "verd": "verde", "ros": "rosa", "amarel": "amarelo", "cinz": "cinza", "marrom": "marrom"}


def color_value(value: str) -> str | None:
    for token in re.findall(r"[a-z]+", normalize(value)):
        for prefix, canonical in COLORS.items():
            if token in {prefix, prefix + "o", prefix + "a", prefix + "os", prefix + "as", canonical, canonical + "s", "azuis" if canonical == "azul" else canonical}:
                return canonical
    return None


def color_values(value: str) -> set[str]:
    return {color for token in re.findall(r"[a-z]+", normalize(value)) if (color := color_value(token))}


def interpret_query(text: str, use_ai: bool = False, *, timeout: float = 15) -> SearchIntent:
    if not isinstance(text, str) or not text.strip() or len(text) > 300:
        raise ValueError("Conte o que procura em até 300 caracteres.")
    cleaned = normalize(text.strip())
    # Do not reinterpret exclusions or color alternatives as a positive hard filter.
    if re.search(r"\b(?:nao(?:\s+\w+){0,3}|exceto|menos|sem)\s+(?:a\s+cor\s+)?(?:pret[oa]s?|azu(?:l|is)|vermelh[oa]s?|branc[oa]s?|verde[s]?|rosa[s]?|amarel[oa]s?|cinza[s]?|marrom)\b", cleaned):
        raise ValueError("Para esta busca, informe a cor desejada. Exemplo: mochila azul, em vez de excluir uma cor.")
    if len(color_values(cleaned)) > 1:
        raise ValueError("Pesquise uma cor por vez para conferir a variação correta do produto.")
    constraints = {}
    maximum = None
    price = re.search(r"\b(?:ate|no maximo|preco maximo(?: de)?)\s*(?:r\$\s*)?([0-9]+(?:[.,][0-9]+)*)", cleaned)
    if price:
        number = price[1]
        if "," in number:
            number = number.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", number):
            number = number.replace(".", "")
        try:
            maximum = Decimal(number)
            if not maximum.is_finite() or maximum <= 0 or maximum.as_tuple().exponent < -2:
                raise InvalidOperation
        except InvalidOperation as exc:
            raise ValueError("Informe um preço máximo válido, por exemplo: até R$ 180.") from exc
        cleaned = cleaned[:price.start()] + " " + cleaned[price.end():]
    color = color_value(cleaned)
    if color:
        constraints["cor"] = color
    size = re.search(r"\b(?:tamanho|tam\.?|numero)\s*[:.]?\s*(\d{1,3})\b", cleaned)
    if size:
        constraints["tamanho"] = size[1]
        cleaned = cleaned[:size.start()] + " " + cleaned[size.end():]
    voltage = re.search(r"\b(127|220|110)\s*v(?:olts)?\b", cleaned)
    if voltage:
        constraints["voltagem"] = voltage[1] + "v"
        cleaned = cleaned[:voltage.start()] + " " + cleaned[voltage.end():]
    if re.search(r"\bimpermeave(?:l|is)\b", cleaned):
        constraints["impermeavel"] = "nao" if re.search(r"\b(?:nao(?:\s+e)?|sem)\s+impermeavel", cleaned) else "sim"
        cleaned = re.sub(r"\b(?:nao\s+(?:e\s+)?|sem\s+)?impermeave(?:l|is)\b", " ", cleaned)
    for gender in ("masculino", "feminino", "unissex"):
        if re.search(rf"\b{gender}\b", cleaned):
            constraints["genero"] = gender
            cleaned = re.sub(rf"\b{gender}\b", " ", cleaned)
    aliases = {"calcado para correr": "tenis corrida", "calcado de corrida": "tenis corrida",
               "para correr": "corrida", "laptop": "notebook"}
    for original, substitute in aliases.items():
        cleaned = cleaned.replace(original, substitute)
    stop = {"quero", "queria", "preciso", "procuro", "procurando", "busco", "comprar", "encontrar",
            "uma", "um", "uns", "umas", "de", "da", "do", "das", "dos", "a", "o", "e", "em",
            "para", "por", "com", "que", "seja", "tem", "voce", "voces", "me", "r", "reais", "cliente"}
    words = re.findall(r"[a-z0-9]+", cleaned)
    terms = tuple(dict.fromkeys(stem(w) for w in words if w not in stop and not color_value(w)))
    if not terms:
        raise ValueError("Inclua o nome do produto, como mochila, tênis ou furadeira.")
    intent = SearchIntent(text.strip(), terms, constraints, maximum)
    if not use_ai:
        return intent
    from coconutray.gemini import assist
    return assist(intent)
