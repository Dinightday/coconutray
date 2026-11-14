"""Full HTTP search -> multiple authorized MCP transports, entirely simulated."""
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from coconutray.api import create_app
from coconutray.network_data import now
from coconutray.network_models import Store
from coconutray.tray import TrayClient


@pytest.mark.parametrize('fail_second',[False,True])
def test_multiple_authorized_mcp_connections_and_individual_failure(fail_second):
    path=Path(__file__).parents[1]/'artifacts'/'api-tests'/f'{uuid4().hex}.sqlite3'
    application=create_app(path)
    for identity in ('origin','partner1','partner2'):
        application.state.repository.add_store(Store(identity,identity,f'https://{identity}.example',mode='real',consent_at=now(),endpoint_checked_at=now()))
    calls=[]
    def transport(request):
        host=request.headers['host']; body=json.loads(request.content);calls.append((host,body['method']))
        if fail_second and host=='partner2.example': return httpx.Response(503)
        if body['method']=='notifications/initialized': return httpx.Response(202)
        if body['method']=='initialize': result={'protocolVersion':'2025-06-18','capabilities':{'tools':{}}}
        else:
            tool=body['params']['name']
            if tool=='product-search': data={'products':[{'id':'12','name':'Mochila preta','price':'149.90','available':'1','stock':'4','link':f'https://{host}/oferta-real-retornada','image':f'https://images.tcdn.com.br/img/img_prod/123/{host}.jpg','specs':{'cor':'preto'}}]}
            elif tool=='variants': data={'variants':[]}
            else: raise AssertionError(tool)
            result={'isError':False,'content':[{'type':'text','text':json.dumps(data)}]}
        return httpx.Response(200,json={'jsonrpc':'2.0','id':body['id'],'result':result})
    def factory(domain,authorized_domains,**kwargs):
        return TrayClient(domain,authorized_domains,transport=httpx.MockTransport(transport),**kwargs)
    with patch('coconutray.tray.socket.getaddrinfo',return_value=[(2,1,6,'',('1.1.1.1',443))]),patch('coconutray.network_search.TrayClient',side_effect=factory),TestClient(application) as client:
        response=client.post('/api/public/search',json={'store_id':'origin','query':'Mochila preta'})
    assert response.status_code==200
    result=response.json()
    assert result['mode']=='real' and result['consulted']==2
    assert result['responded']==(1 if fail_second else 2)
    assert len(result['offers'])==(1 if fail_second else 2)
    assert set(h for h,_ in calls)=={'partner1.example','partner2.example'}
    assert all(o['product']['source']=='tray' and o['product']['price']=='149.90' for o in result['offers'])
    assert all(o['product']['image_url'].startswith('https://images.tcdn.com.br/') for o in result['offers'])
    assert all(o['product']['url']==o['store']['domain']+'/oferta-real-retornada' for o in result['offers'])
    assert result['failed_stores']==(['partner2'] if fail_second else [])
