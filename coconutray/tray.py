"""Small Streamable HTTP MCP client for a single explicitly authorized Tray store.

Protocol/arguments: https://developers.tray.com.br/#novo-mcp-storefront
No payment operations or automatic retries are implemented here.
"""
from __future__ import annotations

import ipaddress
import json
import math
import re
import socket
from time import monotonic
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from coconutray.models import CartResult, Product, ReviewLine

PROTOCOL = "2025-06-18"
TOOLS = frozenset({"store-info", "product-search", "products", "variants", "add-to-cart"})
MAX_RESPONSE = 5 * 1024 * 1024


class IntegrationError(RuntimeError):
    """A safe-to-display failure without customer conversation or credentials."""


class PartialCartError(IntegrationError):
    """A write may have succeeded. Never retry blindly."""

    def __init__(self, message: str, *, cart_url: str | None = None, completed_items: int = 0):
        super().__init__(message)
        self.cart_url = cart_url
        self.completed_items = completed_items


def _hostname(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value):
        raise IntegrationError("Informe o domínio HTTPS canônico da loja.")
    raw = value if "://" in value else "https://" + value
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise IntegrationError("Domínio de loja inválido.") from exc
    if (parsed.scheme != "https" or parsed.username or parsed.password or port is not None
            or parsed.query or parsed.fragment
            or parsed.path not in ("", "/", "/mcp-app/storefront")):
        raise IntegrationError("Use somente o domínio HTTPS da loja, sem porta, credenciais ou parâmetros.")
    try:
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise IntegrationError("Domínio de loja inválido.") from exc
    if (not host or len(host) > 253 or "." not in host
            or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in host.split("."))):
        raise IntegrationError("Informe um domínio público válido da loja.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise IntegrationError("Endereços IP não são aceitos como lojas.")


def _public_addresses(host: str) -> list[str]:
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise IntegrationError("Não foi possível localizar o domínio da loja.") from exc
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses:
        raise IntegrationError("O domínio da loja não possui endereço público.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise IntegrationError("O domínio retornou um endereço inválido.") from exc
        if not ip.is_global or (getattr(ip, "ipv4_mapped", None) and not ip.ipv4_mapped.is_global):
            raise IntegrationError("A conexão exige uma loja com endereço público; redes internas são bloqueadas.")
    return addresses


def validate_store_domain(domain: str, authorized_domains: Iterable[str]) -> str:
    host = _hostname(domain)
    allowed = {_hostname(item) for item in authorized_domains}
    if host not in allowed:
        raise IntegrationError("Este domínio não está na lista de lojas autorizadas.")
    _public_addresses(host)
    return "https://" + host


def validate_offer_url(url: Any, domain: str, *, resolve: bool = True) -> str:
    """Validate a supplied link, preserving its opaque path/query exactly.

    This checks the destination, not whether a product exists or a visit occurred.
    The caller must obtain the link from the authorized store, never synthesize it.
    """
    host = _hostname(domain)
    if (not isinstance(url, str) or not url or len(url) > 4096
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
            or "\\" in url):
        raise IntegrationError("A loja não informou um link de oferta válido.")
    try:
        parsed = urlsplit(url)
        target = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        valid = (parsed.scheme == "https" and target == host and parsed.port is None
                 and not parsed.username and not parsed.password and not parsed.fragment)
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise IntegrationError("O link da oferta não pertence ao domínio HTTPS autorizado.")
    if resolve:
        _public_addresses(host)
    return url


def _supplied_url(data: dict, *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, dict):
            value = value.get("https")
        if isinstance(value, str) and value:
            return value
    return None


def validate_image_url(url: Any, domain: str, *, resolve: bool = True) -> str:
    """Only supplied HTTPS images on the store or documented Tray image hosts."""
    if not isinstance(url, str):
        raise IntegrationError("Imagem não informada pelo catálogo.")
    try:
        host = urlsplit(url).hostname
    except ValueError as exc:
        raise IntegrationError("Imagem inválida.") from exc
    allowed = {_hostname(domain), "images.tcdn.com.br", "images1.tcdn.com.br", "images2.tcdn.com.br", "images3.tcdn.com.br", "images4.tcdn.com.br"}
    if host not in allowed:
        raise IntegrationError("A imagem não pertence à loja ou aos servidores de imagem documentados da Tray.")
    return validate_offer_url(url,"https://"+host,resolve=resolve)


def _attributes(data: dict) -> dict[str, str]:
    """Keep only supplied scalar property values; never infer them from prose."""
    result: dict[str, str] = {}
    for field in ("specs", "properties", "attributes", "Sku", "sku"):
        values = data.get(field)
        if isinstance(values, dict):
            pairs = values.items()
        elif isinstance(values, list):
            pairs = ((v.get("type", v.get("name")), v.get("value"))
                     for v in values[:100] if isinstance(v, dict))
        else:
            continue
        for key, value in pairs:
            if (isinstance(key, str) and key.strip() and len(key) <= 100
                    and isinstance(value, (str, int, float)) and not isinstance(value, bool)):
                text = str(value).strip()
                if text and len(text) <= 300:
                    result[key.strip()] = text
    return result


def _safe_link(value: Any, domain: str | None) -> str | None:
    if domain is None or value is None:
        return None
    try:
        # The request already resolved and pinned this store's public address.
        # Links are resolved again by the UI immediately before handoff.
        return validate_offer_url(value, domain, resolve=False)
    except IntegrationError:
        return None


def _image(data: dict) -> str | None:
    single = _supplied_url(data, "image", "image_url")
    if single and single.startswith("https://"):
        return single
    images = data.get("images", data.get("ProductImage"))
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            nested = first.get("ProductImage", first)
            if isinstance(nested, dict):
                return _supplied_url(nested, "https", "url", "full")
    return None


def _money(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise IntegrationError("O catálogo não informou um preço válido.")
    cleaned = str(value).replace("R$", "").replace("\u00a0", "").replace(" ", "").strip()
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        result = Decimal(cleaned)
    except InvalidOperation as exc:
        raise IntegrationError("O catálogo não informou um preço válido.") from exc
    if not result.is_finite() or result <= 0 or result.as_tuple().exponent < -2:
        raise IntegrationError("O catálogo não informou um preço válido em reais.")
    return result


def _id(value: Any) -> str:
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise IntegrationError("O catálogo retornou um identificador de produto ou variação inválido.")
    return str(value)


def _unwrap(row: Any) -> dict:
    if not isinstance(row, dict):
        raise IntegrationError("O catálogo retornou um produto inválido.")
    for key in ("Product", "Variant"):
        if isinstance(row.get(key), dict):
            return row[key]
    return row


def _rows(data: Any, keys: tuple[str, ...]) -> list[dict]:
    if isinstance(data, list):
        return [_unwrap(row) for row in data]
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                return _rows(data[key], keys)
        if "data" in data:
            return _rows(data["data"], keys)
        if "id" in data or "product_id" in data or "variant_id" in data:
            return [_unwrap(data)]
        if not data:
            return []
    raise IntegrationError("O catálogo retornou um formato não reconhecido.")


def _truth(value: Any) -> bool | None:
    if value is True or value == "true" or value == "1" or type(value) is int and value == 1:
        return True
    if value is False or value == "false" or value == "0" or type(value) is int and value == 0:
        return False
    return None


def _product(row: dict, *, base: dict | None = None, domain: str | None = None) -> Product:
    is_variant = base is not None
    parent = base if is_variant else row
    product_id = _id(parent.get("id", parent.get("product_id")))
    variant_id = _id(row.get("id", row.get("variant_id"))) if is_variant else None
    name = str(parent.get("name", "")).strip()
    if not name:
        raise IntegrationError("O catálogo não informou o nome do produto.")
    summary = row.get("price_summary") or {}
    if not isinstance(summary, dict):
        summary = {}
    price_value = summary.get("display_price")
    if price_value is None:
        promotional = row.get("promotional_price")
        if promotional not in (None, "", 0, "0", "0.00", "0,00"):
            price_value = promotional
        else:
            price_value = row.get("price")
    price = _money(price_value)
    raw_stock = row.get("stock")
    stock = int(str(raw_stock)) if not isinstance(raw_stock, bool) and re.fullmatch(r"[0-9]+", str(raw_stock)) else None
    flag = _truth(row.get("available", row.get("availability")))
    available = flag is True or flag is None and stock is not None and stock > 0
    if stock == 0 or flag is False:
        available = False
    label = None
    if is_variant:
        skus = row.get("Sku", row.get("sku", row.get("attributes", [])))
        if isinstance(skus, list):
            label = " · ".join(str(s.get("value", s.get("name", ""))) for s in skus if isinstance(s, dict)).strip(" ·")
        label = label or str(row.get("name", row.get("label", ""))).strip() or f"Variação {variant_id}"
    attributes = _attributes(parent)
    if is_variant:
        # A concrete variation replaces the matching parent property.
        # Normalize key casing so "cor" replaces "Cor" rather than coexisting.
        for key, value in _attributes(row).items():
            for previous in list(attributes):
                if previous.casefold() == key.casefold():
                    del attributes[previous]
            attributes[key] = value
    product_url = _supplied_url(row, "link", "url", "product_url")
    if not product_url and is_variant:
        product_url = _supplied_url(parent, "link", "url", "product_url")
    image_url = _image(row) or (_image(parent) if is_variant else None)
    description = row.get("description", parent.get("description", ""))
    description = description[:6000] if isinstance(description, str) else ""
    try:
        image_url = validate_image_url(image_url,domain,resolve=False) if image_url and domain else None
    except IntegrationError:
        image_url = None
    return Product(product_id, name, price, available, stock, variant_id, label,
                   description=description, source="tray",
                   url=_safe_link(product_url, domain), image_url=image_url,
                   attributes=attributes, availability_known=flag is not None or stock is not None)


class TrayClient:
    def __init__(self, domain: str, authorized_domains: Iterable[str], *,
                 transport: httpx.BaseTransport | None = None, timeout: float = 25,
                 total_timeout: float = 12):
        if (isinstance(total_timeout, bool) or not isinstance(total_timeout, (int, float))
                or not math.isfinite(total_timeout) or total_timeout <= 0):
            raise ValueError("O prazo da consulta deve ser um número positivo de segundos.")
        self.origin = validate_store_domain(domain, authorized_domains)
        self.host = urlsplit(self.origin).hostname
        self.endpoint = self.origin + "/mcp-app/storefront"
        self._http = httpx.Client(transport=transport, timeout=timeout, follow_redirects=False, trust_env=False)
        self._rpc_id = 0
        self._mcp_session: str | None = None
        self._initialized = False
        self._protocol = PROTOCOL
        self.total_timeout = float(total_timeout)
        self._request_timeout = timeout
        self._deadline: float | None = None

    def close(self) -> None:
        self._http.close()

    def _remaining(self) -> float:
        if self._deadline is None:
            return self._request_timeout
        remaining = self._deadline - monotonic()
        if remaining <= 0:
            raise IntegrationError("A consulta desta loja atingiu o limite de espera.")
        return min(self._request_timeout, remaining)

    def _post(self, payload: dict, *, notification: bool = False) -> dict:
        # Pin the validated IP for the actual socket, retaining TLS SNI and Host.
        # A second DNS lookup inside the HTTP transport cannot rebind to a private IP.
        self._remaining()
        address = _public_addresses(self.host)[0]
        remaining = self._remaining()
        address_literal = f"[{address}]" if ":" in address else address
        pinned_url = f"https://{address_literal}/mcp-app/storefront"
        headers = {"Accept": "application/json, text/event-stream", "Host": self.host,
                   "MCP-Protocol-Version": self._protocol}
        if self._mcp_session:
            headers["Mcp-Session-Id"] = self._mcp_session
        try:
            response = self._http.post(pinned_url, json=payload, headers=headers,
                                       timeout=remaining,
                                       extensions={"sni_hostname": self.host})
        except httpx.HTTPError as exc:
            raise IntegrationError("A loja não respondeu. Verifique a conexão e tente novamente.") from exc
        self._remaining()
        if 300 <= response.status_code < 400:
            raise IntegrationError("A loja redirecionou a conexão. Informe seu domínio canônico, incluindo www quando necessário.")
        if response.status_code >= 400:
            raise IntegrationError(f"A loja recusou a conexão (HTTP {response.status_code}).")
        if len(response.content) > MAX_RESPONSE:
            raise IntegrationError("A resposta da loja excedeu o limite permitido.")
        session = response.headers.get("Mcp-Session-Id")
        if session:
            self._mcp_session = session
        if notification and response.status_code in (200, 202, 204):
            return {}
        try:
            if "text/event-stream" in response.headers.get("content-type", ""):
                envelopes = []
                for block in response.text.replace("\r\n", "\n").split("\n\n"):
                    raw = "\n".join(line[5:].lstrip(" ") for line in block.splitlines() if line.startswith("data:"))
                    if raw and raw != "[DONE]":
                        envelopes.append(json.loads(raw))
                data = next((v for v in envelopes if isinstance(v, dict) and v.get("id") == payload.get("id")), None)
            else:
                data = response.json()
        except (ValueError, TypeError) as exc:
            raise IntegrationError("A loja retornou uma resposta MCP inválida.") from exc
        if not isinstance(data, dict) or data.get("jsonrpc") != "2.0" or data.get("id") != payload.get("id"):
            raise IntegrationError("A loja retornou uma resposta MCP incompatível.")
        if data.get("error"):
            raise IntegrationError("O servidor MCP da loja recusou a solicitação.")
        result = data.get("result")
        if not isinstance(result, dict):
            raise IntegrationError("A loja retornou um resultado MCP inválido.")
        return result

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._rpc_id += 1
        payload = {"jsonrpc": "2.0", "id": self._rpc_id, "method": method}
        if params is not None:
            payload["params"] = params
        return self._post(payload)

    def _initialize(self) -> None:
        if self._initialized:
            return
        result = self._rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                          "clientInfo": {"name": "coconutray", "version": "0.1.0"}})
        if result.get("protocolVersion") not in (PROTOCOL, "2025-11-25", "2025-03-26", "2024-11-05"):
            raise IntegrationError("A loja usa uma versão MCP incompatível com este protótipo.")
        self._protocol = result["protocolVersion"]
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, notification=True)
        self._initialized = True

    def _call(self, tool: str, arguments: dict) -> Any:
        if tool not in TOOLS:
            raise IntegrationError("Esta operação não faz parte do protótipo.")
        self._initialize()
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments})
        if result.get("isError"):
            raise IntegrationError(f"A ferramenta {tool} da loja não concluiu a solicitação.")
        if isinstance(result.get("structuredContent"), (dict, list)):
            data = result["structuredContent"]
        else:
            contents = result.get("content", [])
            data = None
            for content in contents if isinstance(contents, list) else []:
                if isinstance(content, dict) and content.get("type") == "text":
                    try:
                        data = json.loads(content.get("text", ""))
                        break
                    except (ValueError, TypeError):
                        continue
            if data is None:
                raise IntegrationError("A loja não retornou dados estruturados reconhecíveis.")
        if isinstance(data, dict) and (data.get("error") or data.get("success") is False
                                      or isinstance(data.get("status"), int) and data["status"] >= 400):
            raise IntegrationError(f"A ferramenta {tool} da loja informou uma falha.")
        return data

    def store_info(self) -> dict:
        result = self._call("store-info", {})
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            result = result["data"]
        if not isinstance(result, dict) or not result.get("store_id"):
            raise IntegrationError("O domínio não retornou as informações de uma loja Tray compatível.")
        return result

    def _expand(self, row: dict) -> list[Product]:
        product_id = _id(row.get("id", row.get("product_id")))
        data = self._call("variants", {"product_id": product_id, "limit": 100})
        variants = _rows(data, ("variants", "Variants", "Variant"))
        if variants:
            return [_product(variant, base=row, domain=self.origin) for variant in variants]
        # An explicit variation marker with no returned variants must not create a base item.
        if any(_truth(row.get(key)) is True for key in ("has_variation", "has_variations", "has_variant", "has_variants")):
            return []
        return [_product(row, domain=self.origin)]

    def search(self, query: str) -> list[Product]:
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            raise IntegrationError("Informe um termo de produto com até 300 caracteres.")
        previous_deadline = self._deadline
        self._deadline = monotonic() + self.total_timeout
        try:
            return self._search(query)
        finally:
            self._deadline = previous_deadline

    def _search(self, query: str) -> list[Product]:
        code = re.fullmatch(r"(?:(?:c[oó]digo(?:\s+do\s+produto)?|produto)\s*[:#]?\s*)?([1-9][0-9]*)", query.strip(), re.IGNORECASE)
        if code:
            # Tray explicitly treats a numeric code as a product ID, not a search term.
            data = self._call("products", {"id": int(code.group(1))})
        else:
            data = self._call("product-search", {"query": query.strip(), "limit": 10})
        rows = _rows(data, ("products", "Products", "Product"))
        products = []
        for row in rows[:10]:
            products.extend(self._expand(row))
        unique = {(p.id, p.variant_id): p for p in products}
        return list(unique.values())

    def products(self) -> list[Product]:
        data = self._call("products", {"page": 1, "limit": 20})
        return [p for row in _rows(data, ("products", "Products", "Product")) for p in self._expand(row)]

    def _refresh(self, product: Product) -> Product:
        data = self._call("products", {"id": int(_id(product.id))})
        candidates = _rows(data, ("products", "Products", "Product"))
        row = next((row for row in candidates if str(row.get("id", row.get("product_id"))) == product.id), None)
        if row is None:
            raise IntegrationError("Um produto revisado não está mais no catálogo. Analise a conversa novamente.")
        matches = self._expand(row)
        current = next((p for p in matches if p.id == product.id and p.variant_id == product.variant_id), None)
        if (current is None or current.price != product.price
                or current.name != product.name or current.variant_label != product.variant_label
                or current.attributes != product.attributes):
            raise IntegrationError("O produto, a variação ou o preço mudou. Analise e revise novamente antes de gerar o carrinho.")
        return current

    def _cart_url(self, url: Any) -> str:
        if not isinstance(url, str) or any(c.isspace() for c in url):
            raise IntegrationError("A loja não retornou um link de carrinho válido.")
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme == "https" and parsed.hostname == self.host and parsed.port is None
                     and not parsed.username and not parsed.password and not parsed.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise IntegrationError("O link de carrinho retornado não pertence ao domínio HTTPS autorizado.")
        _public_addresses(self.host)
        return url  # Preserve the store's opaque URL exactly, including query encoding.

    def create_cart(self, lines: list[ReviewLine], *, approved: bool) -> CartResult:
        from coconutray.validation import validate_review

        errors = validate_review(lines, approved=approved)
        if errors:
            raise IntegrationError(" ".join(errors))
        if any(line.product.source != "tray" for line in lines):
            raise IntegrationError("Um carrinho real exige produtos verificados nesta loja Tray.")
        if any(line.quantity > 100 for line in lines):
            raise IntegrationError("A Tray aceita no máximo 100 unidades por item nesta integração.")
        fresh = [ReviewLine(self._refresh(line.product), line.quantity, line.request_index) for line in lines]
        errors = validate_review(fresh, approved=approved)
        if errors:
            raise IntegrationError("O catálogo mudou: " + " ".join(errors))
        # Cart session stays in this method: every approved operation begins a new shopper cart.
        shopper_session = None
        cart_url = None
        added = 0
        try:
            for line in fresh:
                arguments = {"product_id": line.product.id, "quantity": line.quantity}
                if line.product.variant_id:
                    arguments["variant_id"] = line.product.variant_id
                if shopper_session:
                    arguments["session_id"] = shopper_session
                result = self._call("add-to-cart", arguments)
                if isinstance(result, dict) and isinstance(result.get("data"), dict):
                    result = result["data"]
                if not isinstance(result, dict) or not isinstance(result.get("session_id"), str) or not result["session_id"]:
                    raise IntegrationError("A loja não devolveu uma sessão de carrinho válida.")
                if shopper_session is not None and result["session_id"] != shopper_session:
                    raise IntegrationError("A loja mudou a sessão durante a criação do carrinho.")
                shopper_session = result["session_id"]
                cart_url = self._cart_url(result.get("cart_url"))
                added += 1
        except IntegrationError as exc:
            raise PartialCartError(
                "O carrinho pode ter sido criado parcialmente. Não repita a operação automaticamente; "
                "verifique o carrinho na loja antes de continuar. " + str(exc),
                cart_url=cart_url, completed_items=added,
            ) from exc
        return CartResult(shopper_session or str(uuid4()), cart_url, "real", fresh,
                          "Carrinho criado na Tray. A compra e o pagamento serão concluídos no checkout oficial.")
