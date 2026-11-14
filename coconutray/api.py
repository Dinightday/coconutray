"""FastAPI backend and production static hosting. Secrets never enter the bundle."""
from dataclasses import replace
import hmac
import os
from pathlib import Path
import re
from threading import Lock
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict, field_validator

from coconutray.auth import MerchantAuth
from coconutray.network_data import NetworkRepository, now, valid_coordinates
from coconutray.network_demo import seed_demo
from coconutray.network_models import Store
from coconutray.service import SearchService, store_json
from coconutray.tray import IntegrationError, PartialCartError, TrayClient

ROOT = Path(__file__).parents[1]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchBody(Payload):
    store_id: str = Field(min_length=1,max_length=100)
    query: str = Field(min_length=1,max_length=300)
    order: str = Field(default="relevance",pattern="^(relevance|price|distance)$")


class SelectionBody(Payload):
    key: str = Field(min_length=1,max_length=400)
    prepare_cart: bool = False


class Credentials(Payload):
    username: str = Field(min_length=3,max_length=50)
    password: str = Field(min_length=1,max_length=128)


class StoreBody(Payload):
    name: str = Field(min_length=1,max_length=100)
    domain: str = Field(min_length=1,max_length=300)
    address: str = Field(default="",max_length=250)
    categories: list[str] = Field(default_factory=list,max_length=10)
    latitude: float | None = None
    longitude: float | None = None
    consent: bool = False


class Participation(Payload):
    active: bool


class ThemeBody(Payload):
    primary: str = "#002F68"
    button: str = "#002F68"
    text: str = "#172B4D"
    background: str = "#FFFFFF"
    border: str = "#DDE3EC"
    radius: int = Field(default=14,ge=0,le=24)
    font: str = Field(default="inherit",max_length=120)

    @field_validator("primary","button","text","background","border")
    @classmethod
    def colors(cls,value):
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}",value):
            raise ValueError("Informe uma cor hexadecimal válida.")
        return value

    @field_validator("font")
    @classmethod
    def fonts(cls,value):
        if not re.fullmatch(r"[\w\s,'\"-]+",value):
            raise ValueError("Informe apenas o nome da fonte.")
        return value


