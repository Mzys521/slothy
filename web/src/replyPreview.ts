/** Opaque-origin previews. Model HTML never becomes part of the application DOM. */
export interface PreviewTheme { dark: boolean; background: string; surface: string; text: string; muted: string; accent: string; font: string }
export function previewTheme(): PreviewTheme {
  const style = getComputedStyle(document.documentElement)
  const dark = style.colorScheme === 'dark' || (style.colorScheme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches)
  const probe = document.createElement('span')
  probe.style.display = 'none'
  document.documentElement.append(probe)
  const color = (token: string) => { probe.style.color = `var(${token})`; return getComputedStyle(probe).color }
  const colors = { background: color('--surface'), surface: color('--neutral'), text: color('--ink'), muted: color('--muted'), accent: color('--accent-ink') }
  probe.remove()
  return {
    dark, ...colors,
    font: style.getPropertyValue('--font').trim(),
  }
}
const escape = (value: string) => value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
// Serialize into script text without allowing a model string to close the script element.
const literal = (value: unknown) => JSON.stringify(value).replace(/</g, '\\u003c')

export function replyPreview(source: string, label: string, kind: 'svg' | 'html'): string {
  const id = crypto.randomUUID()
  const initialTheme = previewTheme()
  const parsed = new DOMParser().parseFromString(kind === 'svg' ? '<body>' + source + '</body>' : source, 'text/html')
  parsed.querySelectorAll('base, meta[http-equiv], iframe, frame, object, embed').forEach(node => node.remove())
  if (kind === 'svg') {
    parsed.querySelectorAll('script, foreignObject').forEach(node => node.remove())
    parsed.querySelectorAll('*').forEach(node => { for (const attr of Array.from(node.attributes)) if (/^on/i.test(attr.name)) node.removeAttribute(attr.name) })
  }
  // A content-sized iframe must not feed its own growing height back into 100vh.
  // Keep the demo's logical viewport stable while allowing the complete document to grow.
  const viewportUnits = (css: string) => css.replace(/(\d*\.?\d+)(?:d|s|l)?vh\b/g, (_, value) => `${Number(value) * 6}px`)
  parsed.querySelectorAll('style').forEach(node => { node.textContent = viewportUnits(node.textContent ?? '') })
  parsed.querySelectorAll('[style]').forEach(node => { node.setAttribute('style', viewportUnits(node.getAttribute('style') ?? '')) })
  const style = parsed.createElement('style')
  style.textContent = `:root{color-scheme:${initialTheme.dark ? 'dark' : 'light'};--preview-bg:${initialTheme.background};--preview-surface:${initialTheme.surface};--preview-text:${initialTheme.text};--preview-muted:${initialTheme.muted};--preview-accent:${initialTheme.accent};--preview-font:${initialTheme.font}}html{height:auto!important;overflow:hidden}body{margin:0!important;min-height:0!important;height:auto!important;display:flow-root;overflow:visible;background:var(--preview-bg);color:var(--preview-text);font-family:var(--preview-font)}*{box-sizing:border-box}body>svg{display:block;width:100%!important;height:auto!important;max-height:none!important}body>:not(script):not(style){max-width:none;max-height:none!important;overflow:visible!important}img,video,canvas{max-width:100%}a{color:var(--preview-accent)}`
  style.textContent += 'body,body *{font-family:var(--preview-font)!important}body :is(pre,code,kbd,samp),body :is(pre,code,kbd,samp) *{font-family:ui-monospace,SFMono-Regular,Consolas,monospace!important}'
  const variables = parsed.createElement('style')
  variables.textContent = style.textContent.slice(0, style.textContent.indexOf('}') + 1)
  parsed.head.prepend(variables)
  parsed.head.append(style)
  const script = parsed.createElement('script')
  script.textContent = `(()=>{
    const id=${literal(id)}, originals=new Map(); let scheduled=false;
    try{Object.defineProperty(window,'innerHeight',{get:()=>600,configurable:true})}catch{}
    const root=document.documentElement;
    function neutral(value){const m=value.match(/^rgba?\\(([^)]+)\\)/);if(!m)return null;const c=m[1].split(/[, /]+/).map(Number);if(c.length>3&&c[3]<.05)return null;return Math.max(...c.slice(0,3))-Math.min(...c.slice(0,3))<34 ? (c[0]*.2126+c[1]*.7152+c[2]*.0722)/255 : null}
    function theme(t){
      for(const [el,css] of originals)el.style.cssText=css; originals.clear();
      root.style.colorScheme=t.dark?'dark':'light';
      for(const [key,value] of Object.entries({bg:t.background,surface:t.surface,text:t.text,muted:t.muted,accent:t.accent,font:t.font}))root.style.setProperty('--preview-'+key,value);
      root.style.background=t.background;root.style.color=t.text;
      for(const el of document.body.querySelectorAll('*')){
        if(!(el instanceof HTMLElement||el instanceof SVGElement)||['SCRIPT','STYLE'].includes(el.tagName))continue;
        const css=getComputedStyle(el), changes=[];
        for(const p of ['color','background-color','border-top-color','border-right-color','border-bottom-color','border-left-color','fill','stroke']){
          const n=neutral(css.getPropertyValue(p));if(n===null)continue;
          if((p==='fill'||p==='stroke')&&!(el instanceof SVGElement))continue;
          let value=p==='background-color'?t.surface:p.includes('border')?t.muted:p==='fill'&&el.tagName.toLowerCase()!=='text'&&n>.8?t.surface:n>.25&&n<.75?t.muted:t.text;
          if(p==='fill'&&el.tagName.toLowerCase()==='rect'&&el.ownerSVGElement){const box=el.ownerSVGElement.viewBox.baseVal;if(Number(el.getAttribute('width'))*Number(el.getAttribute('height'))>=box.width*box.height*.65)value=t.background;}
          changes.push([p,value]);
        }
        if(changes.length){originals.set(el,el.style.cssText);for(const [p,v]of changes)el.style.setProperty(p,v,'important');}
      }
      measure();
    }
    function measure(){if(scheduled)return;scheduled=true;requestAnimationFrame(()=>{scheduled=false;const body=document.body;const height=Math.max(body.scrollHeight,body.getBoundingClientRect().height,...Array.from(body.children).map(el=>el.getBoundingClientRect().bottom+scrollY));const width=Math.max(root.clientWidth,body.scrollWidth);parent.postMessage({kind:'preview-size',id,height:Math.ceil(height)+2,width},'*')})}
    addEventListener('message',e=>{if(e.source!==parent||e.data?.id!==id)return;if(e.data.kind==='preview-theme')theme(e.data.theme)});
    new ResizeObserver(measure).observe(document.body);
    new MutationObserver(measure).observe(document.body,{childList:true,subtree:true,characterData:true,attributes:true});
    addEventListener('load',measure);addEventListener('resize',measure);
    document.fonts.ready.then(measure);theme(${literal(initialTheme)});
  })()`
  parsed.body.append(script)
  const inner = '<!doctype html>' + parsed.documentElement.outerHTML
  // The enclosing document permits only about:srcdoc frames; demos cannot navigate
  // themselves to a network URL. Neither layer has same-origin, top-navigation or popups.
  const policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; frame-src about:; connect-src 'none'; base-uri 'none'; form-action 'none'"
  const wrapper = '<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="' + policy + '">' +
    '<style>html,body{margin:0;width:100%;height:100%;overflow:hidden}iframe{display:block;position:absolute;left:0;top:0;width:100%;height:600px;border:0;transform-origin:0 0}</style>' +
    '<iframe sandbox="allow-scripts" title="' + escape(label) + '" srcdoc="' + escape(inner) + '"></iframe>' +
    '<script>(()=>{const id=' + literal(id) + `, frame=document.querySelector('iframe');let theme,logicalWidth=0;
    function fit(){if(!logicalWidth)return;const scale=Math.min(1,innerWidth/logicalWidth);frame.style.width=(scale<1?logicalWidth:innerWidth)+'px';frame.style.transform='scale('+scale+')';}
    addEventListener('message',e=>{const d=e.data;if(!d||d.id!==id)return;
      if(e.source===parent&&d.kind==='preview-theme'){theme=d;frame.contentWindow.postMessage(d,'*')}
      if(e.source===frame.contentWindow&&d.kind==='preview-size'&&Number.isFinite(d.height)&&Number.isFinite(d.width)){
        logicalWidth=Math.max(innerWidth,Math.min(10000,d.width));fit();const scale=Math.min(1,innerWidth/logicalWidth);const height=Math.max(1,Math.min(100000,d.height));frame.style.height=height+'px';parent.postMessage({kind:'reply-preview-size',id,height:Math.ceil(height*scale)},'*');
      }
    });frame.addEventListener('load',()=>{if(theme)frame.contentWindow.postMessage(theme,'*')});addEventListener('resize',()=>{logicalWidth=0;frame.style.width='100%';frame.style.transform='none'});
    })()</script>`
  return '<iframe class="reply-demo reply-' + kind + '" data-preview-id="' + id + '" sandbox="allow-scripts" referrerpolicy="no-referrer" title="' + escape(label) + '" srcdoc="' + escape(wrapper) + '"></iframe>'
}
