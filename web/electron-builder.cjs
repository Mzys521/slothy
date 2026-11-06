const desktop = require('./desktop.config.cjs')
const metadata = require('./package.json')
const releaseVersion = metadata.displayVersion || metadata.version
if (!/^\d+\.\d+\.\d+(?:\.\d+)?(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?$/.test(releaseVersion)) throw new Error('Invalid release display version.')
const windowsParts = releaseVersion.split('-')[0].split('.')
while (windowsParts.length < 4) windowsParts.push('0')

module.exports = {
  appId: desktop.appId,
  productName: desktop.productName,
  buildVersion: windowsParts.join('.'),
  buildNumber: windowsParts[3],
  asar: true,
  directories: { output: 'release/slothy' },
  files: [
    'dist/**/*',
    'electron/main.cjs',
    'electron/backend.cjs',
    'electron/directory.cjs',
    'electron/preload.cjs',
    'electron/assets/**/*',
    desktop.icon,
    'desktop.config.cjs',
    'package.json',
    '!node_modules/**/*',
  ],
  extraMetadata: { name: 'slothy', displayVersion: releaseVersion, shortVersionWindows: windowsParts.join('.'), scripts: {}, dependencies: {}, devDependencies: {} },
  extraResources: [{ from: 'build/runtime/slothy-service', to: 'backend', filter: ['**/*'] }],
  afterPack: './electron/audit-production.cjs',
  afterAllArtifactBuild: async ({ outDir }) => {
    // Builder diagnostics are useful during development but are not release deliverables.
    await require('node:fs/promises').rm(require('node:path').join(outDir, 'builder-debug.yml'), { force: true })
    return []
  },
  win: {
    target: [{ target: 'nsis', arch: ['x64'] }],
    icon: desktop.icon,
    artifactName: '${productName}-' + releaseVersion + '-${arch}-Setup.${ext}',
  },
  nsis: {
    include: 'build/release-version.nsh',
    uninstallDisplayName: '${productName} ' + releaseVersion,
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: desktop.productName,
  },
  publish: null,
}
