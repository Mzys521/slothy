<script lang="ts">
import MarkdownIt from 'markdown-it'
import { replyPreview, previewTheme } from './replyPreview'
const markdown = new MarkdownIt({ html: false, breaks: true })
// Chat output must not fetch remote images. Keep their alt text visible.
markdown.renderer.rules.image = (tokens, index) => markdown.utils.escapeHtml(tokens[index]?.content ?? '')

interface PreviewLabels {
  diagram: string
  demo: string
}

const previewLimit = 200_000
const svgStart = /^<svg(?=[\s/>])/

function svgImage(source: string, label: string) {
  let svg = source.trim().replace(/^<\?xml[^?]*\?>\s*/, '')
  if (svg.length > previewLimit || !svgStart.test(svg) || /<!DOCTYPE|<!ENTITY/i.test(svg)) return ''
  if (!/^<svg[^>]*\sxmlns\s*=/.test(svg)) {
    svg = svg.replace(svgStart, '<svg xmlns="http://www.w3.org/2000/svg"')
  }
  const root = new DOMParser().parseFromString(svg.replace(/[\uD800-\uDFFF]/gu, '\uFFFD'), 'image/svg+xml').documentElement
  if (root.localName !== 'svg' || root.namespaceURI !== 'http://www.w3.org/2000/svg' || root.querySelector('parsererror')) return ''
  const viewBox = root.getAttribute('viewBox')?.trim().split(/[\s,]+/).map(Number)
  const width = viewBox?.[2]
  const height = viewBox?.[3]
  if (width && height && width > 0 && height > 0 && Number.isFinite(width + height) &&
      !root.hasAttribute('width') && !root.hasAttribute('height')) {
    root.setAttribute('width', String(width))
    root.setAttribute('height', String(height))
  }
  const title = root.getElementsByTagName('title')[0]?.textContent?.trim() || label
  if (!root.hasAttribute('viewBox')) {
    const w = parseFloat(root.getAttribute('width') || '600')
    const h = parseFloat(root.getAttribute('height') || '400')
    if (w > 0 && h > 0) root.setAttribute('viewBox', `0 0 ${w} ${h}`)
  }
  return replyPreview(new XMLSerializer().serializeToString(root), title, 'svg')
}

function htmlDemo(source: string, label: string) {
  if (!source.trim() || source.length > previewLimit) return ''
  return replyPreview(source, label, 'html')
}

const renderFence = markdown.renderer.rules.fence!
markdown.renderer.rules.fence = (tokens, index, options, env, renderer) => {
  const token = tokens[index]!
  const previews = env?.previews as PreviewLabels | undefined
  if (previews) {
    const language = token.info.trim().split(/\s+/, 1)[0]?.toLowerCase()
    if (!language || ['svg', 'xml', 'html'].includes(language)) {
      const svg = svgImage(token.content, previews.diagram)
      if (svg) return svg
    }
    if (!svgStart.test(token.content.trim()) && language === 'html') {
      const demo = htmlDemo(token.content, previews.demo)
      if (demo) return demo
    }
  }
  return renderFence(tokens, index, options, env, renderer)
}

// Also recognize a standalone SVG outside a fenced code block, without enabling raw HTML.
markdown.block.ruler.before('html_block', 'reply_svg', (state, start, end, silent) => {
  const previews = state.env?.previews as PreviewLabels | undefined
  if (!previews || state.sCount[start]! - state.blkIndent >= 4) return false
  const first = state.bMarks[start]! + state.tShift[start]!
  if (!svgStart.test(state.src.slice(first, first + 5))) return false
  for (let line = start; line < end; line++) {
    const lineEnd = state.eMarks[line]!
    if (lineEnd - first > previewLimit) return false
    const lineText = state.src.slice(state.bMarks[line], lineEnd)
    if (!/<\/svg\s*>\s*$/.test(lineText) && !(line === start && /^\s*<svg[^>]*\/>\s*$/.test(lineText))) continue
    const image = svgImage(state.src.slice(first, lineEnd), previews.diagram)
    if (!image) return false
    if (!silent) {
      state.push('reply_svg', '', 0).content = image
      state.line = line + 1
    }
    return true
  }
  return false
}, { alt: ['paragraph'] })
markdown.renderer.rules.reply_svg = (tokens, index) => tokens[index]!.content
</script>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch, nextTick } from 'vue'
import { settings, t } from './preferences'
const props = defineProps<{ text: string; previews?: boolean }>()
const rendered = computed(() => markdown.render(props.text, {
  previews: props.previews ? {
    diagram: t('SVG diagram', 'SVG 图示'),
    demo: t('Interactive demo', '交互演示'),
  } : undefined,
}))
const container = ref<HTMLElement>()
const media = matchMedia('(prefers-color-scheme: dark)')
function syncTheme() {
  for (const frame of container.value?.querySelectorAll<HTMLIFrameElement>('iframe[data-preview-id]') ?? []) {
    const send = () => frame.contentWindow?.postMessage({ kind: 'preview-theme', id: frame.dataset.previewId, theme: previewTheme() }, '*')
    frame.onload = send
    send()
  }
}
function resize(event: MessageEvent) {
  if (event.data?.kind !== 'reply-preview-size' || !Number.isFinite(event.data.height)) return
  for (const frame of container.value?.querySelectorAll<HTMLIFrameElement>('iframe[data-preview-id]') ?? []) {
    if (event.source === frame.contentWindow && event.data.id === frame.dataset.previewId) {
      frame.style.height = Math.max(1, Math.min(100000, event.data.height)) + 'px'
    }
  }
}
watch([rendered, () => settings.mode, () => settings.palette, () => settings.font], () => { void nextTick(syncTheme) })
onMounted(() => { window.addEventListener('message', resize); media.addEventListener('change', syncTheme); syncTheme() })
onUnmounted(() => { window.removeEventListener('message', resize); media.removeEventListener('change', syncTheme) })
</script>

<template>
  <div ref="container" class="markdown-text" v-html="rendered"></div>
</template>
