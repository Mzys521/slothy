import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
import { createRenderer, h } from 'vue'

function moduleUrl(file, imports = {}) {
  let source = readFileSync(new URL('../src/' + file, import.meta.url), 'utf8')
  for (const [name, url] of Object.entries(imports)) source = source.replaceAll("'" + name + "'", JSON.stringify(url))
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return 'data:text/javascript;base64,' + Buffer.from(output).toString('base64')
}
const dataModule = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64')
const vue = import.meta.resolve('vue')
const apiUrl = dataModule(`export class ApiError extends Error { constructor(message,status,code){super(message);this.status=status;this.code=code} } export function workRequest(...args){return globalThis[Symbol.for('slothy.onboarding-test')](...args)}`)
const preferenceUrl = dataModule('export const t = (en,zh) => en')
const formUrl = moduleUrl('providerForm.ts')
const storeUrl = moduleUrl('providerSettings.ts', { vue, './workApi': apiUrl })
const editorUrl = moduleUrl('useProviderEditor.ts', { vue, './preferences': preferenceUrl, './workApi': apiUrl, './providerSettings': storeUrl, './providerForm': formUrl })
const { providerIssue, providerPayload, developmentOnboarding } = await import(formUrl)
const store = await import(storeUrl)
const { useProviderEditor } = await import(editorUrl)
const { ApiError } = await import(apiUrl)
const deferred = () => { let resolve, reject; const promise = new Promise((yes,no)=>{resolve=yes;reject=no}); return {promise,resolve,reject} }
const transport = implementation => { globalThis[Symbol.for('slothy.onboarding-test')] = implementation }
const templates = [{ id:'template-provider', name:'Template Provider', protocol:'openai', base_url:'https://provider.test/v1', model_ids:['model-a','model-b'] }]
const snapshot = (required = true, items = []) => ({ setup_required: required, templates, items })
const draft = () => ({ id:'custom-provider', name:'Custom provider', template:'template-provider', protocol:'openai', base_url:'https://provider.test/v1', api_key:'LOCAL-TEST-KEY', enabled:true, models:[{id:'model-a',name:'Model A',context_window:131072}] })
const renderer = createRenderer({
  createElement: tag=>({tag}), createText: text=>({text}), createComment: text=>({text}),
  insert(){}, remove(){}, setText(){}, setElementText(){}, patchProp(){}, parentNode(){return null}, nextSibling(){return null},
})
function mountEditor() {
  let editor
  const app = renderer.createApp({setup(){editor=useProviderEditor();return()=>h('div')}})
  app.mount({})
  return { editor, close:()=>app.unmount() }
}

test('request fields keep the existing contract and never include an empty replacement key', () => {
  const value=draft()
  assert.equal(providerIssue(value,false),undefined)
  assert.equal(providerPayload(value,'builtin').template,'template-provider')
  value.api_key=''
  assert.equal(providerIssue(value,false),'key')
  assert.equal(providerIssue(value,true),undefined)
  assert.ok(!Object.hasOwn(providerPayload(value,'custom'),'api_key'))
  assert.equal(providerPayload(value,'custom').template,null)
})

test('URLs, models, context windows and provider IDs are validated before local API submission', () => {
  for(const url of ['ftp://provider.test','https://key@provider.test','https://provider.test?key=test','https://provider.test/#fragment','not a URL'])assert.equal(providerIssue({...draft(),base_url:url},false),'url')
  assert.equal(providerIssue({...draft(),models:[]},false),'models')
  assert.equal(providerIssue({...draft(),models:[]},false,false),undefined)
  assert.equal(providerIssue({...draft(),id:'Invalid ID'},false),'id')
  for(const count of [1023,10000001,1.5])assert.equal(providerIssue({...draft(),models:[{id:'model',context_window:count}]},false),'context')
  assert.equal(providerIssue({...draft(),models:[{id:'model'},{id:'model'}]},false),'duplicate')
})

test('forced onboarding is development-only and does not depend on browser persistence', () => {
  assert.equal(developmentOnboarding(true,'?onboarding=1'),true)
  assert.equal(developmentOnboarding(false,'?onboarding=1'),false)
  assert.equal(developmentOnboarding(true,'?onboarding=0'),false)
  assert.equal(developmentOnboarding(true,''),false)
})

test('load failure is not first-run state; retry and empty-provider refresh update the singleton', async () => {
  transport(async()=>snapshot(false,[{id:'configured'}]))
  await store.loadProviderSettings({refresh:true})
  assert.equal(store.setupRequired.value,false)
  transport(async()=>{throw new Error('Local API unavailable')})
  await assert.rejects(store.loadProviderSettings(),/unavailable/)
  assert.equal(store.configurationLoaded.value,false)
  assert.match(store.configurationError.value,/unavailable/)
  transport(async()=>snapshot())
  await store.loadProviderSettings()
  assert.equal(store.configurationLoaded.value,true)
  assert.equal(store.setupRequired.value,true)
  assert.equal(store.configurationError.value,'')
})

