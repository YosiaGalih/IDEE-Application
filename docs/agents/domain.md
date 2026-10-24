# Domain Docs

Cara skill engineering membaca dokumentasi domain repo ini. Layout: **single-context** (satu `GLOSSARY.md` di root). Seluruh platform berbagi satu bahasa domain, meskipun kodenya kelak terbagi ke `backend/`, `frontend/`, dan `sandbox_manager/`.

## Sebelum menjelajah, baca ini

- **`GLOSSARY.md`** di root.
- **`docs/DECISIONS.md`**: log ADR proyek (ADR-001..068). Baca ADR yang menyentuh area yang akan dikerjakan. ADR berstatus *Proposed* belum mengikat sampai diputuskan PO; perlakukan sebagai rancangan yang berlaku dan tandai bila pekerjaan bergantung padanya.
- **Kontrak teknis** untuk area yang disentuh: `docs/ARCHITECTURE.md`, `docs/API_CONTRACTS.md`, `docs/DATA_MODEL.md`, `docs/SECURITY_MODEL.md`, `docs/TOOL_CATALOG.md`, `docs/CONFIGURATION.md`, `docs/TEST_STRATEGY.md`.

## ADR di repo ini: `docs/DECISIONS.md`, bukan `docs/adr/`

Template skill mengasumsikan `docs/adr/NNNN-slug.md`. Repo ini sudah punya log ADR bernomor `ADR-001..068` yang dirujuk puluhan dokumen dan WBS, sehingga:

- ADR baru **ditambahkan ke `docs/DECISIONS.md`** dengan nomor berikutnya (ADR-069, ADR-070, …) dan format yang sama dengan entri yang ada (Konteks → Keputusan → Konsekuensi, plus status).
- Nomor ADR dialokasikan oleh Lead Architect (AG-00).
- `docs/adr/README.md` hanya berisi pointer ke `docs/DECISIONS.md`. Tidak ada file ADR terpisah.

## Pakai kosakata glossary

Saat output menamai konsep domain (judul issue, usulan refactor, hipotesis, nama test), pakai istilah dari `GLOSSARY.md`, termasuk pembedaan **runtime agent** vs **delivery agent**. Jangan beralih ke sinonim yang ditandai _Avoid_.

Bila konsep yang dibutuhkan belum ada di glossary, itu sinyal: bahasa baru yang tidak dipakai proyek (pertimbangkan ulang), atau celah nyata (catat untuk `/domain-modeling`).

## Tandai konflik ADR dan kontrak

Bila output bertentangan dengan ADR atau dokumen kontrak, tampilkan secara eksplisit, jangan ditimpa diam-diam:

> _Bertentangan dengan ADR-053 (worker per antrean), tetapi layak dibuka ulang karena…_

Perubahan kontrak memerlukan ADR baru. Perubahan pada `ARCHITECTURE.md`, `API_CONTRACTS.md`, `DATA_MODEL.md`, dan `DECISIONS.md` hanya dilakukan Lead Architect, atau dilaporkan eksplisit bila agent lain mengusulkannya.
