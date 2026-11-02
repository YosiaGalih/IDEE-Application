import { z } from "zod";
import { apiPath, apiRequest, PageMetaSchema, type PageMeta } from "./client";
import { ApiError } from "./errors";
import { apiUpload } from "./upload";

/** `Stack` (API_CONTRACTS §3): what deterministic detection found in the repository. */
export const StackSchema = z.object({
  languages: z.array(z.string()),
  frameworks: z.array(z.string()),
  package_managers: z.array(z.string()),
  test_profiles: z.array(z.string()),
  entrypoints: z.array(z.string()),
});
export type Stack = z.infer<typeof StackSchema>;

export const PROJECT_STATUSES = ["active", "archived", "deleting_pending_approval", "deleted"] as const;
export type ProjectStatus = (typeof PROJECT_STATUSES)[number];

/** `Project` (API_CONTRACTS §3 + §11 #14). `stats` only on a single project. */
export const ProjectSchema = z.object({
  id: z.guid(),
  name: z.string(),
  slug: z.string(),
  description: z.string().nullable(),
  status: z.enum(PROJECT_STATUSES),
  // An empty object until detection ran; the screens read it as optional.
  stack: StackSchema.partial(),
  default_branch: z.string(),
  settings: z.record(z.string(), z.unknown()),
  owner_id: z.guid(),
  created_at: z.iso.datetime({ offset: true }),
  updated_at: z.iso.datetime({ offset: true }),
  repository: z
    .object({
      provider: z.string(),
      remote_url: z.string().nullable(),
      branch: z.string(),
      sync_state: z.enum(["ready", "importing", "indexing", "error"]),
      last_indexed_at: z.iso.datetime({ offset: true }).nullable(),
      error: z.string().nullable(),
    })
    .nullable(),
  permissions: z.array(z.string()),
  stats: z
    .object({
      files: z.number().int().nullable(),
      last_commit: z.string().nullable(),
      open_tasks: z.number().int(),
    })
    .nullable(),
});
export type Project = z.infer<typeof ProjectSchema>;

export const TemplateSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  stack: z.string(),
  icon: z.string(),
});
export type Template = z.infer<typeof TemplateSchema>;

export type ProjectListFilter = { status: "active" | "archived" | "all"; q: string; page: number };
export type ProjectPage = { items: Project[]; meta: PageMeta };
export const PROJECTS_PAGE_SIZE = 50;
export type ProjectSource =
  | { type: "blank" }
  | { type: "template"; template_id: string }
  // The files come next, as an archive (POST /projects/{id}/imports).
  | { type: "upload" };
export type CreateProjectInput = { name: string; description: string | null; source: ProjectSource };

export async function listProjects(filter: ProjectListFilter, signal?: AbortSignal): Promise<ProjectPage> {
  const params = new URLSearchParams({
    status: filter.status,
    page: String(filter.page),
    page_size: String(PROJECTS_PAGE_SIZE),
  });
  if (filter.q.trim()) params.set("q", filter.q.trim());
  const result = await apiRequest(`/projects?${params.toString()}`, {
    schema: z.array(ProjectSchema),
    ...(signal ? { signal } : {}),
  });
  const meta = PageMetaSchema.safeParse(result.meta);
  if (!meta.success) {
    // Without the total, a later page would silently look like the end of the list.
    throw new ApiError({
      kind: "bad_response",
      message: `List meta does not match the expected shape: ${meta.error.message}`,
      requestId: result.requestId,
    });
  }
  return { items: result.data, meta: meta.data };
}

export async function listTemplates(signal?: AbortSignal): Promise<Template[]> {
  const result = await apiRequest("/projects/templates", {
    schema: z.array(TemplateSchema),
    ...(signal ? { signal } : {}),
  });
  return result.data;
}

export async function createProject(input: CreateProjectInput, idempotencyKey: string): Promise<Project> {
  const body = { name: input.name, description: input.description, source: input.source };
  return (await apiRequest("/projects", { method: "POST", body, schema: ProjectSchema, idempotencyKey }))
    .data;
}

/** Archives the import accepts (FR-015, ADR-005): checked before upload, and again by the server. */
export const ARCHIVE_MAX_BYTES = 200 * 1024 * 1024;
export const ARCHIVE_SUFFIXES = [".zip", ".tar", ".tar.gz", ".tgz"] as const;

const RepositorySchema = ProjectSchema.shape.repository.unwrap();
export const ImportAcceptedSchema = z.object({
  repository: RepositorySchema,
  job_id: z.guid(),
  upload: z.object({ filename: z.string(), size: z.number().int().nonnegative(), sha256: z.string() }),
});
export type ImportAccepted = z.infer<typeof ImportAcceptedSchema>;

export async function getProject(projectId: string, signal?: AbortSignal): Promise<Project> {
  const result = await apiRequest(apiPath("projects", projectId), {
    schema: ProjectSchema,
    ...(signal ? { signal } : {}),
  });
  return result.data;
}

/** Streams the archive to the project (202); the import itself runs in a worker. */
export async function uploadArchive(
  projectId: string,
  file: File,
  options: {
    idempotencyKey: string;
    onProgress?: (sent: number, total: number) => void;
    signal?: AbortSignal;
  },
): Promise<ImportAccepted> {
  const result = await apiUpload(apiPath("projects", projectId, "imports"), {
    file,
    schema: ImportAcceptedSchema,
    idempotencyKey: options.idempotencyKey,
    ...(options.onProgress ? { onProgress: options.onProgress } : {}),
    ...(options.signal ? { signal: options.signal } : {}),
  });
  return result.data;
}

export async function setProjectArchived(projectId: string, archived: boolean): Promise<Project> {
  const path = apiPath("projects", projectId, archived ? "archive" : "unarchive");
  return (await apiRequest(path, { method: "POST", schema: ProjectSchema })).data;
}
