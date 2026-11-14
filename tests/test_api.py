"""HTTP acceptance and ownership tests. No live merchant or paid API calls."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from coconutray.api import create_app
from coconutray.models import Product
from coconutray.network_data import now
from coconutray.network_models import Store


@pytest.fixture
def app():
    path=Path(__file__).parents[1]/"artifacts"/"api-tests"/f"{uuid4().hex}.sqlite3"
    return create_app(path)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def account(client, name="lojista"):
    credentials={"username":name,"password":"senha-de-teste-123"}
    assert client.post('/api/merchant/register',json=credentials).status_code==201
    response=client.post('/api/merchant/login',json=credentials)
    assert response.status_code==200
    return {"X-CSRF-Token":response.json()["csrf"]}


def search(client, query="Mochila preta impermeável até R$ 180"):
    response=client.post('/api/public/search',json={"store_id":"demo-ipe","query":query})
    assert response.status_code==200,response.text
    return response.json()


def test_storefront_and_partner_widget_use_same_filters(client):
    own=client.get('/api/public/storefront/demo-ipe',params={"q":"Mochila preta impermeável até R$ 180"}).json()
    assert own["offers"]==[] and not own["failed"]
    partner=search(client)
    assert partner["origin"]["id"]=="demo-ipe" and partner["mode"]=="demo"
    exact=[o for o in partner["offers"] if o["status"]=="compatible"]
    assert len({o["store"]["id"] for o in exact})==2
    assert all(Decimal(o["product"]["price"])<=180 and o["product"]["attributes"]["cor"]=="preto" for o in exact)
    assert any(o["status"]=="uncertain" for o in partner["offers"])
    assert all(o["product"]["image_url"] is None and o["product"]["url"] is None for o in partner["offers"])


def test_consumer_no_login_selection_handoff_and_idempotent_metrics(client,app):
    result=search(client);key=result["offers"][0]["key"];base=f'/api/public/searches/{result["search_id"]}'
    assert client.post(base+'/select',json={"key":key}).status_code==200
    for _ in range(2):
        handoff=client.post(base+'/handoff',json={"key":key})
        assert handoff.status_code==200 and handoff.json()["url"] is None
        assert "demonstrativo" in handoff.json()["message"]
    metrics=app.state.repository.metrics('demo')
    assert metrics["referrals"]==metrics["buy_link_requested"]==1
    assert metrics["cart_created"]==0 and app.state.repository.metrics('real')["searches"]==0


def test_cannot_submit_product_price_or_domain_to_handoff(client):
    result=search(client);url=f'/api/public/searches/{result["search_id"]}/handoff'
    assert client.post(url,json={"key":"inventada"}).status_code==400
    assert client.post(url,json={"key":result["offers"][0]["key"],"price":"1","domain":"https://evil.example"}).status_code==422
    assert client.post('/api/public/searches/forjada/handoff',json={"key":result["offers"][0]["key"]}).status_code==400


def test_paused_store_and_expired_search_cannot_forward(client,app):
    result=search(client);offer=result["offers"][0];url=f'/api/public/searches/{result["search_id"]}/handoff'
    app.state.repository.set_active(offer["store"]["id"],False)
    assert client.post(url,json={"key":offer["key"]}).status_code==400
    app.state.service.sessions.clear()
    assert 'expirou' in client.post(url,json={"key":offer["key"]}).json()["detail"]


@pytest.mark.parametrize("path",['/api/merchant/stores','/api/merchant/stores/demo-ipe/metrics','/api/merchant/session'])
def test_admin_views_require_login(client,path):
    assert client.get(path).status_code==401


def test_password_hash_session_cookie_csrf_and_logout(client,app):
    headers=account(client)
    cookie=client.cookies.get('coconutray_session')
    assert cookie
    assert client.get('/api/merchant/session').json()["username"]=='lojista'
    with app.state.repository.connection() as db:
        row=db.execute('SELECT password_hash FROM merchant_accounts').fetchone()
        token=db.execute('SELECT token_hash FROM merchant_sessions').fetchone()[0]
    assert b'senha-de-teste' not in row[0] and token!=cookie
    assert client.post('/api/merchant/logout').status_code==403
    assert client.post('/api/merchant/logout',headers=headers).status_code==200
    assert client.get('/api/merchant/stores').status_code==401


def test_bad_password_and_duplicate_username_rejected(client):
    credentials={"username":"lojista","password":"senha-de-teste-123"}
    account(client)
    assert client.post('/api/merchant/login',json={**credentials,"password":"errada"}).status_code==400
    assert client.post('/api/merchant/register',json=credentials).status_code==400
    assert client.post('/api/merchant/register',json={"username":"outro","password":"curta"}).status_code==400


def test_registration_consent_csrf_and_coordinates_before_network(client):
    headers=account(client)
    body={"name":"Loja","domain":"https://loja.example","consent":False}
    with patch('coconutray.api.TrayClient') as factory:
        assert client.post('/api/merchant/stores',json=body).status_code==403
        assert client.post('/api/merchant/stores',json=body,headers=headers).status_code==400
        assert client.post('/api/merchant/stores',json={**body,"consent":True,"latitude":-23},headers=headers).status_code==400
        factory.assert_not_called()


def test_register_verified_store_owner_can_pause_and_style_only_own_store(tmp_path):
    class VerifiedClient:
        origin='https://autorizada.example'
        def __init__(self,*a,**kw): pass
        def store_info(self): return {"store_id":"42","store_name":"Nome da Tray"}
        def close(self): pass
    application=create_app(tmp_path/'owners.sqlite3',client_factory=VerifiedClient)
    with TestClient(application) as first, TestClient(application) as second:
        one=account(first,'primeiro');two=account(second,'segundo')
        store=first.post('/api/merchant/stores',headers=one,json={"name":"Loja autorizada","domain":"https://autorizada.example","consent":True}).json()
        assert store["tray_name"]=='Nome da Tray' and store["endpoint_checked_at"]
        path=f'/api/merchant/stores/{store["id"]}'
        assert first.patch(path+'/participation',headers=one,json={"active":False}).status_code==200
        assert second.patch(path+'/participation',headers=two,json={"active":True}).status_code==403
        assert second.get(path+'/metrics').status_code==403
        assert first.put(path+'/theme',headers=one,json={"primary":"#663E26","radius":8}).status_code==200
        assert first.put(path+'/theme',headers=one,json={"primary":"url(javascript:alert(1))"}).status_code==422
        assert first.put(path+'/theme',headers=one,json={"font":"inherit; background:red"}).status_code==422
        assert first.patch(path+'/participation',headers=one,json={"active":True}).status_code==200
        assert first.get(f'/api/public/stores/{store["id"]}').json()["theme"]["primary"]=='#663E26'


def test_public_real_data_images_prices_and_official_handoff_without_live_request(tmp_path):
    class Catalog:
        def __init__(self,store): self.store=store
        def search(self,query): return [Product('12','Mochila preta',Decimal('149.90'),True,source='tray',url=self.store.domain+'/mochila',image_url='https://images.tcdn.com.br/img/img_prod/123/mochila.jpg',attributes={'cor':'preto'})]
        def close(self): pass
    application=create_app(tmp_path/'real.sqlite3',catalog_factory=Catalog)
    for identity in ('origem','parceira'):
        application.state.repository.add_store(Store(identity,identity,'https://'+identity+'.example',mode='real',consent_at=now(),endpoint_checked_at=now()))
    with TestClient(application) as c,patch('coconutray.tray._public_addresses',return_value=['1.1.1.1']):
        result=c.post('/api/public/search',json={"store_id":"origem","query":"Mochila preta"}).json()
        offer=result["offers"][0]
        assert result["mode"]=='real' and offer["product"]["price"]=='149.90'
        assert offer["product"]["image_url"].startswith('https://images.tcdn.com.br/')
        forward=c.post(f'/api/public/searches/{result["search_id"]}/handoff',json={"key":offer["key"]}).json()
        assert forward["url"]=='https://parceira.example/mochila'
        assert application.state.repository.metrics('real')["buy_link_requested"]==1
        assert application.state.repository.metrics('demo')["buy_link_requested"]==0


def test_untrusted_origin_cannot_write_or_impersonate_store(client):
    assert client.post('/api/public/search',headers={'Origin':'https://evil.example'},json={"store_id":"demo-ipe","query":"mochila"}).status_code==403
    assert client.options('/api/public/search',headers={'Origin':'https://evil.example'}).status_code==403
    r=client.options('/api/public/search',headers={'Origin':'http://127.0.0.1:8000'})
    assert r.status_code==204 and r.headers['access-control-allow-origin']=='http://127.0.0.1:8000'


def test_empty_storefront_never_silently_substitutes_demo(client):
    assert client.get('/api/public/storefront/loja-inexistente').status_code==400
    assert client.post('/api/public/search',json={"store_id":"demo-ipe","query":"produto totalmente inexistente"}).json()["offers"]==[]


def test_limits_and_extra_arguments_are_validated(client):
    assert client.post('/api/public/search',json={"store_id":"demo-ipe","query":"x"*301}).status_code==422
    assert client.post('/api/public/search',json={"store_id":"demo-ipe","query":"mochila","domain":"https://evil.example"}).status_code==422


def test_authorized_embedded_origin_can_load_module_and_api_but_not_admin(client,app):
    app.state.repository.add_store(Store('embedded','Embedded','https://embedded.example',mode='real',consent_at=now(),endpoint_checked_at=now()))
    headers={'Origin':'https://embedded.example'}
    module=client.get('/widget/coconutray-widget.js',headers=headers)
    assert module.headers.get('access-control-allow-origin')=='https://embedded.example'
    assert 'access-control-allow-credentials' not in module.headers
    config=client.get('/api/public/stores/embedded',headers=headers)
    assert config.status_code==200 and config.headers['access-control-allow-origin']=='https://embedded.example'
    assert client.post('/api/merchant/register',headers=headers,json={'username':'external','password':'not-an-admin-password'}).status_code==403
    assert client.post('/api/public/search',headers=headers,json={'store_id':'demo-ipe','query':'mochila'}).status_code==403
