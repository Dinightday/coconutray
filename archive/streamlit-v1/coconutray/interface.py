"""Native Streamlit screens for cooperative discovery and manual referrals."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import os
from pathlib import Path
from uuid import uuid4

import streamlit as st

from coconutray.models import ReviewLine
from coconutray.network_data import NetworkRepository, now, valid_coordinates
from coconutray.network_demo import seed_demo
from coconutray.network_models import Store
from coconutray.network_search import federated_search
from coconutray.query import interpret_query, normalize, ollama_status
from coconutray.ranking import order_matches
from coconutray.tray import IntegrationError, PartialCartError, TrayClient, validate_offer_url

ROOT = Path(__file__).parents[1]


def esc(value):
    return html.escape(str(value))


def brl(value):
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def local_time(value):
    try:
        return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y às %H:%M")
    except (ValueError, TypeError):
        return "Não informada"


@st.cache_resource
def get_repository(path):
    repository = NetworkRepository(path)
    seed_demo(repository)
    return repository


def reset_results():
    for key in ("report", "search_id", "selected_key", "search_origin_id", "link_ready", "share_ready", "cart_url", "cart_attempted"):
        st.session_state[key] = None


def change_environment():
    reset_results()
    st.session_state.origin_id = None
    st.session_state.near_store = None
    st.session_state.page = "Buscar produtos"


def load_example(query):
    st.session_state.query = query
    reset_results()


def product_art(name, large=False, image_url=None):
    """Category illustrations are generated from vector paths, not product photos."""
    if image_url:
        st.image(image_url, width="stretch")
        return
    name = normalize(name)
    if "mochila" in name:
        shape = '<path d="M49 35v-7c0-11 22-11 22 0v7M36 47c0-11 48-11 48 0l5 52H31z" fill="#24493b"/><path d="M45 65h30v25H45z" fill="#52745d"/><path d="M46 42v15m28-15v15M44 75h32M31 55l-8 30m66-30 8 30"/>'
    elif "tenis" in name:
        shape = '<path d="M22 76l15-30 18 15 16-7 9 20 22 10v14H20V85z" fill="#355a47"/><path d="M22 90h80M51 66l16 4m-21 4 17 4m-23 3 18 4"/>'
    elif "caderno" in name:
        shape = '<rect x="34" y="25" width="56" height="80" rx="7" fill="#7e9e87"/><path d="M44 25v80M31 40h14m-14 15h14m-14 15h14m-14 15h14"/><rect x="54" y="42" width="25" height="18" rx="2" fill="#f6f4e9"/>'
    elif "furadeira" in name:
        shape = '<path d="M25 40h50v27H54l8 35H41L31 67H25z" fill="#52694e"/><path d="M75 45h15v17H75M90 53h20M28 49h26m-26 9h17M46 82h12"/>'
    elif "garrafa" in name:
        shape = '<path d="M48 30v12c-10 5-13 12-13 22v35c0 12 49 12 49 0V64c0-10-3-17-13-22V30" fill="#577b64"/><rect x="46" y="22" width="27" height="12" rx="4"/>'
    else:
        shape = '<path d="M34 90l44-57 12 10-44 57-18 7z" fill="#486d57"/><path d="M72 40l12 11M34 90l12 10"/>'
    # st.html sanitizes inline SVG on some clients; the native image renderer
    # supports SVG consistently on desktop and mobile without private selectors.
    st.image('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 180">'
             '<rect width="240" height="180" rx="12" fill="#edf3ee"/>'
             '<g transform="translate(57 20)" fill="none" stroke="#b9cabb" stroke-width="3" '
             'stroke-linecap="round" stroke-linejoin="round">' + shape + '</g></svg>',
             width="stretch", caption="Ilustração da categoria")


def main():
    st.set_page_config(page_title="Coconutray · O comércio local, conectado", page_icon="🥥", layout="wide")
    st.html("<style>" + (ROOT / "assets/style.css").read_text(encoding="utf-8") + "</style>")
    repo = get_repository(os.getenv("COCONUTRAY_DB_PATH", str(ROOT / "data/network.sqlite3")))
    s = st.session_state
    for key, value in {"page": "Buscar produtos", "environment": "Demonstração", "audience": "Para mim", "query": "",
                       "use_ai": False, "origin_id": None, "report": None, "selected_key": None, "search_id": None,
                       "search_origin_id": None, "link_ready": None, "share_ready": None, "cart_url": None, "cart_attempted": None}.items():
        if key not in s:
            s[key] = value
    mode = "demo" if s.environment == "Demonstração" else "real"
    stores = repo.stores(mode)
    with st.sidebar:
        st.html('<div class="cr-brand"><span class="cr-logo" aria-hidden="true"></span>coconutray</div><div class="cr-tagline">O comércio local, conectado.</div>')
        st.radio("Navegação", ["Buscar produtos", "Lojas participantes", "Atividade da rede"], key="page", label_visibility="collapsed")
        st.divider()
        st.selectbox("Ambiente", ["Demonstração", "Lojas reais"], key="environment", on_change=change_environment)
        st.caption("Lojas e produtos fictícios para experimentar." if mode == "demo" else "Somente lojas autorizadas, com conexão conferida.")
        st.html(f'<div class="cr-store"><b>{len(stores)} lojas na rede</b><p>{"Catálogos fictícios" if mode == "demo" else "Participantes ativos"}</p></div>')
        with st.expander("Ajuda na pesquisa"):
            st.toggle("Interpretar com IA local", key="use_ai")
            st.caption("A busca por texto funciona sem IA. O limite da interpretação é 15 segundos.")
            if st.button("Conferir IA local", use_container_width=True):
                st.info(ollama_status())
        st.html('<div class="cr-footer">Equipe Coconut · CONNECT 7.0<br>Coconutray v1.0</div>')
    st.html('<div class="cr-topline"><span>UMA REDE. MAIS POSSIBILIDADES.</span><b>' + ('DEMONSTRAÇÃO · DADOS FICTÍCIOS' if mode == "demo" else 'LOJAS PARTICIPANTES · DADOS REAIS') + '</b></div>')
    if mode == "demo":
        st.info("Demonstração: lojas, produtos, preços e localizações são fictícios. Nenhuma compra real é realizada.", icon="🧪")
    if s.page == "Lojas participantes":
        stores_page(repo, stores, mode)
    elif s.page == "Atividade da rede":
        activity_page(repo, mode)
    else:
        search_page(repo, stores, mode)


def search_page(repo, stores, mode):
    s = st.session_state
    store_map = {store.id: store for store in stores}
    if s.selected_key and s.report:
        match = next((m for m in s.report.matches if m.key == s.selected_key), None)
        if match:
            detail_page(repo, match, store_map, mode)
            return
    if s.report:
        st.title("Opções da rede")
        st.caption("Compare as ofertas e confira os detalhes antes de continuar.")
    else:
        st.html('<div class="cr-hero"><div class="cr-eyebrow">BOAS OPORTUNIDADES MERECEM CIRCULAR</div><h1>O que você procura pode<br>estar na loja ao lado.</h1><p>Encontre produtos em pequenos comércios da rede.<br>Compare as opções e dê o próximo passo com a loja.</p><div class="cr-step">01 &nbsp; Encontre <span>→</span> 02 &nbsp; Confira <span>→</span> 03 &nbsp; Acesse a loja</div></div>')
    a, b = st.columns([1, 1.4], gap="large")
    a.radio("Estou procurando", ["Para mim", "Para um cliente"], horizontal=True, key="audience", on_change=reset_results)
    if s.audience == "Para um cliente":
        ids = list(store_map)
        if s.origin_id not in ids:
            s.origin_id = "demo-ipe" if "demo-ipe" in ids else (ids[0] if ids else None)
        b.selectbox("Minha loja", ids, format_func=lambda x: store_map[x].name, key="origin_id", on_change=reset_results, placeholder="Escolha sua loja")
        st.caption("Não tem o produto? Encontre uma opção em outra loja e prepare uma indicação para o cliente.")
    else:
        b.caption("Uma busca, diferentes lojas participantes. Você escolhe onde continuar.")
    with st.form("search_form"):
        st.text_input("O que você está procurando?", key="query", max_chars=300, placeholder="Ex.: mochila preta impermeável até R$ 180")
        with st.expander("Comparar por proximidade (opcional)"):
            if s.audience == "Para um cliente":
                st.caption("Usaremos a localização informada da sua loja, quando disponível.")
            else:
                if s.get("near_store") not in store_map:
                    s.near_store = None
                st.selectbox("Usar uma loja como referência", [None, *store_map], key="near_store", format_func=lambda x: "Sem referência de localização" if x is None else store_map[x].name)
            st.caption("Distância em linha reta, a partir da loja escolhida. Nenhuma localização pessoal é salva.")
        submitted = st.form_submit_button("Buscar nas lojas  →", type="primary", use_container_width=True, disabled=not stores)
    if submitted:
        origin_id = s.origin_id if s.audience == "Para um cliente" else None
        reference_store = store_map.get(origin_id or s.get("near_store"))
        targets = [store for store in stores if store.id != origin_id]
        try:
            if s.audience == "Para um cliente" and not origin_id:
                raise ValueError("Escolha sua loja antes de procurar em estabelecimentos parceiros.")
            if not targets:
                raise ValueError("Ainda não há lojas parceiras disponíveis neste ambiente.")
            reference = (reference_store.latitude, reference_store.longitude) if reference_store else None
            with st.spinner("Procurando opções nas lojas participantes…"):
                intent = interpret_query(s.query, s.use_ai)
                report = federated_search(targets, intent, reference, repository=repo)
                search_id = repo.record_search(report, mode, origin_id)
            reset_results()
            s.report, s.search_id, s.search_origin_id = report, search_id, origin_id
            st.rerun()
        except (ValueError, RuntimeError, OSError) as exc:
            st.error(str(exc))
    if not stores:
        st.warning("Ainda não há lojas reais cadastradas. Adicione uma loja autorizada em Lojas participantes ou experimente a demonstração.")
    if s.report:
        results(repo, store_map, mode)
    else:
        st.caption("Experimente uma pesquisa")
        for col, label, query in zip(st.columns(3), ["Mochila impermeável", "Tênis tamanho 42", "Furadeira 127 V"], ["Mochila preta impermeável até R$ 180", "Tênis de corrida masculino preto tamanho 42", "Furadeira 127 V"]):
            col.button(label, on_click=load_example, args=(query,), use_container_width=True, disabled=mode != "demo")
        st.html('<div class="cr-source">Uma demanda não atendida pode se tornar uma oportunidade para outra loja.<br>A participação na rede não garante qualidade, disponibilidade ou venda.</div>')


def results(repo, store_map, mode):
    s, report = st.session_state, st.session_state.report
    st.divider()
    st.subheader("Encontramos estas opções" if report.matches else "Não encontramos uma opção para este pedido")
    st.write("Pesquisa: " + report.intent.original)
    labels = {"cor": "Cor", "impermeavel": "Impermeável", "tamanho": "Tamanho", "genero": "Público", "voltagem": "Voltagem"}
    chips = [f"{labels.get(key, key)}: {'sim' if value == 'sim' else 'não' if value == 'nao' else value}" for key, value in report.intent.constraints.items()]
    if report.intent.max_price:
        chips.append("até " + brl(report.intent.max_price))
    st.html('<div class="cr-pills">' + ''.join(f'<span class="cr-pill">{esc(c)}</span>' for c in chips) + '</div>')
    for warning in report.intent.warnings:
        st.warning(warning)
    st.caption(f"{report.responded} de {report.consulted} catálogos consultados com sucesso · {report.elapsed:.1f} s de consulta · " + ("Interpretação local conferida por regras" if report.intent.engine == "ollama" else "Busca por texto"))
    st.caption("As consultas reais podem usar cache de até 60 segundos.") if mode == "real" else None
    if any(getattr(m, "cached", False) for m in report.matches):
        st.caption("Algumas ofertas usam uma consulta guardada por até 60 segundos. A data original aparece nos detalhes; preço e disponibilidade são conferidos novamente antes de preparar um carrinho.")
    if report.failures:
        st.warning("Não conseguimos consultar: " + ", ".join(store_map[k].name for k in report.failures if k in store_map) + ". Os resultados das outras lojas continuam disponíveis.")
        if not report.responded:
            st.info("Todas as consultas falharam. Isso não significa que as lojas não tenham o produto.")
    with st.expander("Como as opções são escolhidas"):
        st.write("Conferimos as palavras do pedido e os requisitos informados. Cores, tamanhos, voltagens ou preços incompatíveis são excluídos. Informações ausentes ficam em uma lista que precisa de confirmação.")
        st.write("A relevância considera os termos no nome e os atributos confirmados. Preço desempata. Nenhuma loja recebe prioridade comercial.")
        st.caption("A comparação cobre os resultados retornados nesta consulta, sem garantir cobertura de todo o catálogo.")
    if not report.matches:
        st.info("Tente um nome mais simples ou revise os requisitos. O catálogo on-line não garante estoque físico.")
        return
    has_distance = any(m.distance_km is not None for m in report.matches)
    options = ["Mais relevante", "Menor preço"] + (["Mais próximo"] if has_distance else [])
    if s.get("sort_order") not in options:
        s.sort_order = options[0]
    order = st.selectbox("Ordenar opções", options, key="sort_order")
    st.caption("Distâncias em linha reta a partir da referência escolhida." if has_distance else "Sem referência de localização suficiente. Distâncias não serão exibidas.")
    matches = order_matches(report.matches, {"Mais relevante": "relevance", "Menor preço": "price", "Mais próximo": "distance"}[order])
    for status, title in (("compatible", "Compatíveis com o pedido"), ("uncertain", "Precisam de confirmação")):
        group = [m for m in matches if m.status == status]
        if not group:
            continue
        st.markdown(f"**{title} · {len(group)}**")
        for offset in range(0, len(group), 3):
            for col, match in zip(st.columns(3, gap="medium"), group[offset:offset + 3]):
                with col, st.container(border=True):
                    product_art(match.product.name, image_url=match.product.image_url)
                    st.html('<div class="cr-source">' + esc(match.store.name) + '</div>')
                    st.html("<strong>" + esc(match.product.name) + "</strong>")
                    st.html('<div class="cr-source">' + esc(match.product.variant_label or "Variação não informada") + '</div>')
                    st.html(f'<div class="cr-price">{brl(match.product.price)}</div>')
                    st.caption("Disponível on-line, segundo o catálogo" if match.product.availability_known else "Disponibilidade a confirmar")
                    if match.distance_km is not None:
                        st.caption(f"{match.distance_km:.1f} km em linha reta" + (" · fictícios" if mode == "demo" else " · referência informada"))
                    if status == "uncertain":
                        st.warning(" · ".join(r for r in match.reasons if r.startswith("Confirmar")))
                    if st.button("Ver detalhes  →", key="detail_" + match.key, use_container_width=True):
                        repo.record_event(s.search_id, match.key, "offer_click")
                        s.selected_key = match.key
                        s.link_ready = s.share_ready = s.cart_url = s.cart_attempted = None
                        st.rerun()


def referral_text(match, origin, mode):
    text = ("[DEMONSTRAÇÃO — OFERTA FICTÍCIA]\n" if mode == "demo" else "")
    text += f"A {origin.name} encontrou uma opção na {match.store.name}:\n{match.product.name}\n{match.product.variant_label or ''}\nPreço consultado: {brl(match.product.price)}.\n"
    if match.status == "uncertain":
        text += "Antes de comprar: " + "; ".join(r for r in match.reasons if r.startswith("Confirmar")) + ".\n"
    if mode == "demo":
        text += "Catálogo demonstrativo. Não há link de compra real."
    elif match.product.url:
        text += "Confira diretamente na loja:\n" + validate_offer_url(match.product.url, match.store.domain, resolve=False)
    else:
        text += "A loja não forneceu um link oficial para esta oferta."
    return text + "\nPreço e disponibilidade podem mudar. Indicação preparada pela Coconutray; não comprova compra."


def detail_page(repo, match, store_map, mode):
    s = st.session_state
    if st.button("← Voltar aos resultados"):
        s.selected_key = None
        st.rerun()
    st.subheader("Confira a opção antes de continuar")
    left, right = st.columns([1, 1.5], gap="large")
    with left:
        product_art(match.product.name, True, match.product.image_url)
        st.caption("Imagem retornada pelo catálogo da loja." if match.product.image_url else "Ilustração da categoria. Confira as fotos e os detalhes do produto na loja.")
    with right:
        st.html('<div class="cr-source">' + esc(match.store.name) + '</div>')
        st.html("<h1>" + esc(match.product.name) + "</h1>")
        st.text(match.product.variant_label or "Variação não informada")
        st.html(f'<div class="cr-price">{brl(match.product.price)}</div>')
        if match.status == "uncertain":
            st.warning("Esta oferta precisa de confirmação. " + ". ".join(r for r in match.reasons if r.startswith("Confirmar")))
        else:
            st.success("Atende aos requisitos conferidos nesta pesquisa.")
        st.text(match.product.description or "Descrição não informada no catálogo.")
        st.caption("Disponibilidade on-line informada no catálogo. Isso não confirma estoque físico." if match.product.availability_known else "Disponibilidade on-line não confirmada pelo catálogo.")
        st.caption("Consulta em " + local_time(match.fetched_at) + " · valores sujeitos a alteração.")
    with st.container(border=True):
        st.markdown("**Sobre a loja**")
        st.text(match.store.name)
        st.text(match.store.domain + (" · domínio fictício" if mode == "demo" else " · domínio autorizado pelo participante"))
        st.text(match.store.address or "Endereço físico não informado.")
        st.caption("Origem: dados fictícios de demonstração." if mode == "demo" else "Endereço, categorias e localização: informados pelo participante. Conexão conferida em " + local_time(match.store.endpoint_checked_at) + ".")
        if mode == "real":
            st.caption(f"Nome retornado pela Tray: {match.store.tray_name or 'Não informado'}. Produto, preço e disponibilidade: catálogo público da Tray.")
        if match.distance_km is not None:
            st.caption(f"Distância aproximada: {match.distance_km:.1f} km em linha reta; não representa trajeto ou tempo de viagem.")
        st.caption("Participar da rede não constitui selo de segurança nem avaliação de consumidores.")
    buy, share = st.columns(2, gap="large")
    with buy:
        st.markdown("**Continue com a loja**")
        if mode == "demo":
            if st.button("Simular acesso à oferta", type="primary", use_container_width=True):
                repo.record_event(s.search_id, match.key, "buy_link_requested")
                s.link_ready = match.key
            if s.link_ready == match.key:
                st.info("Percurso demonstrado: o comprador seguiria para a oferta oficial da loja Tray. Esta oferta é fictícia e não tem checkout ou pagamento.")
        elif match.product.url:
            if st.button("Acessar oferta na Tray", type="primary", use_container_width=True):
                try:
                    s.validated_url = validate_offer_url(match.product.url, match.store.domain)
                    repo.record_event(s.search_id, match.key, "buy_link_requested")
                    s.link_ready = match.key
                except IntegrationError as exc:
                    st.error(str(exc))
            if s.link_ready == match.key:
                st.link_button("Continuar para " + match.store.name + " ↗", s.validated_url, type="primary", use_container_width=True)
                st.caption("Acesso solicitado não comprova carregamento da página ou venda. A compra continua no ambiente da loja.")
        else:
            st.info("O catálogo não forneceu um link oficial do produto.")
        if mode == "real":
            cart_section(repo, match)
    with share:
        st.markdown("**Encaminhe esta oportunidade**")
        origin = store_map.get(s.search_origin_id)
        if not origin:
            st.caption("Para indicar como comerciante, volte à busca e selecione “Para um cliente” e sua loja.")
        elif origin.id != match.store.id:
            st.caption("Prepare um texto e compartilhe manualmente com seu cliente.")
            if st.button("Preparar indicação", use_container_width=True):
                repo.prepare_referral(s.search_id, match.key)
                s.share_ready = match.key
            if s.share_ready == match.key:
                st.success("Indicação preparada. Copie o texto abaixo e envie pelo canal que preferir.")
                st.code(referral_text(match, origin, mode), language=None, wrap_lines=True)
                st.caption("Nenhuma mensagem foi enviada automaticamente. Registramos apenas a preparação desta indicação.")


def cart_section(repo, match):
    s = st.session_state
    with st.expander("Preparar um carrinho nesta loja"):
        st.caption("Uma unidade do produto e da variação acima. A loja confere novamente preço e disponibilidade.")
        checked = st.checkbox("Conferi o produto e a variação", key="cart_review_" + match.key)
        if st.button("Preparar carrinho na Tray", disabled=not checked or match.status != "compatible" or s.cart_attempted == match.key, use_container_width=True):
            client = None
            s.cart_attempted = match.key
            try:
                with st.spinner("Preparando o carrinho na loja…"):
                    client = TrayClient(match.store.domain, [match.store.domain], timeout=10)
                    cart = client.create_cart([ReviewLine(match.product, 1, 0)], approved=True)
                s.cart_url = cart.url
                repo.record_event(s.search_id, match.key, "cart_created")
                st.success("Carrinho preparado. Confira os itens na loja antes de pagar.")
            except PartialCartError as exc:
                s.cart_url = exc.cart_url
                st.warning("A loja pode ter recebido parte da solicitação. Confira na loja antes de tentar novamente.")
            except (IntegrationError, ValueError) as exc:
                st.error(str(exc))
            finally:
                if client:
                    client.close()
        if s.cart_url:
            st.link_button("Conferir carrinho oficial ↗", s.cart_url, use_container_width=True)
        st.caption("Carrinho preparado não é venda confirmada. Pagamento e atendimento acontecem na Tray.")


def stores_page(repo, stores, mode):
    st.title("Lojas participantes")
    st.write("Uma rede começa com estabelecimentos que escolhem cooperar.")
    if mode == "demo":
        st.caption("As lojas abaixo são fictícias. Para cadastrar uma participante autorizada, selecione Lojas reais no menu.")
    for store in repo.stores(mode, active_only=False):
        with st.container(border=True):
            a, b = st.columns([3, 1])
            a.html("<strong>" + esc(store.name) + "</strong>")
            a.caption(" · ".join(store.categories))
            a.text(store.domain)
            a.text(store.address or "Endereço não informado")
            a.caption("Dados fictícios." if mode == "demo" else "Cadastro declarado pelo participante. Conexão conferida em " + local_time(store.endpoint_checked_at) + ".")
            b.caption("Participação ativa" if store.active else "Participação pausada")
            if b.button("Pausar participação" if store.active else "Retomar participação", key="active_" + store.id):
                repo.set_active(store.id, not store.active)
                reset_results()
                st.rerun()
    if mode == "real":
        registration_form(repo)


def registration_form(repo):
    st.subheader("Adicionar uma loja à rede")
    st.caption("Use o domínio oficial de uma loja Tray autorizada. A conexão será conferida antes do cadastro.")
    with st.form("register_store"):
        name = st.text_input("Nome da loja", max_chars=100)
        domain = st.text_input("Site oficial da loja", placeholder="https://www.sualoja.com.br", max_chars=300)
        address = st.text_input("Endereço físico (opcional)", max_chars=250)
        categories = st.text_input("O que a loja vende? (opcional)", placeholder="Ex.: roupas, acessórios", max_chars=200)
        with st.expander("Localização aproximada (opcional)"):
            st.caption("Se já tiver as coordenadas, informe ambas. A loja pode participar sem elas; nesse caso não calculamos distâncias.")
            a, b = st.columns(2)
            latitude = a.text_input("Latitude", max_chars=30)
            longitude = b.text_input("Longitude", max_chars=30)
        consent = st.checkbox("Tenho autorização da loja para cadastrá-la e participar voluntariamente desta rede.")
        save = st.form_submit_button("Conferir conexão e cadastrar", type="primary", use_container_width=True)
    if st.session_state.pop("store_saved", False):
        st.success("Loja cadastrada e pronta para participar das pesquisas.")
    if not save:
        return
    if not consent:
        st.warning("O cadastro depende da autorização e participação voluntária da loja.")
        return
    client = None
    try:
        if not name.strip():
            raise ValueError("Informe o nome da loja.")
        lat = float(latitude.replace(",", ".")) if latitude.strip() else None
        lon = float(longitude.replace(",", ".")) if longitude.strip() else None
        if (lat is not None or lon is not None) and not valid_coordinates(lat, lon):
            raise ValueError("Informe duas coordenadas válidas ou deixe ambas vazias.")
        with st.spinner("Conferindo a conexão com esta loja…"):
            client = TrayClient(domain.strip(), [domain.strip()], timeout=6)
            info = client.store_info()
            repo.add_store(Store(uuid4().hex, name.strip(), client.origin, mode="real", address=address.strip(),
                                 categories=tuple(c.strip() for c in categories.split(",") if c.strip()),
                                 latitude=lat, longitude=lon, location_source="merchant", tray_name=str(info.get("store_name", ""))[:150],
                                 consent_at=now(), endpoint_checked_at=now()))
        reset_results()
        st.session_state.store_saved = True
        st.rerun()
    except (ValueError, IntegrationError, OSError) as exc:
        st.error(str(exc))
    finally:
        if client:
            client.close()


def activity_page(repo, mode):
    st.title("Atividade da rede")
    st.write("Pesquisas e oportunidades, com o significado de cada evento.")
    metrics = repo.metrics(mode)
    for columns, values in ((st.columns(3), [("Pesquisas realizadas", "searches"), ("Com opções encontradas", "with_results"), ("Indicações preparadas", "referrals")]),
                            (st.columns(3), [("Ofertas conferidas", "offer_click"), ("Acessos solicitados", "buy_link_requested"), ("Falhas de consulta", "failures")])):
        for col, (label, key) in zip(columns, values):
            col.metric(label, metrics[key])
    st.caption(f'{metrics["without_results"]} pesquisas sem opções · {metrics["stores_consulted"]} consultas a lojas · {metrics["cart_created"]} carrinhos preparados')
    st.info("Estes números não representam vendas. Uma indicação preparada não comprova envio da mensagem. Um acesso solicitado não comprova que a página da loja carregou ou que houve pagamento.")
    st.caption("Eventos demonstrativos, separados dos reais." if mode == "demo" else "Eventos reais observados nesta instalação. Os resultados comerciais ainda precisam de validação com os lojistas.")
    st.subheader("Indicações preparadas recentemente")
    rows = repo.recent_referrals(mode)
    if not rows:
        st.caption("Ainda não há indicações neste ambiente. Procure para um cliente e escolha uma loja parceira.")
    for row in rows:
        with st.container(border=True):
            st.write(f'**{esc(row["origin"])} → {esc(row["destination"])}**')
            st.write(row["product_name"])
            st.caption(local_time(row["created_at"]) + " · indicação preparada, envio não confirmado")
