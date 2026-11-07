import type { CommandName } from '../commands/types'
import type { ReferenceItem, ReferenceKind } from '../references/types'
export interface PaletteItem {
  id: string
  title: string
  description: string
  group: string
  icon: string
  command?: CommandName
  argument?: string
  reference?: ReferenceItem
  category?: ReferenceKind
  disabled?: boolean
}
