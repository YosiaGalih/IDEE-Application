// Repository rules that no published plugin expresses (frontend.md, IMPLEMENTATION_DESIGN_V2 §4.4).
import path from "node:path";

const SRC = path.resolve(import.meta.dirname, "../src");
const FEATURES = path.join(SRC, "features");

function featureOf(file) {
  const relative = path.relative(FEATURES, file);
  if (relative.startsWith("..") || path.isAbsolute(relative)) return null;
  return relative.split(path.sep)[0] ?? null;
}

function resolveSource(source, fromFile) {
  if (source.startsWith("@/")) return path.join(SRC, source.slice(2));
  if (source.startsWith(".")) return path.resolve(path.dirname(fromFile), source);
  return null;
}

/** A feature imports api/, components/, lib/, i18n/, stores/ and itself, never another feature. */
const featureIsolation = {
  meta: {
    type: "problem",
    schema: [],
    messages: { crossFeature: "Feature '{{from}}' must not import feature '{{to}}'; move shared code out." },
  },
  create(context) {
    const file = context.filename;
    const from = featureOf(file);
    function check(node) {
      if (!from || !node.source || typeof node.source.value !== "string") return;
      const target = resolveSource(node.source.value, file);
      const to = target && featureOf(target);
      if (to && to !== from)
        context.report({ node: node.source, messageId: "crossFeature", data: { from, to } });
    }
    return {
      ImportDeclaration: check,
      ExportNamedDeclaration: check,
      ExportAllDeclaration: check,
      ImportExpression: check,
    };
  },
};

export default { rules: { "feature-isolation": featureIsolation } };
