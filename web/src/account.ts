/**
 * Local account state: profile fields, avatar URL and the account API calls.
 *
 * 本地账户状态：资料字段、头像 URL 以及账户相关 API 调用。
 */

import { computed, ref } from 'vue'
import { uploadAccountAvatar, workRequest } from './workApi'

/** Account shape returned by `GET /account`. / `GET /account` 返回的账户结构。 */
export interface AccountProfile {
  display_name: string | null
  handle: string | null
  avatar_version: string | null
}

export const accountProfile = ref<AccountProfile>()
// Cache-busting query keeps a replaced avatar from showing the stale image.
// 通过版本查询参数避免替换后的头像仍显示旧图。
export const avatarUrl = computed(() => accountProfile.value?.avatar_version
  ? '/api/v1/account/avatar?v=' + encodeURIComponent(accountProfile.value.avatar_version)
  : undefined)

let accountRequest: Promise<void> | undefined

/**
 * Loads the local account once and shares the in-flight request.
 *
 * 加载本地账户，并复用进行中的请求。
 *
 * @returns Shared promise resolving after the profile is stored. / 共享 Promise，资料写入后完成。
 */
export function loadAccount() {
  accountRequest ??= workRequest<AccountProfile>('/account').then(value => {
    accountProfile.value = value
  }).finally(() => { accountRequest = undefined })
  return accountRequest
}

/**
 * Persists display name and handle, then refreshes the local profile.
 *
 * 保存显示名称与用户名，并刷新本地资料。
 *
 * @param displayName - Display name, or `null` to clear it. / 显示名称；传 `null` 表示清空。
 * @param handle - Handle without the leading `@`, or `null` to clear it. / 不含前缀 `@` 的用户名；传 `null` 表示清空。
 */
export async function saveAccount(displayName: string | null, handle: string | null) {
  accountProfile.value = await workRequest<AccountProfile>('/account', 'PUT', {
    display_name: displayName, handle,
  })
}

/**
 * Uploads a processed PNG avatar, or deletes it when given `null`.
 *
 * 上传已处理好的 PNG 头像；传入 `null` 时删除头像。
 *
 * @param image - Square PNG blob already resized to 256px, or `null` to remove. / 已缩放为 256px 的正方形 PNG；传 `null` 表示删除。
 */
export async function setAccountAvatar(image: Blob | null) {
  accountProfile.value = image
    ? await uploadAccountAvatar<AccountProfile>(image)
    : await workRequest<AccountProfile>('/account/avatar', 'DELETE')
}
