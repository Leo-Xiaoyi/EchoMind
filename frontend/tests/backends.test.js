import test from 'node:test'
import assert from 'node:assert/strict'
import { chatAttemptKey, createInitialSettings, evaluationTaskKey, normalizeChatResponse } from '../src/lib/backends.js'

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

test('new visitors default to English and do not reopen a pre-locale conversation', () => {
  globalThis.localStorage = {
    getItem: () => JSON.stringify({ userId: 'u1', conversationId: 'old-chat' }),
    setItem() {}
  }
  const migrated = createInitialSettings()
  assert.equal(migrated.locale, 'en')
  assert.equal(migrated.conversationId, '')

  globalThis.localStorage = {
    getItem: () => JSON.stringify({ userId: 'u1', conversationId: 'chinese-chat', locale: 'zh-CN' }),
    setItem() {}
  }
  const chinese = createInitialSettings()
  assert.equal(chinese.locale, 'zh-CN')
  assert.equal(chinese.conversationId, 'chinese-chat')
})
