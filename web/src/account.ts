import { computed, ref } from 'vue'
import { uploadAccountAvatar, workRequest } from './workApi'

export interface AccountProfile {
  display_name: string | null
  handle: string | null
  avatar_version: string | null
}

export const accountProfile = ref<AccountProfile>()
export const avatarUrl = computed(() => accountProfile.value?.avatar_version
  ? '/api/v1/account/avatar?v=' + encodeURIComponent(accountProfile.value.avatar_version)
  : undefined)

let accountRequest: Promise<void> | undefined

export function loadAccount() {
  accountRequest ??= workRequest<AccountProfile>('/account').then(value => {
    accountProfile.value = value
  }).finally(() => { accountRequest = undefined })
  return accountRequest
}

export async function saveAccount(displayName: string | null, handle: string | null) {
  accountProfile.value = await workRequest<AccountProfile>('/account', 'PUT', {
    display_name: displayName, handle,
  })
}

export async function setAccountAvatar(image: Blob | null) {
  accountProfile.value = image
    ? await uploadAccountAvatar<AccountProfile>(image)
    : await workRequest<AccountProfile>('/account/avatar', 'DELETE')
}
