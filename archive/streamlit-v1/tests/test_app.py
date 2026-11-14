"""Native Streamlit acceptance flow for cooperative discovery, without live services."""
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from coconutray.network_data import NetworkRepository

ROOT = Path(__file__).parents[1]
TEST_ARTIFACTS = ROOT / "artifacts" / "app-tests"
TEST_ARTIFACTS.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("COCONUTRAY_DB_PATH", str(TEST_ARTIFACTS / f"{uuid4().hex}.sqlite3"))
    result = AppTest.from_file(str(ROOT / "app.py"), default_timeout=15).run()
    assert not result.exception
    return result


def button(app, label):
    return next(widget for widget in app.button if widget.label.startswith(label))


def search(app, query="Mochila preta impermeável até R$ 180"):
    app.text_input(key="query").set_value(query)
    button(app, "Buscar nas lojas").click().run()
    assert not app.exception
    assert app.session_state["report"] is not None
    return app.session_state["report"]


def metrics(mode="demo"):
    return NetworkRepository(os.environ["COCONUTRAY_DB_PATH"]).metrics(mode)


def open_match(app, selected):
    app.button(key="detail_" + selected.key).click().run()
    assert not app.exception
    assert app.session_state["selected_key"] == selected.key


def test_landing_and_all_pages_render_with_persistent_demo_label(app):
    assert app.session_state["page"] == "Buscar produtos"
    assert app.session_state["report"] is None
    assert len(app.metric) == 0
    for page in ("Lojas participantes", "Atividade da rede", "Buscar produtos"):
        app.radio(key="page").set_value(page).run()
        assert not app.exception
        assert any("Demonstração: lojas, produtos" in message.value for message in app.info)
    assert metrics()["searches"] == 0


def test_example_loads_query_without_submitting_a_search(app):
    button(app, "Mochila impermeável").click().run()
    assert app.text_input(key="query").value == "Mochila preta impermeável até R$ 180"
    assert app.session_state["report"] is None
    assert metrics()["searches"] == 0


def test_consumer_compares_partner_offers_and_unknowns_without_invented_distance(app):
    report = search(app)
    assert len({m.store.id for m in report.matches if m.status == "compatible"}) >= 2
    assert any(m.status == "uncertain" for m in report.matches)
    assert all(m.distance_km is None for m in report.matches)
    assert "Mais próximo" not in app.selectbox(key="sort_order").options
    app.selectbox(key="sort_order").set_value("Menor preço").run()
    assert not app.exception and metrics()["searches"] == 1
    app.run()
    assert metrics()["searches"] == 1
    selected = next(m for m in report.matches if m.status == "compatible")
    open_match(app, selected)
    assert metrics()["offer_click"] == 1
    assert not any(widget.label == "Preparar indicação" for widget in app.button)
    assert all(m.product.url is None for m in report.matches)
    button(app, "Simular acesso à oferta").click().run()
    assert not app.exception
    assert metrics()["buy_link_requested"] == 1
    assert metrics()["cart_created"] == 0
    assert any("não tem checkout ou pagamento" in message.value for message in app.info)
    assert not app.get("link_button")


def test_merchant_full_demo_prepares_manual_referral_and_simulates_offer_access(app):
    app.radio(key="audience").set_value("Para um cliente").run()
    assert app.selectbox(key="origin_id").value == "demo-ipe"
    report = search(app)
    assert app.session_state["search_origin_id"] == "demo-ipe"
    assert all(m.store.id != "demo-ipe" for m in report.matches)
    assert all(m.distance_km is not None for m in report.matches)
    assert "Mais próximo" in app.selectbox(key="sort_order").options
    selected = next(m for m in report.matches if m.status == "compatible")
    open_match(app, selected)
    button(app, "Preparar indicação").click().run()
    assert not app.exception
    assert app.session_state["share_ready"] == selected.key
    assert metrics()["referrals"] == 1
    assert any("Nenhuma mensagem foi enviada automaticamente" in caption.value for caption in app.caption)
    code = app.get("code")[0].value
    assert "DEMONSTRAÇÃO" in code
    assert "Papelaria Ipê" in code and selected.store.name in code
    assert "Não há link de compra real" in code
    button(app, "Preparar indicação").click().run()
    assert metrics()["referrals"] == 1
    button(app, "Simular acesso à oferta").click().run()
    assert metrics()["buy_link_requested"] == 1
    button(app, "← Voltar aos resultados").click().run()
    assert not app.exception and app.session_state["selected_key"] is None
    app.radio(key="page").set_value("Atividade da rede").run()
    assert not app.exception
    values = {metric.label: metric.value for metric in app.metric}
    assert values["Pesquisas realizadas"] == "1"
    assert values["Indicações preparadas"] == "1"
    assert values["Acessos solicitados"] == "1"
    assert any("não representam vendas" in message.value for message in app.info)
    assert metrics("real")["searches"] == 0


def test_changing_audience_clears_previous_consumer_results(app):
    search(app)
    app.radio(key="audience").set_value("Para um cliente").run()
    assert not app.exception
    assert app.session_state["report"] is None
    assert app.session_state["search_id"] is None
    assert metrics()["searches"] == 1


