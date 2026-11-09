/** Model selection layout regression on real components. / 真实组件的模型选择布局回归。 */
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createSmokeBackend, runSmoke } from './smoke.mjs'

const web = fileURLToPath(new URL('..', import.meta.url))
const output = path.resolve(web, '../_internal/core-qa/context-cache')
await mkdir(output, { recursive: true })
for (const theme of ['dark', 'light']) for (const width of [1440, 900]) {
  const backend = await createSmokeBackend({ handle: async (request, response) => {
    const json = body => { response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(body)); return true }
    if (request.url === '/api/v1/models') return json({ default: 'deepseek/deepseek-flash', items: [
      { id: 'deepseek/deepseek-flash', display_name: 'deepseek-flash', provider: 'deepseek', reasoning_levels: ['none', 'high'], default_effort: 'high' },
      { id: 'deepseek/long-model', display_name: 'deepseek-model-with-a-very-long-display-name', provider: 'deepseek', reasoning_levels: ['none', 'high'], default_effort: 'high' },
    ] })
    if (request.url === '/api/v1/desktop-smoke/layout') return json({ theme, width })
    if (request.url === '/api/v1/desktop-smoke/layout-result') {
      let raw = ''; for await (const chunk of request) raw += chunk
      await writeFile(path.join(output, `model-picker-${theme}-${width}.json`), raw + '\n')
      return json({ ok: true })
    }
    if (request.url === '/api/v1/threads' && request.method === 'POST') return json({ thread_id: 'layout', mode: 'chat', title: '' })
    return false
  } })
  await runSmoke({ backend, viewport: { width, height: 980 }, screenshot: path.join(output, `model-picker-${theme}-${width}.png`), verify: async function () {
    const check = (value, message) => { if (!value) throw Error(message) }
    const until = async condition => {
      for (let i = 0; i < 120; i++) { if (condition()) return; await new Promise(resolve => setTimeout(resolve, 40)) }
      throw Error('UI did not reach the expected state')
    }
    const { theme, width } = await (await fetch('/api/v1/desktop-smoke/layout')).json()
    location.hash = '#settings'
    const themeLabel = theme === 'light' ? '浅色' : '深色'
    await until(() => [...document.querySelectorAll('button')].some(b => b.textContent.trim() === themeLabel))
    ;[...document.querySelectorAll('button')].find(b => b.textContent.trim() === themeLabel).click()
    location.hash = '#'
    await until(() => document.querySelector('.model-trigger'))
    async function choose(label) {
      document.querySelector('.model-trigger').click()
      await until(() => document.querySelector('.model-popup'))
      document.querySelector('.effort-model')?.click()
      await until(() => [...document.querySelectorAll('.model-row')].some(b => b.textContent.includes(label)))
      ;[...document.querySelectorAll('.model-row')].find(b => b.textContent.includes(label)).click()
      document.querySelector('.model-picker').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      await until(() => !document.querySelector('.model-popup'))
    }
    const measurements = []
    function measure(state) {
      const trigger = document.querySelector('.model-trigger')
      const picker = trigger.closest('.model-picker')
      const send = document.querySelector('.composer .voice-button')
      const rect = trigger.getBoundingClientRect(), button = send.getBoundingClientRect()
      const difference = Math.abs(rect.y + rect.height / 2 - button.y - button.height / 2)
      check(difference < 1, state + ': model and send button are not vertically centered: ' + difference)
      check(parseFloat(getComputedStyle(picker).marginBottom) === 0, 'Provider settings leaked bottom margin into the composer')
      check(rect.right < button.left && rect.left >= 0 && button.right <= innerWidth, state + ': controls overlap or overflow')
      measurements.push({ state, centerDifference: difference, pickerMarginBottom: getComputedStyle(picker).marginBottom })
    }
    await choose('deepseek-flash')
    measure('chat')
    const field = document.querySelector('.composer textarea')
    field.value = 'Line one\nLine two\nLine three'
    field.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise(resolve => requestAnimationFrame(resolve))
    measure('multiline')
    const data = new DataTransfer(); data.items.add(new File(['fixture'], 'fixture.txt'))
    const input = document.querySelector('.composer input[type="file"]')
    input.files = data.files; input.dispatchEvent(new Event('change', { bubbles: true }))
    await until(() => document.querySelector('.composer-with-files'))
    measure('attachment')
    document.querySelector('.attachment-list button').click()
    await until(() => !document.querySelector('.attachment-list'))
    const modes = [...document.querySelectorAll('.chat-modes button')]
    modes.find(b => /Work|工作/.test(b.textContent))?.click()
    await until(() => document.querySelector('.work-mode'))
    measure('work')
    await choose('deepseek-model-with-a-very-long-display-name')
    measure('long-name')
    modes.find(b => /Chat|聊天/.test(b.textContent))?.click()
    await until(() => !document.querySelector('.work-mode'))
    await choose('deepseek-flash')
    field.value = ''; field.dispatchEvent(new Event('input', { bubbles: true }))
    const diagnostic = { theme, width, measurements }
    await fetch('/api/v1/desktop-smoke/layout-result', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(diagnostic) })
    return diagnostic
  } })
}
