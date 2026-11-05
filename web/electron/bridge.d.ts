export {}

declare global {
  const __APP_VERSION__: string
  interface Window {
    desktop?: Readonly<{
      platform: string
      apiOrigin: string
      getAppInfo(): Promise<{ name: string; version: string; platform: string }>
      minimize(): Promise<void>
      toggleMaximize(): Promise<boolean>
      isMaximized(): Promise<boolean>
      close(): Promise<void>
      selectDirectory(): Promise<string | null>
      onMaximized(listener: (value: boolean) => void): () => void
    }>
  }
}
