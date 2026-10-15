import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import os from 'node:os'
import path from 'node:path'

import type { Configuration } from 'app-builder-lib'
import { afterEach, test } from 'vitest'

import type { ChannelBuildRequest, InstallStamp } from './install-stamp'
import { applyDesktopIdentity, type ProductIdentity } from './product-identity'

const require: NodeJS.Require = createRequire(import.meta.url)
const desktop: string = path.resolve(import.meta.dirname, '..')
const repo: string = path.resolve(desktop, '../..')

function request(sequence: number = 65536, token: string = 'ab12cd34ef56ab78'): ChannelBuildRequest {
  return {
    schema: 1,
    buildId: 'a'.repeat(32),
    channel: 'no-registry-needed',
    sequence,
    repository: 'fixture/project',
    commit: 'b'.repeat(40),
    sourceVersion: '1.2.3',
    version: `0.0.${sequence}`,
    windowsVersion: `0.${Math.floor(sequence / 65536)}.${sequence % 65536}.0`,
    identity: {
      token,
      displayName: 'Hermes no-registry-needed',
      appId: `com.nousresearch.hermes-channel-${token}`,
      appNamePascal: `HermesChannel${token}`,
      artifactNamePascal: `HermesChannel${token}`,
      cliName: 'hermes-no-registry-needed',
      windowsExecutableName: 'hermes-no-registry-needed',
      msixAppIdWithOrg: `NousResearch.HermesChannel${token}`
    },
    bundleEnv: { HERMES_PREVIEW_FULL_CONNECTORS: '1' },
    publicBase: 'https://builds.example.test'
  }
}

interface PackagingFacts {
  identity: ProductIdentity
  config: Configuration
}
interface StampPayload {
  runtime: { repoDir: string; commands: { hermes: string } }
}

function load(build: ChannelBuildRequest): PackagingFacts {
  process.env.HERMES_DESKTOP_VARIANT = 'bundled'
  process.env._HERMES_CHANNEL_REQUEST_JSON = JSON.stringify(build)

  for (const file of ['../product-identity.cjs', '../electron-builder.config.cjs']) {
    delete require.cache[require.resolve(file)]
  }

  return { identity: require('../product-identity.cjs'), config: require('../electron-builder.config.cjs') }
}

afterEach((): void => {
  for (const key of [
    '_HERMES_CHANNEL_REQUEST_JSON',
    'HERMES_DESKTOP_VARIANT',
    'HERMES_BUILD_COMMIT',
    'HERMES_PAYLOAD_TAG'
  ]) {
    delete process.env[key]
  }

  for (const file of ['../product-identity.cjs', '../electron-builder.config.cjs']) {
    delete require.cache[require.resolve(file)]
  }
})

test('channel packaging reuses admitted identity and rejects unsupported or unsafe identities', (): void => {
  const first: ChannelBuildRequest = request()
  const a: ReturnType<typeof load> = load(first)

  const b: ReturnType<typeof load> = load({
    ...first,
    ...request(65537),
    commit: 'c'.repeat(40),
    sourceVersion: '0.1.0'
  })

  for (const field of [
    'displayName',
    'appId',
    'appNamePascal',
    'cliName',
    'windowsExecutableName',
    'msixAppIdWithOrg'
  ] as const) {
    assert.equal(a.identity[field], first.identity[field])
    assert.equal(a.identity[field], b.identity[field])
  }

  const other: ReturnType<typeof load> = load(request(1, '1234567890abcdef'))
  assert.notEqual(other.identity.appId, a.identity.appId)
  assert.equal(a.config.extraMetadata?.version, first.version)
  assert.equal(a.config.extraMetadata?.shortVersionWindows, first.windowsVersion)
  const appData: string = fs.mkdtempSync(path.join(os.tmpdir(), 'channel-runtime-identity-'))
  let userData: string = appData
  let displayName: string = ''

  try {
    assert.equal(
      applyDesktopIdentity(
        {
          getPath: (): string => appData,
          setPath: (_name: 'userData', value: string): void => {
            userData = value
          },
          setName: (value: string): void => {
            displayName = value
          }
        },
        a.identity
      ),
      first.identity.displayName
    )
    assert.equal(userData, path.join(appData, first.identity.appNamePascal))
    assert.equal(displayName, first.identity.displayName)
  } finally {
    fs.rmSync(appData, { recursive: true, force: true })
  }

  assert.deepEqual(a.config.mac?.publish, [
    {
      provider: 'generic',
      url: `${first.publicBase}/releases/channel-builds/${first.buildId}/darwin/`,
      channel: 'latest'
    }
  ])
  process.env.HERMES_DESKTOP_VARIANT = 'light'
  delete require.cache[require.resolve('../product-identity.cjs')]
  assert.throws((): void => {
    require('../product-identity.cjs')
  }, /bundled/)
  const invalid: ChannelBuildRequest = request()
  invalid.identity.cliName = 'con'
  assert.throws((): void => {
    load(invalid)
  }, /identity/)
  invalid.identity.cliName = first.identity.cliName
  invalid.identity.windowsExecutableName = 'trailing.'
  assert.throws((): void => {
    load(invalid)
  }, /identity/)
})

