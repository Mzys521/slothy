const desktop = require('./desktop.config.cjs')

module.exports = {
  appId: desktop.appId,
  productName: desktop.productName,
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
  extraMetadata: { name: 'slothy', scripts: {}, dependencies: {}, devDependencies: {} },
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
    artifactName: '${productName}-${version}-${arch}-Setup.${ext}',
  },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: desktop.productName,
  },
  publish: null,
}
