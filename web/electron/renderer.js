// Injected by Vite before the existing application entry.
// Keep the Vue API client unchanged when the production page loads from disk.
if (window.desktop?.apiOrigin) {
  const nativeFetch = window.fetch.bind(window)
  const apiOrigin = window.desktop.apiOrigin

  window.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), document.baseURI)
    const apiPath = url.pathname.replace(/^\/[a-zA-Z]:(?=\/api\/v1\/)/, '')
    if (url.origin === location.origin && apiPath.startsWith('/api/v1/')) {
      const target = new URL(apiPath + url.search, apiOrigin)
      return nativeFetch(input instanceof Request ? new Request(target, input) : target, init)
    }
    return nativeFetch(input, init)
  }
}
