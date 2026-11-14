"""Network-free contract/security tests against the documented Tray MCP flow."""
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

import httpx

from coconutray.models import Product, ReviewLine
from coconutray.tray import IntegrationError, PartialCartError, TrayClient, validate_offer_url, validate_store_domain


DNS = [(2, 1, 6, "", ("1.1.1.1", 443))]
BASE = {"id": "12", "name": "Caderno universitário", "price": "19.90", "available": "1", "stock": "8"}


class FakeStore:
    def __init__(self, *, sse=False):
        self.calls = []
        self.requests = []
        self.product = dict(BASE)
        self.variants = []
        self.error_tool = None
        self.embedded_error = False
        self.cart_url = "https://loja.example/carrinho?session=a%2Bb"
        self.cart_calls = 0
        self.sse = sse

    def __call__(self, request):
        self.requests.append(request)
        payload = json.loads(request.content)
        self.calls.append(payload)
        method = payload["method"]
        headers = {"Mcp-Session-Id": "mcp-test"}
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
        else:
            tool = payload["params"]["name"]
            if self.error_tool == tool:
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"],
                                               "result": {"isError": True, "content": [{"type": "text", "text": "Failure"}]}})
            if tool == "store-info":
                data = {"store_id": "42", "store_name": "Papelaria de teste"}
            elif tool in ("product-search", "products"):
                data = {"products": [self.product]}
            elif tool == "variants":
                data = {"variants": self.variants}
            elif tool == "add-to-cart":
                self.cart_calls += 1
                data = {"session_id": "shopper-" + str(self.cart_calls), "cart_url": self.cart_url}
                if "session_id" in payload["params"]["arguments"]:
                    data["session_id"] = payload["params"]["arguments"]["session_id"]
            else:
                raise AssertionError(tool)
            if self.embedded_error:
                data = {"error": "Not found", "status": 404, "success": True}
            result = {"content": [{"type": "text", "text": json.dumps(data)}], "isError": False}
        envelope = {"jsonrpc": "2.0", "id": payload["id"], "result": result}
        if self.sse:
            return httpx.Response(200, text="event: message\ndata: " + json.dumps(envelope) + "\n\n",
                                  headers={**headers, "content-type": "text/event-stream"})
        return httpx.Response(200, json=envelope, headers=headers)


