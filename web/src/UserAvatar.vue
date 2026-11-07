<script setup lang="ts">
import { ref } from 'vue'
import { UserRound } from 'lucide-vue-next'
import { avatarUrl } from './account'

/** URL whose image failed to load, so the fallback icon can be shown instead. / 加载失败的图片 URL，用于改为显示回退图标。 */
const broken = ref<string>()
</script>

<template>
  <img v-if="avatarUrl && broken !== avatarUrl" :src="avatarUrl" class="avatar account-avatar"
    alt="" @error="broken = avatarUrl">
  <UserRound v-else class="avatar account-avatar" aria-hidden="true" />
</template>
