import { z } from "zod";
import type id from "@/i18n/locales/id.json";

/** Every message is a key of `projects.errors`; a key added to id.json and not here is a typecheck error. */
export type ProjectErrorKey = `projects.errors.${keyof (typeof id)["projects"]["errors"]}`;
const KEYS: Record<ProjectErrorKey, true> = {
  "projects.errors.nameRequired": true,
  "projects.errors.nameTooLong": true,
  "projects.errors.nameInvalid": true,
  "projects.errors.descriptionTooLong": true,
  "projects.errors.templateRequired": true,
};
export const isProjectErrorKey = (value: string | undefined): value is ProjectErrorKey =>
  value !== undefined && Object.hasOwn(KEYS, value);

const message = (key: ProjectErrorKey) => key;
const NAME_MAX = 120;
const DESCRIPTION_MAX = 2000;
// eslint-disable-next-line no-control-regex -- refusing control characters is the point (backend rule)
const CONTROL = /[\u0000-\u001F\u007F]/;

/** The backend's rules (services/projects.py): 1-120 characters after trim, no control characters. */
export const newProjectSchema = z
  .object({
    name: z
      .string()
      .trim()
      .min(1, message("projects.errors.nameRequired"))
      .max(NAME_MAX, message("projects.errors.nameTooLong"))
      .refine((value) => !CONTROL.test(value), message("projects.errors.nameInvalid")),
    description: z.string().max(DESCRIPTION_MAX, message("projects.errors.descriptionTooLong")),
    source: z.enum(["blank", "template"]),
    templateId: z.string(),
  })
  .refine((values) => values.source === "blank" || values.templateId !== "", {
    message: message("projects.errors.templateRequired"),
    path: ["templateId"],
  });
export type NewProjectValues = z.infer<typeof newProjectSchema>;

/** A server VALIDATION_ERROR on a field, shown on that field. */
export const SERVER_FIELD_ERRORS: Partial<
  Record<string, { field: keyof NewProjectValues; key: ProjectErrorKey }>
> = {
  name: { field: "name", key: "projects.errors.nameInvalid" },
  description: { field: "description", key: "projects.errors.descriptionTooLong" },
  source: { field: "templateId", key: "projects.errors.templateRequired" },
};
