/**
 * Brand asset URL helper shared by every UI surface.
 *
 * 品牌资源 URL 助手，供所有界面复用。
 */

/**
 * Resolves a brand image URL against the Vite base URL.
 *
 * 基于 Vite base URL 解析品牌图片地址。
 *
 * @param filename - File name inside `public/brand`, for example `mark.png`. / `public/brand` 内的文件名，例如 `mark.png`。
 * @returns Absolute URL usable in `src` attributes. / 可直接用于 `src` 的绝对 URL。
 */
export const brandAsset = (filename: string) => import.meta.env.BASE_URL + 'brand/' + filename
