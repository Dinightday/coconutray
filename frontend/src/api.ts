export type Store = {id:string;name:string;domain:string;mode:'demo'|'real';address:string;categories:string[];latitude:number|null;longitude:number|null;location_source:string;tray_name:string;endpoint_checked_at:string;active:boolean;theme?:WidgetTheme};
export type Product = {id:string;name:string;price:string;available:boolean;availability_known:boolean;variant_id:string|null;variant_label:string|null;description:string;source:string;url:string|null;image_url:string|null;attributes:Record<string,string>};
export type Offer = {key:string;store:Store;product:Product;status:'compatible'|'uncertain';reasons:string[];distance_km:number|null;fetched_at:string;cached:boolean};
export type SearchResult = {search_id:string;mode:'demo'|'real';origin:Store;offers:Offer[];constraints:Record<string,string>;max_price:string|null;consulted:number;responded:number;elapsed:number;failed_stores:string[];warnings:string[];engine:string};
export type WidgetTheme = {primary?:string;button?:string;text?:string;background?:string;border?:string;radius?:number;font?:string};
export const money = (value:string) => new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(Number(value));
export const date = (value:string) => new Intl.DateTimeFormat('pt-BR',{dateStyle:'short',timeStyle:'short',timeZone:'America/Sao_Paulo'}).format(new Date(value));
export async function api<T>(path:string, options:RequestInit={}, base=''):Promise<T> {
  const response=await fetch(base.replace(/\/$/,'')+path,{...options,credentials:path.startsWith('/api/merchant')?'include':'omit',headers:{'Content-Type':'application/json',...options.headers}});
  const data=await response.json().catch(()=>null);
  if(!response.ok) throw new Error(typeof data?.detail==='string'?data.detail:response.status===422?'Confira os campos preenchidos e tente novamente.':'Não foi possível concluir agora. Tente novamente.');
  return data as T;
}
