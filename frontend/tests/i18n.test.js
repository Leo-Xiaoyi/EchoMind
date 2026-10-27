import test from 'node:test'
import assert from 'node:assert/strict'
import { dictionary, getLocale, setLocale, t, messageCountLabel } from '../src/lib/i18n.js'

test('English and Chinese dictionaries cover the same keys', () => {
  const en = Object.keys(dictionary.en).sort()
  const zh = Object.keys(dictionary['zh-CN']).sort()
  assert.deepEqual(en, zh)
  assert.ok(en.length > 100)
})

test('t interpolates variables and switches locale', () => {
  setLocale('en')
  assert.equal(t('chat.messageCountMany', { count: 3 }), '3 messages')
  assert.equal(t('knowledge.relevance', { score: 0.9 }), 'Relevance 0.9')
  setLocale('zh-CN')
  assert.equal(t('chat.messageCountMany', { count: 3 }), '3 条消息')
  assert.equal(t('knowledge.relevance', { score: 0.9 }), '相关度 0.9')
  assert.equal(getLocale(), 'zh-CN')
})

test('unsupported locale is ignored and missing keys fall back to the key', () => {
  setLocale('en')
  assert.equal(setLocale('fr'), false)
  assert.equal(getLocale(), 'en')
  assert.equal(t('missing.key'), 'missing.key')
})

test('message count uses the singular form only for one', () => {
  setLocale('en')
  assert.equal(messageCountLabel(1), '1 message')
  assert.equal(messageCountLabel(2), '2 messages')
})
