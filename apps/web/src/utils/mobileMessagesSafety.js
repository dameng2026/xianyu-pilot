function stableMessageKey(message) {
  const pnmId = String(message?.pnmId || message?.messageUid || message?.uuid || '').trim()
  if (pnmId) return pnmId
  const id = String(message?.id || '').trim()
  if (id) return id
  return ''
}

export function createLatestOnlyGuard() {
  let version = 0
  return {
    begin(identity = '') {
      return { version: ++version, identity: String(identity || '') }
    },
    invalidate() {
      version += 1
    },
    isCurrent(token, identity = token?.identity) {
      return Boolean(
        token
        && token.version === version
        && token.identity === String(identity || '')
      )
    },
  }
}

export function createSingleFlight(task) {
  let inFlight = null
  return function runSingleFlight(...args) {
    if (inFlight) return inFlight
    const execution = Promise.resolve(task(...args))
    let wrapped
    wrapped = execution.finally(() => {
      if (inFlight === wrapped) inFlight = null
    })
    inFlight = wrapped
    return wrapped
  }
}

export function mergeRemoteMessagesWithLocalAttempts(remoteMessages, currentMessages) {
  const merged = Array.isArray(remoteMessages) ? remoteMessages.map(item => ({ ...item })) : []
  const remoteIndex = new Map()
  merged.forEach((message, index) => {
    const key = stableMessageKey(message)
    if (key) remoteIndex.set(key, index)
  })

  for (const local of Array.isArray(currentMessages) ? currentMessages : []) {
    if (!local?.idempotencyKey || !local?.sendStatus) continue
    const key = stableMessageKey(local)
    const matchingIndex = key ? remoteIndex.get(key) : undefined
    if (matchingIndex !== undefined) {
      merged[matchingIndex] = {
        ...merged[matchingIndex],
        ...local,
      }
      continue
    }
    merged.push({ ...local })
  }

  return merged
}
