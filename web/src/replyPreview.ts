/**
 * Sandboxed reply previews: renders model-produced HTML/SVG inside double-nested opaque-origin iframes.
 *
 * 沙箱化的回复预览：在双层不透明源 iframe 中渲染模型产出的 HTML/SVG。
 */

/** Resolved preview theme passed into the sandbox. / 传入沙箱的已解析预览主题值。 */
export interface PreviewTheme { dark: boolean; background: string; surface: string; text: string; muted: string; accent: string; font: string }
/**
 * Reads the active application theme into plain values the sandbox can apply.
 *
 * 读取当前应用主题，转换为沙箱内可直接应用的纯值。
 *
 * @returns Theme snapshot with resolved colors and font stack. / 含已解析颜色与字体栈的主题快照。
 */
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
/**
 * Escapes a string for safe use inside an HTML attribute.
 *
 * 转义字符串，以便安全地放入 HTML 属性。
 *
 * @param value - Raw attribute value. / 原始属性值。
 * @returns Escaped attribute value. / 转义后的属性值。
 */
const escape = (value: string) => value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
/**
 * Serializes a value as a JS literal safe inside a `<script>` body.
 *
 * Model strings can never close the script element because `<` is escaped.
 * 由于 `<` 被转义，模型字符串无法闭合 script 元素。
 *
 * 将值序列化为可安全嵌入 `<script>` 的 JS 字面量。
 *
 * @param value - Serializable value. / 可序列化的值。
 * @returns JSON text with `<` escaped. / 已转义 `<` 的 JSON 文本。
 */
const literal = (value: unknown) => JSON.stringify(value).replace(/</g, '\\u003c')

/**
 * Builds the double-iframe preview markup for one assistant reply.
 *
 * 为一条助手回复构建双层 iframe 预览标记。
 *
 * @param source - Raw HTML or SVG emitted by the model. / 模型输出的原始 HTML 或 SVG。
 * @param label - Accessible iframe title. / iframe 的无障碍标题。
 * @param kind - Whether the source is a full document or an SVG fragment. / 源内容属于完整文档还是 SVG 片段。
 * @returns Outer iframe HTML string to inject with `v-html`. / 可用 `v-html` 注入的外层 iframe HTML 字符串。
 */
