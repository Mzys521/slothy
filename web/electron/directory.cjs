const path = require('node:path')

async function selectWorkingDirectory(dialog, window) {
  if (!window) throw new Error('No application window.')
  const result = await dialog.showOpenDialog(window, {
    title: '选择工作区目录', properties: ['openDirectory', 'createDirectory'],
  })
  const selected = result.canceled ? undefined : result.filePaths[0]
  return typeof selected === 'string' && selected ? path.resolve(selected) : null
}

module.exports = { selectWorkingDirectory }
