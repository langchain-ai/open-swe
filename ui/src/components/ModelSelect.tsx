import {
  ChatComposerModelSelect,
  type ChatComposerModelOption,
  type ChatComposerModelSelectProps,
} from "@langchain/macaw-components/ChatComposer"
import {
  AnthropicLogo,
  GeminiLogo,
  OpenAILogo,
} from "@langchain/macaw-components/ProviderLogo"
import { CpuIcon } from "@phosphor-icons/react/dist/ssr/Cpu"

import type { ModelOption } from "@/lib/api"

const PROVIDERS = new Map([
  [
    "anthropic",
    { name: "Anthropic", icon: <AnthropicLogo className="size-4" /> },
  ],
  ["openai", { name: "OpenAI", icon: <OpenAILogo className="size-4" /> }],
  ["google_genai", { name: "Google", icon: <GeminiLogo className="size-4" /> }],
  ["fireworks", { name: "Fireworks AI", icon: <CpuIcon size={16} /> }],
])

type ModelSelectProps = Omit<ChatComposerModelSelectProps, "options"> & {
  models: ModelOption[]
  leadingOption?: ChatComposerModelOption
  requireImageSupport?: boolean
}

export function ModelSelect({
  models,
  leadingOption,
  requireImageSupport = false,
  ...props
}: ModelSelectProps) {
  const options = models.map((model): ChatComposerModelOption => {
    const provider = PROVIDERS.get(model.id.split(":", 1)[0] ?? "")
    const disabled = requireImageSupport && !model.supports_images
    return {
      value: model.id,
      label: model.label,
      group: provider?.name ?? "Other Models",
      icon: provider?.icon ?? <CpuIcon size={16} />,
      disabled,
    }
  })
  return (
    <ChatComposerModelSelect
      options={leadingOption ? [leadingOption, ...options] : options}
      searchThreshold={0}
      {...props}
    />
  )
}