test('a channel that copies the stable identity packages and runs as the regular app', (): void => {
  process.env.HERMES_DESKTOP_VARIANT = 'bundled'

  for (const file of ['../product-identity.cjs', '../electron-builder.config.cjs']) {
    delete require.cache[require.resolve(file)]
  }

  const stable: PackagingFacts = {
    identity: require('../product-identity.cjs'),
    config: require('../electron-builder.config.cjs')
  }

  const { identity: official } = stable

  const branded: PackagingFacts = load({
    ...request(),
    identity: {
      token: 'ab12cd34ef56ab78',
      displayName: official.displayName,
      appId: official.appId,
      appNamePascal: official.appNamePascal,
      artifactNamePascal: official.artifactNamePascal,
      cliName: official.cliName,
      windowsExecutableName: official.windowsExecutableName,
      msixAppIdWithOrg: official.msixAppIdWithOrg
    }
  })

  // A token would make the runtime pin a userData dir that installed stable doesn't use.
  assert.equal(branded.identity.token, undefined)
  assert.equal(branded.identity.appId, official.appId)
  assert.equal(branded.config.extraMetadata?.productName, stable.config.extraMetadata?.productName)
  assert.equal(branded.config.extraMetadata?.name, stable.config.extraMetadata?.name)
  assert.equal(branded.config.artifactName, stable.config.artifactName)

  assert.equal(
    applyDesktopIdentity(
      {
        getPath: (): string => assert.fail('stable userData must not be relocated'),
        setPath: (): void => assert.fail('stable userData must not be relocated'),
        setName: (): void => assert.fail('stable name must not change')
      },
      branded.identity
    ),
    null
  )
})

