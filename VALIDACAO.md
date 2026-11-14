# Validação da atualização Coconutray v1.0

Executada em 14/11/2026. O objetivo desta entrega é a vitrine React com widget cooperativo e backend FastAPI; não uma instalação homologada em uma loja Tray.

## Evidências executadas

- **161 testes automatizados e 23 subtestes aprovados**, em 115,82 segundos. Comando: `.\.venv\Scripts\python.exe -m pytest -q --tb=short`.
- Há um aviso de depreciação do adaptador TestClient/httpx da versão instalada de Starlette. Não houve falha de teste. Os testes não fazem chamadas comerciais reais.
- **TypeScript e as duas compilações Vite aprovados:** vitrine e pacote independente do widget, via `pnpm run build`.
- Saída final da vitrine: JavaScript 267,52 kB (83,25 kB gzip) e CSS 44,23 kB (9,67 kB gzip).
- Saída final do widget: 438,98 kB (100,81 kB gzip), incluindo React e estilos isolados; sem dependência de React no host.
- Após reiniciar a versão final, `/api/health`, `/` e `/embed-example` responderam HTTP 200. O servidor anterior Streamlit foi encerrado.
- SDK `google-genai` instalado; configuração de saída JSON e opções de timeout/repetição conferidas no SDK real. As respostas de interpretação foram simuladas, com chamadas remotas desativadas pela configuração.

## Percurso conferido no navegador

1. A vitrine Papelaria Ipê foi carregada com produtos fictícios e estado neutro de fotografia ausente.
2. A pesquisa **Mochila preta impermeável até R$ 180** não encontrou opção no catálogo da loja atual.
3. O convite abriu o widget na mesma URL e aproveitou a consulta já digitada. Nenhuma conta de comprador foi criada.
4. A busca consultou três parceiras e retornou duas ofertas compatíveis de lojas diferentes. A opção sem informação de impermeabilidade ficou separada.
5. Duas ofertas foram selecionadas e comparadas lado a lado; a escolha mostrou produto, variação, preço e estabelecimento.
6. **Comprar na loja parceira**, no ambiente fictício, registrou um encaminhamento demonstrativo e informou explicitamente que não existe checkout ou pagamento.
7. Fechar o widget manteve a consulta na página de origem. Escape também fechou o componente e devolveu o foco ao botão que o abriu.
8. Foram conferidas as dimensões 1440 × 1000 e 390 × 844. No painel móvel, largura interna e largura de rolagem do diálogo foram ambas 390 px, sem transbordamento horizontal interno.
9. O exemplo `/embed-example` carregou o pacote independente em um host com estilos diferentes. O widget aplicou cor `#663E26` e fonte `Georgia, serif`, com Shadow DOM presente. A busca de tênis preto tamanho 42 retornou duas variações corretas; o tamanho 40 não apareceu.
10. O cadastro e o login do comerciante foram exercitados no navegador com um acesso local temporário. A primeira tela ofereceu a pesquisa em parceiras e métricas rotuladas como demonstrativas. A sessão foi encerrada e a conta temporária, sem lojas, removida após a verificação.

## Cobertura do backend

Os testes cobrem cadastro e propriedade das lojas, hash de senha, sessões, CSRF, origens permitidas, configuração de tema, pausa de participação, isolamento entre usuários, pesquisa pública, filtros, preços Decimal, variações, atributos ausentes, negações, distâncias, ordenação, deduplicação, expiração de pesquisa, separação real/demo, cache, privacidade e falhas individuais.

O teste de federação HTTP usa FastAPI → busca federada → **dois clientes MCP com transportes HTTP simulados**, mantendo handshake, domínios autorizados, produto, preço, imagem e URL efetivamente fornecidos por essas respostas. Também verifica a falha de um servidor sem perder o resultado do outro. Isso prova o contrato implementado, não conectividade com lojas reais.

As imagens são aceitas somente quando fornecidas pelo catálogo em HTTPS no domínio da loja ou em hosts de imagem Tray documentados. Testes rejeitam hosts falsos, credenciais, portas indevidas e HTTP; também verificam o campo `image` numérico sem ocultar uma fotografia válida em `ProductImage`.

