// 功能操作守卫（开源版最小实现）
// 开源版为单用户版本，无会员等级浏览/预览限制模式。
// 保留与商业版一致的 API 形态，供 FishShopEditPage 等页面复用，守卫恒放行。

/**
 * 功能操作守卫：正常模式返回 true。
 * @returns {Promise<boolean>}
 */
export async function guardFeatureAction() {
  return true
}

/** 通知用户等级不足（开源版无等级限制，no-op）。 */
export async function notifyLevelBlocked() {}

/** 通知用户预览模式限制（开源版无预览模式，no-op）。 */
export async function notifyPreviewBlocked() {}

/** 是否处于浏览模式。 */
export function isInBrowseMode() {
  return false
}

/** 是否处于预览模式。 */
export function isInPreviewMode() {
  return false
}
