/**
 * Renderer entry point: mounts the root Vue application.
 *
 * 渲染进程入口：挂载 Vue 根应用。
 */

import { createApp } from 'vue'
import '@fontsource-variable/geist'
import './style.css'
import './appearance.css'
import './onboarding.css'
import App from './App.vue'

// The single mount target declared by index.html.
// index.html 中声明的唯一挂载节点。
createApp(App).mount('#app')
