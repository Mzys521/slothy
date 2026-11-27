import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { createServer } from 'vite'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'

let server, EnvironmentContext, LoadedTools
before(async () => {
  // The preferences module applies its saved theme when the view is loaded.
  globalThis.document = { documentElement: { dataset: {}, style: { setProperty() {} } } }
  server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)), configFile: false, plugins: [vue()],
    appType: 'custom', optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, hmr: false, watch: null },
  })
  EnvironmentContext = (await server.ssrLoadModule('/src/monitor/EnvironmentContext.vue')).default
  LoadedTools = (await server.ssrLoadModule('/src/monitor/LoadedTools.vue')).default
})
after(async () => {
  await server?.close()
  delete globalThis.document
})
const render = (component, props) => renderToString(createSSRApp(component, props))
const env = { workspace: 'Slothy', directory: '', status: '空闲', model: 'agent-model', memoryEnabled: true }
const catalog = { workMode: false, ready: true, loading: false }

test('workspace-only view hides session metadata and does not offer to copy an absent path', async () => {
  const html = await render(EnvironmentContext, env)
  assert.match(html, /Slothy/)
  assert.doesNotMatch(html, /复制工作目录|定制记忆|agent-model/)
})

test('work session preserves approval and profile version zero, chat hides work-only fields', async () => {
  const props = { ...env, directory: 'D:/slothy', session: { title: '文档整理', mode: 'work' },
    turn: { profileVersion: 0, work: { approval: 'every' } } }
  const work = await render(EnvironmentContext, props)
  for (const value of ['D:/slothy', '文档整理', '空闲', '逐项批准', 'agent-model', '开启', '画像版本', '>0<'])
    assert.ok(work.includes(value), value)
  const chat = await render(EnvironmentContext, { ...props, session: { title: '', mode: 'chat' }, memoryEnabled: false, turn: {} })
  assert.match(chat, /新建会话/)
  assert.match(chat, /关闭/)
  assert.doesNotMatch(chat, /工具审批|画像版本/)
})

test('renders each mode-visible tool once and uses only reported classifications', async () => {
  const html = await render(LoadedTools, { ...catalog, names: ['fetch_x', 'fetch_x', 'external'], groups: { network: ['fetch_x', 'work_only'] } })
  assert.equal((html.match(/<li\b/g) ?? []).length, 2)
  assert.match(html, /网络/)
  assert.match(html, /未分类/)
  assert.doesNotMatch(html, /work_only/)
  const legacy = await render(LoadedTools, { ...catalog, names: ['legacy'] })
  assert.match(legacy, /legacy/)
  assert.match(legacy, /未分类/)
  const unready = await render(LoadedTools, { ...catalog, ready: false, names: ['listed_tool'] })
  assert.match(unready, /listed_tool/)
  assert.match(unready, /暂不可用/)
})

test('loading, unready and empty tool catalogs each render a distinct status card', async () => {
  for (const [state, label] of [[{ loading: true, ready: false }, '正在读取工具'], [{ ready: false }, '暂不可用'], [{}, '当前未装载工具']]) {
    const html = await render(LoadedTools, { ...catalog, ...state, names: [] })
    assert.ok(html.includes(label))
    assert.match(html, /role="status"/)
    assert.doesNotMatch(html, /<li\b/)
  }
})
