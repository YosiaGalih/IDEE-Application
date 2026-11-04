/** Backend `language` (workspace/index.py `_LANGUAGES`) → Monaco language id; anything else is plain text. */
const MONACO_IDS = new Set([
  "python",
  "javascript",
  "typescript",
  "java",
  "go",
  "rust",
  "ruby",
  "php",
  "csharp",
  "kotlin",
  "swift",
  "c",
  "cpp",
  "html",
  "css",
  "scss",
  "json",
  "yaml",
  "markdown",
  "shell",
  "sql",
  "xml",
  "dockerfile",
  "ini",
]);
// Monaco has no TOML mode; INI highlights its keys, sections and strings closely enough.
const ALIASES: Record<string, string> = { toml: "ini" };

export function monacoLanguage(language: string | null): string {
  if (language === null) return "plaintext";
  const id = ALIASES[language] ?? language;
  return MONACO_IDS.has(id) ? id : "plaintext";
}
