import {useEffect,useState} from 'react';
import {ImageOff,Network,ArrowUpRight,MapPin} from 'lucide-react';
import {type Offer,money} from './api';
export function CoconutMark(){return <span className="coconut-mark" aria-hidden="true"><Network size={19} strokeWidth={2.2}/></span>}
export function ProductPhoto({offer,large=false}:{offer:Offer;large?:boolean}){
 const [failed,setFailed]=useState(false);useEffect(()=>setFailed(false),[offer.product.image_url]);
 return <div className={'product-photo'+(large?' large':'')}>
 {offer.product.image_url&&!failed?<img src={offer.product.image_url} alt={offer.product.name} loading="lazy" width="400" height="320" onError={()=>setFailed(true)}/>:<div className="photo-missing"><ImageOff size={large?40:29} strokeWidth={1.3}/><span>Imagem não fornecida</span></div>}
 </div>
}
export function OfferCard({offer,onSelect,onCompare,compared=false}:{offer:Offer;onSelect:()=>void;onCompare?:()=>void;compared?:boolean}){
 return <article className="offer-card">
 <ProductPhoto offer={offer}/>
 <div className="offer-content"><div className="offer-store"><span className="store-dot"/>{offer.store.name}</div>
 <h3>{offer.product.name}</h3><p className="variant">{offer.product.variant_label||'Variação não informada'}</p>
 <strong className="product-price">{money(offer.product.price)}</strong>
 <p className="availability"><span className={'availability-dot '+(!offer.product.availability_known?'unknown':'')}/>{offer.product.availability_known?'Disponível on-line, segundo o catálogo':'Disponibilidade a confirmar'}</p>
 {offer.distance_km!==null&&<p className="distance"><MapPin size={13}/>{offer.distance_km.toLocaleString('pt-BR',{maximumFractionDigits:1})} km em linha reta{offer.store.mode==='demo'?' · fictícios':''}</p>}
 {offer.status==='uncertain'&&<p className="uncertain-text">{offer.reasons.filter(r=>r.startsWith('Confirmar')).join(' · ')}</p>}
 <button className="secondary-btn full" onClick={onSelect}>Ver oferta <ArrowUpRight size={16}/></button>
 {onCompare&&<label className="compare-check"><input type="checkbox" checked={compared} onChange={onCompare}/>Comparar esta opção</label>}
 <span className="product-source">{offer.store.mode==='demo'?'Dados fictícios de demonstração':'Produto e preço: catálogo da loja'}</span>
 </div></article>
}
