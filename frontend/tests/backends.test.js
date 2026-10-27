import test from 'node:test'
import assert from 'node:assert/strict'
import { chatAttemptKey, createInitialSettings, evaluationTaskKey, normalizeChatResponse, runEvaluation } from '../src/lib/backends.js'

const settings = {
  backend: 'python',
  userId: 'u1',
  conversationId: 'c1',
  locale: 'en',
  endpoints: { python: '/api/python' }
}

test('chat attempt key includes the selected locale', () => {
  const english = chatAttemptKey('python', settings, 'hello')
  const chinese = chatAttemptKey('python', { ...settings, locale: 'zh-CN' }, 'hello')
  assert.notEqual(english, chinese)
  assert.equal(english, chatAttemptKey('python', { ...settings }, 'hello'))
  assert.equal(english, chatAttemptKey('python', { ...settings, locale: undefined }, 'hello'))
})

test('evaluation task key is isolated by locale', () => {
  const english = evaluationTaskKey('python', settings)
  const chinese = evaluationTaskKey('python', { ...settings, locale: 'zh-CN' })
  assert.notEqual(english, chinese)
  assert.match(english, /:en$/)
  assert.match(chinese, /:zh-CN$/)
})

test('response locale falls back to the same default as the request payload', () => {
  const raw = { response: 'x', intent: 'other', agent_type: 'general', escalated: false }
  assert.equal(normalizeChatResponse('python', raw, {}).locale, 'en')
  assert.equal(normalizeChatResponse('python', raw, { locale: 'zh-CN' }).locale, 'zh-CN')
  assert.equal(normalizeChatResponse('python', { ...raw, locale: 'en' }, { locale: 'zh-CN' }).locale, 'en')
})

test('new visitors default to English; legacy Chinese settings retain their conversation', () => {
  globalThis.localStorage = { getItem: () => null, setItem() {} }
  const fresh = createInitialSettings()
  assert.equal(fresh.locale, 'en')
  assert.equal(fresh.conversationId, '')

  globalThis.localStorage = {
    getItem: () => JSON.stringify({ userId: 'u1', conversationId: 'old-chat' }),
    setItem() {}
  }
  const migrated = createInitialSettings()
  assert.equal(migrated.locale, 'zh-CN')
  assert.equal(migrated.conversationId, 'old-chat')

  globalThis.localStorage = {
    getItem: () => JSON.stringify({ userId: 'u1', conversationId: 'chinese-chat', locale: 'zh-CN' }),
    setItem() {}
  }
  const chinese = createInitialSettings()
  assert.equal(chinese.locale, 'zh-CN')
  assert.equal(chinese.conversationId, 'chinese-chat')
})

test('legacy Chinese evaluation job resumes under the locale-aware key', async () => {
  const saved = new Map()
  globalThis.sessionStorage = {
    getItem: key => saved.get(key) ?? null,
    setItem: (key, value) => saved.set(key, value),
    removeItem: key => saved.delete(key)
  }
  const chinese = { ...settings, locale: 'zh-CN' }
  const legacyKey = 'echomind.eval:python:/api/python:u1'
  saved.set(legacyKey, 'existing-job')
  globalThis.fetch = async url => {
    assert.match(url, /\/eval\/jobs\/existing-job$/)
    return { ok: true, text: async () => JSON.stringify({ job_id: 'existing-job', status: 'completed', result: { passed: 1 } }) }
  }
  const result = await runEvaluation('python', chinese, null, { interval: 0 })
  assert.deepEqual(result, { passed: 1 })
  assert.equal(saved.has(legacyKey), false)
  assert.equal(saved.has(evaluationTaskKey('python', chinese)), false)
})