export function replyPreview(source: string, label: string, kind: 'svg' | 'html'): string {
  const id = crypto.randomUUID()
  const initialTheme = previewTheme()
  const parsed = new DOMParser().parseFromString(kind === 'svg' ? '<body>' + source + '</body>' : source, 'text/html')
  parsed.querySelectorAll('base, meta[http-equiv], iframe, frame, object, embed').forEach(node => node.remove())
  if (kind === 'svg') {
    parsed.querySelectorAll('script, foreignObject').forEach(node => node.remove())
    parsed.querySelectorAll('*').forEach(node => { for (const attr of Array.from(node.attributes)) if (/^on/i.test(attr.name)) node.removeAttribute(attr.name) })
  }
  // HTML uses a stable, scrollable viewport. Only static diagrams size to content.
  // HTML 使用固定、可滚动的视口；只有静态图示按内容调整高度。
  const autoSize = kind === 'svg'
  const viewportUnits = (css: string) => css.replace(/(\d*\.?\d+)(?:d|s|l)?vh\b/g, (_, value) => `${Number(value) * 6}px`)
  if (autoSize) {
    parsed.querySelectorAll('style').forEach(node => { node.textContent = viewportUnits(node.textContent ?? '') })
    parsed.querySelectorAll('[style]').forEach(node => { node.setAttribute('style', viewportUnits(node.getAttribute('style') ?? '')) })
  }
  const style = parsed.createElement('style')
  style.textContent = `:root{color-scheme:${initialTheme.dark ? 'dark' : 'light'};--preview-bg:${initialTheme.background};--preview-surface:${initialTheme.surface};--preview-text:${initialTheme.text};--preview-muted:${initialTheme.muted};--preview-accent:${initialTheme.accent};--preview-font:${initialTheme.font}}body{margin:0!important;background:var(--preview-bg);color:var(--preview-text);font-family:var(--preview-font)}*{box-sizing:border-box}img,video,canvas{max-width:100%}a{color:var(--preview-accent)}`
  style.textContent += autoSize
    ? 'html{height:auto!important;overflow:hidden}body{min-height:0!important;height:auto!important;display:flow-root;overflow:visible}body>svg{display:block;width:100%!important;height:auto!important;max-height:none!important}'
    : 'html{height:100%!important;overflow:auto!important}body{min-height:100%;display:flow-root}'
  // Scrollbars are an implicit affordance: keep scrolling functional but never paint
  // native scrollbars or reserve their gutter inside a reply preview.
  // 滚动条作为隐式交互保留滚动能力，但预览内不绘制原生滚动条，也不预留滚动条槽位。
  style.textContent += 'html,body,body *{scrollbar-width:none!important;-ms-overflow-style:none!important}html::-webkit-scrollbar,body::-webkit-scrollbar,body *::-webkit-scrollbar{display:none!important;width:0!important;height:0!important}'
  style.textContent += 'body,body *{font-family:var(--preview-font)!important}body :is(pre,code,kbd,samp),body :is(pre,code,kbd,samp) *{font-family:ui-monospace,SFMono-Regular,Consolas,monospace!important}'
  const variables = parsed.createElement('style')
  variables.textContent = style.textContent.slice(0, style.textContent.indexOf('}') + 1)
  parsed.head.prepend(variables)
  parsed.head.append(style)
  const script = parsed.createElement('script')
  script.textContent = `(()=>{
    const id=${literal(id)}, autoSize=${autoSize}, originals=new Map(), colors=new Map(); let scheduled=false,lastTheme,themeScheduled=false,observer;
    if(autoSize)try{Object.defineProperty(window,'innerHeight',{get:()=>600,configurable:true})}catch{}
    const root=document.documentElement;
    const paint=document.createElement('canvas').getContext('2d',{willReadFrequently:true});
    function neutral(value){
      if(colors.has(value))return colors.get(value);
      let result=null;
      if(paint&&CSS.supports('color',value)){paint.clearRect(0,0,1,1);paint.fillStyle=value;paint.fillRect(0,0,1,1);const c=paint.getImageData(0,0,1,1).data;
        if(c[3]>=13&&Math.max(...c.slice(0,3))-Math.min(...c.slice(0,3))<34)result={light:(c[0]*.2126+c[1]*.7152+c[2]*.0722)/255,alpha:c[3]/255};}
      colors.set(value,result);return result;
    }
    function theme(t){
      lastTheme=t;observer?.disconnect();
      // Restore only properties still owned by us; preserve changes made by the demo.
      // 仅恢复仍由主题持有的属性，保留演示脚本主动修改的其他样式。
      for(const [el,props] of originals)for(const [p,old]of props)if(el.style.getPropertyValue(p)===old.applied&&el.style.getPropertyPriority(p)==='important'){
        if(old.value)el.style.setProperty(p,old.value,old.priority);else el.style.removeProperty(p);
      } originals.clear();
      root.style.colorScheme=t.dark?'dark':'light';
      for(const [key,value] of Object.entries({bg:t.background,surface:t.surface,text:t.text,muted:t.muted,accent:t.accent,font:t.font}))root.style.setProperty('--preview-'+key,value);
      root.style.background=t.background;root.style.color=t.text;
      const snapshots=[];
      // Resolve inherited colors before changing any parent, including SVG paint servers.
      // 先解析所有继承色，再修改父节点；同时处理 SVG 渐变的颜色停靠点。
      for(const el of [document.body,...document.body.querySelectorAll('*')]){
        if(!(el instanceof HTMLElement||el instanceof SVGElement)||['script','style'].includes(el.tagName.toLowerCase()))continue;
        const css=getComputedStyle(el), changes=[];
        for(const p of ['color','background-color','border-top-color','border-right-color','border-bottom-color','border-left-color','fill','stroke','stop-color','flood-color']){
          const n=neutral(css.getPropertyValue(p));if(n===null)continue;
          if(['fill','stroke','stop-color','flood-color'].includes(p)&&!(el instanceof SVGElement))continue;
          let value=p==='background-color'?(el===document.body?t.background:t.surface):p.includes('border')?t.muted:['fill','stop-color','flood-color'].includes(p)&&el.tagName.toLowerCase()!=='text'&&n.light>.8?t.surface:n.light>.25&&n.light<.75?t.muted:t.text;
          if(p==='fill'&&el.tagName.toLowerCase()==='rect'&&el.ownerSVGElement){const box=el.ownerSVGElement.viewBox.baseVal;if(Number(el.getAttribute('width'))*Number(el.getAttribute('height'))>=box.width*box.height*.65)value=t.background;}
          if(n.alpha<1)value='color-mix(in srgb,'+value+' '+(n.alpha*100)+'%,transparent)';
          // Mask luminance is geometry, not a visible surface; pin its original paint.
          // 遮罩亮度决定几何裁剪，不能当作可见背景改色；固定其原始绘制值。
          if(el instanceof SVGElement&&el.closest('mask,clipPath,filter'))value=css.getPropertyValue(p);
          changes.push([p,value]);
        }
        if(changes.length)snapshots.push([el,changes]);
      }
      for(const [el,changes]of snapshots){const props=[];for(const [p,v]of changes){const old={value:el.style.getPropertyValue(p),priority:el.style.getPropertyPriority(p)};el.style.setProperty(p,v,'important');old.applied=el.style.getPropertyValue(p);props.push([p,old]);}originals.set(el,props);}
      observer?.observe(root,{childList:true,subtree:true,characterData:true,attributes:true});
      measure();
    }
    function measure(){if(!autoSize||scheduled)return;scheduled=true;requestAnimationFrame(()=>{scheduled=false;const body=document.body;const height=Math.max(body.scrollHeight,body.getBoundingClientRect().height,...Array.from(body.children).map(el=>el.getBoundingClientRect().bottom+scrollY));const width=Math.max(root.clientWidth,body.scrollWidth);parent.postMessage({kind:'preview-size',id,height:Math.ceil(height)+2,width},'*')})}
    addEventListener('message',e=>{if(e.source!==parent||e.data?.id!==id)return;if(e.data.kind==='preview-theme')theme(e.data.theme)});
    new ResizeObserver(measure).observe(document.body);
    observer=new MutationObserver(records=>{measure();if(!themeScheduled&&records.some(r=>r.type!=='attributes'||/^(style|class|fill|stroke|color|stop-color|flood-color)$/.test(r.attributeName))){themeScheduled=true;requestAnimationFrame(()=>{themeScheduled=false;if(lastTheme)theme(lastTheme)})}});
    addEventListener('load',measure);addEventListener('resize',measure);
    document.fonts.ready.then(measure);theme(${literal(initialTheme)});
  })()`
  parsed.body.append(script)
  const inner = '<!doctype html>' + parsed.documentElement.outerHTML
  // The enclosing document permits only about:srcdoc frames; demos cannot navigate
  // themselves to a network URL. Neither layer has same-origin, top-navigation or popups.
  // 外层文档只允许 about:srcdoc 框架，演示内容无法自行跳转到网络地址。
  // 两层都不具备同源权限、顶层导航或弹窗能力。
  const policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; frame-src about:; connect-src 'none'; base-uri 'none'; form-action 'none'"
  const wrapper = '<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="' + policy + '">' +
    '<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;scrollbar-width:none;-ms-overflow-style:none}html::-webkit-scrollbar,body::-webkit-scrollbar{display:none;width:0;height:0}iframe{display:block;position:absolute;left:0;top:0;width:100%;height:' + (autoSize ? '600px' : '100%') + ';border:0;transform-origin:0 0}</style>' +
    '<iframe sandbox="allow-scripts" title="' + escape(label) + '" srcdoc="' + escape(inner) + '"></iframe>' +
    '<script>(()=>{const id=' + literal(id) + `, autoSize=${autoSize}, frame=document.querySelector('iframe');let theme,logicalWidth=0;
    function fit(){if(!logicalWidth)return;const scale=Math.min(1,innerWidth/logicalWidth);frame.style.width=(scale<1?logicalWidth:innerWidth)+'px';frame.style.transform='scale('+scale+')';}
    addEventListener('message',e=>{const d=e.data;if(!d||d.id!==id)return;
      if(e.source===parent&&d.kind==='preview-theme'){theme=d;frame.contentWindow.postMessage(d,'*')}
      if(autoSize&&e.source===frame.contentWindow&&d.kind==='preview-size'&&Number.isFinite(d.height)&&Number.isFinite(d.width)){
        logicalWidth=Math.max(innerWidth,Math.min(10000,d.width));fit();const scale=Math.min(1,innerWidth/logicalWidth);const height=Math.max(1,Math.min(100000,d.height));frame.style.height=height+'px';parent.postMessage({kind:'reply-preview-size',id,height:Math.ceil(height*scale)},'*');
      }
    });frame.addEventListener('load',()=>{if(theme)frame.contentWindow.postMessage(theme,'*')});addEventListener('resize',()=>{logicalWidth=0;frame.style.width='100%';frame.style.transform='none'});
    })()</script>`
  return '<iframe class="reply-demo reply-' + kind + '" data-preview-id="' + id + '" sandbox="allow-scripts" referrerpolicy="no-referrer" title="' + escape(label) + '" srcdoc="' + escape(wrapper) + '"></iframe>'
}
