<script setup lang="ts">
import { brandAsset } from './brand'
</script>

<template>
  <span class="brand-wordmark">
    <img class="wordmark-image wordmark-dark" :src="brandAsset('wordmark-dark.png')" alt="slothy">
    <img class="wordmark-image wordmark-light" :src="brandAsset('wordmark-light.png')" alt="slothy">
    <img class="wordmark-compact" :src="brandAsset('mark.png')" alt="slothy">
  </span>
</template>
