<script setup lang="ts">
// Sidebar brand: theme-aware wordmark with a compact mark fallback for narrow layouts.
// 侧边栏品牌：随主题切换的文字标识，窄布局下回退为紧凑图标。
import { brandAsset } from './brand'
</script>

<template>
  <span class="brand-wordmark">
    <img class="wordmark-image wordmark-dark" :src="brandAsset('wordmark-dark.png')" alt="slothy">
    <img class="wordmark-image wordmark-light" :src="brandAsset('wordmark-light.png')" alt="slothy">
    <img class="wordmark-compact" :src="brandAsset('mark.png')" alt="slothy">
  </span>
</template>
