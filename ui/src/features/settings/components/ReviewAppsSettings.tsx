import { useEffect, useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import { Switch } from "@langchain/macaw-components/Switch"
import { Textarea } from "@langchain/macaw-components/Textarea"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import type { App } from "@/features/plugins/lib/api"
import { useApps } from "@/features/plugins/lib/queries"
import type { ScopedSettings } from "@/features/settings/lib/settingsScope"
import type { AppRelease } from "@/lib/api"
import { TierRow } from "./WorkspaceSettingsSections"

/** What an app's newest release has a review do, in a few words. */
function stepSummary(app: App): string {
  const step = app.latest?.manifest.review
  if (!step) return "Its newest release no longer has a review step."
  const parts = [
    step.browser ? "browses the pull request's preview" : null,
    step.writes.length > 0 ? `saves to ${step.writes.join(", ")}` : null,
  ].filter((part) => part !== null)
  return parts.length > 0
    ? `In each review, ${parts.join(" and ")}.`
    : "In each review, follows its instructions."
}

/**
 * The apps reviews do work for, at one tier: listed apps whose newest release
 * has a review step. Each is pinned to the release an admin turned on, so its
 * owner's later releases change no review until an admin updates it.
 */
export function ReviewAppsSettings({
  settings,
  editable,
}: {
  settings: ScopedSettings
  editable: boolean
}) {
  const apps = useApps()
  const chosen = settings.data?.review_apps ?? []
  const pinned = new Map(chosen.map((entry) => [entry.app, entry.release]))
  const offered = (apps.data ?? []).filter(
    (app) => app.latest?.manifest.review || pinned.has(app.id)
  )
  const missing = chosen.filter(
    (entry) => !offered.some((app) => app.id === entry.app)
  )

  const save = (next: Array<AppRelease>) => settings.save({ review_apps: next })
  const without = (id: string) => chosen.filter((entry) => entry.app !== id)
  const pin = (app: App) => {
    if (app.latest)
      save([...without(app.id), { app: app.id, release: app.latest.id }])
  }

  return (
    <SettingsSection title="Apps in reviews">
      <div className="divide-y divide-default">
        <TierRow
          settings={settings}
          fields={["review_apps"]}
          label="Apps reviews work for"
          description="Reviews of private repositories also do what these apps ask, such as saving screenshots of a frontend pull request's preview to Design QA."
          control={null}
        />
        {offered.map((app) => {
          const release = pinned.get(app.id)
          const behind =
            release !== undefined &&
            app.latest?.manifest.review != null &&
            release !== app.latest.id
          return (
            <SettingsRow
              key={app.id}
              label={app.latest?.manifest.displayName ?? app.name}
              description={
                behind
                  ? `Reviews follow the release that was newest when it was turned on. Release ${app.latest?.number} is out; update to follow it.`
                  : stepSummary(app)
              }
              control={
                <div className="flex items-center gap-space-2">
                  {behind && (
                    <Button
                      size="xs"
                      color="secondary"
                      variant="outlined"
                      onClick={() => pin(app)}
                      disabled={!editable}
                    >
                      Update
                    </Button>
                  )}
                  <Switch
                    aria-label={`Reviews work for ${app.name}`}
                    checked={release !== undefined}
                    onChange={(on) => (on ? pin(app) : save(without(app.id)))}
                    disabled={
                      !editable || (release === undefined && !app.latest)
                    }
                  />
                </div>
              }
            />
          )
        })}
        {missing.map((entry) => (
          <SettingsRow
            key={entry.app}
            label="An app you can't see"
            description="Reviews still work for it; turn it off to stop."
            control={
              <Switch
                aria-label={`Reviews work for app ${entry.app}`}
                checked
                onChange={() => save(without(entry.app))}
                disabled={!editable}
              />
            }
          />
        ))}
        {!apps.isPending && offered.length === 0 && missing.length === 0 && (
          <SettingsRow
            label="No apps yet"
            description="An app appears here once its newest release declares a review step and it is listed or yours."
            control={null}
          />
        )}
        <PreviewChecks
          // A newly saved list starts a fresh draft.
          key={(settings.saved?.review_app_preview_checks ?? []).join("\n")}
          settings={settings}
          editable={editable}
        />
      </div>
    </SettingsSection>
  )
}

/**
 * The checks whose success means a pull request's preview serves its head
 * commit, so the review does its apps' work then, not only while it reviews.
 */
function PreviewChecks({
  settings,
  editable,
}: {
  settings: ScopedSettings
  editable: boolean
}) {
  const [draft, setDraft] = useState(
    (settings.saved?.review_app_preview_checks ?? []).join("\n")
  )
  // One per line: check names hold spaces, and a matrix job's commas.
  const checks = draft
    .split("\n")
    .map((name) => name.trim())
    .filter(Boolean)
  const dirty =
    checks.join("\n") !==
    (settings.data?.review_app_preview_checks ?? []).join("\n")
  return (
    <div>
      <TierRow
        settings={settings}
        fields={["review_app_preview_checks"]}
        label="Preview checks"
        description="A review usually finishes before a pull request's preview is up. When one of these checks passes for a pull request, Robin does the apps' work on its preview."
        control={null}
      />
      <div className="flex flex-col gap-space-2 px-space-4 pb-space-4">
        <Textarea
          aria-label="Preview checks"
          size="md"
          placeholder="wait-for-dns"
          hintText="One check name per line, as the pull request's checks list shows it: a job that passes only once the preview serves the pull request's head commit, and is skipped when there's no preview."
          value={draft}
          onChange={setDraft}
          disabled={!editable}
        />
        <div className="flex items-center gap-space-2">
          <Button
            size="xs"
            color="primary"
            disabled={!editable || !dirty}
            onClick={() => settings.save({ review_app_preview_checks: checks })}
          >
            Save checks
          </Button>
          {dirty && (
            <span className="text-xs text-secondary">Unsaved changes</span>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * Where the browser of a review that works for apps may sign in, and the MCP
 * tool that signs it in. Instance only: they decide where every workspace's
 * reviews sign in, and as whom.
 */
export function ReviewBrowserSettings({
  settings,
  editable,
}: {
  settings: ScopedSettings
  editable: boolean
}) {
  const savedDomains = (settings.saved?.review_browser_domains ?? []).join(", ")
  const savedTool = settings.saved?.review_browser_session_tool ?? ""
  const [domainsDraft, setDomainsDraft] = useState(savedDomains)
  const [toolDraft, setToolDraft] = useState(savedTool)

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    setDomainsDraft(savedDomains)
  }, [savedDomains])
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    setToolDraft(savedTool)
  }, [savedTool])

  const domains = domainsDraft.split(/[\s,]+/).filter(Boolean)
  const tool = toolDraft.trim()
  const dirty =
    domains.join(", ") !==
      (settings.data?.review_browser_domains ?? []).join(", ") ||
    tool !== (settings.data?.review_browser_session_tool ?? "")

  return (
    <SettingsSection
      title="Review browser"
      description="Reviews that work for apps, such as Design QA, open the pull request's preview in this browser. Every workspace's reviews sign in only to these domains, and only on LangSmith sites, with the one tool named here."
    >
      <div className="flex flex-col gap-space-3 p-space-4">
        <Input
          label="Sign-in domains"
          aria-label="Sign-in domains"
          size="md"
          placeholder="review.example.com, dev.example.com"
          hintText="Each covers its subdomains. Pages the pull request's author controls open here, so never list production."
          value={domainsDraft}
          onChange={setDomainsDraft}
          disabled={!editable}
        />
        <Input
          label="Session tool"
          aria-label="Session tool"
          size="md"
          placeholder="connection/tool"
          hintText="An instance or workspace MCP connection's tool that returns a browser session. Leave it empty to browse signed out."
          value={toolDraft}
          onChange={setToolDraft}
          disabled={!editable}
        />
        <div className="flex items-center gap-space-2">
          <Button
            size="xs"
            color="primary"
            disabled={!editable || !dirty}
            onClick={() =>
              settings.save({
                review_browser_domains: domains,
                review_browser_session_tool: tool || null,
              })
            }
          >
            Save
          </Button>
          {dirty && (
            <span className="text-xs text-secondary">Unsaved changes</span>
          )}
        </div>
      </div>
    </SettingsSection>
  )
}
