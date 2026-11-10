/** Actual review UI, including legacy payloads. / 实际工具审查界面与旧载荷回归。 */
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createSmokeBackend, runSmoke } from './smoke.mjs'

const output = path.resolve(fileURLToPath(new URL('..', import.meta.url)), '../_internal/core-qa/tool-review')
await mkdir(output, { recursive: true })
const write = { name: 'workspace_write', risk: 'write', irreversible: false, arguments: { action: 'write_file', path: 'report.txt', content: 'new report', overwrite: false } }
const remove = { name: 'workspace_delete', risk: 'delete', irreversible: true, arguments: { path: 'obsolete.txt' } }
const cases = [
  { id: 'write', calls: [write], high: false },
  { id: 'delete', calls: [remove], high: true },
  { id: 'legacy-delete', calls: [{ name: 'workspace_delete', risk: 'write', arguments: { path: 'old-review.txt' } }], high: true },
  { id: 'recursive-delete', calls: [{ ...remove, arguments: { path: 'obsolete-folder', recursive: true } }], high: true },
  { id: 'overwrite', calls: [{ ...write, irreversible: true, arguments: { ...write.arguments, overwrite: true } }], high: true },
  { id: 'mixed', calls: [write, remove], high: true },
  { id: 'declared-irreversible', calls: [{ name: 'external_action', risk: 'execute', irreversible: true, arguments: { target: 'review-fixture' } }], high: true },
]

