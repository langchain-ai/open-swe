// Temporary designer-demo fixtures. Removal and regeneration: README.md.
export const ids = {
  run: "00000000-0000-0000-0000-000000000001",
  blocked: "00000000-0000-0000-0000-000000000002",
  thread: "00000000-0000-0000-0000-000000000099",
}
export const revision = { base: "a".repeat(40), head: "b".repeat(40) }
export const date = "2026-10-29T14:00:00Z"

export const checkpoints = [
  {
    id: "color",
    title: "Button color · subtle shift",
    note: "Button fill changed by 8 RGB levels",
    body: "A deliberately faint color change. At threshold 0 the changed fill is visible; threshold 18 filters it out.",
    rect: [720, 88, 140, 36],
  },
  {
    id: "size",
    title: "Button size · 2 px taller",
    note: "Button height increased from 34 to 36 px",
    body: "Check alignment with the neighboring search field. Use 200% or 400% zoom to inspect the two-pixel edge.",
    rect: [720, 88, 140, 36],
  },
  {
    id: "hover",
    title: "New trace · Hover",
    interaction: true,
    actions: ["hover New trace"],
    note: "Hover label loses contrast",
    body: "The candidate uses a pale hover fill with white text. This scripted example needs a contrast check.",
    rect: [720, 88, 140, 36],
  },
  {
    id: "pressed",
    title: "New trace · Pressed",
    interaction: true,
    actions: ["hover New trace", "press New trace (held)"],
    rect: [720, 88, 140, 36],
  },
  {
    id: "dialog",
    title: "Create project · Open dialog",
    interaction: true,
    actions: ["click New trace"],
    note: "Dialog footer extends below the viewport",
    body: "The candidate dialog extends 60 px beyond the image. Check the close action, footer, and scrolling with long content.",
    rect: [290, 110, 500, 450],
  },
  {
    id: "filled",
    title: "Create project · Filled input",
    interaction: true,
    actions: [
      "click New trace",
      "focus Project name",
      "type Customer support evaluation",
    ],
    rect: [320, 225, 400, 50],
  },
]

const text = (x, y, label, size = 14, color = "#283542") =>
  `<text x="${x}" y="${y}" font-size="${size}" fill="${color}">${label}</text>`
const rect = (x, y, w, h, fill, radius = 6, stroke = "none") =>
  `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${radius}" fill="${fill}" stroke="${stroke}"/>`

