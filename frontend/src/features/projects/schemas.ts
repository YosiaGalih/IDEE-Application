import { z } from "zod";
import { ARCHIVE_MAX_BYTES, ARCHIVE_SUFFIXES } from "@/api/projects";
import type id from "@/i18n/locales/id.json";

/** Every message is a key of `projects.errors`; a key added to id.json and not here is a typecheck error. */
export type ProjectErrorKey = `projects.errors.${keyof (typeof id)["projects"]["errors"]}`;
const KEYS: Record<ProjectErrorKey, true> = {
  "projects.errors.nameRequired": true,
  "projects.errors.nameTooLong": true,
  "projects.errors.nameInvalid": true,
  "projects.errors.descriptionTooLong": true,
  "projects.errors.templateRequired": true,
  "projects.errors.fieldRejected": true,
  "projects.errors.archiveRequired": true,
  "projects.errors.archiveTooLarge": true,
  "projects.errors.archiveType": true,
};
export const isProjectErrorKey = (value: string | undefined): value is ProjectErrorKey =>
  value !== undefined && Object.hasOwn(KEYS, value);

const message = (key: ProjectErrorKey) => key;
const NAME_MAX = 120;
const DESCRIPTION_MAX = 2000;
/** Characters as the backend counts them (Python `len`: code points), not UTF-16 units. */
const length = (value: string) => [...value].length;
/** Controls, format characters (bidi overrides, zero width) and line/paragraph separators: a name holding
 * them can display as another project's name. The backend refuses the same categories. */
const HIDDEN = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u;

/** The backend's rules (services/projects.py): 1-120 characters after trim, nothing hidden. */
export const newProjectSchema = z
  .object({
    name: z
      .string()
      .trim()
      .min(1, message("projects.errors.nameRequired"))
      .refine((value) => length(value) <= NAME_MAX, message("projects.errors.nameTooLong"))
      .refine((value) => !HIDDEN.test(value), message("projects.errors.nameInvalid")),
    description: z
      .string()
      .refine((value) => length(value) <= DESCRIPTION_MAX, message("projects.errors.descriptionTooLong")),
    source: z.enum(["blank", "template", "archive"]),
    templateId: z.string(),
    archive: z.instanceof(File).nullable(),
  })
  .refine((values) => values.source !== "template" || values.templateId !== "", {
    message: message("projects.errors.templateRequired"),
    path: ["templateId"],
  })
  .superRefine((values, ctx) => {
    if (values.source !== "archive") return;
    const file = values.archive;
    const issue = (key: ProjectErrorKey) => ctx.addIssue({ code: "custom", message: key, path: ["archive"] });
    if (file === null) issue("projects.errors.archiveRequired");
    else if (!isArchiveName(file.name)) issue("projects.errors.archiveType");
    else if (file.size > ARCHIVE_MAX_BYTES) issue("projects.errors.archiveTooLarge");
  });

/** The server decides by the file's bytes; this only spares an upload that would be refused. */
export const isArchiveName = (name: string) =>
  ARCHIVE_SUFFIXES.some((suffix) => name.toLowerCase().endsWith(suffix));
export type NewProjectValues = z.infer<typeof newProjectSchema>;

/** A server VALIDATION_ERROR on a field, shown on that field. */
export const SERVER_FIELD_ERRORS: Partial<
  Record<string, { field: keyof NewProjectValues; key: ProjectErrorKey }>
> = {
  // The server's reason is not translated: say it refused the value, never guess which rule it broke.
  name: { field: "name", key: "projects.errors.fieldRejected" },
  description: { field: "description", key: "projects.errors.fieldRejected" },
  source: { field: "templateId", key: "projects.errors.templateRequired" },
};