test('channel stamps verify the real checkout and retain source version and native ownership', async (): Promise<void> => {
  const {
    resolveStamp,
    buildStampPayload,
    writeDesktopStamp
  }: {
    resolveStamp: (options: { env: NodeJS.ProcessEnv; repoRoot: string }) => InstallStamp
    buildStampPayload: (
      stamp: InstallStamp,
      env: NodeJS.ProcessEnv,
      platform: string,
      payload: StampPayload
    ) => InstallStamp
    writeDesktopStamp: (directory: string, stamp: InstallStamp) => void
  } = await import('../scripts/write-build-stamp.mjs')

  const dir: string = fs.mkdtempSync(path.join(os.tmpdir(), 'channel-stamp-'))

  try {
    execFileSync('git', ['init', '-q', dir])
    execFileSync(
      'git',
      [
        '-c',
        'user.name=Fixture',
        '-c',
        'user.email=fixture@example.test',
        '-c',
        'commit.gpgsign=false',
        'commit',
        '--allow-empty',
        '-qm',
        'fixture'
      ],
      { cwd: dir }
    )

    const build: ChannelBuildRequest = {
      ...request(),
      commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: dir, encoding: 'utf8' }).trim()
    }

    const env: NodeJS.ProcessEnv = {
      ...process.env,
      HERMES_DESKTOP_VARIANT: 'bundled',
      _HERMES_CHANNEL_REQUEST_JSON: JSON.stringify(build),
      GITHUB_SHA: 'd'.repeat(40)
    }

    const provenance: InstallStamp = resolveStamp({ env, repoRoot: dir })
    const payload: StampPayload = { runtime: { repoDir: 'repo', commands: { hermes: 'bin/hermes' } } }
    const built: InstallStamp = buildStampPayload(provenance, env, 'darwin', payload)
    assert.equal(built.source, 'channel-build')
    assert.equal(built.updateMechanism, 'electron-updater')
    assert.equal(built.baseVersion, build.sourceVersion)
    assert.equal(built.tag, null)
    assert.equal(built.receiverProtocol, 1)
    assert.deepEqual(built.channelBuild, build)
    assert.ok(Object.isFrozen(built.channelBuild?.identity))
    fs.mkdirSync(path.join(dir, 'out/agent-payload/repo'), { recursive: true })
    writeDesktopStamp(path.join(dir, 'out'), built)
    assert.deepEqual(
      JSON.parse(fs.readFileSync(path.join(dir, 'out/install-stamp.json'), 'utf8')),
      JSON.parse(fs.readFileSync(path.join(dir, 'out/agent-payload/repo/install-stamp.json'), 'utf8'))
    )
    assert.throws((): void => {
      resolveStamp({ env: { ...env, _HERMES_CHANNEL_REQUEST_JSON: JSON.stringify(request()) }, repoRoot: dir })
    }, /checkout/)
    delete process.env._HERMES_CHANNEL_REQUEST_JSON
    process.env.HERMES_PAYLOAD_TAG = 'v0.0.1'
    delete require.cache[require.resolve('../product-identity.cjs')]
    const official: ProductIdentity = require('../product-identity.cjs')
    delete process.env.HERMES_PAYLOAD_TAG

    const receiver: ChannelBuildRequest = {
      ...build,
      channel: 'stable',
      sequence: 1,
      version: '0.0.1',
      windowsVersion: '0.0.1.0',
      releaseTag: 'v0.0.1',
      receiverCandidate: true,
      bundleEnv: {},
      publicBase: 'https://builds.example.test/ci-disposable/1/2',
      identity: { ...official, token: build.identity.token }
    }

    const receiverEnv: NodeJS.ProcessEnv = { ...env, _HERMES_CHANNEL_REQUEST_JSON: JSON.stringify(receiver) }

    const stable: InstallStamp = buildStampPayload(
      resolveStamp({ env: receiverEnv, repoRoot: dir }),
      receiverEnv,
      'darwin',
      payload
    )

    assert.equal(stable.channelBuild, undefined)
    assert.equal(stable.source, 'build')
    assert.equal(stable.tag, receiver.releaseTag)
    assert.equal(stable.displayVersion, receiver.version)
    assert.equal(stable.updateMechanism, 'electron-updater')
    const packaged: PackagingFacts = load(receiver)
    assert.deepEqual(packaged.config.mac?.publish, [
      { provider: 'generic', url: `${receiver.publicBase}/releases/darwin/stable/`, channel: 'stable' }
    ])

    const {
      verifyBundleStamp
    }: {
      verifyBundleStamp: (
        stamp: InstallStamp,
        options: {
          commit: string
          platform: string
          channelRequest: ChannelBuildRequest
        }
      ) => string
    } = await import('../../../tests/install/e2e-assets/bundle-smoke-metadata.mjs')

    assert.equal(
      verifyBundleStamp(stable, { commit: receiver.commit, platform: 'darwin', channelRequest: receiver }),
      receiver.version
    )
    assert.throws((): void => {
      verifyBundleStamp(built, { commit: receiver.commit, platform: 'darwin', channelRequest: receiver })
    })
    execFileSync(
      process.env.HERMES_PYTHON || 'python3',
      [
        '-c',
        `
import json, sys
from scripts.bundles.release_artifacts import stamp_matches
stamp, request = json.loads(sys.argv[1]), json.loads(sys.argv[2])
stamp_matches(stamp, '', request['commit'], channel_request=request)
stamp['channelBuild'] = request
try:
    stamp_matches(stamp, '', request['commit'], channel_request=request)
except ValueError:
    pass
else:
    raise AssertionError('receiver accepted preview updater ownership')
`,
        JSON.stringify(stable),
        JSON.stringify(receiver)
      ],
      { cwd: repo }
    )
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})
