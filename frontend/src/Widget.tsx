import {forwardRef,useEffect,useImperativeHandle,useRef,useState,type CSSProperties,type FormEvent} from 'react';
import {ArrowLeft,ArrowRight,ArrowUpRight,Check,CheckCheck,ChevronRight,ExternalLink,Info,LoaderCircle,MapPin,Network,Search,ShieldCheck,SlidersHorizontal,Store as StoreIcon,X,TriangleAlert} from 'lucide-react';
import {api,date,money,type Offer,type SearchResult,type Store,type WidgetTheme} from './api';
import {CoconutMark,OfferCard,ProductPhoto} from './components';
export type WidgetHandle={open:(query?:string)=>void};
export type WidgetProps={storeId:string;apiBase?:string;theme?:WidgetTheme;hideTrigger?:boolean;onHandoff?:()=>void};
function themeStyle(theme:WidgetTheme):CSSProperties{
 const style:Record<string,string>={};const names={primary:'primary',button:'button',text:'text',background:'surface',border:'border'};
 for(const [key,name] of Object.entries(names)){const value=theme[key as keyof WidgetTheme];if(typeof value==='string'&&/^#[\da-f]{6}$/i.test(value))style[`--cr-${name}`]=value;}
 if(typeof theme.radius==='number')style['--cr-radius']=`${Math.min(24,Math.max(0,theme.radius))}px`;
 if(theme.font&&/^[\w\s,'"-]+$/.test(theme.font))style.fontFamily=theme.font;
 return style as CSSProperties;
}
export const CoconutrayWidget=forwardRef<WidgetHandle,WidgetProps>(function CoconutrayWidget({storeId,apiBase='',theme={},hideTrigger=false,onHandoff},ref){
 const dialog=useRef<HTMLDialogElement>(null),activeRequest=useRef<AbortController|null>(null);
 const [open,setOpen]=useState(false),[query,setQuery]=useState(''),[report,setReport]=useState<SearchResult|null>(null),[selected,setSelected]=useState<Offer|null>(null);
 const [config,setConfig]=useState<{store:Store;theme:WidgetTheme;partner_count:number}|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[handoff,setHandoff]=useState('');
 const [sort,setSort]=useState('relevance'),[compare,setCompare]=useState<string[]>([]),[comparing,setComparing]=useState(false),[cart,setCart]=useState(false);
 useEffect(()=>{api<{store:Store;theme:WidgetTheme;partner_count:number}>(`/api/public/stores/${encodeURIComponent(storeId)}`,{},apiBase).then(setConfig).catch(e=>setError(e.message));return()=>activeRequest.current?.abort()},[storeId,apiBase]);
 useEffect(()=>{if(open&&!dialog.current?.open)dialog.current?.showModal();if(!open&&dialog.current?.open)dialog.current?.close()},[open]);
 async function search(value=query){
  if(!value.trim()){setError('Conte qual produto você procura.');return;}
  activeRequest.current?.abort();const controller=new AbortController();activeRequest.current=controller;
  setBusy(true);setError('');setHandoff('');setSelected(null);setReport(null);setComparing(false);setCompare([]);
  try{const result=await api<SearchResult>('/api/public/search',{method:'POST',body:JSON.stringify({store_id:storeId,query:value.trim(),order:sort}),signal:controller.signal},apiBase);if(!controller.signal.aborted)setReport(result)}
  catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:'Não foi possível pesquisar agora.')}
  finally{if(!controller.signal.aborted)setBusy(false)}
 }
 function openPanel(value?:string){setOpen(true);if(value!==undefined){setQuery(value);if(value.trim())void search(value)}}
 useImperativeHandle(ref,()=>({open:openPanel}));
 async function select(offer:Offer){setSelected(offer);setComparing(false);setCart(false);setHandoff('');setError('');if(report)try{await api(`/api/public/searches/${report.search_id}/select`,{method:'POST',body:JSON.stringify({key:offer.key})},apiBase)}catch(e){setError((e as Error).message)}}
 async function buy(){if(!report||!selected)return;setBusy(true);setError('');try{
  const result=await api<{mode:string;url:string|null;message:string}>(`/api/public/searches/${report.search_id}/handoff`,{method:'POST',body:JSON.stringify({key:selected.key,prepare_cart:cart})},apiBase);
  onHandoff?.();if(result.mode==='real'&&result.url){window.location.assign(result.url)}else setHandoff(result.message);
 }catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 function toggleCompare(key:string){setCompare(old=>old.includes(key)?old.filter(k=>k!==key):old.length<3?[...old,key]:old)}
 const offers=[...(report?.offers||[])].sort((a,b)=>{if(a.status!==b.status)return a.status==='compatible'?-1:1;if(sort==='price')return Number(a.product.price)-Number(b.product.price);if(sort==='distance')return (a.distance_km??Infinity)-(b.distance_km??Infinity);return 0});
 const step=handoff?4:selected?3:report?2:1;
 return <div className="coconutray-root" style={themeStyle({...config?.theme,...theme})}>
 {!hideTrigger&&<button className="partner-trigger" onClick={()=>openPanel()}><Network size={19}/>Não encontrou? Buscar nas lojas parceiras.<ArrowRight size={18}/></button>}
 <dialog ref={dialog} className="cr-widget" aria-labelledby="widget-title" onCancel={()=>setOpen(false)} onClose={()=>setOpen(false)} onClick={e=>{if(e.target===e.currentTarget)setOpen(false)}} style={themeStyle({...config?.theme,...theme})}>
 <header className="widget-header"><div className="widget-brand"><CoconutMark/><div><strong id="widget-title">Encontre nas lojas parceiras</strong><span>Uma oportunidade conecta outra.</span></div></div><button className="icon-btn" onClick={()=>setOpen(false)} aria-label="Fechar busca de parceiros"><X size={22}/></button></header>
 <div className="widget-context"><span><StoreIcon size={15}/>Você está na {config?.store.name||'loja de origem'}</span><span className={'mode-pill '+(config?.store.mode==='real'?'real':'')}>{config?.store.mode==='real'?'Lojas participantes':'Demonstração · dados fictícios'}</span></div>
 <ol className="widget-steps" aria-label="Etapas da busca">{['Busque','Compare','Escolha','Continue na loja'].map((label,i)=><li className={step===i+1?'current':step>i+1?'complete':''} key={label}><span>{step>i+1?<Check size={12}/>:i+1}</span>{label}</li>)}</ol>
 <div className="widget-body" aria-busy={busy}>
 {error&&<div className="notice error" role="alert"><TriangleAlert size={18}/>{error}</div>}
 {handoff?<div className="handoff-success" role="status"><span className="success-ring"><CheckCheck size={30}/></span><span className="eyebrow">PERCURSO DEMONSTRADO</span><h2>Uma nova oportunidade, conectada.</h2><p>{handoff}</p><div className="notice"><Info size={18}/>Registramos um encaminhamento demonstrativo, sem confirmar venda.</div><button className="primary-btn" onClick={()=>{setHandoff('');setSelected(null)}}>Voltar às opções<ArrowLeft size={16}/></button></div>:
 selected?<>
 <button className="text-btn" onClick={()=>{setSelected(null);setError('')}}><ArrowLeft size={16}/>Voltar aos resultados</button>
 <div className="offer-detail"><div><ProductPhoto offer={selected} large/><p className="microcopy">{selected.product.image_url?'Imagem fornecida pelo catálogo da loja.':'O catálogo não forneceu fotografia deste produto.'}</p></div><div className="detail-copy"><span className="store-label"><StoreIcon size={15}/>{selected.store.name}</span><h2>{selected.product.name}</h2><p className="variant">{selected.product.variant_label||'Variação não informada'}</p><strong className="detail-price">{money(selected.product.price)}</strong>
 {selected.status==='uncertain'?<div className="notice warning"><Info size={18}/>{selected.reasons.filter(r=>r.startsWith('Confirmar')).join('. ')}</div>:<p className="match-good"><Check size={16}/>Atende aos requisitos conferidos nesta busca</p>}
 <p className="product-description">{selected.product.description||'Descrição não informada.'}</p><dl className="attribute-list">{Object.entries(selected.product.attributes).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
 <p className="microcopy">{selected.product.availability_known?'Disponibilidade on-line informada no catálogo.':'Disponibilidade on-line a confirmar.'} Isso não garante estoque físico.</p><p className="microcopy">Consulta: {date(selected.fetched_at)}{selected.cached?' · consulta guardada por até 60 s':''}.</p>
 </div></div>
 <div className="store-information"><div><StoreIcon size={22}/><div><h3>Conheça quem oferece</h3><strong>{selected.store.name}</strong><p className="official-domain">{selected.store.domain.replace('https://','')}</p></div></div><div><p><MapPin size={15}/>{selected.store.address||'Endereço físico não informado'}</p>{selected.distance_km!==null&&<p>{selected.distance_km.toLocaleString('pt-BR',{maximumFractionDigits:1})} km em linha reta · não representa trajeto</p>}<p className="microcopy">{selected.store.mode==='demo'?'Loja, endereço e localização fictícios.':'Endereço e localização declarados pelo participante. Produto e preço: catálogo público da loja.'}</p></div></div>
 <p className="microcopy trust-note"><ShieldCheck size={16}/>Participar da rede não é um selo de segurança ou avaliação de consumidores.</p>
 {selected.store.mode==='real'&&selected.status==='compatible'&&<label className="cart-option"><input type="checkbox" checked={cart} onChange={e=>setCart(e.target.checked)}/>Preparar um carrinho com uma unidade desta variação, se a loja permitir.</label>}
 <div className="purchase-footer"><div><strong>A compra acontece na {selected.store.name}.</strong><span>{selected.store.mode==='demo'?'Esta é uma simulação. Não há checkout ou pagamento.':'Você continuará no ambiente oficial da loja escolhida.'}</span></div><button className="primary-btn" onClick={()=>void buy()} disabled={busy||selected.store.mode==='real'&&!selected.product.url&&!cart}>{busy?<LoaderCircle className="spin" size={18}/>:<ExternalLink size={18}/>}Comprar na loja parceira</button></div>
 </>:comparing?<><button className="text-btn" onClick={()=>setComparing(false)}><ArrowLeft size={16}/>Voltar aos resultados</button><h2>Compare antes de escolher</h2><div className="comparison-grid">{offers.filter(o=>compare.includes(o.key)).map(offer=><OfferCard key={offer.key} offer={offer} onSelect={()=>void select(offer)}/>)}</div></>:<>
 {!report&&<div className="widget-intro"><span className="eyebrow">O COMÉRCIO LOCAL, CONECTADO</span><h2>Se não tem aqui,<br/>pode ter bem perto.</h2><p>Busque nas lojas participantes e escolha com qual delas continuar. Sem criar conta.</p></div>}
 <form className="widget-search" onSubmit={(e:FormEvent)=>{e.preventDefault();void search()}}><label htmlFor="partner-query">O que você está procurando?</label><div><Search size={19}/><input id="partner-query" value={query} onChange={e=>setQuery(e.target.value)} maxLength={300} placeholder="Ex.: mochila preta impermeável até R$ 180" autoComplete="off"/><button type="submit" aria-label="Buscar produtos nas lojas parceiras" className="primary-btn" disabled={busy}>{busy?<LoaderCircle className="spin" size={18}/>:<Search size={18}/>}<span>Buscar</span></button></div></form>
 {busy&&<div className="search-loading" role="status"><LoaderCircle size={22} className="spin"/><span>Conferindo as opções nas lojas participantes…</span></div>}
 {report&&!busy&&<>
 <div className="results-heading"><div><h2>{offers.length?'Encontramos estes caminhos':'Não encontramos uma opção compatível'}</h2><p>{report.responded} de {report.consulted} catálogos consultados · {report.mode==='demo'?'ofertas fictícias':'informações das lojas'}</p></div>{offers.length>0&&<label className="sort-control"><SlidersHorizontal size={15}/><span className="sr-only">Ordenar ofertas</span><select aria-label="Ordenar ofertas" value={sort} onChange={e=>setSort(e.target.value)}><option value="relevance">Mais relevantes</option><option value="price">Menor preço</option>{offers.some(o=>o.distance_km!==null)&&<option value="distance">Mais próximas</option>}</select></label>}</div>
 <div className="constraint-pills">{Object.entries(report.constraints).map(([k,v])=><span key={k}>{k==='impermeavel'?'Impermeável':k==='cor'?'Cor':k==='tamanho'?'Tamanho':k==='genero'?'Público':k==='voltagem'?'Voltagem':k}: {v==='nao'?'não':v}</span>)}{report.max_price&&<span>Até {money(report.max_price)}</span>}</div>
 {report.failed_stores.length>0&&<div className="notice warning" role="status"><Info size={18}/>Não conseguimos consultar {report.failed_stores.join(', ')}. As outras opções continuam disponíveis.</div>}
 {report.warnings.map(w=><div className="notice" key={w}>{w}</div>)}
 {!offers.length&&<div className="no-partner-results"><Search size={34}/><p>{report.responded===0?'Não foi possível consultar os catálogos agora. Isso não comprova ausência de produtos.':'Tente um nome mais simples ou revise os requisitos. Nenhum produto será inventado para completar os resultados.'}</p></div>}
 {(['compatible','uncertain'] as const).map(status=>{const group=offers.filter(o=>o.status===status);return group.length>0&&<section className="result-section" key={status}><h3>{status==='compatible'?'Compatíveis com seu pedido':'Precisam de confirmação'}<span>{group.length}</span></h3>{status==='uncertain'&&<p className="section-note">Faltam informações importantes. Confira os pontos indicados com a loja.</p>}<div className="partner-grid">{group.map(offer=><OfferCard key={offer.key} offer={offer} onSelect={()=>void select(offer)} onCompare={()=>toggleCompare(offer.key)} compared={compare.includes(offer.key)}/>)}</div></section>})}
 {compare.length>=2&&<div className="compare-bar"><span>{compare.length} opções selecionadas</span><button className="primary-btn" onClick={()=>setComparing(true)}>Comparar opções<ArrowRight size={16}/></button></div>}
 <details className="ranking-info"><summary>Como escolhemos estas opções?</summary><p>Conferimos palavras e requisitos do pedido. Conflitos conhecidos são excluídos; informações ausentes ficam separadas. A relevância considera termos no nome e atributos confirmados. Preço desempata. Nenhum parceiro recebe prioridade comercial.</p><p>A busca cobre os resultados retornados pelos catálogos. Informações reais podem usar cache de até 60 segundos. Distância depende das coordenadas informadas, sempre em linha reta.</p></details>
 </>}
 {!report&&!busy&&<div className="widget-reassurance"><span><Check size={16}/>Sem cadastro para pesquisar</span><span><StoreIcon size={16}/>Cada oferta pertence a uma loja</span><span><ArrowUpRight size={16}/>Compra no ambiente da parceira</span></div>}
 </>}
 </div><footer className="widget-footer"><span><CoconutMark/>coconutray</span><p>Rede cooperativa independente. Não é um recurso oficial da Tray.</p></footer>
 </dialog></div>
});
