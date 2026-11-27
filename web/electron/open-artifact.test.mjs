import assert from 'node:assert/strict'
import { mkdtemp, realpath, rmdir, unlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { openArtifact } from './open-artifact.cjs'

const origin = 'http://127.0.0.1:3000'
const artifactId = 'a'.repeat(64)

async function exampleFile(t, name) {
  const directory = await mkdtemp(path.join(tmpdir(), 'slothy-file-test-'))
  const file = path.join(directory, name)
  await writeFile(file, 'example file')
  t.after(async () => { await unlink(file); await rmdir(directory) })
  return realpath(file)
}

test('opens the actual registered file fetched by its thread and artifact IDs', async t => {
  const file = await exampleFile(t, 'notes.md')
  const shell = { openPath: async target => { assert.equal(target, file); return '' } }
  const result = await openArtifact(shell, origin, 'test-token', 'current-thread', artifactId, {
    request: async (url, options) => {
      assert.equal(url.href, `${origin}/api/v1/threads/current-thread/artifacts/${artifactId}/location`)
      assert.equal(options.headers['X-Slothy-Token'], 'test-token')
      assert.equal(options.redirect, 'error')
      return Response.json({ path: file })
    },
  })
  assert.equal(result, 'opened')
})

test('script files use the text editor or the file manager without using their shell association', async t => {
  const file = await exampleFile(t, 'script.js')
  const shell = {
    openPath: () => assert.fail('should not invoke the script association'),
    showItemInFolder: target => assert.equal(target, file),
  }
  const request = async () => Response.json({ path: file })
  const openText = async target => { assert.equal(target, file); return true }
  assert.equal(await openArtifact(shell, origin, '', 'thread', artifactId, { request, openText }), 'opened')
  assert.equal(await openArtifact(shell, origin, '', 'thread', artifactId, {
    request, openText: async () => false,
  }), 'revealed')
})

test('invalid inputs and unavailable artifacts never reach the shell', async () => {
  const shell = { openPath: () => assert.fail('should not open a path') }
  const request = () => assert.fail('should never fetch an unsafe request')
  await assert.rejects(openArtifact(shell, origin, '', '../other', artifactId, { request }), /file identity/)
  await assert.rejects(openArtifact(shell, 'http://example.com', '', 'thread', artifactId, { request }), /local backend/)
  assert.equal(await openArtifact(shell, origin, '', 'thread', artifactId, {
    request: async () => new Response('', { status: 404 }),
  }), 'missing')
  await assert.rejects(openArtifact(shell, origin, '', 'thread', artifactId, {
    request: async () => Response.json({ path: 'https://example.com/notes.md' }),
  }), /file location/)
})
