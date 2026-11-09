/** Real sandbox layout and theme regressions. / 真实沙箱的尺寸与主题回归。 */
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createSmokeBackend, runSmoke } from './smoke.mjs'

const output = path.resolve(fileURLToPath(new URL('..', import.meta.url)), '../_internal/core-qa/parallel-preview')
await mkdir(output, { recursive: true })
const reply = [
  'HTML animation with scrollable content:', '```html',
  '<style>@keyframes grow{0%,100%{height:30px}50%{height:1800px}}#animated{animation:grow 1s linear infinite;background:#eee}</style>',
  '<main id="preview-html"><h2>Bounded animation</h2><div id="animated"></div><p id="tail">SCROLLABLE_END</p><button onclick="this.textContent=\'INTERACTION_OK\'">Interact</button></main>',
  '```', 'SVG follows the application theme:', '```svg',
  '<svg id="preview-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360">',
  '<defs><linearGradient id="paper"><stop id="stop-a" stop-color="#fff"/><stop id="stop-b" offset="1" stop-color="#f5f5f5"/></linearGradient><mask id="cutout"><rect id="mask-fill" width="640" height="360" fill="white"/></mask></defs>',
  '<rect id="background" width="640" height="360" fill="url(#paper)"/>',
  '<rect id="white" x="20" y="20" width="200" height="200" fill="white"/>',
  '<rect id="modern" x="240" y="20" width="160" height="200" style="fill:oklch(100% 0 0)!important"/>',
  '<rect id="accent" x="420" y="20" width="200" height="200" fill="#d94040"/>',
  '<path id="empty" d="M20 240 L620 240" fill="none" stroke="#111"/>',
  '<text id="label" x="20" y="290" fill="#111">Theme-aware SVG</text></svg>', '```',
].join('\n')
const thread = { thread_id: 'previews', mode: 'chat', title: 'Preview regression', workspace_id: 'personal', archived: false }
const previewBackend = () => createSmokeBackend({ handle: async (request, response) => {
  const url = new URL(request.url, 'http://localhost')
  const json = body => { response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(body)); return true }
  if (url.pathname === '/api/v1/threads') return json({ items: url.searchParams.get('archived') === 'true' ? [] : [thread], next_offset: null })
  if (url.pathname.endsWith('/approval')) return json(null)
  if (url.pathname.endsWith('/messages')) return json({ items: [{ run_id: 'preview-run', message: 'Show previews', response: reply, attachments: [], activity: [] }], next_cursor: null })
  if (url.pathname.endsWith('/memory-receipts')) return json({ pending: false, items: [], suggestion_ids: [] })
  return false
} })

