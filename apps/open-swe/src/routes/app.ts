import { Hono } from "hono";
import { registerRunRoute } from "../server/routes/run.js";
import { registerFeatureGraphRoute } from "../server/routes/feature-graph.js";

export const app = new Hono();

registerRunRoute(app);
registerFeatureGraphRoute(app);
