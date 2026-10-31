import { describe, expect, it } from "vitest";
import { z } from "zod";
import { zodResolver } from "@/lib/zodResolver";

const options = { fields: {}, shouldUseNativeValidation: false } as const;

describe("zodResolver", () => {
  it("returns the parsed values when the schema accepts them", async () => {
    const resolve = zodResolver(z.object({ name: z.string().trim() }));
    expect(await resolve({ name: "  a  " }, undefined, options)).toEqual({
      values: { name: "a" },
      errors: {},
    });
  });

  it("nests errors the way React Hook Form reads them, first issue per field", async () => {
    const schema = z.object({
      address: z.object({ street: z.string().min(1, "street.required").min(3, "street.short") }),
      tags: z.array(z.string().min(1, "tag.required")),
    });
    const result = await zodResolver(schema)(
      { address: { street: "" }, tags: ["ok", ""] },
      undefined,
      options,
    );
    expect(result.errors).toEqual({
      address: { street: { type: "too_small", message: "street.required" } },
      tags: [undefined, { type: "too_small", message: "tag.required" }],
    });
  });

  it("puts an issue on the whole object under root", async () => {
    const schema = z.object({ a: z.string(), b: z.string() }).refine((v) => v.a !== v.b, { message: "same" });
    const result = await zodResolver(schema)({ a: "x", b: "x" }, undefined, options);
    expect(result.errors).toEqual({ root: { type: "custom", message: "same" } });
  });
});
