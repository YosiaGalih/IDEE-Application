# Mockup PRD v2.0 & v2.1 — KodeKantor

> **Mockup konseptual hi-fi diambil dari prototipe/replay KodeKantor (21 Okt 2026). Layar IDE berisi data eksekusi nyata; layar Portal AI, GitHub, dan Portfolio adalah prototipe dengan data contoh — bukan bukti implementasi.**

- Sumber: halaman *KodeKantor Replay* (`/home/claude/kodekantor-demo/kodekantor-replay.html`, versi 21 Okt 2026).
- Layar IDE berasal dari replay tiga eksekusi nyata (skenario *Alur normal*, *Debug loop*, *Remediasi gap*). Agent memakai provider *scripted* (bukan Claude); test berjalan di sandbox Docker nyata `kodekantor/sandbox-python:1`.
- Layar Portal AI, Integrasi GitHub, Portfolio, dan Peta Platform adalah **prototipe alur** dari `docs/PLATFORM_EXPANSION.md` (ADR-036..045, status *Proposed*). Isinya data contoh berlabel "DATA CONTOH"; fakta bernomor sitasi berasal dari dokumentasi resmi yang dibaca 21 Okt 2026.
- Cara ambil: viewport 1440×900, tema terang, `device_scale_factor` 2 (file 2880×1800 px). Pita atas REPLAY/PROTOTIPE, chip "prototipe" di pojok, dan tombol "Tentang demo/prototipe" disembunyikan **hanya saat capture** (CSS disuntik lewat Playwright); halaman yang dipublikasikan tidak diubah. Label "DATA CONTOH" di dalam konten tetap tampil.
- Skrip: `kodekantor-demo/capture_mockups.py` + `compose_figures.py` (v2.0, layar 01–24b, gambar 1–5) `capture_v21.py` + `compose_v21.py` (v2.1, layar 25–31b, gambar 6–7), dan `capture_v22.py` + `compose_v22.py` (v2.2, layar 32–37, gambar 8–9).

## Gambar gabungan

| File | Keterangan |
|---|---|
| `figure-1-portal-ide.png` | **Gambar 1. Portal IDE** — layar 01–11 dari replay eksekusi nyata. |
| `figure-2-portal-ai.png` | **Gambar 2. Portal Asisten AI** — layar 12–15 (prototipe, data contoh). |
| `figure-3-integrasi-github.png` | **Gambar 3. Integrasi GitHub (Portal IDE)** — layar 16–19 (prototipe, data contoh). |
| `figure-4-portal-portfolio.png` | **Gambar 4. Portal Portfolio** — layar 20–23 (prototipe, data contoh). |
| `figure-5-peta-platform.png` | **Gambar 5. Peta Platform** — layar 24 dan detail 24b (rancangan, Proposed). |
| `figure-6-editor-kode.png` | **Gambar 6. Editor kode & test nyata** — layar 25, 27, 28, 29 (PRD v2.1; file rekaman nyata + eksekusi Pyodide di browser). |
| `figure-7-sdlc-skala.png` | **Gambar 7. Siklus SDLC & skala S/M/L** — layar 30, 26, 31, 31b (SDLC dari bukti nyata; tier = rancangan). |
| `figure-8-monitoring-biaya-token.png` | **Gambar 8. Monitoring biaya & token realtime** — layar 32–35 (PRD v2.2; event usage dari rekaman nyata, provider scripted → biaya tidak tersedia; tarif simulasi bukan harga Claude). |
| `figure-9-polyglot-integrasi.png` | **Gambar 9. Language packs & lingkungan integrasi** — layar 36–37 (rancangan, data contoh). |

## Layar individual

