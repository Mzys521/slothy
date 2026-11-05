<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { t } from './preferences'
import { brandAsset } from './brand'
const version = ref(__APP_VERSION__)
const isDesktop = !!window.desktop
onMounted(async () => { if (window.desktop) version.value = (await window.desktop.getAppInfo()).version })
</script>
<template>
  <main class="about-page">
    <h1 class="settings-content-title">{{ t('About slothy', '关于 slothy') }}</h1>
    <div class="about-identity"><img :src="brandAsset('mark.png')" alt="slothy"><h2>slothy</h2><p>{{ t('Your personal, adaptive agent.', '理解你，陪伴你成长的智能体。') }}</p><span>{{ t('Version', '版本') }} {{ version }}</span></div>
    <dl><div><dt>{{ t('Platform', '平台') }}</dt><dd>{{ isDesktop ? 'Windows · x64' : t('Web', '网页') }}</dd></div><div><dt>{{ t('Data', '数据') }}</dt><dd>{{ t('Stored locally on your device', '保存在本机用户目录') }}</dd></div><div><dt>{{ t('API keys', 'API 密钥') }}</dt><dd>{{ t('Encrypted with your Windows account', '使用 Windows 账户保护加密') }}</dd></div></dl>
  </main>
</template>