export function snapshot(id, side) {
  const head = side === "head"
  let fill = "#244a3f"
  if (id === "color" && head) fill = "#2c5247"
  if (id === "hover") fill = head ? "#d7e7df" : "#326655"
  if (id === "pressed") fill = head ? "#244a3f" : "#16352d"
  let content = rect(0, 0, 900, 560, "#ffffff", 0)
  content += rect(0, 0, 165, 560, "#f5f8fb", 0) + text(18, 34, "LangSmith", 19)
  content += text(18, 65, "Demo workspace", 12, "#62717e")
  content += rect(10, 99, 145, 32, "#e3efeb") + text(25, 120, "Tracing")
  content +=
    text(25, 161, "Datasets") +
    text(25, 202, "Evaluations") +
    text(25, 243, "Settings")
  content += text(195, 34, "Projects / Customer support", 12, "#62717e")
  content +=
    rect(165, 52, 735, 1, "#d9e1e8", 0) + text(195, 83, "Customer support", 23)
  content +=
    rect(195, 101, 310, 32, "#ffffff", 6, "#ccd5dd") +
    text(208, 122, "Search traces…", 12, "#7a8793")
  content +=
    rect(522, 101, 100, 32, "#ffffff", 6, "#ccd5dd") +
    text(541, 122, "Last 7 days", 12)
  content +=
    rect(720, 88, 140, id === "size" && !head ? 34 : 36, fill) +
    text(750, 111, "New trace", 14, "#ffffff")
  content +=
    text(195, 175, "Recent traces", 15) +
    text(750, 175, "24 traces", 12, "#62717e")
  content +=
    rect(195, 194, 665, 32, "#edf2f7", 4) +
    text(210, 215, "Name", 12) +
    text(550, 215, "Status", 12) +
    text(735, 215, "Latency", 12)
  for (let i = 0; i < 6; i++) {
    const y = 252 + i * 43
    content += text(
      210,
      y,
      ["Customer question", "Search knowledge base", "Draft response"][i % 3],
      13
    )
    content +=
      rect(547, y - 15, 74, 21, "#e3efeb", 10) +
      text(560, y, "Success", 11, "#326655")
    content +=
      text(740, y, `${320 + i * 41} ms`, 12, "#62717e") +
      rect(195, y + 16, 665, 1, "#edf2f7", 0)
  }
  if (id === "dialog" || id === "filled") {
    const y = head ? 160 : 70
    const height = head ? 460 : 420
    content += `<rect width="900" height="560" fill="#101828" opacity="0.35"/>`
    content += rect(270, y, 500, height, "#ffffff", 12, "#ccd5dd")
    content +=
      text(300, y + 44, "Create project", 21) + text(730, y + 42, "×", 22)
    content += text(
      300,
      y + 76,
      "Keep your next evaluation organized.",
      13,
      "#62717e"
    )
    content += text(300, y + 119, "Project name", 13)
    content += rect(
      300,
      y + 134,
      440,
      40,
      "#ffffff",
      6,
      id === "filled" ? "#4285f4" : "#ccd5dd"
    )
    content += text(
      312,
      y + 159,
      id === "filled" ? "Customer support evaluation" : "Enter a project name…",
      14,
      id === "filled" ? "#283542" : "#7a8793"
    )
    content +=
      text(300, y + 216, "Description", 13) +
      rect(300, y + 231, 440, 90, "#f5f8fb")
    content += rect(300, y + height - 67, 440, 1, "#edf2f7", 0)
    content +=
      rect(600, y + height - 49, 140, 34, "#244a3f") +
      text(617, y + height - 27, "Create project", 13, "#ffffff")
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="560" viewBox="0 0 900 560"><g font-family="Arial, sans-serif">${content}</g></svg>`
}

export function makeRun(number, metrics = {}, id = ids.run) {
  const root = `/dashboard/api/reviews/synthetic/frontend-review/${number}/frontend-reviews/${id}/media`
  return {
    id,
    status: "complete",
    auth_mode: "none",
    base_url: "https://synthetic-base.invalid",
    head_url: "https://synthetic-pr.invalid",
    base_sha: revision.base,
    head_sha: revision.head,
    paths: [
      "/projects/customer-support",
      "/projects/customer-support/settings",
    ],
    created_at: date,
    updated_at: date,
    error: null,
    report: {
      summary:
        "Scripted demo recap: the button has a subtle color shift and a 2 px height change. Check the low-contrast hover state and the dialog footer that falls below the viewport. These are authored examples, not findings from a live AI review.",
      ai_review_status: "complete",
      build_verification: "unverified",
      video_status: "unavailable",
      scenarios: checkpoints.map((point) => ({
        id: point.id,
        title: point.title,
        path:
          point.id === "size"
            ? "/projects/customer-support/settings"
            : "/projects/customer-support",
        status: "complete",
        before_url: `${root}/${point.id}-base.png`,
        after_url: `${root}/${point.id}-head.png`,
        diff_url: null,
        mask_url: null,
        before_video_url: null,
        after_video_url: null,
        width: 900,
        height: 560,
        metrics: metrics[point.id] ?? null,
        regions: [],
        mask_rects: [],
        tile_size: 24,
        min_region_pixels: 1,
        error: null,
        interaction: Boolean(point.interaction),
        actions: point.actions ?? [],
        before_error: null,
        after_error: null,
        layout_warnings:
          point.id === "dialog" || point.id === "filled"
            ? [
                "PR: Dialog extends 60 px below the viewport. Scripted geometry warning; inspect scrolling and the footer.",
              ]
            : [],
        annotations: point.note
          ? [
              {
                x: point.rect[0],
                y: point.rect[1],
                width: point.rect[2],
                height: point.rect[3],
                title: `Demo: ${point.note}`,
                body: point.body,
              },
            ]
          : [],
      })),
    },
  }
}
