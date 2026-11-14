import {createRoot,type Root} from 'react-dom/client';
import {CoconutrayWidget} from './Widget';
import type {WidgetTheme} from './api';
import styles from './styles.css?inline';
class CoconutrayElement extends HTMLElement{
 private reactRoot:Root|null=null;
 static observedAttributes=['store-id','api-base','theme'];
 connectedCallback(){if(!this.reactRoot){const shadow=this.shadowRoot||this.attachShadow({mode:'open'});const style=document.createElement('style');style.textContent=styles;const mount=document.createElement('div');shadow.replaceChildren(style,mount);this.reactRoot=createRoot(mount)}this.renderWidget()}
 attributeChangedCallback(){this.renderWidget()}
 private renderWidget(){if(!this.reactRoot)return;let theme:WidgetTheme={};try{theme=JSON.parse(this.getAttribute('theme')||'{}')}catch{/* Invalid optional theme uses safe defaults. */}const storeId=this.getAttribute('store-id');if(!storeId){this.reactRoot.render(<p>Informe a loja participante para ativar a busca.</p>);return}this.reactRoot.render(<CoconutrayWidget storeId={storeId} apiBase={this.getAttribute('api-base')||''} theme={theme}/>)}
 disconnectedCallback(){this.reactRoot?.unmount();this.reactRoot=null;if(this.shadowRoot)this.shadowRoot.replaceChildren()}
}
if(!customElements.get('coconutray-widget'))customElements.define('coconutray-widget',CoconutrayElement);
