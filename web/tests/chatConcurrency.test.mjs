import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const moduleUrl = code => 'data:text/javascript;base64,' + Buffer.from(code).toString('base64')
const compiled = (file, replacements = {}) => {
  let code = ts.transpileModule(readFileSync(new URL('../src/' + file, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText
  for (const [from, to] of Object.entries({ "'vue'": JSON.stringify(import.meta.resolve('vue')), ...replacements })) code = code.replaceAll(from, to)
  return moduleUrl(code)
}
const runUrl = compiled('sessionRuns.ts')
const runs = await import(runUrl)
const guardUrl = compiled('readGuard.ts')
const { ReadGuard } = await import(guardUrl)
const workApi = await import(compiled('workApi.ts'))
const preferences = moduleUrl('export const readLocal=(key,fallback)=>fallback;export const writeLocal=()=>{};export const t=(en)=>en;')
const api = moduleUrl('export class ApiError extends Error{};export const threadPath=id=>"/threads/"+id;export const workRequest=(...args)=>globalThis.chatConcurrencyRequest(...args);')
const chat = await import(compiled('chat.ts', { "'./sessionRuns'": JSON.stringify(runUrl), "'./preferences'": JSON.stringify(preferences), "'./workApi'": JSON.stringify(api), "'./readGuard'": JSON.stringify(guardUrl) }))

test('a stale thread-list snapshot cannot remove a now-server-backed running session', async () => {
  const complete = []
  globalThis.chatConcurrencyRequest = () => new Promise(resolve => complete.push(resolve))
  chat.sessions.value = [{ id: 'local-a', title: 'A', mode: 'chat', turns: [], draft: false }]
  const target = chat.sessions.value[0]
  const controller = runs.beginSessionRequest(target.id, 'turn-a')
  const refreshing = chat.syncThreads()
  target.apiThreadId = 'server-a'
  for (const resolve of complete) resolve({ items: [], next_offset: null })
  await refreshing
  assert.equal(chat.sessions.value.length, 1)
  assert.equal(chat.sessions.value[0], target)
  assert.equal(runs.runningRequests.get(target.id), controller)
  runs.finishSessionRequest(target.id, controller)
})

test('a current remote index keeps the same live session and turn objects', async () => {
  globalThis.chatConcurrencyRequest = path => Promise.resolve({ items: path.includes('archived=false')
    ? [{ thread_id: 'server-a', title: 'Renamed A', mode: 'chat', archived: false }] : [], next_offset: null })
  const target = chat.sessions.value[0]
  target.turns.push({ id: 'turn-a', prompt: 'A', files: [], time: '' })
  const turn = target.turns[0]
  const controller = runs.beginSessionRequest(target.id, turn.id)
  await chat.syncThreads()
  assert.equal(chat.sessions.value[0], target)
  assert.equal(chat.sessions.value[0].turns[0], turn)
  assert.equal(target.title, 'Renamed A')
  runs.finishSessionRequest(target.id, controller)
})

test('a new session finishing before a stale index returns is still retained', async () => {
  const complete = []
  globalThis.chatConcurrencyRequest = () => new Promise(resolve => complete.push(resolve))
  const refreshing = chat.syncThreads()
  chat.sessions.value.push({ id: 'local-b', title: 'B', mode: 'chat', turns: [] })
  const target = chat.sessions.value.at(-1)
  const controller = runs.beginSessionRequest(target.id, 'turn-b')
  target.apiThreadId = 'server-b'
  target.turns.push({ id: 'turn-b', prompt: 'B', files: [], time: '', serverReply: 'complete' })
  runs.finishSessionRequest(target.id, controller)
  for (const resolve of complete) resolve({ items: [], next_offset: null })
  await refreshing
  assert.ok(chat.sessions.value.includes(target))
})

test('a history read that finishes after a new run starts cannot replace its live turns', async () => {
  let complete
  globalThis.chatConcurrencyRequest = () => new Promise(resolve => { complete = resolve })
  const target = chat.sessions.value.find(item => item.apiThreadId === 'server-b'), turn = target.turns[0]
  const refreshing = chat.syncThreadMessages(target)
  const controller = runs.beginSessionRequest(target.id, turn.id)
  complete({ items: [], next_cursor: null })
  await refreshing
  assert.equal(target.turns[0], turn)
  runs.finishSessionRequest(target.id, controller)
})

test('a checkpoint restores an unfinished request with no local chat cache', async () => {
  const pending = { run_id: 'saved-run', checkpoint_id: 'saved-checkpoint', message: 'original request', status: 'interrupted',
    model_selection: { id: 'original-model', effort: 'high' }, attachments: [{ id: 'file', filename: 'original.txt' }],
    references: [], personalization_enabled: false, activity: [] }
  globalThis.chatConcurrencyRequest = () => Promise.resolve({ items: [], next_cursor: null, pending_run: pending })
  chat.sessions.value = [{ id: 'saved-session', apiThreadId: 'server', title: 'Saved', mode: 'work', turns: [] }]
  const target = chat.sessions.value[0]
  await chat.syncThreadMessages(target)
  assert.equal(target.turns.length, 1)
  const turn = target.turns[0]
  assert.equal(turn.serverRunId, 'saved-run')
  assert.equal(turn.checkpointId, 'saved-checkpoint')
  assert.equal(turn.prompt, 'original request')
  assert.equal(turn.model, 'original-model')
  assert.deepEqual(turn.files, ['original.txt'])
  assert.equal(turn.memory, false)
  assert.ok(turn.serverError.includes('checkpoint'))
})

test('an archived completed run clears a lost acknowledgement error and stale checkpoint', async () => {
  const target = chat.sessions.value[0]
  globalThis.chatConcurrencyRequest = () => Promise.resolve({ items: [{ run_id: 'saved-run', message: 'original request', response: 'completed', attachments: [] }], next_cursor: null, pending_run: null })
  await chat.syncThreadMessages(target)
  assert.equal(target.turns.length, 1)
  assert.equal(target.turns[0].serverReply, 'completed')
  assert.equal(target.turns[0].serverError, undefined)
  assert.equal(target.turns[0].checkpointId, undefined)
  assert.equal(target.pendingRun, null)
})

// F12: an index snapshot is invalidated by every local index mutation.
// F12：每个本地索引变更都会作废更早的索引快照。
test('an index snapshot taken before an archive cannot restore the old archived flag', async () => {
  const complete = []
  globalThis.chatConcurrencyRequest = (path, method) => method === 'PATCH'
    ? Promise.resolve({ thread_id: 'server-a', title: 'A', mode: 'chat', archived: true })
    : new Promise(resolve => complete.push(resolve))
  chat.sessions.value = [{ id: 'server-a', apiThreadId: 'server-a', title: 'A', mode: 'chat', turns: [], archived: false }]
  const target = chat.sessions.value[0]
  const refreshing = chat.syncThreads()
  await chat.archiveSession('server-a', true)
  assert.equal(target.archived, true)
  for (const resolve of complete) resolve({ items: [], next_offset: null })
  await refreshing
  assert.equal(chat.sessions.value[0], target)
  assert.equal(target.archived, true)
})

test('an index snapshot taken before a deletion cannot resurrect the session', async () => {
  const complete = []
  globalThis.chatConcurrencyRequest = (path, method) => method === 'DELETE'
    ? Promise.resolve(undefined)
    : new Promise(resolve => complete.push(resolve))
  chat.sessions.value = [{ id: 'server-a', apiThreadId: 'server-a', title: 'A', mode: 'chat', turns: [] }]
  const refreshing = chat.syncThreads()
  await chat.deleteSession('server-a')
  assert.equal(chat.sessions.value.length, 0)
  for (const resolve of complete) resolve({ items: [{ thread_id: 'server-a', title: 'A', mode: 'chat', archived: false }], next_offset: null })
  await refreshing
  assert.equal(chat.sessions.value.length, 0)
})

test('an index snapshot taken before a fork keeps the new session', async () => {
  const complete = []
  globalThis.chatConcurrencyRequest = (path, method) => method === 'POST'
    ? Promise.resolve({ thread_id: 'server-fork', title: 'Fork', mode: 'chat', archived: false, created_at: '2026-01-01T00:00:00Z' })
    : new Promise(resolve => complete.push(resolve))
  chat.sessions.value = [{ id: 'server-a', apiThreadId: 'server-a', title: 'A', mode: 'chat', turns: [] }]
  const source = chat.sessions.value[0]
  const refreshing = chat.syncThreads()
  await chat.forkSession(source, 'Fork')
  const fork = chat.sessions.value.find(item => item.id === 'server-fork')
  assert.ok(fork)
  for (const resolve of complete) resolve({ items: [{ thread_id: 'server-a', title: 'A', mode: 'chat', archived: false }], next_offset: null })
  await refreshing
  assert.ok(chat.sessions.value.includes(fork))
})

// F12: a history read is only allowed to write under its own session data version.
// F12：历史读取只能在自己捕获的会话数据版本下写入。
test('a history read that spans a complete run cannot replace its live turns', async () => {
  let complete
  globalThis.chatConcurrencyRequest = () => new Promise(resolve => { complete = resolve })
  const turn = { id: 'turn-live', prompt: 'live', files: [], time: '' }
  const target = { id: 'session-live', apiThreadId: 'server-live', title: 'Live', mode: 'chat', turns: [turn] }
  chat.sessions.value = [target]
  const reading = chat.syncThreadMessages(target)
  const controller = runs.beginSessionRequest(target.id, turn.id)
  runs.finishSessionRequest(target.id, controller)
  complete({ items: [{ run_id: 'other-run', message: 'server', response: 'reply', attachments: [] }], next_cursor: null })
  await reading
  assert.equal(target.turns.length, 1)
  assert.equal(target.turns[0], turn)
})

test('a history read that started before a clear cannot refill the turns', async () => {
  let complete
  globalThis.chatConcurrencyRequest = (path, method) => method === 'DELETE'
    ? Promise.resolve(undefined)
    : new Promise(resolve => { complete = resolve })
  const target = { id: 'session-clear', apiThreadId: 'server-clear', title: 'Clear', mode: 'chat',
    turns: [{ id: 'old-turn', prompt: 'old', files: [], time: '' }] }
  chat.sessions.value = [target]
  const reading = chat.syncThreadMessages(target)
  await chat.clearSession(target.id)
  complete({ items: [{ run_id: 'old-run', message: 'old', response: 'reply', attachments: [] }], next_cursor: null, pending_run: null })
  await reading
  assert.equal(target.turns.length, 0)
})

test('two history reads of one session apply only the newer response', async () => {
  const pending = []
  globalThis.chatConcurrencyRequest = () => new Promise(resolve => pending.push(resolve))
  const target = { id: 'session-order', apiThreadId: 'server-order', title: 'Order', mode: 'chat', turns: [] }
  chat.sessions.value = [target]
  const first = chat.syncThreadMessages(target)
  const second = chat.syncThreadMessages(target)
  assert.equal(pending.length, 2)
  pending[1]({ items: [{ run_id: 'newer', message: 'n', response: 'N', attachments: [] }], next_cursor: null })
  await second
  pending[0]({ items: [{ run_id: 'older', message: 'o', response: 'O', attachments: [] }], next_cursor: null })
  await first
  assert.deepEqual(target.turns.map(turn => turn.serverRunId), ['newer'])
})

test('a pending run is matched by run id and never by matching text', async () => {
  const pending = { run_id: 'run-9', checkpoint_id: 'checkpoint-9', message: 'identical text', status: 'interrupted',
    attachments: [], references: [], model_selection: {}, personalization_enabled: false, activity: [] }
  globalThis.chatConcurrencyRequest = () => Promise.resolve({ items: [], next_cursor: null, pending_run: pending })
  const local = { id: 'local-turn', clientRequestId: 'local-request', prompt: 'identical text', files: [], time: '' }
  const target = { id: 'session-pending', apiThreadId: 'server-pending', title: 'Pending', mode: 'chat', turns: [local] }
  chat.sessions.value = [target]
  await chat.syncThreadMessages(target)
  assert.equal(target.turns.length, 2)
  assert.equal(target.turns[0], local)
  assert.equal(local.serverRunId, undefined)
  assert.equal(target.turns[1].id, 'run-9')
  assert.equal(target.turns[1].serverRunId, 'run-9')
})

// F02: a projected client_request_id maps a lost-acknowledgement run back to its local turn.
// F02：服务端投影的 client_request_id 把回执丢失的运行映射回其本地轮次。
test('an archived run maps back to its local turn by client request id', async () => {
  const local = { id: 'local-turn', clientRequestId: 'local-request', prompt: 'B', files: [], time: '',
    serverError: 'stream ended before a reply' }
  const target = { id: 'session-lost', apiThreadId: 'server-lost', title: 'Lost', mode: 'chat', turns: [local] }
  chat.sessions.value = [target]
  globalThis.chatConcurrencyRequest = () => Promise.resolve({ items: [{ run_id: 'run-lost',
    client_request_id: 'local-request', message: 'B', response: 'done', attachments: [] }],
    next_cursor: null, pending_run: null })
  await chat.syncThreadMessages(target)
  assert.equal(target.turns.length, 1)
  assert.notEqual(target.turns[0], local)
  assert.equal(target.turns[0].id, 'local-turn')
  assert.equal(target.turns[0].clientRequestId, 'local-request')
  assert.equal(target.turns[0].serverRunId, 'run-lost')
  assert.equal(target.turns[0].serverReply, 'done')
  assert.equal(target.turns[0].serverError, undefined)
})

test('a pending run maps back to its local turn by client request id', async () => {
  const pending = { run_id: 'run-pending', checkpoint_id: 'checkpoint-pending', message: 'B',
    client_request_id: 'local-request', status: 'interrupted', attachments: [], references: [],
    model_selection: {}, personalization_enabled: false, activity: [] }
  globalThis.chatConcurrencyRequest = () => Promise.resolve({ items: [], next_cursor: null, pending_run: pending })
  const local = { id: 'local-turn', clientRequestId: 'local-request', prompt: 'B', files: [], time: '' }
  const target = { id: 'session-pending-id', apiThreadId: 'server-pending-id', title: 'Pending', mode: 'chat', turns: [local] }
  chat.sessions.value = [target]
  await chat.syncThreadMessages(target)
  assert.equal(target.turns.length, 1)
  assert.equal(target.turns[0], local)
  assert.equal(target.turns[0].serverRunId, 'run-pending')
  assert.equal(target.turns[0].checkpointId, 'checkpoint-pending')
  assert.equal(target.turns[0].clientRequestId, 'local-request')
})

test('an archived turn without a projected request id never matches by text', async () => {
  const local = { id: 'local-turn', clientRequestId: 'local-request', prompt: 'same text', files: [], time: '',
    serverError: 'failed before a run' }
  const target = { id: 'session-null', apiThreadId: 'server-null', title: 'Null', mode: 'chat', turns: [local] }
  chat.sessions.value = [target]
  globalThis.chatConcurrencyRequest = () => Promise.resolve({ items: [{ run_id: 'run-older',
    client_request_id: null, message: 'same text', response: 'older', attachments: [] }],
    next_cursor: null, pending_run: null })
  await chat.syncThreadMessages(target)
  assert.equal(target.turns.length, 2)
  assert.equal(target.turns[0].serverRunId, 'run-older')
  assert.equal(target.turns[1].id, 'local-turn')
})

// F02: retry identity is decided by the saved run id, never by matching text.
// F02：重试身份由已保存的运行标识决定，绝不按文本匹配。
test('retry identity resumes only the failing turn own pending run', () => {
  const pending = { run_id: 'run-b', checkpoint_id: 'checkpoint-b', message: 'B', status: 'interrupted',
    attachments: [], references: [], model_selection: {}, personalization_enabled: false, activity: [] }
  const own = { id: 'turn-b', prompt: 'B', files: [], time: '', serverRunId: 'run-b', serverError: 'lost receipt' }
  const session = { id: 's', title: 'S', turns: [own], pendingRun: pending }
  assert.deepEqual(chat.retryPlan(session, own), { action: 'resume', runId: 'run-b', checkpointId: 'checkpoint-b' })
})

test('retry identity resends a pre-submit failure with the same request id', () => {
  const failed = { id: 'turn-b', clientRequestId: 'turn-b', prompt: 'B', files: ['report.pdf'], time: '',
    serverError: 'REFERENCE_INVALID' }
  const session = { id: 's', title: 'S', turns: [failed], pendingRun: null }
  assert.deepEqual(chat.retryPlan(session, failed), { action: 'resend', clientRequestId: 'turn-b' })
  const restored = { id: 'run-1', prompt: 'B', files: [], time: '', serverError: 'MODEL_UNAVAILABLE' }
  assert.deepEqual(chat.retryPlan(session, restored), { action: 'resend', clientRequestId: 'run-1' })
})

test('retry identity never resumes a pending run that belongs to another turn', () => {
  const pending = { run_id: 'run-a', checkpoint_id: 'checkpoint-a', message: 'same text', status: 'interrupted',
    attachments: [], references: [], model_selection: {}, personalization_enabled: false, activity: [] }
  const failed = { id: 'turn-b', prompt: 'same text', files: [], time: '', serverRunId: 'run-b', serverError: 'failed' }
  const session = { id: 's', title: 'S', turns: [failed], pendingRun: pending }
  assert.equal(chat.retryPlan(session, failed).action, 'resend')
})

test('a completed turn without an error becomes a new logical request', () => {
  const done = { id: 'turn-a', clientRequestId: 'turn-a', prompt: 'A', files: [], time: '', serverRunId: 'run-a', serverReply: 'ok' }
  const session = { id: 's', title: 'S', turns: [done], pendingRun: null }
  assert.deepEqual(chat.retryPlan(session, done), { action: 'new' })
})

test('an approval-pending run or a missing turn blocks a retry', () => {
  const pending = { run_id: 'run-a', checkpoint_id: 'checkpoint-a', message: 'A', status: 'awaiting_approval',
    attachments: [], references: [], model_selection: {}, personalization_enabled: false, activity: [] }
  const session = { id: 's', title: 'S', turns: [], pendingRun: pending }
  assert.deepEqual(chat.retryPlan(session, { id: 'turn-a', prompt: 'A', files: [], time: '', serverRunId: 'run-a' }),
    { action: 'blocked', reason: 'awaiting_approval' })
  assert.deepEqual(chat.retryPlan(session, undefined), { action: 'blocked', reason: 'no_request' })
})

// F12: an approval read publishes only while it still owns the session and its data version.
// F12：审批读取只有在仍拥有会话且数据版本未变时才会提交。
test('an approval read that started before a run cannot publish after it', () => {
  const reviews = new ReadGuard()
  const session = { id: 'review-session' }
  // Mirrors ChatPage.refreshReview: both the read token and the session version must hold.
  // 对应 ChatPage.refreshReview：读取令牌与会话版本都必须仍然有效。
  const version = runs.sessionVersion(session.id)
  const stale = reviews.begin(session.id)
  const publishStale = () => reviews.owns(stale, session.id) && runs.sessionVersion(session.id) === version
  const controller = runs.beginSessionRequest(session.id, 'turn')
  runs.finishSessionRequest(session.id, controller)
  assert.equal(publishStale(), false)
  const newer = reviews.begin(session.id)
  const stable = runs.sessionVersion(session.id)
  assert.equal(reviews.owns(newer, session.id) && runs.sessionVersion(session.id) === stable, true)
})

// F05: one upload identity per file, derived from the turn, name, size and modification time.
// F05：每个文件一个上传标识，由轮次、文件名、大小与修改时间派生。
test('upload request ids are stable per file and fit the request limit', () => {
  const turn = { id: 'turn-upload', clientRequestId: 'turn-upload', prompt: '', files: [], time: '' }
  const file = { name: 'report.pdf', size: 1024, lastModified: 1700000000000 }
  const first = chat.fileRequestId(turn, file, 0)
  assert.equal(chat.fileRequestId(turn, file, 0), first)
  // A retry rebuilds the same file from the restored draft and must reuse the identity.
  // 重试会从恢复的草稿重建同一文件，必须复用该标识。
  assert.equal(chat.fileRequestId(turn, { name: file.name, size: file.size, lastModified: file.lastModified }, 0), first)
  // Learning the run id later must not change the upload identity of the same attempt.
  // 之后获知运行标识不得改变同一次尝试的上传标识。
  assert.equal(chat.fileRequestId({ ...turn, serverRunId: 'run-1' }, file, 0), first)
  assert.notEqual(chat.fileRequestId(turn, file, 1), first)
  assert.notEqual(chat.fileRequestId({ ...turn, clientRequestId: 'other-turn' }, file, 0), first)
  assert.notEqual(chat.fileRequestId(turn, { ...file, size: 1025 }, 0), first)
  assert.notEqual(chat.fileRequestId(turn, { ...file, lastModified: 1 }, 0), first)
  assert.ok(first.length >= 1 && first.length <= 128)
  assert.ok(!first.includes('|') && !first.includes(' '))
})

test('every upload attempt sends the same file request id', async () => {
  const urls = []
  const original = globalThis.fetch
  globalThis.fetch = async url => {
    urls.push(String(url))
    return { status: 201, ok: true, json: async () => ({ id: 'file-1', filename: 'report.pdf', byte_size: 1024 }) }
  }
  try {
    const turn = { id: 'turn-upload', clientRequestId: 'turn-upload', prompt: '', files: [], time: '' }
    const file = { name: 'report.pdf', size: 1024, lastModified: 1700000000000 }
    const first = await workApi.uploadAttachment('thread-1', file, undefined, chat.fileRequestId(turn, file, 0))
    const second = await workApi.uploadAttachment('thread-1', { ...file }, undefined, chat.fileRequestId(turn, { ...file }, 0))
    assert.equal(first.id, 'file-1')
    assert.equal(second.id, 'file-1')
  } finally {
    globalThis.fetch = original
  }
  assert.equal(urls.length, 2)
  assert.ok(urls.every(url => url.startsWith('/api/v1/threads/thread-1/attachments?filename=report.pdf')))
  assert.ok(urls.every(url => url.includes('file_request_id=')))
  assert.equal(urls[0], urls[1])
})
