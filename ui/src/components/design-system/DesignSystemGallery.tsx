import { useState } from "react"
import { LightningIcon, PlusIcon } from "@phosphor-icons/react"
import type { ReactNode } from "react"

import { EmptyState } from "@/components/patterns/empty-state"
import { PageHeader } from "@/components/patterns/page-header"
import {
  SettingsPanel,
  SettingsRow,
  SettingsSection,
} from "@/components/patterns/settings"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button, IconButton } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { CopyButton } from "@/components/ui/copy-button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipPopup,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useTheme } from "@/lib/theme"

const SWATCHES = [
  { name: "background", className: "bg-background" },
  { name: "card", className: "bg-card" },
  { name: "popover", className: "bg-popover" },
  { name: "muted", className: "bg-muted" },
  { name: "accent", className: "bg-accent" },
  { name: "primary", className: "bg-primary" },
  { name: "success", className: "bg-success" },
  { name: "warning", className: "bg-warning" },
  { name: "info", className: "bg-info" },
  { name: "destructive", className: "bg-destructive" },
]

function GallerySection({
  title,
  source,
  children,
}: {
  title: string
  source: string
  children: ReactNode
}) {
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium">{title}</h2>
        <code className="text-xs break-all text-muted-foreground">
          {source}
        </code>
      </div>
      {children}
    </section>
  )
}

export default function DesignSystemGallery() {
  const { theme, setTheme } = useTheme()
  const [notifications, setNotifications] = useState(true)
  const [environment, setEnvironment] = useState("sandbox")
  const [saved, setSaved] = useState(false)

  return (
    <TooltipProvider>
      <main className="min-h-svh bg-background text-foreground">
        <div className="mx-auto max-w-4xl space-y-10 px-4 py-10 sm:px-8">
          <PageHeader
            title="Open SWE design system"
            description="The components behind the dashboard. A local reference for building consistent screens."
            actions={
              <div
                role="group"
                aria-label="Preview theme"
                className="flex gap-1"
              >
                {(["light", "dark", "system"] as const).map((value) => (
                  <Button
                    key={value}
                    variant={theme === value ? "secondary" : "ghost"}
                    aria-pressed={theme === value}
                    onClick={() => setTheme(value)}
                    className="capitalize"
                  >
                    {value}
                  </Button>
                ))}
              </div>
            }
          />

          <GallerySection title="Foundations" source="styles/tokens.css">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              {SWATCHES.map(({ name, className }) => (
                <div key={name} className="space-y-2">
                  <div
                    className={`h-14 rounded-lg border border-border ${className}`}
                  />
                  <code className="text-xs text-muted-foreground">{name}</code>
                </div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              Inter · compact controls · semantic colors · light and dark themes
            </p>
          </GallerySection>

          <GallerySection
            title="Actions"
            source="components/ui/button.tsx · copy-button.tsx"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Button>Primary</Button>
              <Button variant="secondary">Secondary</Button>
              <Button variant="outline">Outline</Button>
              <Button variant="ghost">Ghost</Button>
              <Button variant="destructive">Destructive</Button>
              <Button disabled>Disabled</Button>
              <CopyButton
                text="Hello from Open SWE"
                label="Copy example text"
                size="icon"
              />
              <Tooltip>
                <TooltipTrigger
                  render={
                    <IconButton variant="outline" aria-label="Add item" />
                  }
                >
                  <PlusIcon />
                </TooltipTrigger>
                <TooltipPopup>Add item</TooltipPopup>
              </Tooltip>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="xs" variant="outline">
                Extra small
              </Button>
              <Button size="sm" variant="outline">
                Small
              </Button>
              <Button variant="outline">Default</Button>
              <Button size="lg" variant="outline">
                Large
              </Button>
            </div>
          </GallerySection>

          <GallerySection title="Form controls" source="components/ui/">
            <div className="grid gap-6 sm:grid-cols-2">
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="gallery-name">Connection name</Label>
                  <Input id="gallery-name" placeholder="my-workspace" />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="gallery-invalid">Server URL</Label>
                  <Input
                    id="gallery-invalid"
                    defaultValue="https://"
                    aria-invalid="true"
                    aria-describedby="gallery-error"
                  />
                  <p id="gallery-error" className="text-xs text-destructive">
                    Enter a complete URL.
                  </p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="gallery-disabled">Read-only setting</Label>
                  <Input
                    id="gallery-disabled"
                    value="Managed by your workspace"
                    disabled
                  />
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="gallery-instructions">Instructions</Label>
                <Textarea
                  id="gallery-instructions"
                  placeholder="Describe how the agent should work…"
                  className="min-h-36"
                />
              </div>
            </div>
          </GallerySection>

          <GallerySection title="Feedback and surfaces" source="components/ui/">
            <div className="flex flex-wrap gap-2">
              <Badge>Default</Badge>
              <Badge variant="secondary">Secondary</Badge>
              <Badge variant="outline">Outline</Badge>
              <Badge variant="destructive">Failed</Badge>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {(["info", "success", "warning", "error"] as const).map(
                (variant) => (
                  <Alert key={variant} variant={variant} role="status">
                    <AlertTitle className="capitalize">{variant}</AlertTitle>
                    <AlertDescription>
                      A short explanation and a useful next step.
                    </AlertDescription>
                  </Alert>
                )
              )}
              <Card>
                <CardHeader>
                  <CardTitle>Card surface</CardTitle>
                </CardHeader>
                <CardContent>
                  Group related information and controls.
                </CardContent>
              </Card>
              <div
                aria-label="Loading preview"
                className="space-y-2 rounded-lg border p-4"
              >
                <Skeleton className="h-4 w-1/3" />
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-2/3" />
              </div>
            </div>
          </GallerySection>

          <GallerySection
            title="Settings patterns"
            source="components/patterns/settings.tsx"
          >
            <SettingsSection
              title="Preferences"
              description="Controls here update this preview only."
            >
              <SettingsRow
                label="Notifications"
                htmlFor="gallery-notifications"
                description="Notify when an agent run finishes."
                control={
                  <Switch
                    id="gallery-notifications"
                    checked={notifications}
                    onCheckedChange={setNotifications}
                  />
                }
              />
              <SettingsRow
                label="Environment"
                htmlFor="gallery-environment"
                description="Where new agent tasks start."
                control={
                  <Select
                    value={environment}
                    onValueChange={(value) => value && setEnvironment(value)}
                    items={{ sandbox: "Sandbox", local: "Local" }}
                  >
                    <SelectTrigger id="gallery-environment" className="w-40">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="sandbox">Sandbox</SelectItem>
                      <SelectItem value="local">Local</SelectItem>
                    </SelectContent>
                  </Select>
                }
              />
              <SettingsPanel>
                <div className="flex items-center gap-3">
                  <Button onClick={() => setSaved(true)}>Save preview</Button>
                  <span role="status" className="text-xs text-muted-foreground">
                    {saved
                      ? "Preview saved. No workspace settings were changed."
                      : ""}
                  </span>
                </div>
              </SettingsPanel>
            </SettingsSection>
          </GallerySection>

          <GallerySection
            title="Empty states"
            source="components/patterns/empty-state.tsx"
          >
            <EmptyState
              className="bg-card"
              icon={<LightningIcon className="size-5" />}
              title="No automations yet"
              description="Explain what belongs here and give people a clear first action."
              action={
                <Button onClick={() => setSaved(true)}>
                  <PlusIcon />
                  Try an action
                </Button>
              }
            />
          </GallerySection>
        </div>
      </main>
    </TooltipProvider>
  )
}
