import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
import { nextTick } from 'vue'

const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText).toString('base64')
const colorsUrl = moduleUrl(readFileSync(new URL('../src/appearance.ts', import.meta.url), 'utf8'))
const { DEFAULT_ACCENT, normalizeAccent, savedPalette, customAccentTokens } = await import(colorsUrl)
let instance = 0
async function preferences(saved) {
  const storage = new Map(saved ? [['adaptive.preferences', JSON.stringify(saved)]] : [])
  globalThis.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) }
  const tokens = new Map()
  const root = { dataset: {}, style: { setProperty: (key, value) => tokens.set(key, value) } }
  globalThis.document = { documentElement: root }
  let source = readFileSync(new URL('../src/preferences.ts', import.meta.url), 'utf8')
  source = source.replace("'vue'", JSON.stringify(import.meta.resolve('vue'))).replace("'./appearance'", JSON.stringify(colorsUrl))
  const store = await import(moduleUrl(source + '\n// instance ' + instance++))
  return { ...store, root, tokens, storage }
}

test('a clean installation uses #60DD07 and exposes every saved preset without overriding user choices', async () => {
  const fresh = await preferences()
  assert.equal(fresh.settings.palette, 'green')
  assert.equal(fresh.settings.customAccent, '#60DD07')
  assert.equal(fresh.root.dataset.palette, 'green')
  for (const palette of ['copper', 'neutral', 'slate', 'green', 'custom']) assert.equal(savedPalette(palette), palette)
  const migrated = await preferences({ appearanceVersion: 2, palette: 'copper', mode: 'light', font: 'serif', language: 'en' })
  assert.equal(migrated.settings.palette, 'copper')
  assert.equal(migrated.settings.mode, 'light')
  assert.equal(migrated.settings.font, 'serif')
  assert.equal(migrated.settings.language, 'en')
})

test('custom accents survive storage reload and language changes update the root language', async () => {
  const state = await preferences()
  state.settings.palette = 'custom'
  state.settings.customAccent = '#A85423'
  state.settings.language = 'en'
  await nextTick()
  assert.equal(state.tokens.get('--custom-accent'), '#A85423')
  assert.equal(state.root.lang, 'en')
  const restored = await preferences(JSON.parse(state.storage.get('adaptive.preferences')))
  assert.equal(restored.settings.palette, 'custom')
  assert.equal(restored.settings.customAccent, '#A85423')
})

test('bad saved values fall back to green and CSS values accept only hexadecimal colors', async () => {
  for (const value of ['url(https://example.test)', '#12', '#12345678', '#12345Z', 'red;--ink:red', null]) {
    assert.equal(normalizeAccent(value), undefined)
    assert.equal(customAccentTokens(value)['--custom-accent'], DEFAULT_ACCENT)
  }
  assert.equal(normalizeAccent(' #abc '), '#AABBCC')
  const state = await preferences({ palette: 'retired', customAccent: 'invalid' })
  assert.equal(state.settings.palette, 'green')
  assert.equal(state.settings.customAccent, '#60DD07')
})

function rgb(hex) { return [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16)) }
function luminance(channels) {
  const linear = channels.map(channel => channel / 255 <= .04045 ? channel / 255 / 12.92 : ((channel / 255 + .055) / 1.055) ** 2.4)
  return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]
}
function contrast(a, b) { const first = luminance(a), second = luminance(b); return (Math.max(first, second) + .05) / (Math.min(first, second) + .05) }
test('extreme and saturated custom accents keep normal text above AA on both theme surfaces and accent washes', () => {
  for (const accent of ['#FFFFFF', '#000000', '#60DD07', '#FF0000', '#0000FF', '#FFFF00', '#808080', '#01FEFE', '#F001F0']) {
    const tokens = customAccentTokens(accent)
    for (const [mode, surfaces] of [['light', ['#FFFFFF', '#F7F6F2', '#EEEDE9']], ['dark', ['#121212', '#1A1A1A', '#242424']]]) {
      const ink = rgb(tokens[`--custom-accent-${mode}-ink`])
      for (const surface of surfaces) {
        const background = rgb(surface)
        const wash = background.map((channel, index) => Math.round(channel * .86 + rgb(accent)[index] * .14))
        assert.ok(contrast(ink, background) >= 4.5, `${accent} ${mode} surface`)
        assert.ok(contrast(ink, wash) >= 4.5, `${accent} ${mode} wash`)
      }
    }
  }
})