class TrayTests(unittest.TestCase):
    def setUp(self):
        self.dns = patch("coconutray.tray.socket.getaddrinfo", return_value=DNS)
        self.dns.start()
        self.addCleanup(self.dns.stop)
        self.store = FakeStore()
        self.client = TrayClient("https://loja.example", ["loja.example"], transport=httpx.MockTransport(self.store))
        self.addCleanup(self.client.close)

    def line(self, quantity=2):
        return ReviewLine(Product("12", BASE["name"], Decimal("19.90"), True, 8, source="tray"), quantity, 0)

    def test_init_notification_session_and_pinned_socket(self):
        info = self.client.store_info()
        self.assertEqual(info["store_name"], "Papelaria de teste")
        self.assertEqual([p["method"] for p in self.store.calls], ["initialize", "notifications/initialized", "tools/call"])
        for request in self.store.requests:
            self.assertEqual(request.url.host, "1.1.1.1")
            self.assertEqual(request.headers["host"], "loja.example")
            self.assertEqual(request.extensions["sni_hostname"], "loja.example")
        self.assertEqual(self.store.requests[-1].headers["mcp-session-id"], "mcp-test")

    def test_sse_response_supported(self):
        self.store.sse = True
        self.assertEqual(self.client.store_info()["store_id"], "42")

    def test_explicit_domain_authorization(self):
        with self.assertRaises(IntegrationError):
            validate_store_domain("loja.example", ["outra.example"])
        for invalid in ["http://loja.example", "https://u:p@loja.example", "https://loja.example:443", "https://loja.example/a", "127.0.0.1", "https://loja.example?x=1"]:
            with self.subTest(invalid=invalid), self.assertRaises(IntegrationError):
                validate_store_domain(invalid, ["loja.example"])

    def test_private_and_mixed_dns_blocked(self):
        for ip in ["127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "::1", "fd00::1"]:
            with self.subTest(ip=ip), patch("coconutray.tray.socket.getaddrinfo", return_value=DNS + [(2, 1, 6, "", (ip, 443))]), self.assertRaises(IntegrationError):
                self.client.store_info()
        self.assertEqual(self.store.calls, [])

    def test_redirect_not_followed(self):
        client = TrayClient("loja.example", ["loja.example"], transport=httpx.MockTransport(lambda request: httpx.Response(301, headers={"Location": "http://127.0.0.1/"})))
        self.addCleanup(client.close)
        with self.assertRaisesRegex(IntegrationError, "redirecionou"):
            client.store_info()

    def test_search_money_and_verified_availability(self):
        self.store.product["price_summary"] = {"display_price": "R$ 1.299,90"}
        found = self.client.search("caderno")
        self.assertEqual(found[0].price, Decimal("1299.90"))
        self.assertTrue(found[0].available)
        self.assertEqual(found[0].source, "tray")
        self.store.product.pop("available")
        self.store.product.pop("stock")
        self.assertFalse(self.client.search("caderno")[0].available)

    def test_stock_zero_overrides_available_flag(self):
        self.store.product["stock"] = "0"
        self.assertFalse(self.client.search("caderno")[0].available)

    def test_offer_urls_are_supplied_preserved_and_bound_to_authorized_store(self):
        original = "https://loja.example/caderno?cor=azul%2Bpreto"
        self.store.product.update({"link": original, "image": "https://loja.example/imagens/caderno.jpg"})
        found = self.client.search("caderno")[0]
        self.assertEqual(found.url, original)
        self.assertEqual(found.image_url, "https://loja.example/imagens/caderno.jpg")
        self.assertEqual(validate_offer_url(original, "loja.example"), original)
        for invalid in ("https://outra.example/item", "http://loja.example/item", "javascript:alert(1)",
                        "https://loja.example:443/item", "https://u:p@loja.example/item",
                        "https://loja.example\\@outra.example/item", "https://loja.example/item\n"):
            with self.subTest(url=invalid), self.assertRaises(IntegrationError):
                validate_offer_url(invalid, "loja.example", resolve=False)

    def test_missing_or_external_product_links_are_not_invented(self):
        self.assertIsNone(self.client.search("caderno")[0].url)
        self.store.product.update({"url": "https://outra.example/item", "image": "https://cdn.example/item.jpg"})
        found = self.client.search("caderno")[0]
        self.assertIsNone(found.url)
        self.assertIsNone(found.image_url)
        with patch("coconutray.tray.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(IntegrationError):
                validate_offer_url("https://loja.example/item", "loja.example")

    def test_variant_preserves_product_link_and_supplied_properties(self):
        self.store.product.update({"url": {"https": "https://loja.example/caderno"},
                                   "description": "Capa resistente", "specs": {"Cor": "Preta", "Material": "Papel"}})
        self.store.variants = [{"id": "99", "price": "21.50", "stock": "3",
                                "Sku": [{"type": "cor", "value": "Azul"}, {"type": "Tamanho", "value": "A4"}]}]
        found = self.client.search("caderno")[0]
        self.assertEqual(found.url, "https://loja.example/caderno")
        self.assertEqual(found.description, "Capa resistente")
        self.assertEqual(found.attributes, {"cor": "Azul", "Material": "Papel", "Tamanho": "A4"})
        self.assertTrue(found.availability_known)

    def test_unknown_availability_remains_distinguishable_from_out_of_stock(self):
        self.store.product.pop("available")
        self.store.product.pop("stock")
        unknown = self.client.search("caderno")[0]
        self.assertFalse(unknown.available)
        self.assertFalse(unknown.availability_known)
        self.store.product["stock"] = "0"
        known = self.client.search("caderno")[0]
        self.assertFalse(known.available)
        self.assertTrue(known.availability_known)

    def test_search_total_budget_expires_before_next_network_call_and_resets(self):
        now = [10.0]
        original = self.client._http.post

        def slow_response(*args, **kwargs):
            result = original(*args, **kwargs)
            now[0] += 13
            return result

        with patch("coconutray.tray.monotonic", side_effect=lambda: now[0]), patch.object(self.client._http, "post", side_effect=slow_response):
            with self.assertRaisesRegex(IntegrationError, "limite de espera"):
                self.client.search("caderno")
        self.assertEqual(len(self.store.requests), 1)
        self.assertIsNone(self.client._deadline)
        self.assertEqual(self.client.search("caderno")[0].id, "12")

    def test_search_request_timeout_uses_remaining_total_budget(self):
        self.client.search("caderno")
        for request in self.store.requests:
            self.assertTrue(all(0 < value <= 12 for value in request.extensions["timeout"].values()))

    def test_numeric_codes_use_integer_product_lookup_not_text_search(self):
        for query in ("12", "código 12", "Código do produto: 12", "produto #12"):
            with self.subTest(query=query):
                self.store.calls.clear()
                self.assertEqual(self.client.search(query)[0].id, "12")
                calls = [c["params"] for c in self.store.calls if c["method"] == "tools/call"]
                self.assertEqual(calls[0], {"name": "products", "arguments": {"id": 12}})
                self.assertTrue(all(call["name"] != "product-search" for call in calls))

    def test_variants_use_own_price_and_stock(self):
        self.store.variants = [{"id": "99", "price": "21.50", "stock": "3", "Sku": [{"type": "Cor", "value": "Azul"}]}]
        found = self.client.search("caderno")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].variant_id, "99")
        self.assertEqual(found[0].price, Decimal("21.50"))
        self.assertEqual(found[0].variant_label, "Azul")

    def test_missing_variant_price_is_not_invented(self):
        self.store.variants = [{"id": "99", "stock": "3"}]
        with self.assertRaisesRegex(IntegrationError, "preço"):
            self.client.search("caderno")

    def test_three_error_layers(self):
        self.store.error_tool = "store-info"
        with self.assertRaises(IntegrationError):
            self.client.store_info()
        self.store.error_tool = None
        self.store.embedded_error = True
        with self.assertRaises(IntegrationError):
            self.client.store_info()
        client = TrayClient("loja.example", ["loja.example"], transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"jsonrpc": "2.0", "id": json.loads(request.content)["id"], "error": {"code": -32601}})))
        self.addCleanup(client.close)
        with self.assertRaises(IntegrationError):
            client.store_info()

    def test_cart_requires_approval_before_any_network(self):
        with self.assertRaises(IntegrationError):
            self.client.create_cart([self.line()], approved=False)
        self.assertEqual(self.store.calls, [])

    def test_cart_preflight_rejects_stale_price_before_write(self):
        self.store.product["price"] = "20.90"
        with self.assertRaisesRegex(IntegrationError, "preço mudou"):
            self.client.create_cart([self.line()], approved=True)
        self.assertEqual(self.store.cart_calls, 0)

    def test_cart_preflight_rejects_lost_stock(self):
        self.store.product["stock"] = "1"
        with self.assertRaises(IntegrationError):
            self.client.create_cart([self.line()], approved=True)
        self.assertEqual(self.store.cart_calls, 0)

    def test_cart_preflight_rejects_product_name_changed_at_same_price(self):
        self.store.product["name"] = "Outro caderno"
        with self.assertRaises(IntegrationError):
            self.client.create_cart([self.line()], approved=True)
        self.assertEqual(self.store.cart_calls, 0)

    def test_cart_preflight_rejects_changed_requirement_before_any_write(self):
        self.store.product["specs"] = {"Impermeável": "Sim", "Cor": "Preto"}
        reviewed = self.client.search("caderno")[0]
        self.store.product["specs"]["Impermeável"] = "Não"
        with self.assertRaisesRegex(IntegrationError, "revise novamente"):
            self.client.create_cart([ReviewLine(reviewed, 1, 0)], approved=True)
        self.assertEqual(self.store.cart_calls, 0)

    def test_cart_preflight_rejects_variant_label_changed_at_same_price(self):
        self.store.variants = [{"id": "99", "price": "21.50", "stock": "3", "Sku": [{"value": "Vermelho"}]}]
        reviewed = Product("12", BASE["name"], Decimal("21.50"), True, 3, "99", "Azul", source="tray")
        with self.assertRaises(IntegrationError):
            self.client.create_cart([ReviewLine(reviewed, 1, 0)], approved=True)
        self.assertEqual(self.store.cart_calls, 0)

    def test_cart_preserves_url_and_separates_shoppers(self):
        first = self.client.create_cart([self.line()], approved=True)
        second = self.client.create_cart([self.line()], approved=True)
        self.assertEqual(first.url, self.store.cart_url)
        self.assertNotEqual(first.id, second.id)
        writes = [c["params"]["arguments"] for c in self.store.calls if c.get("params", {}).get("name") == "add-to-cart"]
        self.assertTrue(all("session_id" not in args for args in writes))

    def test_multi_item_cart_reuses_only_its_session(self):
        cart = self.client.create_cart([self.line(), ReviewLine(self.line().product, 1, 1)], approved=True)
        writes = [c["params"]["arguments"] for c in self.store.calls if c.get("params", {}).get("name") == "add-to-cart"]
        self.assertNotIn("session_id", writes[0])
        self.assertEqual(writes[1]["session_id"], cart.id)

    def test_bad_cart_url_marks_uncertain_write_and_does_not_retry(self):
        self.store.cart_url = "https://evil.example/cart"
        with self.assertRaises(PartialCartError) as result:
            self.client.create_cart([self.line()], approved=True)
        self.assertIn("Não repita", str(result.exception))
        self.assertEqual(self.store.cart_calls, 1)

    def test_real_quantity_limit(self):
        line = ReviewLine(Product("12", "Caderno", Decimal("1"), True, 999, source="tray"), 101, 0)
        with self.assertRaises(IntegrationError):
            self.client.create_cart([line], approved=True)
        self.assertEqual(self.store.cart_calls, 0)

    def test_rejects_demo_product_in_real_cart(self):
        with self.assertRaises(IntegrationError):
            self.client.create_cart([ReviewLine(Product("12", "Caderno", Decimal("1"), True, 9), 1, 0)], approved=True)
        self.assertEqual(self.store.calls, [])


if __name__ == "__main__":
    unittest.main()
