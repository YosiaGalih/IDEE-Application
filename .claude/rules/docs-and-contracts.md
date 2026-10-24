---
paths:
  - "docs/**"
  - "agents/**"
---
# Dokumen kontrak, registry, dan WBS

- Dokumen kontrak (`ARCHITECTURE`, `API_CONTRACTS`, `DATA_MODEL`, `DECISIONS`, `TOOL_CATALOG`, `CONFIGURATION`, `IMPLEMENTATION_TRACKER`) dimiliki Lead (AG-00). Agent lain mengusulkan perubahan di laporannya.
- ID tidak pernah digeser atau dipakai ulang: FR-xxx, AC/AX, UC, NFR, T, ADR, WP, gate. Perubahan isi FR dicatat di `docs/DECISIONS.md`.
- ADR baru ditambahkan di `docs/DECISIONS.md` dengan nomor berikutnya (lihat `docs/agents/domain.md`).
- Jangan menomori ulang `SECURITY_MODEL.md` §4 dan §7; `ARCHITECTURE.md` merujuknya.
- PRD versi lama (`PRD.md`, `PRD_v2.md`, `PRD_v2.1.md`, `PRD_source.txt`) tidak diedit; v2.2 menambah, tidak menimpa.
- Status di tracker hanya berubah dengan bukti (perintah + output). Klaim tanpa output ditulis sebagai `[Menebak]` atau tidak ditulis.
- `agents/registry.v2.yaml` adalah data runtime agent (produk), bukan konfigurasi Claude Code. Perubahan registry mengikuti `docs/AGENT_CATALOG.md` §11: validasi, naikkan `version`, review AG-13 untuk perluasan izin, catat di `DECISIONS.md`. `approval_grant` selalu `deny`.
- Workbook WBS dibangkitkan dari `docs/wbs/wbs_data.py` lewat `docs/wbs/build_wbs.py`; ubah sumber datanya, bukan file `.xlsx`.