for (const theme of ['dark', 'light']) {
  const decisions = [], dismissed = new Set()
  const backend = await createSmokeBackend({ handle: async (request, response) => {
    const url = new URL(request.url, 'http://localhost')
    const json = body => { response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(body)); return true }
    if (url.pathname === '/api/v1/desktop-smoke/reviews') return json({ theme, cases })
    if (url.pathname === '/api/v1/desktop-smoke/decisions') return json(decisions)
    if (url.pathname === '/api/v1/threads') return json({ items: url.searchParams.get('archived') === 'true' ? [] : cases.map(item => ({
      thread_id: item.id, title: item.id, mode: 'work', workspace_id: 'personal', archived: false,
    })), next_offset: null })
    const item = cases.find(item => url.pathname.startsWith('/api/v1/threads/' + item.id + '/'))
    if (!item) return false
    if (url.pathname.endsWith('/messages')) return json({ items: [], next_cursor: null })
    if (url.pathname.endsWith('/approval')) return json(dismissed.has(item.id) ? null : {
      approval_id: 'review-' + item.id, thread_id: item.id, run_id: 'run-' + item.id, message: 'REVIEW ' + item.id,
      calls: item.calls, used_memory_ids: [], activity: item.calls.map((call, index) => ({
        id: 'tool:' + index, kind: 'tool', status: 'approval', name: call.name, risk: call.risk, arguments: JSON.stringify(call.arguments),
      })),
    })
    if (url.pathname.includes('/approval/') && request.method === 'POST') {
      let body = ''; for await (const chunk of request) body += chunk
      decisions.push({ id: item.id, ...JSON.parse(body) }); dismissed.add(item.id)
      response.writeHead(200, { 'Content-Type': 'text/event-stream' })
      response.end('event: done\ndata: ' + JSON.stringify({ run_id: 'run-' + item.id, thread_id: item.id, response: 'REVIEW_FINISHED', used_memory_ids: [], activity: [] }) + '\n\n')
      return true
    }
    return false
  } })
  await runSmoke({ backend, viewport: { width: 1440, height: 1080 }, screenshot: path.join(output, `review-${theme}.png`), verify: async function () {
    const check = (ok, message) => { if (!ok) throw Error(message) }
    const wait = async fn => { for (let n = 0; n < 150; n++) { if (await fn()) return; await new Promise(resolve => setTimeout(resolve, 30)) } throw Error('Review UI did not reach expected state') }
    const { theme, cases } = await (await fetch('/api/v1/desktop-smoke/reviews')).json()
    location.hash = '#settings'
    const themeLabel = theme === 'dark' ? '深色' : '浅色'
    await wait(() => [...document.querySelectorAll('button')].some(b => b.textContent.trim() === themeLabel))
    ;[...document.querySelectorAll('button')].find(b => b.textContent.trim() === themeLabel).click()
    const canvas = document.createElement('canvas').getContext('2d', { willReadFrequently: true })
    const color = value => { canvas.clearRect(0, 0, 1, 1); canvas.fillStyle = value; canvas.fillRect(0, 0, 1, 1); return [...canvas.getImageData(0, 0, 1, 1).data] }
    const select = async item => {
      const button = [...document.querySelectorAll('.sidebar-session')].find(b => b.querySelector('.sidebar-session-title')?.textContent.trim() === item.id)
      check(button, 'Missing review session ' + item.id); button.click()
      await wait(() => document.querySelector('.chat-turn')?.textContent.includes('REVIEW ' + item.id) && document.querySelectorAll('.tool-review li').length === item.calls.length)
      await new Promise(resolve => requestAnimationFrame(resolve))
    }
    const measurements = []
    for (const item of cases) {
      await select(item)
      const panel = document.querySelector('.tool-review'), approve = panel.querySelector('.tool-review-approve')
      check(panel.classList.contains('tool-review-danger') === item.high, item.id + ': high-risk styling mismatch')
      check(Boolean(panel.querySelector('.tool-review-warning')) === item.high, item.id + ': warning mismatch')
      const rows = [...panel.querySelectorAll('.tool-review-risk')]
      for (let index = 0; index < item.calls.length; index++) {
        const call = item.calls[index], deletion = call.name === 'workspace_delete' || call.risk === 'delete'
        if (deletion) {
          check(rows[index].textContent.includes('删除') && !rows[index].textContent.includes('写入'), item.id + ': delete mislabeled as write')
          check(rows[index].textContent.includes('高危') && rows[index].textContent.includes('不可逆'), item.id + ': missing deletion warning')
        }
        if (call.risk === 'write' && !deletion) check(rows[index].textContent.includes('写入'), 'Write label changed')
      }
      const probe = document.createElement('span'); probe.style.color = 'var(--danger)'; document.body.append(probe)
      const css = getComputedStyle(panel), danger = color(getComputedStyle(probe).color)
      probe.remove()
      const row = { id: item.id, high: item.high, background: color(css.backgroundColor), border: color(css.borderTopColor), approve: color(getComputedStyle(approve).backgroundColor) }
      if (item.high) {
        check(panel.getAttribute('role') === 'alertdialog', 'High risk review should be announced')
        check(panel.contains(document.activeElement) && document.activeElement !== approve, 'Initial focus must not approve')
        check(JSON.stringify(color(getComputedStyle(document.activeElement).outlineColor)) === JSON.stringify(danger), 'High-risk focus ring must use the danger color')
        check(approve.textContent.includes('高危'), 'Approval must name the high-risk operation')
        check(row.background[0] > row.background[1] + 5 && row.background[0] > row.background[2] + 5, 'Panel is not red-tinted')
        check(JSON.stringify(row.border) === JSON.stringify(danger), 'Panel border is not the danger color')
        check(JSON.stringify(row.approve) === JSON.stringify(danger), 'Approval button is not red')
      } else {
        check(panel.getAttribute('role') === 'region', 'Normal writes should keep the standard panel')
        check(!panel.textContent.includes('高危'), 'Normal write mislabeled as high risk')
      }
      if (item.calls.some(call => call.name === 'workspace_delete')) {
        check([...document.querySelectorAll('.trace-tool .tool-line')].some(row => row.textContent.includes('删除') && !row.textContent.includes('写入')), 'Activity trace still says Write for Delete')
      }
      measurements.push(row)
    }
    check((await (await fetch('/api/v1/desktop-smoke/decisions')).json()).length === 0, 'Review navigation must not execute tools')
    await select(cases.find(item => item.id === 'delete'))
    document.querySelector('.tool-review-actions .outline-button').click()
    await wait(() => !document.querySelector('.tool-review'))
    const decisions = await (await fetch('/api/v1/desktop-smoke/decisions')).json()
    check(decisions.length === 1 && decisions[0].approved === false, 'Reject must send exactly one negative decision')
    await select(cases.find(item => item.id === 'recursive-delete'))
    window.toolReviewVerification = { theme, measurements, rejected: true, noImplicitApproval: true }
    return window.toolReviewVerification
  }, inspect: async ({ evaluate }) => {
    const measurements = await evaluate(`(()=>{const p=document.querySelector('.tool-review');return {...window.toolReviewVerification,text:p.textContent,background:getComputedStyle(p).backgroundColor,border:getComputedStyle(p).borderTopColor}})()`)
    assert.ok(measurements.text.includes('高危') && measurements.text.includes('删除'))
    await writeFile(path.join(output, `review-${theme}.json`), JSON.stringify(measurements, null, 2) + '\n')
    await evaluate("document.querySelector('.tool-review').scrollIntoView({block:'center'})")
  } })
}