def create_app(database_path=None, catalog_factory=None, client_factory=TrayClient):
    repository = NetworkRepository(database_path or os.getenv("COCONUTRAY_DB_PATH",str(ROOT/"data/network.sqlite3")))
    seed_demo(repository)
    auth = MerchantAuth(repository)
    service = SearchService(repository,catalog_factory,client_factory)
    app = FastAPI(title="Coconutray",version="1.0.0",docs_url="/api/docs",openapi_url="/api/openapi.json",redoc_url=None)
    app.state.repository, app.state.service, app.state.auth = repository,service,auth
    buckets, lock = {},Lock()
    admin_origins={"http://127.0.0.1:8000","http://localhost:8000","http://127.0.0.1:5173","http://localhost:5173"}
    admin_origins.update(x.strip() for x in os.getenv("COCONUTRAY_ADMIN_ORIGINS","").split(",") if x.strip())

    def limit(request, key, maximum=30):
        identity=(request.client.host if request.client else "local",key)
        with lock:
            current=monotonic()
            if len(buckets)>5000:
                buckets.clear()
            times=[t for t in buckets.get(identity,[]) if current-t<60]
            if len(times)>=maximum:
                raise HTTPException(429,"Muitas tentativas em pouco tempo. Aguarde um minuto e tente novamente.")
            buckets[identity]=[*times,current]

    def session(request,write=False):
        user=auth.session(request.cookies.get("coconutray_session"))
        if not user:
            raise HTTPException(401,"Entre na área do comerciante para continuar.")
        if write and not hmac.compare_digest(request.headers.get("x-csrf-token",""),user["csrf"]):
            raise HTTPException(403,"Sua sessão precisa ser atualizada. Entre novamente.")
        return user

    def owner(request,store_id,write=False):
        user=session(request,write)
        if not auth.owns(user["id"],store_id):
            raise HTTPException(403,"Você não administra esta loja.")
        return user

    @app.middleware("http")
    async def headers(request,call_next):
        origin=request.headers.get("origin")
        public_origins={s.domain for s in repository.stores("real")}
        allowed=origin in admin_origins or (origin in public_origins and request.url.path.startswith(("/api/public/","/widget/")))
        if origin and not allowed and request.method not in {"GET","HEAD"}:
            return JSONResponse({"detail":"Esta origem não está autorizada a usar a rede."},status_code=403)
        if request.method=="OPTIONS":
            response=Response(status_code=204 if allowed else 403)
        else:
            response=await call_next(request)
        if allowed:
            response.headers["Access-Control-Allow-Origin"]=origin
            response.headers["Vary"]="Origin"
            response.headers["Access-Control-Allow-Methods"]="GET,POST,PATCH,PUT,OPTIONS"
            response.headers["Access-Control-Allow-Headers"]="Content-Type,X-CSRF-Token"
            if origin in admin_origins:
                response.headers["Access-Control-Allow-Credentials"]="true"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"]="no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request,exc):
        return JSONResponse({"detail":str(exc)},status_code=400)

    @app.exception_handler(IntegrationError)
    async def integration(request,exc):
        message=("As informações da oferta mudaram. Faça uma nova busca antes de continuar."
                 if "mudou" in str(exc) else "Não foi possível concluir a conexão com a loja. Tente novamente ou confira a oferta diretamente com o estabelecimento.")
        return JSONResponse({"detail":message},status_code=502)

    @app.exception_handler(Exception)
    async def unexpected(request,exc):
        return JSONResponse({"detail":"Não foi possível concluir agora. Tente novamente em instantes."},status_code=500)

    @app.get("/api/health")
    def health():
        return {"status":"ok","version":"1.0.0","frontend":"react","ai_configured":bool(os.getenv("GEMINI_API_KEY"))}

    @app.get("/api/public/stores/{store_id}")
    def public_store(store_id:str):
        store=service.store(store_id)
        return {"store":store_json(store),"theme":auth.theme(store_id),
                "partner_count":sum(s.id!=store_id for s in repository.stores(store.mode))}

    @app.get("/api/public/storefront/{store_id}")
    def storefront(store_id:str,request:Request,q:str=""):
        if len(q)>300:
            raise ValueError("Use até 300 caracteres na pesquisa.")
        limit(request,"storefront",60)
        return service.storefront(store_id,q)

    @app.post("/api/public/search")
    def search(body:SearchBody,request:Request):
        limit(request,"search")
        origin=request.headers.get("origin")
        store=service.store(body.store_id)
        # An embedded storefront may only attribute opportunities to itself.
        if origin and origin not in admin_origins and origin!=store.domain:
            raise HTTPException(403,"Esta vitrine não corresponde à loja de origem.")
        return service.search(body.store_id,body.query,body.order)

    @app.post("/api/public/searches/{search_id}/select")
    def select(search_id:str,body:SelectionBody):
        service.selection(search_id,body.key)
        repository.record_event(search_id,body.key,"offer_click")
        return {"ok":True}

    @app.post("/api/public/searches/{search_id}/handoff")
    def handoff(search_id:str,body:SelectionBody,request:Request):
        limit(request,"handoff",15)
        return service.handoff(search_id,body.key,body.prepare_cart)

    @app.post("/api/merchant/register",status_code=201)
    def register(body:Credentials,request:Request):
        limit(request,"register",5)
        auth.register(body.username,body.password)
        return {"message":"Acesso criado. Entre para cadastrar sua loja."}

    @app.post("/api/merchant/login")
    def login(body:Credentials,request:Request,response:Response):
        limit(request,"login",8)
        token,user=auth.login(body.username,body.password)
        response.set_cookie("coconutray_session",token,max_age=43200,httponly=True,samesite="lax",secure=request.url.scheme=="https",path="/api/merchant")
        return user

    @app.get("/api/merchant/session")
    def current(request:Request):
        return session(request)

    @app.post("/api/merchant/logout")
    def logout(request:Request,response:Response):
        session(request,True)
        auth.logout(request.cookies.get("coconutray_session"))
        response.delete_cookie("coconutray_session",path="/api/merchant")
        return {"ok":True}

    @app.get("/api/merchant/stores")
    def merchant_stores(request:Request):
        user=session(request)
        ids=auth.store_ids(user["id"])
        return {"stores":[{**store_json(s),"theme":auth.theme(s.id)} for s in repository.stores("real",active_only=False) if s.id in ids],
                "demo_store":store_json(service.store("demo-ipe",include_paused=True))}

    @app.post("/api/merchant/stores",status_code=201)
    def add_store(body:StoreBody,request:Request):
        user=session(request,True)
        limit(request,"store_registration",5)
        if not body.consent:
            raise ValueError("Confirme a autorização e a participação voluntária da loja.")
        if (body.latitude is not None or body.longitude is not None) and not valid_coordinates(body.latitude,body.longitude):
            raise ValueError("Informe latitude e longitude válidas ou deixe ambas vazias.")
        if not body.name.strip():
            raise ValueError("Informe o nome da loja.")
        client=client_factory(body.domain,[body.domain],timeout=6)
        try:
            info=client.store_info()
            store=Store(uuid4().hex,body.name.strip(),client.origin,mode="real",address=body.address,
                        categories=tuple(c[:60] for c in body.categories),latitude=body.latitude,longitude=body.longitude,
                        location_source="merchant",tray_name=str(info.get("store_name",""))[:150],consent_at=now(),endpoint_checked_at=now())
            repository.add_store(store)
            auth.assign(user["id"],store.id)
            return store_json(store)
        finally:
            client.close()

    @app.patch("/api/merchant/stores/{store_id}/participation")
    def participation(store_id:str,body:Participation,request:Request):
        owner(request,store_id,True)
        repository.set_active(store_id,body.active)
        return {"active":body.active}

    @app.put("/api/merchant/stores/{store_id}/theme")
    def theme(store_id:str,body:ThemeBody,request:Request):
        owner(request,store_id,True)
        auth.save_theme(store_id,body.model_dump())
        return body.model_dump()

    @app.get("/api/merchant/stores/{store_id}/metrics")
    def store_metrics(store_id:str,request:Request):
        session(request)
        store=service.store(store_id,include_paused=True)
        if store.mode=="real":
            owner(request,store_id)
        with repository.connection() as db:
            sent=db.execute("""SELECT count(*) FROM network_events e JOIN network_searches s ON s.id=e.search_id
               WHERE e.kind='buy_link_requested' AND s.origin_id=?""",(store_id,)).fetchone()[0]
            received=db.execute("""SELECT count(*) FROM network_events e JOIN network_results r
               ON r.search_id=e.search_id AND r.match_key=e.match_key
               WHERE e.kind='buy_link_requested' AND r.store_id=?""",(store_id,)).fetchone()[0]
            searches=db.execute("SELECT count(*) FROM network_searches WHERE origin_id=?",(store_id,)).fetchone()[0]
            recent=[dict(r) for r in db.execute("""SELECT r.created_at,a.name origin,b.name destination,p.product_name
                FROM network_referrals r JOIN network_events e ON e.search_id=r.search_id AND e.match_key=r.match_key
                JOIN network_stores a ON a.id=r.origin_id JOIN network_stores b ON b.id=r.destination_id
                JOIN network_results p ON p.search_id=r.search_id AND p.match_key=r.match_key
                WHERE e.kind='buy_link_requested' AND (r.origin_id=? OR r.destination_id=?)
                ORDER BY r.created_at DESC LIMIT 20""",(store_id,store_id)).fetchall()]
        return {"mode":store.mode,"sent":sent,"received":received,"searches":searches,
                "recent":recent,
                "notice":"Encaminhamentos solicitados. Não comprovam visitas carregadas ou vendas."}

    @app.get("/embed-example")
    def embed_example():
        path=ROOT/"frontend/dist/embed-example.html"
        if not path.exists():
            raise HTTPException(503,"Compile o frontend antes de abrir o exemplo.")
        return FileResponse(path)

    distribution=ROOT/"frontend/dist"
    if distribution.exists():
        app.mount("/",StaticFiles(directory=str(distribution),html=True),name="frontend")
    return app


app=create_app()
