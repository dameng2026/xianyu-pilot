import request from '../utils/request.js'

// 远程滑块求解配置
export const getRemoteSliderConfig = () => request.get('/system/remote-slider-config')
export const saveRemoteSliderConfig = data => request.post('/system/remote-slider-config', data)

// 远程滑块求解预检验
export const precheckRemoteSlider = data => request.post('/system/remote-slider-precheck', data)

// 远程滑块求解记录
export const getRemoteSolveRecords = (params = {}) =>
  request.get('/captcha/remote-solve-records', { params })

// 远程滑块求解统计
export const getRemoteSolveStats = (params = {}) =>
  request.get('/captcha/remote-solve-stats', { params })