# Coconutray v1.0 · widget cooperativo

Uma camada de cooperação entre lojas participantes da Tray. A experiência principal é uma **vitrine de demonstração React com um widget que pesquisa nas parceiras sem sair da página**. A compra, quando há uma oferta real, continua no domínio oficial da loja escolhida.

Projeto independente da equipe Coconut, CONNECT 7.0. Não é um plugin oficial Tray, não processa pagamentos e não identifica vendas confirmadas.

## Executar

Requer Python 3.11+, Node.js 22.12+ e pnpm 11. O frontend é compilado uma vez; depois, o FastAPI serve a aplicação inteira em um único endereço.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
cd frontend
pnpm install --frozen-lockfile
pnpm run build
cd ..
.\iniciar.ps1
```

Abra [a Coconutray local](http://127.0.0.1:8000). Alternativa ao iniciador: `.\.venv\Scripts\python.exe run.py`.

Para desenvolver, execute o backend e, em outro terminal, `pnpm dev` dentro de `frontend`. O Vite encaminha `/api` ao backend local. Nenhuma chave é necessária para a demonstração ou a busca textual.

## História para apresentar

1. Visite a Papelaria Ipê, identificada como uma loja fictícia.
2. Pesquise **Mochila preta impermeável até R$ 180** na barra da própria loja.
3. A vitrine informa que não encontrou essa opção no catálogo e oferece **Não encontrou? Buscar nas lojas parceiras.**
4. Abra o widget. O pedido já é aproveitado, sem outra aba ou cadastro de comprador.
5. Compare as ofertas fictícias da Passo Livre e do Bazar da Praça. A Casa Aurora fica separada porque não informou impermeabilidade. Cores e preços incompatíveis são excluídos.
6. Use **Ver oferta** para conferir produto, variação, estabelecimento, disponibilidade informada e origem dos dados.
7. Clique em **Comprar na loja parceira**. No modo demonstrativo, o sistema registra o encaminhamento simulado e explica que não existe checkout ou pagamento.
8. Volte às opções ou feche o widget. A pesquisa da loja permanece na página.

Outros pedidos: **Tênis de corrida masculino preto tamanho 42**, **Furadeira 127 V** e **Caderno universitário azul**. A comparação pode reunir até três ofertas; nunca cria um carrinho com produtos de lojas diferentes.

## O que é demonstrativo e o que é real

A instalação inicial tem quatro lojas e catálogos fictícios. O widget faz requisições reais ao FastAPI e executa a mesma busca federada, os mesmos filtros Python, a mesma classificação e o mesmo registro usados pelo adaptador Tray. As ofertas não são respostas fixas programadas no frontend.

**Nenhuma loja Tray autorizada foi disponibilizada para esta atualização.** Não houve instalação de widget, consulta de catálogo ou criação de carrinho em uma loja real. Não apresentamos a simulação como homologação.

Também não há fotografias reais de catálogos autorizados nos dados demonstrativos. Por isso, a interface mostra **Imagem não fornecida**. Quando a Tray fornece uma foto, o backend preserva a URL do produto correto, valida o destino e o frontend utiliza essa fotografia, com proporção preservada e carregamento sob demanda. Não há imagens de IA nem fotos de outros produtos como substitutas.

## Área do comerciante

O acesso **Sou comerciante** abre uma área secundária. O usuário pode criar um acesso local e entrar. A primeira tela oferece imediatamente a pesquisa nas parceiras, além de oportunidades enviadas e recebidas.

Funções administrativas exigem login, sessão e proteção contra requisições forjadas. Cada conta administra apenas as lojas que cadastrou. As senhas usam scrypt com salt individual; sessões opacas são guardadas por hash, expiram em 12 horas e usam cookie HttpOnly, SameSite e Secure quando há HTTPS. Não há conta de comprador.

O cadastro pede nome, domínio oficial e confirmação de autorização/participação voluntária. Endereço, categorias e coordenadas são opcionais. O backend verifica destino público e compatibilidade com `store-info` antes de incluir a loja. A confirmação de autorização é declarada pelo participante; conectividade técnica não comprova titularidade, qualidade ou segurança comercial.

O comerciante pode pausar/retomar sua loja e configurar a aparência do widget. As lojas fictícias estão disponíveis para leitura e demonstração, sem alterar uma vitrine Tray. O aplicativo não inclui ERP, estoque, frete, pagamentos, comissões ou avaliações de clientes.

## Widget reutilizável

A compilação gera `frontend/dist/widget/coconutray-widget.js`. Veja [o teste local de incorporação](http://127.0.0.1:8000/embed-example), com um tema diferente e estilos isolados por Shadow DOM.

```html
<script type="module" src="https://SEU_BACKEND/widget/coconutray-widget.js"></script>
<coconutray-widget
  store-id="ID_DA_LOJA_CADASTRADA"
  api-base="https://SEU_BACKEND"
  theme='{"primary":"#002F68","button":"#002F68","text":"#172B4D","background":"#FFFFFF","border":"#DDE3EC","radius":14,"font":"inherit"}'>