test('startup reads coalesce and a stale response cannot overwrite the post-save refresh', async () => {
  const older=deferred(), newer=deferred();let reads=0
  transport(()=>++reads===1?older.promise:newer.promise)
  const first=store.loadProviderSettings()
  assert.equal(store.loadProviderSettings(),first)
  const latest=store.loadProviderSettings({refresh:true})
  newer.resolve(snapshot(false));await latest
  older.resolve(snapshot(true));await first
  assert.equal(store.setupRequired.value,false)
  assert.equal(store.configurationLoaded.value,true)
})

test('template choice uses server data and save clears the key before the settings reload finishes', async () => {
  transport(async()=>snapshot());await store.loadProviderSettings({refresh:true})
  const {editor,close}=mountEditor()
  editor.openEditor()
  assert.equal(editor.form.template,templates[0].id)
  assert.deepEqual(editor.form.models.map(model=>model.id),templates[0].model_ids)
  editor.form.api_key='LOCAL-TEST-KEY'
  const reload=deferred();let calls=[]
  transport(async(path,method,body)=>{calls.push({path,method,body});if(method==='POST')return{};return reload.promise})
  const saving=editor.save()
  await new Promise(resolve=>setImmediate(resolve))
  assert.equal(editor.form.api_key,'')
  assert.equal(editor.showKey.value,false)
  assert.equal(editor.saving.value,true)
  assert.equal(calls[0].path,'/settings/providers')
  assert.equal(calls[0].body.api_key,'LOCAL-TEST-KEY')
  reload.resolve(snapshot(false));await saving
  assert.equal(store.setupRequired.value,false)
  close()
})

test('HTTP errors preserve entered fields and redact the key from visible error text', async () => {
  const {editor,close}=mountEditor();Object.assign(editor.form,draft())
  transport(async()=>{throw new ApiError('Rejected LOCAL-TEST-KEY',422,'PROVIDER_INVALID')})
  await editor.save()
  assert.equal(editor.form.api_key,'LOCAL-TEST-KEY')
  assert.equal(editor.form.base_url,draft().base_url)
  assert.equal(editor.form.models.length,1)
  assert.match(editor.error.value,/configuration/)
  assert.ok(!editor.error.value.includes('LOCAL-TEST-KEY'))
  close();assert.equal(editor.form.api_key,'')
})

test('conflict and gateway messages stay readable without dropping the form', async () => {
  for (const [status, expected] of [[409,/conflict/],[502,/connect/]]) {
    const {editor,close}=mountEditor();Object.assign(editor.form,draft())
    transport(async()=>{throw new ApiError('Backend detail',status)})
    await editor.save()
    assert.match(editor.error.value,expected)
    assert.match(editor.error.value,/Backend detail/)
    assert.equal(editor.form.api_key,'LOCAL-TEST-KEY')
    close()
  }
})

test('a successful POST followed by reload failure can retry with PUT and the stored masked key', async () => {
  const {editor,close}=mountEditor();Object.assign(editor.form,draft())
  let calls=[]
  transport(async(path,method,body)=>{calls.push({path,method,body});if(method==='POST')return{};throw new Error('Reload failed')})
  await editor.save()
  assert.equal(editor.form.api_key,'')
  assert.equal(editor.savedKey.value,true)
  transport(async(path,method,body)=>{calls.push({path,method,body});return method==='PUT'?{}:snapshot(false)})
  await editor.save()
  const update=calls.find(call=>call.method==='PUT')
  assert.equal(update.path,'/settings/providers/custom-provider')
  assert.ok(!Object.hasOwn(update.body,'api_key'))
  close()
})

test('discover failure allows manual model entry and does not block saving a compatible endpoint', async () => {
  const {editor,close}=mountEditor();Object.assign(editor.form,{...draft(),models:[]})
  transport(async()=>{throw new ApiError('No model-list endpoint',502)})
  await editor.discover()
  assert.equal(editor.form.api_key,'LOCAL-TEST-KEY')
  assert.equal(editor.lastOperation.value,'discover')
  Object.assign(editor.newModel,{id:'manual-model',name:'Manual',context:'131072'})
  assert.equal(editor.addModel(),true)
  assert.equal(editor.canSave.value,true)
  transport(async(path,method)=>method==='POST'?{}:snapshot(false))
  assert.ok(await editor.save())
  close()
})

test('discover merges real catalog items without duplicates and a busy editor cannot submit twice', async () => {
  const {editor,close}=mountEditor();Object.assign(editor.form,draft())
  transport(async()=>({items:[{id:'model-a'},{id:'model-c'}]}))
  await editor.discover()
  assert.deepEqual(editor.form.models.map(model=>model.id),['model-a','model-c'])
  const posted=deferred();let writes=0
  transport(async(path,method)=>{if(method==='POST'){writes++;return posted.promise}return snapshot(false)})
  const first=editor.save();await editor.save();assert.equal(writes,1)
  posted.resolve({});await first;close()
})
