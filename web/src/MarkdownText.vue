<script lang="ts">
import MarkdownIt from 'markdown-it'
import { replyPreview, previewTheme } from './replyPreview'
const markdown = new MarkdownIt({ html: false, breaks: true })
// Chat output must not fetch remote images. Keep their alt text visible.
// 聊天输出不得加载远程图片，改为保留其替代文本。
markdown.renderer.rules.image = (tokens, index) => markdown.utils.escapeHtml(tokens[index]?.content ?? '')

interface PreviewLabels {
  diagram: string
  demo: string
}

const previewLimit = 200_000
const svgStart = /^<svg(?=[\s/>])/

/**
 * Sanitize an inline SVG fence and turn it into a sandboxed preview.
 * 清洗内联 SVG 代码块，并转换为沙箱预览。
 *
 * @param source - Raw SVG markup from a fenced code block. / 围栏代码块中的原始 SVG 标记。
 * @param label - Fallback accessible title for the preview. / 预览的后备无障碍标题。
 * @returns Serialized preview HTML, or an empty string when the SVG is unsafe or invalid. / 序列化后的预览 HTML；SVG 不安全或无效时返回空字符串。
 */
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

/**
 * Wrap raw HTML demo markup as a sandboxed preview when it is small enough.
 * 在体积允许时，将原始 HTML 演示包装为沙箱预览。
 *
 * @param source - Raw HTML from a fenced code block. / 围栏代码块中的原始 HTML。
 * @param label - Accessible title for the preview. / 预览的无障碍标题。
 * @returns Preview HTML, or an empty string when empty or oversized. / 预览 HTML；为空或过大时返回空字符串。
 */
function htmlDemo(source: string, label: string) {
  if (!source.trim() || source.length > previewLimit) return ''
  return replyPreview(source, label, 'html')
}

const renderFence = markdown.renderer.rules.fence!
// Route fenced SVG/HTML blocks to sandboxed previews when the reply enables previews.
// 当回复启用预览时，将围栏 SVG/HTML 代码块交给沙箱预览渲染。
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
// 同时识别围栏代码块之外的独立 SVG，且不放开原始 HTML。
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
/** Push the current theme into every rendered preview iframe. / 将当前主题同步到所有已渲染的预览 iframe。 */
function syncTheme() {
  for (const frame of container.value?.querySelectorAll<HTMLIFrameElement>('iframe[data-preview-id]') ?? []) {
    const send = () => frame.contentWindow?.postMessage({ kind: 'preview-theme', id: frame.dataset.previewId, theme: previewTheme() }, '*')
    frame.onload = send
    send()
  }
}
/**
 * Resize a preview iframe to the height reported by its sandboxed document.
 * 根据沙箱文档上报的高度调整预览 iframe。
 *
 * @param event - Message event sent by a preview iframe. / 预览 iframe 发送的消息事件。
 */
function resize(event: MessageEvent) {
  if (event.data?.kind !== 'reply-preview-size' || !Number.isFinite(event.data.height)) return
  for (const frame of container.value?.querySelectorAll<HTMLIFrameElement>('iframe[data-preview-id]') ?? []) {
    if (event.source === frame.contentWindow && event.data.id === frame.dataset.previewId) {
      frame.style.height = Math.max(1, Math.min(100000, event.data.height)) + 'px'
    }
  }
}
// Re-sync preview themes after a re-render or a theme, font or mode change.
// 重新渲染，或主题、字体、模式变化后，重新同步预览主题。
watch([rendered, () => settings.mode, () => settings.palette, () => settings.font], () => { void nextTick(syncTheme) })
// Preview iframes report their height and need theme updates while this component is mounted.
// 预览 iframe 会回报高度，且在本组件挂载期间需要主题更新。
onMounted(() => { window.addEventListener('message', resize); media.addEventListener('change', syncTheme); syncTheme() })
onUnmounted(() => { window.removeEventListener('message', resize); media.removeEventListener('change', syncTheme) })
</script>

<template>
  <div ref="container" class="markdown-text" v-html="rendered"></div>
</template>