### Portal IDE (data eksekusi nyata)
| File | Keterangan |
|---|---|
| `screen-01-login.png` | Login: panel brand dengan alur agent dan form masuk. |
| `screen-02-dashboard.png` | Dashboard: metrik setelah 3 eksekusi terekam, task terbaru, penggunaan model (biaya tidak tersedia), aktivitas agent, kartu Platform. |
| `screen-03-project-baru.png` | Project Baru: template *API Service* dan pilihan eksekusi terekam (tab "Dari template"). |
| `screen-04-workspace.png` | Workspace/IDE saat replay berjalan (skenario *Alur normal*, test sedang dijalankan): explorer worktree, editor, chat agent dengan bukti tool, terminal sandbox. |
| `screen-05-ai-agents.png` | Katalog 42 AI Agents dengan filter mode dan status. |
| `screen-06-task-rencana.png` | Detail task — Rencana: requirement, kriteria penerimaan, langkah rencana, rencana test. |
| `screen-07-task-ringkasan.png` | Detail task — Ringkasan & workflow: status akhir, riwayat status, pipeline. Menggantikan layar *Preview & Result* PRD v1 karena replay tidak merekam preview aplikasi. |
| `screen-08-testing.png` | Testing & debugging (skenario *Debug loop*): 9 run, 3 gagal; drill-down kasus gagal `assert 7 == 12`, lalu run berikutnya lulus. |
| `screen-09-review-diff.png` | Review & diff: temuan review (0), diff branch per file dengan nomor baris lama/baru. |
| `screen-10-user-proxy.png` | Validasi User Proxy (skenario *Remediasi gap*): iterasi 1 *Perlu perbaikan* dengan AC-2 gagal dan gap, iterasi 2 *Diterima*. Berlabel "Rekomendasi User Proxy bukan persetujuan Anda". |
| `screen-11-approval.png` | Halaman Persetujuan: kartu `apply_changes` R3 tertunda dengan branch, commit, diff, kriteria, hash payload. |

### Portal Asisten AI (prototipe, data contoh)
| File | Keterangan |
|---|---|
| `screen-12-ai-riset-sumber.png` | Riset web bersitasi: catatan "Bukan output model", jawaban contoh dengan nomor sitasi, panel sumber terbuka (judul, URL, tanggal baca, ringkasan sumber). |
| `screen-13-ai-buat-dokumen.png` | Buat dokumen: outline contoh, pilihan engine, pratinjau 4 slide HTML ("file .pptx belum dihasilkan"). |
| `screen-14-ai-buat-gambar.png` | Buat gambar: form, checklist moderasi, dan keadaan kosong "Provider gambar belum dikonfigurasi (ADR-040a) — Claude tidak menghasilkan gambar". Tidak ada gambar hasil. |
| `screen-15-ai-tanya-jawab.png` | Tanya jawab: thread kosong dengan composer dan label kebijakan "Tanpa sumber web". |

### Integrasi GitHub di Portal IDE (prototipe, data contoh)
| File | Keterangan |
|---|---|
| `screen-16-github-izin.png` | Langkah 1 · pasang GitHub App: tabel izin minimum (Workflows: tidak diminta) dan dua kutipan verbatim GitHub Docs. |
| `screen-17-github-pilih-repo.png` | Langkah 2 · pilih repo contoh (privat/publik). |
| `screen-18-github-import.png` | Langkah 3 · import: token 1 jam khusus repo, clone tanpa token di URL, deteksi stack, index — semua bertanda "simulasi". |
| `screen-19-github-push-pr.png` | Kartu persetujuan prototipe "Push & buka PR" (R3) dengan branch dan head commit dari rekaman, tabel risiko operasi, fakta webhook HMAC. |

### Portal Portfolio (prototipe, data contoh)
| File | Keterangan |
|---|---|
| `screen-20-portfolio-profil.png` | Profil: pengalaman dengan kolom pencapaian kosong ("AI hanya membantu menyusun draf, tidak mengarang"), pendidikan, keahlian. |
| `screen-21-portfolio-proyek.png` | Proyek: KodeKantor (data nyata, tautan ke replay IDE) dan dua slot contoh. |
| `screen-22-portfolio-cv.png` | CV ramah ATS satu kolom sans-serif; tombol unduh belum diimplementasikan. |
| `screen-23-portfolio-situs-publik.png` | Situs publik: pratinjau dan kartu persetujuan publikasi R3 dengan cek privasi; "Prototipe: tidak ada yang dipublikasikan". |

