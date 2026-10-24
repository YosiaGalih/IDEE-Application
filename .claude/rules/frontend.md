---
paths:
  - "frontend/**/*.ts"
  - "frontend/**/*.tsx"
  - "tests/e2e/**/*.ts"
---
# Frontend (React 19 · TypeScript strict · Vite)

Kontrak: PRD v2.2 §4 (UX) dan §8.1 (stack), `docs/ARCHITECTURE.md` §3.1b (route), `docs/IMPLEMENTATION_DESIGN_V2.md` §4.2/§4.4, `docs/API_CONTRACTS.md`.

## Stack dan dependensi

- React Router 7, TanStack Query 5, Tailwind CSS 4 + Radix (gaya shadcn/ui), Lucide, Monaco dan xterm.js lokal, React Hook Form + Zod, Zustand hanya bila state harus melewati route, i18next.
- Versi **exact pin** di `package.json` (tanpa `^`) + `package-lock.json`; npm. Library baru: rilis ≥ 2 minggu, `npm audit` bersih.
- Tidak ada CDN pihak ketiga (CSP `default-src 'self'`): Monaco, pdf.js worker, font, dan grammar Shiki dibundel lokal.

## Struktur

- Fitur di `src/features/<fitur>`; satu fitur tidak mengimpor fitur lain (ESLint `no-restricted-imports`). Klien API, hook, dan SSE di `src/api/`.
- `new WebSocket` hanya di `src/api/ws/terminal.ts`.
- Komponen: function component, props bertipe eksplisit (`type Props`), event handler `handleX` / prop `onX`, boolean `isX`/`hasX`. Hindari `any`; data eksternal masuk sebagai `unknown` lalu divalidasi Zod.

## Keamanan dan konten AI

- Konten agent, web, atau repo dirender lewat `react-markdown` + `remark-gfm` + `rehype-sanitize`; kode lewat Shiki `codeToHast`. `dangerouslySetInnerHTML` tidak dipakai sama sekali (lint `react/no-danger`).
- URL dari data divalidasi skemanya (`http`, `https`, `mailto`); link `target="_blank"` memakai `rel="noopener noreferrer"`.
- Sesi memakai cookie HttpOnly dari server; token tidak disimpan di `localStorage`. Tidak ada secret di variabel `VITE_*`.
- Aksi keluar (apply, push, PR, publish) hanya lewat dialog approval yang menampilkan payload dan hash-nya.

## UX wajib (PRD §4.1–§4.2, NFR-21)

- Setiap layar punya state normal, loading, empty, error, timeout, disconnected, permission denied, partial success, plus v2: kuota habis, capability nonaktif, provider degraded.
- Status selalu teks + ikon, bukan warna saja. Semua teks UI lewat kunci i18n (default `id`); tidak ada teks hard-coded.
- Output AI dipisah secara visual dari bukti tool; konten bertanda AI diberi penanda sampai disetujui; angka biaya berlabel `aktual`/`estimasi`/`resmi`/`tidak tersedia`.

## Test

- Vitest + RTL per fitur (state normal/loading/empty/error/permission denied), hook API dengan MSW, hook SSE untuk reconnect + dedupe.
- Playwright di `tests/e2e/` dengan stack scripted; `@axe-core/playwright` tanpa pelanggaran serious/critical.