A incorporação possui testes de CORS para módulo e API públicos em origem participante. Essa origem não ganha acesso administrativo nem pode atribuir uma busca a outra loja. Configurações e participação exigem sessão, CSRF e propriedade da loja.

Gemini: consultas simples não gastam chamadas; chave e confirmação de modalidade gratuita são exigidas para ativação; falhas, cota, dados inválidos e limites locais mantêm busca textual. Sinais de contato ou identificação pessoal impedem envio remoto. A interpretação não substitui requisitos explícitos nem autoriza operações comerciais.

## Critérios de aceite e situação

| Critério do documento | Evidência atual |
|---|---|
| React e TypeScript | Código em `frontend/src`, verificação TypeScript e compilação Vite aprovadas |
| Referências visuais Tray | Site atual inspecionado; guia de cores lido, inclusive imagem oficial; tokens `#002F68` e `#46A1DB` documentados |
| Identidade configurável | Tokens de cor, botões, texto, fundo, borda, fonte e raio; teste do tema independente e salvamento com propriedade |
| Pesquisa sem conta e sem nova aba | Percurso público exercitado no mesmo documento e diálogo |
| Múltiplos MCPs autorizados | Contrato completo testado com dois transportes simulados; homologação externa pendente |
| Fotografias corretas quando disponíveis | URLs do catálogo preservadas e validadas; renderização com proporção e lazy loading; ausência explicitada na demonstração |
| Preços e características com origem | Campos do adaptador Tray preservados; dados demonstrativos separados e identificados |
| Identificação do estabelecimento | Loja mostrada em cartão, comparação e detalhes; origem de endereço e disponibilidade indicada |
| URL oficial no encaminhamento | Validação HTTPS/domínio e testes de retorno da URL fornecida; nenhuma URL de checkout fabricada |
| Limitações sinalizadas | Avisos na vitrine, widget, compra demonstrativa, área administrativa e README |
| Indicação cooperativa demonstrável | Origem → parceira registrada por oferta/pesquisa; não rotulada como venda |
| Responsividade e teclado | Revisão em desktop/celular, Escape e retorno de foco; estilos de movimento reduzido e foco inspecionados |
| Sem checkout próprio ou venda fictícia | Sem coleta de pagamento; compra real restrita ao destino Tray; demonstração não possui checkout |
| Escopo preservado | Sem marketplace nacional, ERP, frete, comissões, anúncios ou autoinstalação |

## Pendências externas e limites das conclusões

- O usuário confirmou que **não há loja Tray autorizada** para homologação. Nenhum tema foi modificado, nenhum catálogo real foi consultado e nenhum carrinho real foi criado. A incorporação real depende de autorização e capacidade do tema/plano.
- Uma chave foi encontrada no `.env`, sem exibir seu conteúdo. As flags `COCONUTRAY_GEMINI_ENABLED` e `COCONUTRAY_GEMINI_FREE_TIER_CONFIRMED` estavam desativadas e foram preservadas. Não foi feita chamada autenticada ao Google, nem aferida a cota efetiva do projeto. A busca textual funciona sem esse acesso.
- Os produtos demonstrativos não possuem fotografias de catálogos autorizados. Os espaços neutros não são apresentados como fotos reais nem substituídos por imagens de outros produtos.
- Não houve estudo com pessoas para medir compreensão em cinco segundos. O protocolo está em `docs/TESTE_USABILIDADE.md`; a meta não é declarada atingida pela inspeção técnica.
- A adesão dos comerciantes, a disposição de encaminhar clientes e o benefício comercial ainda precisam de validação real.
- Cliques, encaminhamentos e carrinhos não comprovam visita carregada, pagamento ou venda.

Fontes de implementação: [Tray MCP](https://developers.tray.com.br/), [guia de cores Tray](https://partners.tray.com.br/guia-de-design-1/logo-tray/cores), [temas Tray](https://partners.tray.com.br/themes/), [Google GenAI SDK](https://googleapis.github.io/python-genai/), [preços](https://ai.google.dev/gemini-api/docs/pricing) e [limites Gemini](https://ai.google.dev/gemini-api/docs/rate-limits).