### Peta Platform (rancangan)
| File | Keterangan |
|---|---|
| `screen-24-peta-platform.png` | Tiga portal di atas satu core: diagram arsitektur (garis putus-putus = belum ada/rancangan). |
| `screen-24b-peta-platform-detail.png` | Detail: empat kebenaran yang tidak nyaman, capability registry (ADR-044), awal fase P9–P12. |

### Developer Workspace v2, SDLC, dan skala (PRD v2.1, diambil 22 Okt 2026 — `kodekantor-demo/capture_v21.py`)
| File | Keterangan |
|---|---|
| `screen-25-editor-kode.png` | Editor kode: file project direkonstruksi dari patch commit rekaman (sha256 cocok 3/3) + 7 file template dari repo rekaman; tombol Jalankan test/project, Simulasikan perubahan agent. |
| `screen-26-editor-diff.png` | Diff vs versi rekaman setelah editan manual. |
| `screen-27-editor-konflik.png` | Dialog 409 VERSION_CONFLICT dengan kolom Milikmu / Server / Basis dan opsi gabungkan 3-way (simulasi di memori). |
| `screen-28-test-pyodide-lulus.png` | Test dijalankan di browser lewat Pyodide (label "bukan sandbox Docker KodeKantor"): 11/11 lulus, cocok dengan run nyata b8a1fe83. |
| `screen-29-test-pyodide-gagal.png` | Editan `a + b` → `test_multiply` FAILED `assert 7 == 12`, ditandai "berbeda dengan rekaman". |
| `screen-30-siklus-sdlc.png` | Papan SDLC 8 fase untuk tiga eksekusi terekam; fase tanpa bukti ditandai "Sebagian"/"Tidak ada di rekaman". |
| `screen-31-skala-sml.png` | Tier S/M/L (rancangan, belum diimplementasikan). |
| `screen-31b-skala-arsitektur.png` | Arsitektur tier M/L (rancangan, ADR-060). |

### Monitoring biaya & token, language packs, lingkungan integrasi (PRD v2.2, diambil 23 Okt 2026 — `kodekantor-demo/capture_v22.py`)
| File | Keterangan |
|---|---|
| `screen-32-monitoring-aliran-langsung.png` | Aliran langsung lingkup *Remediasi gap* dijeda di 14/27 panggilan (64.246 token); kartu ringkasan berlabel ESTIMASI (SCRIPTED) / AKTUAL / TIDAK TERSEDIA; budget demo 100.000 → alert 50% terlampaui. |
| `screen-33-monitoring-simulasi-budget.png` | Setelah 27/27 panggilan (127.908 token = `usage_totals` rekaman): simulasi tarif 3/15 USD per juta token yang diisi pengguna (US$0,4071, berlabel "bukan harga Claude") dan alert 50/80/100%. |
| `screen-34-monitoring-event-breakdown.png` | Tabel event usage (terbaru di atas) + breakdown per skenario, agent, model; kolom cek "cocok" terhadap `usage_totals` dan `agent_runs`. |
| `screen-35-monitoring-rekonsiliasi.png` | Panel rekonsiliasi Usage & Cost Admin API: "tidak tersedia di demo" beserta alasan dan tabel sumber angka (§3.1). |
| `screen-36-language-packs.png` | Tabel 11 pack gelombang 1 (7 L1, 4 L2), target di luar gelombang 1, proxy dependensi (rancangan). |
| `screen-37-lingkungan-integrasi.png` | Diagram contoh lingkungan integrasi: web-bff (node), orders (jvm), payments (go), PostgreSQL, Kafka di network tanpa egress; aturan ADR-065 (rancangan, data contoh). |

