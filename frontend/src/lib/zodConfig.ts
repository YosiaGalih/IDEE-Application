import { z } from "zod";

// Imported first by main.tsx: Zod reads this when a schema is built, i.e. while the API modules load.
// The CSP has no 'unsafe-eval'; without it Zod probes `new Function` and the browser reports a violation
// on every load (Zod falls back either way, so parsing is unchanged).
z.config({ jitless: true });
