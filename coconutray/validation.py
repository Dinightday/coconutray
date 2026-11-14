"""Commercial gates shared by both catalog adapters."""
from collections import defaultdict
from decimal import Decimal

from coconutray.models import ReviewLine


def validate_review(lines: list[ReviewLine], approved: bool) -> list[str]:
    errors: list[str] = []
    if approved is not True:
        errors.append("Confirme a revisão antes de gerar o carrinho.")
    if not lines:
        errors.append("Selecione pelo menos um produto do catálogo.")
    quantities: dict[tuple[str, str | None], int] = defaultdict(int)
    products = {}
    for line in lines:
        product = line.product
        label = product.name
        if type(line.quantity) is not int or not 1 <= line.quantity <= 999:
            errors.append(f"{label}: informe uma quantidade inteira entre 1 e 999.")
        else:
            key = (product.id, product.variant_id)
            quantities[key] += line.quantity
            products[key] = product
        if not product.id:
            errors.append(f"{label}: identificador de catálogo ausente.")
        if product.available is not True:
            errors.append(f"{label}: produto indisponível ou disponibilidade não confirmada.")
        if not isinstance(product.price, Decimal) or not product.price.is_finite() or product.price <= 0:
            errors.append(f"{label}: preço de catálogo inválido.")
    for key, quantity in quantities.items():
        product = products[key]
        if product.stock is not None and quantity > product.stock:
            errors.append(f"{product.name}: quantidade total ({quantity}) supera o estoque ({product.stock}).")
    return list(dict.fromkeys(errors))
