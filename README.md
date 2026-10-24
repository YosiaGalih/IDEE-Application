# KodeKantor Platform

Platform kerja self-hosted dengan tiga portal (IDE agentik, Portal AI, Portal Portfolio) di atas satu core. Produk ini dibangun oleh tim delivery agentik dengan gate persetujuan manusia.

> **Status (24 Okt 2026): rancangan, belum ada kode produk.** Repo berisi PRD v2.2, dokumen turunannya, dan konfigurasi pengembangan. Kode "snapshot" yang dirujuk dokumen tidak termasuk di repo ini. Lihat [rancangan sistem pengembangan](docs/superpowers/specs/2026-10-24-development-system-design.md) §1 dan §7.

## Peta dokumen

| Mulai dari | Isi |
|---|---|
| [`docs/PRD_v2.2.md`](docs/PRD_v2.2.md) | PRD terbaru (M01–M30, FR-001..292, P0–P16) |
| [`docs/superpowers/specs/2026-10-24-development-system-design.md`](docs/superpowers/specs/2026-10-24-development-system-design.md) | Cara repo ini dikembangkan, konflik dokumen, pertanyaan terbuka |
| [`CLAUDE.md`](CLAUDE.md) | Aturan kerja agent dan alur skill |
| [`GLOSSARY.md`](GLOSSARY.md) | Bahasa domain |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md), [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md), [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md) | Kontrak teknis |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | ADR-001..068 |
| [`docs/wbs/`](docs/wbs/) | WBS agentik: 137 WP, gate G0–G16 |
| [`docs/agents/`](docs/agents/) | Tracker, label, domain docs, roster delivery agent |

Peta lengkap: PRD v2.2 Lampiran C.

## Lingkungan pengembangan Claude Code

`.claude/settings.json` mengaktifkan plugin **Superpowers** dan **mattpocock/skills**. Saat repo dibuka, Claude Code menawarkan pemasangan marketplace-nya. Untuk memasang manual (mis. di setup script environment cloud):

```bash
claude plugin marketplace add obra/superpowers-marketplace
claude plugin install superpowers@superpowers-marketplace
claude plugin marketplace add mattpocock/skills
claude plugin install mattpocock-skills@mattpocock
```

Reviewer dan rules yang diadaptasi dari **ECC** sudah ada di `.claude/agents/` dan `.claude/rules/`, sehingga plugin ECC tidak perlu dipasang (alasannya di spec §4.2).