</coconutray-widget>
```

Há tokens de cor principal, botões, texto, fundo, borda, fonte e arredondamento. A fonte pode ser herdada do tema. No celular, o painel ocupa a tela e usa cantos retos. A Coconutray se identifica discretamente como rede independente.

Este exemplo **não instala o componente na Tray**. A incorporação real exige autorização do lojista, compatibilidade e permissões do tema/plano, além de um backend HTTPS acessível. Nenhum tema externo foi modificado. Para origens administrativas adicionais, configure `COCONUTRAY_ADMIN_ORIGINS` no backend. Origem de loja participante pode acessar apenas recursos públicos do widget, sem credenciais administrativas.

## Gemini opcional, somente no backend

Copie `.env.example` para `.env` e informe a chave exclusivamente ali ou nas variáveis do servidor. `run.py` lê esse arquivo como dados, sem executar seu conteúdo. Não use variáveis `VITE_` para segredos.

A interpretação é desativada por padrão. Para ativar, configure `GEMINI_API_KEY`, `COCONUTRAY_GEMINI_ENABLED=true` e `COCONUTRAY_GEMINI_FREE_TIER_CONFIRMED=true` **depois de conferir que seu projeto Google usa a modalidade gratuita**. O SDK não confirma automaticamente a situação de cobrança do projeto. O modelo configurável inicial é `gemini-2.5-flash-lite`, cuja modalidade gratuita foi conferida na documentação em 14/11/2026.

Consultas simples não chamam a IA. A interpretação de pedidos mais longos usa `google-genai`, saída estruturada, espera de até 6 segundos, nenhuma ferramenta comercial e uma única tentativa. Os limites locais iniciais são 4 chamadas por minuto e 30 por dia, por processo. Não são uma promessa de cota do Google; os limites efetivos dependem do projeto. Em falha, resposta inválida ou limite atingido, a busca textual continua. Um erro 429 ativa uma pausa de 60 segundos, sem troca automática para serviço pago.

A IA não recebe cadastro do comerciante, endereço, histórico, clientes ou catálogo. Consultas com sinais de contato pessoal são mantidas fora da interpretação remota. Os atributos precisam ter evidência no pedido; requisitos explícitos, preço e distância continuam validados em Python. A categoria pode ajudar a recuperar candidatos, mas não remove os termos obrigatórios da classificação final.

**A chave permanece exclusivamente no backend.** No estado verificado, ela estava presente no `.env`, mas as flags de ativação e de confirmação da modalidade gratuita continuavam desativadas. Essa configuração foi preservada. O contrato, os limites e o fallback foram testados com respostas simuladas; não houve chamada autenticada ao Google. A busca textual continua funcionando.

## Integração e dados

- MCP público por domínio canônico: `/mcp-app/storefront`, sem credenciais administrativas.
- Somente participantes previamente cadastrados e autorizados; nenhuma descoberta de todas as lojas ou scraping.
- Até quatro consultas simultâneas, orçamento de 12 segundos por catálogo e falhas isoladas. DNS síncrono ainda depende do resolvedor do sistema operacional.
- Preços usam Decimal. Conflitos de tamanho, cor, voltagem e preço são excluídos; informação ausente fica como **precisa confirmar**.
- URLs de produto/carrinho precisam ser recebidas da loja e usar seu domínio HTTPS autorizado. Imagens aceitam o domínio da loja e os hosts de imagens Tray documentados, sem sintetizar caminhos.
- O botão de compra usa o link oficial ou, quando solicitado e viável, prepara uma unidade da variação em um carrinho daquela loja. O adaptador confere novamente preço, atributos e disponibilidade. Escritas incertas não são repetidas automaticamente.
- Distâncias só aparecem com coordenadas válidas e são em linha reta. Coordenadas e endereços reais são declarações do participante, sem validação física automática.
- Cache SQLite de produtos públicos por até 60 segundos, mantendo a data original. A compra revalida as informações relevantes.

O banco permanece em `data/network.sqlite3`, ou no caminho `COCONUTRAY_DB_PATH`. O banco do protótipo anterior foi preservado. Há cadastro de comerciantes, lojas, configurações, pesquisas operacionais, encaminhamentos e cache; não há dados de pagamento ou conversas de compradores. O texto original da pesquisa não é persistido. A seleção usa um contexto temporário em memória, com expiração de 15 minutos.

**Encaminhamento solicitado não comprova carregamento da página, pagamento ou venda.** Os modos real e demonstrativo são separados. Cada evento por pesquisa/oferta é deduplicado. As métricas do comerciante contam solicitações enviadas/recebidas, sem alegar faturamento ou conversão.

## Organização

- `frontend/src/App.tsx`: vitrine demonstrativa; `Widget.tsx`: componente público compartilhado.
- `Merchant.tsx`: login e gestão básica; `widget-entry.tsx`: custom element isolado.
- `frontend/src/styles.css`: Tailwind, tokens e CSS dos componentes.
- `coconutray/api.py`: FastAPI, rotas, limites e hospedagem estática.
- `service.py`: mesma orquestração para vitrine e widget; `auth.py`: sessões e propriedade de lojas.
- `network_*`, `ranking.py`, `models.py`, `validation.py` e `tray.py`: núcleo reaproveitado.
- `query.py` e `gemini.py`: interpretação textual e Google GenAI.
- `archive/streamlit-v1/`: código e testes anteriores, preservados como histórico e fora da aplicação ativa.

Não há dependência ativa de Streamlit ou Ollama. Consulte `VALIDACAO.md` para verificações executadas, condições não homologadas e critérios de aceite.

## Verificações

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
pnpm run build
```

Referências: [site Tray](https://tray.com.br/), [guia de cores](https://partners.tray.com.br/guia-de-design-1/logo-tray/cores), [temas Tray](https://partners.tray.com.br/themes/), [MCP Storefront](https://developers.tray.com.br/), [SDK Google GenAI](https://googleapis.github.io/python-genai/), [preços Gemini](https://ai.google.dev/gemini-api/docs/pricing), [limites Gemini](https://ai.google.dev/gemini-api/docs/rate-limits).
