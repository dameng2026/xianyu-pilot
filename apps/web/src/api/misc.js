import request from '../utils/request.js'

export const addressDictTree = () => request.get('/address-dict/tree')
export const uploadImage = (accountId, file) => {
  const form = new FormData()
  form.append('accountId', String(accountId))
  form.append('file', file)
  return request.post('/image/upload', form)
}
export const uploadImageFromUrl = data => request.post('/image/uploadFromUrl', data)
export const detectCaptcha = data => request.post('/captcha/detect', data)
export const listMedia = data => request.post('/media/list', data || {})
export const deleteMedia = data => request.post('/media/delete', data)
export const queryOperationLogs = data => request.post('/operationLog/list', data || {})
export const runtimeLogFiles = data => request.post('/operationLog/runtime/files', data || {})
export const kamiTemplateUrl = () => '/api/excel/template/kami'
