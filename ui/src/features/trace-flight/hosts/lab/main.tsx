import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./lab.css"
import { BirdLab } from "./BirdLab"
import { GameHarness } from "./GameHarness"

const page = new URLSearchParams(window.location.search).get("page")
const root = document.getElementById("root")
if (root)
  createRoot(root).render(
    <StrictMode>{page === "game" ? <GameHarness /> : <BirdLab />}</StrictMode>
  )