for (const { width, height } of [{ width: 1440, height: 980 }, { width: 900, height: 520 }]) {
  // Each launch uses a fresh profile; the fake backend has no user files or credentials.
  // 每次使用全新测试配置，伪后端不接触用户文件或凭据。
  const testBackend = await previewBackend()
  await runSmoke({ backend: testBackend, viewport: { width, height }, screenshot: path.join(output, `previews-${width}.png`), verify: async function () {
    const wait = async fn => { for (let n = 0; n < 120; n++) { if (fn()) return; await new Promise(resolve => setTimeout(resolve, 40)) } throw Error('Preview UI did not load') }
    // Use the actual settings control so OS theme changes reach mounted previews.
    // 使用实际设置控件，让系统主题变化传递给已挂载的预览。
    location.hash = '#settings'
    await wait(() => [...document.querySelectorAll('button')].some(b => /^(System|跟随系统)$/.test(b.textContent.trim())))
    ;[...document.querySelectorAll('button')].find(b => /^(System|跟随系统)$/.test(b.textContent.trim())).click()
    document.querySelector('.sidebar-session').click()
    await wait(() => document.querySelectorAll('.reply-demo').length === 2)
    return { previews: 2 }
  }, inspect: async ({ evaluate, command }) => {
    const flat = node => [node.frame, ...(node.childFrames ?? []).flatMap(flat)]
    const attached = new Map()
    let svg, html
    for (let attempt = 0; attempt < 60 && !(svg && html); attempt++) {
      const { targetInfos } = await command('Target.getTargets')
      for (const target of targetInfos.filter(t => t.type === 'iframe')) {
        if (!attached.has(target.targetId)) attached.set(target.targetId, (await command('Target.attachToTarget', { targetId: target.targetId, flatten: true })).sessionId)
        const sessionId = attached.get(target.targetId)
        const tree = await command('Page.getFrameTree', {}, sessionId)
        for (const frame of flat(tree.frameTree)) {
          const { executionContextId } = await command('Page.createIsolatedWorld', { frameId: frame.id, worldName: 'preview-regression' }, sessionId)
          const found = await command('Runtime.evaluate', { contextId: executionContextId,
            expression: '({svg:!!document.querySelector("svg#preview-svg"),html:!!document.querySelector("#preview-html")})', returnByValue: true }, sessionId)
          if (found.result.value?.svg) svg = { executionContextId, sessionId }
          if (found.result.value?.html) html = { executionContextId, sessionId }
        }
      }
      if (!(svg && html)) await new Promise(resolve => setTimeout(resolve, 50))
    }
    assert.ok(svg && html, 'Expected both opaque-origin inner documents: ' + JSON.stringify({ tree: await command('Page.getFrameTree'), targets: await command('Target.getTargets') }))
    const inside = async ({ executionContextId: contextId, sessionId }, expression) => {
      const result = await command('Runtime.evaluate', { contextId, expression, awaitPromise: true, returnByValue: true }, sessionId)
      assert.ok(!result.exceptionDetails, JSON.stringify(result.exceptionDetails))
      return result.result.value
    }
    const themes = []
    for (const mode of ['dark', 'light', 'dark']) {
      await command('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: mode }] })
      await new Promise(resolve => setTimeout(resolve, 180))
      const colors = await inside(svg, `(()=>{
        const ctx=document.createElement('canvas').getContext('2d',{willReadFrequently:true});
        const rgba=c=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=c;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data]};
        const css=id=>getComputedStyle(document.getElementById(id));
        const root=getComputedStyle(document.documentElement);
        return {mode:root.colorScheme,surface:rgba(root.getPropertyValue('--preview-surface')),text:rgba(root.getPropertyValue('--preview-text')),
          white:rgba(css('white').fill),modern:rgba(css('modern').fill),gradientA:rgba(css('stop-a').stopColor),gradientB:rgba(css('stop-b').stopColor),
          accent:rgba(css('accent').fill),label:rgba(css('label').fill),empty:css('empty').fill,mask:rgba(css('mask-fill').fill)};
      })()`)
      assert.equal(colors.mode, mode, 'Existing SVG must update on an OS theme change')
      for (const key of ['white', 'modern', 'gradientA', 'gradientB']) assert.deepEqual(colors[key], colors.surface, `${key} did not follow the ${mode} surface`)
      assert.deepEqual(colors.label, colors.text)
      assert.deepEqual(colors.accent, [217, 64, 64, 255], 'Semantic colors must survive theme conversion')
      assert.equal(colors.empty, 'none')
      assert.deepEqual(colors.mask, [255, 255, 255, 255], 'Mask luminance must remain unchanged')
      themes.push({ mode, colors })
    }
    const heights = await evaluate(`(async()=>{const samples=[];for(let n=0;n<25;n++){samples.push(document.querySelector('.reply-html').getBoundingClientRect().height);await new Promise(r=>setTimeout(r,50))}return samples})()`)
    assert.ok(heights.every(height => height >= 240 && height <= 560), 'HTML viewport must stay within the preset range: ' + heights)
    assert.ok(Math.abs(heights[0] - Math.min(560, Math.max(240, height * .6))) < 1, 'Preview must follow the window height')
    assert.ok(Math.max(...heights) - Math.min(...heights) < 1, 'Animation changed the message height')
    const scrolling = await inside(html, `(()=>{document.getElementById('animated').style.animation='none';document.getElementById('animated').style.height='1800px';const root=document.scrollingElement;root.scrollTop=100000;const button=document.querySelector('button');button.click();return {height:root.clientHeight,scrollHeight:root.scrollHeight,scrollTop:root.scrollTop,interactive:button.textContent==='INTERACTION_OK'}})()`)
    assert.ok(scrolling.scrollHeight > scrolling.height && scrolling.scrollTop > 0, 'Tall content must remain scrollable inside the preview')
    assert.ok(scrolling.interactive, 'Sandbox interactions stopped working')
    const isolation = await evaluate(`(()=>{const frame=document.querySelector('.reply-html'),before=frame.getBoundingClientRect().height;window.postMessage({kind:'reply-preview-size',id:frame.dataset.previewId,height:99999},'*');return new Promise(resolve=>setTimeout(()=>resolve({before,after:frame.getBoundingClientRect().height,sandbox:frame.getAttribute('sandbox')}),80))})()`)
    assert.equal(isolation.before, isolation.after, 'A forged resize must be ignored')
    assert.equal(isolation.sandbox, 'allow-scripts')
    const diagnostic = { width, height, themes, heights, scrolling, isolation }
    await writeFile(path.join(output, `previews-${width}.json`), JSON.stringify(diagnostic, null, 2) + '\n')
    console.log('Reply preview checks:', JSON.stringify({ width, themes: themes.map(t => t.mode), height: heights[0], scrolling }))
    await evaluate("document.querySelector('.reply-svg').scrollIntoView({block:'end'})")
  } })
}
