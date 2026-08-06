import request from '../utils/request'

/**
 * 鱼小铺多规格商品发布。
 * 仅用于鱼小铺账号 + 多规格商品。
 * @param {Object} data - { xianyuAccountId, title, description, imageUrls, itemProperties, itemSkuList, shippingMode, supportSelfPick, postFee, location, category }
 */
export const publishFishShopItem = data => request.post('/fish-shop/publish', data)

/**
 * 鱼小铺多规格商品编辑。
 * 必须携带 itemId，且商品必须归属当前账号。
 * @param {Object} data - { xianyuAccountId, itemId, title, description, imageUrls, itemProperties, itemSkuList, ... }
 */
export const editFishShopItem = data => request.post('/fish-shop/edit', data)

/**
 * 获取鱼小铺商品完整详情，用于编辑回显。
 * 优先返回本地编辑快照，其次本地 SKU/规格表，最后本地 xianyu_goods 简略字段。
 * @param {Object} data - { xianyuAccountId, itemId }
 */
export const getFishShopDetail = data => request.post('/fish-shop/detail', data)