def test_real_mode_without_participants_has_no_demo_results_or_automatic_query(app):
    search(app)
    app.selectbox(key="environment").set_value("Lojas reais").run()
    assert not app.exception
    assert app.session_state["report"] is None
    assert app.session_state["selected_key"] is None
    assert button(app, "Buscar nas lojas").disabled
    assert any("Ainda não há lojas reais cadastradas" in message.value for message in app.warning)
    assert metrics("real")["searches"] == 0
    app.radio(key="page").set_value("Atividade da rede").run()
    assert all(metric.value == "0" for metric in app.metric)
    app.selectbox(key="environment").set_value("Demonstração").run()
    assert app.session_state["page"] == "Buscar produtos"
    assert app.session_state["report"] is None
    assert not button(app, "Buscar nas lojas").disabled


def test_real_registration_without_authorization_makes_no_network_request(app):
    app.selectbox(key="environment").set_value("Lojas reais").run()
    app.radio(key="page").set_value("Lojas participantes").run()
    next(w for w in app.text_input if w.label == "Nome da loja").set_value("Loja autorizada")
    next(w for w in app.text_input if w.label == "Site oficial da loja").set_value("https://loja.example")
    with patch("coconutray.interface.TrayClient") as client:
        button(app, "Conferir conexão e cadastrar").click().run()
    assert not app.exception
    client.assert_not_called()
    assert any("autorização" in message.value for message in app.warning)
    assert NetworkRepository(os.environ["COCONUTRAY_DB_PATH"]).stores("real") == []


def test_real_registration_validates_partial_coordinates_before_connecting(app):
    app.selectbox(key="environment").set_value("Lojas reais").run()
    app.radio(key="page").set_value("Lojas participantes").run()
    next(w for w in app.text_input if w.label == "Nome da loja").set_value("Loja autorizada")
    next(w for w in app.text_input if w.label == "Site oficial da loja").set_value("https://loja.example")
    next(w for w in app.text_input if w.label == "Latitude").set_value("-23,55")
    app.checkbox[0].check()
    with patch("coconutray.interface.TrayClient") as client:
        button(app, "Conferir conexão e cadastrar").click().run()
    assert not app.exception
    client.assert_not_called()
    assert any("duas coordenadas válidas" in message.value for message in app.error)


def test_authorized_real_registration_checks_store_identity_then_persists(app):
    app.selectbox(key="environment").set_value("Lojas reais").run()
    app.radio(key="page").set_value("Lojas participantes").run()
    next(w for w in app.text_input if w.label == "Nome da loja").set_value("Nome informado")
    next(w for w in app.text_input if w.label == "Site oficial da loja").set_value("https://loja.example")
    app.checkbox[0].check()
    with patch("coconutray.interface.TrayClient") as client_type:
        client = client_type.return_value
        client.origin = "https://loja.example"
        client.store_info.return_value = {"store_id": "42", "store_name": "Nome retornado pela Tray"}
        button(app, "Conferir conexão e cadastrar").click().run()
        client.store_info.assert_called_once_with()
        client.close.assert_called_once_with()
    assert not app.exception
    stores = NetworkRepository(os.environ["COCONUTRAY_DB_PATH"]).stores("real")
    assert len(stores) == 1
    assert stores[0].name == "Nome informado"
    assert stores[0].tray_name == "Nome retornado pela Tray"
    assert stores[0].consent_at and stores[0].endpoint_checked_at
    assert stores[0].latitude is None and stores[0].longitude is None
    assert any("Loja cadastrada" in message.value for message in app.success)


def test_pausing_participant_clears_results_and_does_not_reseed_it_active(app):
    search(app)
    app.radio(key="page").set_value("Lojas participantes").run()
    app.button(key="active_demo-passo").click().run()
    assert not app.exception
    assert app.session_state["report"] is None
    repository = NetworkRepository(os.environ["COCONUTRAY_DB_PATH"])
    assert len(repository.stores("demo")) == 3
    app.radio(key="page").set_value("Buscar produtos").run()
    report = search(app)
    assert report.consulted == 3
    assert all(selected.store.id != "demo-passo" for selected in report.matches)


def test_empty_or_unmatched_search_has_clear_state_without_fabricated_offer(app):
    button(app, "Buscar nas lojas").click().run()
    assert not app.exception
    assert app.session_state["report"] is None
    assert app.error
    assert metrics()["searches"] == 0
    report = search(app, "Produto que não existe")
    assert report.matches == []
    assert metrics()["without_results"] == 1
    assert not any(widget.label.startswith("Ver detalhes") for widget in app.button)


def test_size_filter_in_ui_keeps_only_requested_variations(app):
    report = search(app, "Tênis de corrida masculino preto tamanho 42")
    assert len(report.matches) >= 2
    assert all(selected.product.attributes["tamanho"] == "42" for selected in report.matches)
    assert all(selected.product.attributes["cor"] == "preto" for selected in report.matches)
