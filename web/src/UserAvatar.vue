<script setup lang="ts">
import { ref } from 'vue'
import { UserRound } from 'lucide-vue-next'
import { avatarUrl } from './account'

const broken = ref<string>()
</script>

<template>
  <img v-if="avatarUrl && broken !== avatarUrl" :src="avatarUrl" class="avatar account-avatar"
    alt="" @error="broken = avatarUrl">
  <UserRound v-else class="avatar account-avatar" aria-hidden="true" />
</template>
