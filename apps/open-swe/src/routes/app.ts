import { Hono } from "hono";
import { registerRunRoute } from "../server/routes/run.js";

export const app = new Hono();

registerRunRoute(app);
