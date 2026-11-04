import { generatePath } from "react-router";

/** In-app paths of the project screens (ARCHITECTURE §3.1b), shared by every feature that links to them. */
export const PROJECTS_PATH = "/projects";
export const NEW_PROJECT_PATH = "/projects/new";
export const workspacePath = (projectId: string) =>
  generatePath("/projects/:projectId/workspace", { projectId });
export const importPath = (projectId: string) => generatePath("/projects/:projectId/import", { projectId });
export const taskPath = (taskId: string) => generatePath("/tasks/:taskId", { taskId });
