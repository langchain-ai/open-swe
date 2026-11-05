import type { IconComponent } from "@langchain/macaw-components/Icon"
import { createElement } from "react"
import { BookOpenIcon } from "@phosphor-icons/react/dist/ssr/BookOpen"
import { CalendarIcon } from "@phosphor-icons/react/dist/ssr/Calendar"
import { ChartBarIcon } from "@phosphor-icons/react/dist/ssr/ChartBar"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { FileTextIcon } from "@phosphor-icons/react/dist/ssr/FileText"
import { KanbanIcon } from "@phosphor-icons/react/dist/ssr/Kanban"
import { LightbulbIcon } from "@phosphor-icons/react/dist/ssr/Lightbulb"
import { NotebookIcon } from "@phosphor-icons/react/dist/ssr/Notebook"
import { PuzzlePieceIcon } from "@phosphor-icons/react/dist/ssr/PuzzlePiece"

import type { SidebarIcon } from "@/features/plugins/lib/api"

/** A plugin names its sidebar icon from this set; it never supplies markup. */
export const PLUGIN_ICONS: Record<SidebarIcon, IconComponent> = {
  "puzzle-piece": PuzzlePieceIcon,
  notebook: NotebookIcon,
  "file-text": FileTextIcon,
  "book-open": BookOpenIcon,
  kanban: KanbanIcon,
  "chart-bar": ChartBarIcon,
  "chat-circle": ChatCircleIcon,
  calendar: CalendarIcon,
  lightbulb: LightbulbIcon,
}

export function pluginIcon(icon: string): IconComponent {
  return Object.hasOwn(PLUGIN_ICONS, icon)
    ? PLUGIN_ICONS[icon as SidebarIcon]
    : PuzzlePieceIcon
}

/** A plugin's sidebar icon, drawn by the dashboard. */
export function PluginIcon({
  icon,
  size,
  className,
}: {
  icon: string
  size: number
  className?: string
}) {
  return createElement(pluginIcon(icon), { size, className })
}

export function pluginRoot(name: string): `/agents/plugins/${string}` {
  return `/agents/plugins/${name}`
}
