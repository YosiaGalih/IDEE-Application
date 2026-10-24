# KodeKantor Platform — Product Requirements Document (PRD v2.1)

> **Status dokumen: RANCANGAN v2.1 (22 Oktober 2026) — belum diimplementasikan.** PRD v2.1 melanjutkan PRD v2.0 (`docs/PRD_v2.md`, tetap utuh) dan PRD v1.0 (`docs/PRD.md`, baseline M01–M20). Platform tiga portal — **Portal IDE**, **Portal AI**, **Portal Portfolio** — dengan integrasi **GitHub App**, ditambah v2.1: **Portal IDE untuk SDLC end-to-end dari project kecil sampai mega project enterprise (tier S/M/L)** dan **Developer Workspace v2** agar pengguna dapat mengedit, menjalankan, menguji, dan men-debug kode sendiri. Seluruh ADR v2/v2.1 berstatus *Proposed* sampai diputuskan Product Owner (sebelum atau di WP-9.0 / WP-13.0).
>
> **Label keyakinan:** **[Pasti]** = didukung dokumentasi resmi atau bukti eksekusi; **[Kemungkinan Besar]** = inferensi kuat; **[Menebak]** = mengisi celah informasi, wajib divalidasi. Target angka adalah sasaran pilot, bukan klaim performa.

---

## Kontrol Dokumen

| Field | Nilai |
|---|---|
| Nama produk | KodeKantor Platform — Self-Hosted Agentic IDE, Portal AI & Portfolio |
| Versi dokumen | 2.1 (rancangan) |
| Tanggal | 22 Oktober 2026 |
| Status | Draft untuk review Product Owner; ADR v2 *Proposed* |
| Pemilik produk | Product Owner (Tira) |
| Penyusun | Lead Architect (AG-00) bersama tim agent dokumentasi; review independen |
| Lingkungan | Tier S: VPS Linux mandiri, single-node, Docker Compose (pilot); tier M/L: node kontrol + node executor (rancangan P13–P14) |
| Runtime AI | Claude Platform API (teks, riset web, dokumen, KB, code execution); vendor gambar lewat adapter netral (ADR-040a) |
| Bahasa | Indonesia |
| Dokumen turunan | `SYSTEM_SPEC.md` (SRS v2 + §12 v2.1), `FR_CATALOG.md` (FR-001..FR-258), `IMPLEMENTATION_DESIGN_V2.md` (+§13 v2.1), `AGENT_CATALOG.md` §12–§13, `agents/registry.v2.yaml`, `API_CONTRACTS.md` §12–§13, `DATA_MODEL.md` (Ekstensi v2, V2.9–V2.10), `DECISIONS.md` (ADR-001..062), `SDLC_SCALE_DESIGN.md` (KONTRAK v2.1), WBS `docs/wbs/` |

### Riwayat versi

| Versi | Tanggal | Ringkasan |
|---|---|---|
| 1.0 | 21 Okt 2026 | Baseline PRD IDE agentik self-hosted: 20 modul, 41 specialist + Orchestrator, AC-01..AC-20, Phase 0–8 |
| 2.0 (rancangan) | 21 Okt 2026 | Tiga portal di atas satu core; modul M21–M24 (FR-160..FR-220); 11 agent baru + promosi E29 (53 entri registry); tech stack v2; mockup hi-fi; AX-01..AX-11; fase P9–P12; ADR-036..056 |
| 2.1 (rancangan) | 22 Okt 2026 | SDLC end-to-end 8 fase; hierarki Program → Epic → Story → Task + merge queue; Developer Workspace v2 (edit, take-over, run, test, terminal); tier S/M/L; modul M25–M27 (FR-221..FR-258); AX-12..AX-18; NFR-23..27; ADR-057..062; fase P13–P14; mockup editor kode, test Pyodide, SDLC, skala |

### Kebenaran yang perlu diketahui lebih dulu

1. **Perluasan v2 belum punya satu baris kode produk.** Portal IDE pun masih *snapshot* yang belum terintegrasi end-to-end (backend 1316 test lulus, 2 gagal; belum ada E2E Playwright, readiness gate Claude dengan key nyata, atau staging). Fase P9–P12 hanya boleh dimulai setelah gate G3. [Pasti]
2. **Claude tidak menghasilkan foto/ilustrasi.** Generate gambar wajib memakai vendor kedua lewat `ImageProvider`; vendor belum dipilih (ADR-040a). [Pasti — Claude Help Center]
3. **Anthropic tidak menyediakan model embedding.** Permintaan "semua AI lewat Claude API" berarti tanya jawab dokumen memakai retrieval leksikal PostgreSQL + ringkasan kontekstual Claude + blok `search_result` bersitasi (ADR-046), bukan vector search. Recall pada sinonim lebih rendah dan harus diukur (ambang Recall@8 ≥ 0,85 di WP-11.3). [Pasti — dokumentasi Embeddings; ambang = Menebak]
4. **Agent Skills dan Files API tidak tercakup Zero Data Retention.** Data berlabel `confidential` wajib diproses engine lokal di sandbox. [Pasti]
5. **Scope bertambah ±3×.** Menjalankan tiga portal paralel sebelum IDE stabil mengulang risiko R-06; urutan wajib P9 → P10, lalu P11 dan P12 setelah G10. [Kemungkinan Besar]
6. **Arsitektur pilot tidak melayani mega project.** Pilot = 1 VPS, 2 task aktif, single-owner. Skala tim dan enterprise membutuhkan profil deployment lain (node executor, SSO, tenancy, HA/DR) yang dirancang sebagai tier M/L (P13–P14); satu basis kode, tier = konfigurasi + topologi. [Pasti — ADR-010, ADR-060]
7. **Agent tidak menyelesaikan mega project sendirian.** Pekerjaan dipecah Program → Epic → Story → Task; setiap task tetap tervalidasi bukti dan diputuskan manusia. Pada skala besar, kecepatan dibatasi review manusia dan merge queue. [Kemungkinan Besar]
8. **Edit kode manual sudah P0 sejak v1** (M05, FR-026..032); v2.1 menambah operasi file lengkap, take-over worktree agent, run/test oleh user, dan terminal interaktif (default nonaktif, ADR-059). Demo live menjalankan test hasil editan di browser lewat Pyodide — itu **bukan** sandbox KodeKantor. [Pasti]

---

## Daftar Isi

0. Ringkasan perubahan v1.0 → v2.0 → v2.1
1. Ringkasan eksekutif, visi, tujuan, indikator keberhasilan, non-goals
2. Persona dan use case (UC-01..UC-24)
3. Alur end-to-end (termasuk SDLC 8 fase dan edit manual)
4. Desain mockup dan spesifikasi UX
5. Modul aplikasi dan kebutuhan fungsional (M01–M27, FR-001..FR-258)
6. Katalog agent v2 (53 entri)
7. Arsitektur sistem (termasuk tier S/M/L)
8. Tech stack v2
9. Data model
10. API surface
11. Keamanan, isolasi, dan approval
12. Non-functional requirements
13. Acceptance criteria dan strategi uji
14. Roadmap implementasi dan delivery agentik (P0–P14)
15. Risiko, asumsi, dan keputusan terbuka
16. Ringkasan ADR
17. Glosarium
18. Referensi
Lampiran A — Traceability ringkas · Lampiran B — Galeri mockup · Lampiran C — Peta dokumen repo

---

## 0. Ringkasan Perubahan v1.0 → v2.0 → v2.1

| Area | PRD v1.0 | PRD v2.0 | Rujukan |
|---|---|---|---|
| Lingkup produk | IDE agentik self-hosted | Platform tiga portal (IDE · AI · Portfolio) di atas satu core *modular monolith* | ADR-036 |
| Modul | M01–M20 | + M21 Portal AI, M22 Integrasi GitHub App, M23 Portal Portfolio, M24 Capability Registry & Provider Gateway | §5 |
| Kebutuhan fungsional | FR-001..FR-159 (turunan) | + FR-160..FR-220 (61 FR) | `FR_CATALOG.md` §7 |
| Agent | 41 specialist + Orchestrator (42 entri) | + 11 agent baru (I42–I47, J48, K49–K51, S52) + promosi E29 ke `llm` fase P9 → 53 entri | ADR-048, §6 |
| Remote Git | Remote/PR P2 lewat credential generik (ADR-003) | GitHub App: repo privat terpilih, token per operasi ≤ 1 jam, push/PR = R3 | ADR-041, ADR-056 |
| Provider | Claude untuk agent LLM | Provider gateway per capability; Claude utama (teks, riset web, dokumen, KB, code execution); vendor gambar lewat adapter | ADR-037..040 |
| Pengetahuan | Index repo tanpa vector | KB dokumen pengguna tanpa embedding (FTS + `pg_trgm` + `search_result`) | ADR-046 (menggantikan ADR-043) |
| Bentuk jawaban agent | `submit_result` terstruktur | Agent bersitasi mengakhiri run dengan teks bersitasi | ADR-047 |
| Approval | `apply_changes`, `budget_increase`, `tool_r3`, `project_delete`; P2: `git_push`, `deploy_*` | + `github_push_pr`, `portfolio_publish`; `ApprovalGrant` sekali pakai; scope approval non-project | ADR-052, ADR-056 |
| Runtime | 1 worker Celery | Worker per antrean (`ide`, `assistant`, `media`, `integrations`) dengan secret per role | ADR-053 |
| Sandbox | `sandbox-python`, `sandbox-node` | + `kodekantor/sandbox-docs` dan workspace job `_jobs/<uuid>` | ADR-054 |
| Kriteria penerimaan | AC-01..AC-20 | + AX-01..AX-11 | §13 |
| Roadmap | Phase 0–8 | + P9 GitHub, P10 Portal AI inti, P11 Gambar/KB/Analisis, P12 Portfolio | §14 |
| Mockup | Gambar mockup statis PRD v1 | Mockup hi-fi dari prototipe/replay (25 layar, 5 gambar gabungan) | §4, Lampiran B |
| **v2.1** — SDLC | Task tunggal → apply | SDLC 8 fase dengan bukti per fase; Program → Epic → Story → Task; merge queue per repo; release train; traceability end-to-end | ADR-057, §3.5 |
| **v2.1** — edit manual | Edit + simpan dengan version check (FR-026..032), terminal read-only | Buat/rename/hapus file, take-over worktree task, run & test oleh user, format/lint on save, terminal interaktif sandbox (opsional), Git manual; LSP/debugger P2 | ADR-058, ADR-059, §3.6 |
| **v2.1** — skala | 1 VPS, 2 task aktif | Tier S (pilot) / M (tim, node executor, OIDC) / L (enterprise: SAML/SCIM, tenant, microVM, HA/DR, repo ≥ 1 juta LOC) | ADR-060..062, §7.9 |
| **v2.1** — modul & FR | M01–M24, FR-001..220 | + M25, M26, M27; FR-221..FR-258 (38 FR) | §5.6–5.9 |
| **v2.1** — roadmap | P0–P12 | + P13 (SDLC & tier M, gate G13), P14 (enterprise tier L, gate G14) | §14 |

---

## 1. Ringkasan Eksekutif

KodeKantor berkembang dari IDE agentik menjadi **platform kerja pribadi/tim yang self-hosted** dengan tiga ruang kerja dalam satu aplikasi:

- **Portal IDE** — membuat, memahami, memodifikasi, menguji, mereview, dan memvalidasi software dengan specialist agent, sandbox, dan User Proxy; kini terhubung ke **repo GitHub privat** lewat GitHub App.
- **Portal AI** — tanya jawab, riset web bersitasi, pembuatan dokumen PPT/Word/Excel/PDF, tanya jawab atas dokumen sendiri, analisis data dengan kode yang dapat diperiksa, dan generate/edit gambar.
- **Portal Portfolio** — profil profesional, impor proyek dari GitHub dan KodeKantor, CV ramah ATS, asisten lamaran, dan situs publik yang dipublikasikan lewat approval.

Ketiganya berbagi satu core: autentikasi/RBAC/audit, Capability Registry, Provider Gateway, Tool Gateway + approval R0–R4, usage & budget, asset store, dan integrasi. Prinsip v1 tidak berubah: **evidence over claims, satu pintu side effect, sandbox untuk semua eksekusi, approval hanya oleh manusia**.

> **North-star outcome v2**
> Setiap hasil — perubahan kode, jawaban riset, dokumen, gambar, atau publikasi portfolio — dapat ditelusuri ke bukti atau sumbernya, biayanya terlihat, aliran datanya transparan, dan setiap aksi yang keluar dari VPS hanya terjadi atas persetujuan pengguna.

### 1.1 Visi produk

Menjadi workspace AI milik pengguna sendiri — kode, pengetahuan, dan identitas profesionalnya — dengan kualitas setara layanan AI komersial untuk kemampuan yang didukung Claude Platform, tanpa menyerahkan kendali atas data, repository, dan aksi berisiko.

### 1.2 Tujuan produk

1. Mempertahankan seluruh tujuan v1 (lifecycle software development berbasis bukti, User Proxy, audit, biaya, approval).
2. Menghubungkan Portal IDE ke repo GitHub privat dengan permission minimum, tanpa token tersimpan, dan push/PR hanya lewat approval.
3. Menyediakan Portal AI dengan jawaban bersitasi yang dapat diverifikasi, dokumen siap pakai, dan analisis data berbasis eksekusi nyata.
4. Menyediakan Portal Portfolio yang membangun profil dan CV dari data nyata pengguna — AI hanya membuat draft, tidak mengarang.
5. Menjadikan setiap kemampuan sebagai **capability module** yang dapat diaktifkan, dibatasi budget-nya, dan dimatikan (kill switch) tanpa mengubah kode domain.
6. Menjaga transparansi aliran data per capability, termasuk pengungkapan bagian yang tidak tercakup ZDR.
7. Mendukung **SDLC end-to-end** — requirement, desain, implementasi, test, run/preview, review & acceptance, release, operate — dari project kecil sampai program enterprise, lewat tier deployment S/M/L tanpa mengubah kode domain.
8. Menjadikan IDE alat kerja penuh bagi developer manusia: **edit kode sendiri**, jalankan project & test, terminal, Git, dan mengambil alih (take-over) pekerjaan agent dengan aturan konflik yang jelas.

### 1.3 Indikator keberhasilan

Indikator v1 (task success rate, test pass evidence, requirement traceability, human approval coverage, reproducibility, cost visibility) tetap berlaku. Indikator v2:

| Metric | Definisi | Target pilot |
|---|---|---|
| Citation fidelity | Jawaban mode riset yang memuat klaim web dan punya sitasi tersimpan & tampil; sitasi yang benar-benar mendukung klaim | 100% punya sitasi; ≥ 90% sitasi mendukung klaim pada set uji [Menebak, ambang final PO di WP-10.6] |
| Label tanpa sumber | Jawaban tanpa `web_search` yang diberi label "Tanpa sumber web" | 100% |
| Kebocoran rahasia GitHub | Token instalasi/private key yang muncul di DB, log, event, artefak, `.git/config`, env sandbox | 0 kemunculan |
| Approval aksi keluar | Push, PR, dan publikasi portfolio yang melalui approval R3 terikat payload | 100% |
| Kepatuhan data-flow | Panggilan Skills/Files API untuk data `confidential` | 0 panggilan |
| Kualitas KB | Recall@8 pada set evaluasi KB | ≥ 0,85 [Menebak]; di bawah ambang → ADR `EmbeddingProvider` |
| Transparansi biaya | Panggilan provider/vendor tercatat dengan `cost_status`: token LLM di `usage_records` (+`capability`), unit non-token di `capability_usage` | 100% |
| Kejujuran portfolio | Konten AI belum disetujui yang terpublikasi | 0 |
| Integritas branch target | Commit di branch target (repo dengan merge queue aktif) yang tidak melewati test wajib | 0 |
| Konflik edit tanpa kehilangan data | Penyimpanan yang menimpa versi lebih baru tanpa 409 | 0 |
| Traceability release | Story dalam release yang dapat ditelusuri sampai test run & acceptance | 100% |
| Ketahanan node (tier M) | Task di node yang mati dilanjutkan dari checkpoint | ≤ 5 menit, 0 side effect ganda [Menebak] |

### 1.4 Non-goals v2

- Bukan klon penuh ChatGPT/Claude.ai: suara (voice), agen browsing otonom, dan marketplace plugin tidak termasuk (voice = P2).
- Tidak menjalankan model gambar self-hosted (butuh GPU) dan tidak memakai vector database atau provider embedding pihak ketiga pada fase ini (ADR-046).
- Tidak melakukan merge PR, force push, hapus branch remote, atau mengubah `.github/workflows/**` — semua R4.
- Tidak mempublikasikan portfolio atau mengirim kode ke GitHub tanpa approval eksplisit.
- Tidak menjanjikan "akurat" tanpa bukti: akurasi diwujudkan sebagai sitasi wajib, label tanpa sumber, dan evaluasi terukur.
- Tidak menambah microservice, Kubernetes, atau message bus untuk tier S/M (ADR-036, ADR-049); orkestrator tier L diputuskan di WP-14.0 berdasarkan data beban.
- Tidak menjanjikan bahwa agent menyelesaikan mega project tanpa review manusia, atau bahwa seluruh repo besar masuk ke konteks model.
- Agent tidak mendapat terminal bebas; terminal interaktif hanya untuk manusia, di sandbox, default nonaktif.

---

## 2. Persona dan Use Case

### 2.1 Persona

| Persona | Portal | Kebutuhan utama | Izin RBAC terkait |
|---|---|---|---|
| Solo developer / founder | IDE | Membangun MVP cepat dari prompt sampai patch tervalidasi | developer / owner |
| Full-stack developer | IDE + GitHub | Mengubah dan men-debug repo yang ada, termasuk repo GitHub privat; membuka PR | developer + `github:push` (`integrations:github:manage` = owner/admin) |
| QA / test engineer | IDE | Memastikan acceptance criteria dan user flow berjalan dengan bukti | developer / reviewer |
| Tech lead / reviewer | IDE | Mereview diff, menyetujui apply; push/PR hanya bila juga memegang `github:push` (owner/admin/developer) | reviewer + `approval:decide` |
| Administrator platform | Semua | Mengelola user, provider, capability, budget, kill switch, data-flow, audit | admin / owner |
| Pengguna Portal AI | AI | Jawaban bersumber, dokumen siap pakai, analisis data, tanya jawab dokumen sendiri, gambar | `assistant:use`, `assistant:docs`, `assistant:image` |
| Profesional / pencari kerja | Portfolio | Portfolio & CV dari data nyata, versi per lamaran, situs publik aman | `portfolio:edit`, `portfolio:publish` |
| Pengunjung situs portfolio | — | Melihat situs publik | tanpa akun |
| Engineering / program manager *(v2.1)* | IDE | Memecah program menjadi epic & story, memantau progres, merge queue, release | `workitem:write`, `workitem:plan`, `release:manage` |
| Administrator enterprise *(v2.1, tier L)* | Semua | SSO/SCIM, organisasi, node executor, anggaran organisasi, audit SIEM | `admin:sso`, `admin:nodes`, `admin:orgs` |

Pilot berjalan *single-owner* (ADR-002): PO adalah satu-satunya pengguna manusia. Mode multi-user untuk Portal AI masih keputusan PO.

### 2.2 Use case

| UC | Nama | Portal | Hasil yang dapat diverifikasi |
|---|---|---|---|
| UC-01 | Create from prompt | IDE | Requirement, plan, patch di worktree, test, review, laporan User Proxy, approval apply |
| UC-02 | Import repository (arsip / git HTTPS / GitHub App) | IDE | Workspace terindeks, stack terdeteksi |
| UC-03 | Modify existing feature | IDE | Patch scoped + test + diff + acceptance |
| UC-04 | Debug failing task | IDE | Root cause, patch, test ulang (≤ 5 percobaan) |
| UC-05 | Run end-to-end tests (P1) | IDE | Browser flow dengan evidence |
| UC-06 | Validate against user intent | IDE | Status per kriteria + link evidence |
| UC-07 | Review and approve changes | IDE | Apply atau reject terikat payload hash |
| UC-08 | Prepare deployment (P2) | IDE | Staging terverifikasi, rollback plan |
| UC-09 | Resume interrupted task | IDE | Status dipulihkan tanpa mengulang side effect |
| UC-10 | Control budget and providers | Core | Biaya terkendali dan transparan |
| UC-11 | Tanya jawab umum | AI | Jawaban streaming; berlabel bila tanpa sumber web |
| UC-12 | Riset web bersitasi (Cepat/Mendalam) | AI | Laporan dengan sitasi (url, judul, kutipan, tanggal pencarian) |
| UC-13 | Buat dokumen PPT/Word/Excel/PDF | AI, Portfolio | File valid, dapat diunduh lewat URL bertanda tangan |
| UC-14 | Buat/ubah gambar | AI | Gambar lolos moderasi dengan provenance, dalam kuota |
| UC-15 | Tanya jawab dokumen sendiri | AI | Jawaban bersitasi ke potongan dokumen sumber |
| UC-16 | Analisis data CSV/XLSX | AI | Tabel/grafik dari output run tercatat + kode yang dapat diperiksa |
| UC-17 | Hubungkan GitHub & impor repo privat | IDE | Project dari repo privat tanpa token tersimpan |
| UC-18 | Push branch & buka PR | IDE | PR terbuka setelah approval R3 terikat `head_commit` |
| UC-19 | Kelola portfolio & CV | Portfolio | Item tersetujui, CV DOCX/PDF ramah ATS |
| UC-20 | Publikasikan situs portfolio | Portfolio | Situs publik setelah cek privasi + approval R3 |
| UC-21 | Edit, jalankan & uji kode manual di IDE | IDE | File tersimpan dengan version check; run & test oleh user tersimpan sebagai bukti; take-over memaksa validasi ulang |
| UC-22 | Kelola program/epic/story & dekomposisi | IDE | `ProgramPlan` A03 disetujui user → task terjadwal paralel sesuai DAG |
| UC-23 | Merge queue, CI & release | IDE | Branch target hanya berisi commit yang lulus test wajib; release dapat ditelusuri |
| UC-24 | Operasi tier M/L (node executor, SSO, tenant) | Core | Node ditambah/gagal tanpa kehilangan task; akses mengikuti SSO/SCIM & scope organisasi |

Rincian prasyarat, alur alternatif, dan FR per use case: `SYSTEM_SPEC.md` §7 (UC-01..UC-20) dan §12 (UC-21..UC-24).

---

## 3. Alur End-to-End

### 3.1 Portal IDE (tidak berubah dari v1, plus sumber GitHub)

Prompt → A02 menyusun requirement & acceptance criteria → A03 menyusun plan (approve bila wajib) → B14 mengimplementasikan di worktree `kk/task-*` → test di sandbox → D22/D23 review → A07 User Proxy menilai per kriteria dengan evidence → user memutuskan apply (R3). Status task hanya berubah lewat state machine (`workflow/states.py`), dan hanya hasil tool nyata yang menjadi bukti. Tambahan v2: project dapat berasal dari repo GitHub App (UC-17), dan setelah apply, pengguna dapat meminta **Push & buka PR** sebagai approval R3 terpisah (UC-18).

### 3.2 Portal AI — alur request

```mermaid
sequenceDiagram
  participant U as User (browser)
  participant API as API
  participant W as Worker (assistant/media)
  participant CL as Claude API
  participant IP as ImageProvider
  participant FS as Asset store
  U->>API: POST /assistant/threads/{id}/messages {content, mode, attachments}
  API->>API: auth, kuota capability, redaksi secret
  API->>W: enqueue assistant.respond (202 + SSE)
  W->>CL: messages.stream(system, history, tools: web_search, web_fetch, code_execution+skills, client tools)
  CL-->>W: delta teks + server_tool_use + citations
  W->>W: client tool_use? → ToolGateway (image.generate, kb.search, portfolio.*)
  W->>IP: generate (bila diminta)
  IP-->>W: image bytes
  W->>FS: simpan file/gambar (+checksum, provenance)
  W-->>U: SSE: assistant.delta, citation.added, asset.created, usage.updated, message.completed
```

Aturan kunci: konten web adalah data tidak tepercaya dan tidak dapat memicu tool R3/R4; setiap server tool dicatat ToolGateway dengan `executor: provider` (ADR-051); jawaban bersitasi berbentuk teks, sitasinya diekstrak ke `message_citations` (ADR-047).

### 3.3 Integrasi GitHub — instal, impor, push & PR

```mermaid
sequenceDiagram
  participant U as User
  participant API as KodeKantor API
  participant GH as GitHub
  participant W as Worker (integrations)
  U->>API: Settings → Hubungkan GitHub
  API-->>U: redirect ke halaman install GitHub App (state=nonce, terikat sesi)
  U->>GH: pilih akun + repo → Install
  GH-->>API: callback ?installation_id&state
  API->>API: verifikasi state (nonce terikat sesi)
  API->>W: job verifikasi instalasi (api tanpa akses outbound)
  W->>GH: JWT → GET /app/installations/{id}
  W-->>API: simpan installation + tautan user GitHub
  GH-->>API: webhook installation_repositories (HMAC SHA-256 diverifikasi)
  U->>API: Import repo X sebagai project
  API->>W: job github.import_repo(repo X)
  W->>GH: JWT → POST /app/installations/{id}/access_tokens (repo X, contents:read)
  W->>W: git clone tanpa token di URL/config → workspace repo
  W-->>API: project siap, stack terdeteksi, index dibuat
  Note over U,W: Setelah apply disetujui: "Push & buka PR" = approval R3 baru<br/>(repo, branch kk/task-xxx, head_commit, judul PR) → token write khusus repo X → push branch → POST /pulls
```

### 3.4 Portal Portfolio — impor sampai publikasi

1. Pengguna memilih repo GitHub/project KodeKantor untuk diimpor; untuk repo privat hanya field metadata yang dipilih yang diambil.
2. K49 membuat **draft bertanda AI** dengan rujukan bukti (README, metadata, hasil test nyata). Draft tidak dapat dipublikasikan sebelum disetujui per kalimat.
3. CV dirender deterministik dari item yang disetujui (DOCX/PDF, template ATS sans-serif) di `sandbox-docs`.
4. Pratinjau situs: snapshot versi → cek privasi + secret scan → build Jinja2 di sandbox → pratinjau di iframe sandbox.
5. Publikasi = approval R3 `portfolio_publish` terikat `snapshot_sha256` dan `bundle_sha256`; K51 mempublikasikan ke `portfolio.<domain>` (swap symlink atomik) atau GitHub Pages (push `gh-pages` tanpa force).

---

### 3.5 SDLC end-to-end — 8 fase (v2.1)

```mermaid
flowchart LR
  R["1 Requirement<br/>A02 · A07"] --> D["2 Desain<br/>A05 · A06 · B12"]
  D --> I["3 Implementasi<br/>B14 · user edit"]
  I --> T["4 Test<br/>C17 · C20 · C19"]
  T --> RUN["5 Run/preview<br/>sandbox"]
  RUN --> RV["6 Review & acceptance<br/>D22 · D23 · A07 · approval"]
  RV -->|"gap"| I
  RV --> REL["7 Release<br/>merge queue · CI · PR · F32"]
  REL --> OP["8 Operate<br/>F33 · F34 · G37"]
  OP -->|"insiden → story"| R
```

| # | Fase | Bukti wajib | Ketersediaan |
|---|---|---|---|
| 1 | Requirement & intent | Requirement baseline, acceptance criteria, klarifikasi | MVP |
| 2 | Desain | ADR/diagram/skema API & DB sebagai patch dokumen yang direview; analisis dampak | Sebagian MVP (G35); penuh P13 |
| 3 | Implementasi | Patch di worktree task, commit per task, edit manual user | MVP (+P13 edit v2) |
| 4 | Testing | `test_runs` nyata: unit, integrasi, E2E, performa, aksesibilitas, keamanan | Unit/integrasi MVP; E2E P1; perf/a11y P13–P14 |
| 5 | Run / preview | Run profile + preview terisolasi, log tersimpan | Preview P1; run/terminal user P13 |
| 6 | Review & acceptance | Findings, laporan User Proxy per kriteria, approval apply | MVP |
| 7 | Release | Merge queue lulus, status CI, PR (P9), release notes, staging/production R3 | PR P9; merge queue & release P13 |
| 8 | Operate | Log/metrik, insiden → story perbaikan, dokumentasi & memori project | P13–P14 |

Fase hanya "selesai" bila buktinya ada — bukan karena agent mengklaimnya. Pada tier M/L, story memiliki banyak task paralel (worktree terpisah) yang masuk ke branch target lewat **merge queue**: rebase ke head terbaru → test wajib (+status CI bila sah) → merge; gagal → `needs_changes`, branch target tidak berubah (ADR-057). Hierarki Program → Epic → Story → Task: status turunan deterministik; dekomposisi A03 (`ProgramPlan`) hanya usulan yang wajib disetujui user.

### 3.6 Edit manual, run, test, dan take-over (Developer Workspace v2)

| Mode | Yang diedit | Aturan |
|---|---|---|
| Edit repo | Checkout repo project | Simpan dengan version check (`expected_sha256`); buat/rename/hapus file teraudit; commit user ke `kk/user-*` lalu ke default branch lewat merge queue bila aktif (commit langsung hanya bila merge queue nonaktif, opsi tier S) |
| Pantau task | Worktree task yang sedang berjalan | Read-only untuk user |
| Take-over task | Worktree task (dari `running` di checkpoint, `waiting_approval`, `needs_changes`, `failed`) | Task → `interrupted` (`status_reason=user_takeover`); user mengedit & commit di branch task; bukti lama **stale**; "Selesai take-over" → validasi ulang (test → review → User Proxy) sebelum apply |

- **Konflik:** server berubah sejak file dibuka → 409 + diff 3-way (milikmu / server / basis); agent tidak pernah menimpa perubahan user.
- **Run & test oleh user:** profil `run.*` dan test terpilih (semua / file / satu test) di sandbox; hasil tersimpan sebagai bukti (`initiated_by = user`).
- **Terminal interaktif:** PTY di container sandbox, network off default, batas waktu & resource, audit teredaksi, default nonaktif, khusus manusia (ADR-059). Agent tetap hanya profil allowlist.
- **Git manual:** stage/commit/branch/diff/discard lokal; push ke GitHub tetap lewat approval R3.
- **P2 (tier M+):** language server dan debugger (DAP) di sandbox.

---

## 4. Desain Mockup dan Spesifikasi UX

> **Mockup konseptual hi-fi diambil dari prototipe/replay KodeKantor (21–22 Okt 2026).** Layar Portal IDE berisi data eksekusi nyata (tiga skenario terekam di stack nyata dengan provider *scripted* dan sandbox Docker nyata). Layar Portal AI, Integrasi GitHub, Portfolio, dan Peta Platform adalah **prototipe alur dengan data contoh** — bukan bukti implementasi. Label "DATA CONTOH" di dalam layar sengaja dipertahankan.

### 4.1 Prinsip visual

- Satu SPA dengan pengalih ruang kerja **IDE · AI · Portfolio** di bagian atas sidebar; grup menu hanya tampil bila capability terkait aktif.
- Tipografi sans-serif (Inter untuk UI; template dokumen hasil default sans-serif). Warna lewat token Tailwind (`brand-*`, `sidebar-*`, `status-*`); tema terang dan gelap.
- Status selalu berupa teks + ikon, bukan warna saja (aksesibilitas, NFR-21).
- Output AI dipisahkan secara visual dari bukti tool/eksekusi; sitasi tampil di samping klaim; konten bertanda AI diberi penanda sampai disetujui.
- Aksi keluar (apply, push, PR, publish) hanya lewat dialog approval yang menampilkan payload persis dan hash-nya.

### 4.2 Perilaku UX lintas layar

1. Setiap aksi panjang menampilkan state (queued, running, waiting approval, validating, completed, failed, cancelled). Setiap layar punya state empty, loading, error, timeout, disconnected, permission denied, partial success — ditambah v2: **kuota habis**, **capability nonaktif**, **provider degraded**.
2. Jawaban Markdown dirender dengan `react-markdown` + `rehype-sanitize`; tidak ada HTML/JS dari konten agent atau web yang dieksekusi.
3. Jawaban tanpa pencarian web berlabel **"Tanpa sumber web"**; angka/tanggal terkini tanpa sitasi diberi peringatan.
4. Biaya dan kuota ditampilkan sebelum aksi berbiaya per unit (gambar, riset Mendalam) dan sesudahnya.
5. Badge engine/vendor pada hasil (mis. "dibuat lokal — data confidential") dengan tautan ke tabel data-flow.
6. Teks UI lewat i18n key (default `id`).

### 4.3 Gambar mockup

![Gambar 1. Portal IDE — layar 01–11 dari replay eksekusi nyata (login, dashboard, project baru, workspace, katalog agent, rencana task, ringkasan, testing & debug loop, review diff, validasi User Proxy, approval).](mockups/figure-1-portal-ide.png)

![Gambar 2. Portal Asisten AI — layar 12–15: riset web bersitasi dengan panel sumber, pembuat dokumen, buat gambar (provider belum dikonfigurasi), tanya jawab berlabel "Tanpa sumber web". Prototipe, data contoh.](mockups/figure-2-portal-ai.png)

![Gambar 3. Integrasi GitHub di Portal IDE — layar 16–19: izin minimum GitHub App, pilih repo, impor tanpa token di URL, kartu approval "Push & buka PR" R3. Prototipe, data contoh.](mockups/figure-3-integrasi-github.png)

![Gambar 4. Portal Portfolio — layar 20–23: profil (AI tidak mengarang pencapaian), proyek, CV ATS sans-serif, situs publik dengan approval publikasi R3 dan cek privasi. Prototipe, data contoh.](mockups/figure-4-portal-portfolio.png)

![Gambar 5. Peta Platform — tiga portal di atas satu core, capability registry, dan fase P9–P12 (rancangan, Proposed).](mockups/figure-5-peta-platform.png)

![Gambar 6. Editor kode & test nyata (v2.1) — edit file project hasil rekaman, konflik 409 dengan tampilan 3-way, test hasil editan dijalankan di browser lewat Pyodide: tanpa editan 11/11 lulus cocok dengan run nyata, editan a + b → FAILED assert 7 == 12. Pyodide bukan sandbox KodeKantor.](mockups/figure-6-editor-kode.png)

![Gambar 7. Siklus SDLC & skala S/M/L (v2.1) — papan SDLC 8 fase dengan bukti nyata dari rekaman, diff vs versi rekaman, tier S/M/L dan arsitektur tier M/L (rancangan, belum diimplementasikan).](mockups/figure-7-sdlc-skala.png)

### 4.4 Daftar layar dan cakupan mockup

| No. | Layar | Route (usulan untuk v2) | FR utama | Mockup |
|---|---|---|---|---|
| 1 | Login | `/login` | FR-001–006 | screen-01 |
| 2 | Dashboard | `/` | FR-007–011 | screen-02 |
| 3 | Create Project (+ tab GitHub) | `/projects/new` | FR-012–016, FR-189 | screen-03, screen-17 |
| 4 | Workspace / IDE | `/projects/:id/workspace` | FR-019–040 | screen-04 |
| 5 | AI Agents (+ grup v2 `planned`) | `/agents` | FR-041–047 | screen-05 |
| 6 | Task Planning & Workflow | `/tasks/:id` | FR-048–058 | screen-06, screen-07 |
| 7 | Preview & Result (P1) | `/projects/:id/preview` | FR-076 | — (diganti ringkasan task, screen-07) |
| 8 | Testing & Debugging | `/tasks/:id/testing` | FR-084–094 | screen-08 |
| 9 | Code Review & Diff (+ panel Push & PR) | `/tasks/:id/review` | FR-095–102 | screen-09 |
| 10 | User Proxy Validation | `/tasks/:id/acceptance` | FR-103–112 | screen-10 |
| — | Approvals | `/approvals` | FR-063, FR-067 | screen-11 |
| 11 | Peta Platform | `/platform` | FR-214, FR-217 | screen-24, screen-24b |
| 12 | Asisten AI — Chat | `/ai/spaces/:sid/threads/:tid` | FR-160–165 | screen-15 |
| 13 | Riset web (panel sitasi) | thread mode riset | FR-164, FR-166–170 | screen-12 |
| 14 | Pembuat dokumen | `/ai/documents` | FR-171–174 | screen-13 |
| 15 | Gambar | `/ai/images` | FR-181–183 | screen-14 |
| 16 | Knowledge base | `/ai/knowledge` | FR-175–177 | belum ada mockup |
| 17 | Analisis data | `/ai/analysis` | FR-179–180 | belum ada mockup |
| 18 | Asisten kustom | `/ai/assistants` | FR-184 | belum ada mockup |
| 19 | GitHub — Hubungkan | `/settings/integrations/github` | FR-185–187, FR-194–195 | screen-16 |
| 20 | GitHub — Pilih repo & impor | `/projects/new` (tab GitHub) | FR-187, FR-189–190 | screen-17, screen-18 |
| 21 | GitHub — Push & PR | panel review + `/approvals` | FR-191–193, FR-196 | screen-19 |
| 22 | Portfolio — Profil & item | `/portfolio` | FR-197–199, FR-202–204, FR-212 | screen-20 |
| 23 | Portfolio — Proyek | `/portfolio/projects` | FR-199–201 | screen-21 |
| 24 | Portfolio — CV & asisten lamaran | `/portfolio/cv` | FR-206–207 | screen-22 |
| 25 | Portfolio — Situs publik | `/portfolio/site` | FR-205, FR-208–211 | screen-23 |
| 26 | Settings — Capability & data-flow | `/settings/capabilities` | FR-213–219 | belum ada mockup |
| 27 | Editor kode (edit repo, operasi file, diff, konflik) | `/projects/:id/workspace` (mode edit) | FR-235–237, FR-244 | screen-25, 26, 27 |
| 28 | Run & test oleh user | panel bawah IDE | FR-238–240 | screen-28, 29 (demo Pyodide) |
| 29 | Terminal interaktif | panel bawah IDE | FR-241 | belum ada mockup |
| 30 | Papan SDLC per story | `/work-items/:id/board` | FR-226 | screen-30 |
| 31 | Program/Epic/Story & rencana A03 | `/projects/:id/work-items` | FR-221–224 | belum ada mockup (pohon contoh berlabel DATA CONTOH ada di halaman Skala demo) |
| 32 | Merge queue & release | `/projects/:id/merge-queue`, `/releases` | FR-225, FR-228–231 | belum ada mockup |
| 33 | Skala & kapasitas (admin node, SSO) | `/admin/nodes`, `/admin/sso`, `/capacity` | FR-245–258 | screen-31, 31b (rancangan) |

Layar No. 16 (Knowledge base), 17 (Analisis data), 18 (Asisten kustom), dan 26 (Settings capability) pada tabel di atas belum punya mockup hi-fi; spesifikasi komponen dan interaksinya ada di `SYSTEM_SPEC.md` §4.1.3 dan harus dimockup sebelum WP-10.5 / WP-11.5. FR-184 (asisten kustom) belum punya work package. Galeri layar individual: Lampiran B.

---

## 5. Modul Aplikasi dan Kebutuhan Fungsional

Kebutuhan fungsional lengkap (pernyataan "Sistem harus …", prioritas, sumber, AC/AX, use case) ada di `FR_CATALOG.md`: §3 untuk FR-001..FR-159 dan §7 untuk FR-160..FR-220. Semua FR berstatus turunan dan perlu review PO; ID tidak boleh digeser. Prioritas **P0ᵉ** = P0 di dalam perluasan (wajib untuk fase terkait), bukan P0 MVP.

### 5.1 Modul v1 — Portal IDE dan core (M01–M20)

| Modul | Nama | FR | Prioritas | Kode snapshot |
|---|---|---|---|---|
| M01 | Authentication & User Access | FR-001..006 | P0 | ada + test |
| M02 | Dashboard | FR-007..011 | P0 | ada + test |
| M03 | Project Management (+ sumber `github_app` v2) | FR-012..018 | P0 | ada + test |
| M04 | Repository & Context | FR-019..025 | P0 | ada + test |
| M05 | Browser IDE | FR-026..032 | P0 | ada + test |
| M06 | AI Chat Workspace | FR-033..040 | P0 | ada + test |
| M07 | Agent Registry (+ 11 agent v2) | FR-041..047 | P0 registry / P1 UI admin | ada + test |
| M08 | Orchestrator & Workflow Engine | FR-048..058 | P0 | sebagian |
| M09 | Tool Gateway & Policy (+ tool K3, permission v2) | FR-059..067 | P0 | ada + test |
| M10 | Sandbox & Runtime | FR-068..076 | P0 | ada + test (Docker nyata) |
| M11 | Terminal & Command Runner | FR-077..083 | P0 terbatas | ada + test |
| M12 | Testing & Debugging | FR-084..094 | P0 / E2E P1 | sebagian |
| M13 | Diff & Code Review | FR-095..102 | P0 | ada + test |
| M14 | User Proxy Acceptance | FR-103..112 | P0 | ada + test |
| M15 | Git & Remote Collaboration (+ jalur GitHub App P9) | FR-113..120 | P0 lokal / P2 remote | sebagian |
| M16 | Documentation & Knowledge | FR-121..127 | P1 | sebagian |
| M17 | Provider & Model Settings | FR-128..135 | P0 | sebagian |
| M18 | Deployment & Environments | FR-136..143 | P2 | sebagian |
| M19 | Observability & Audit | FR-144..151 | P0 audit / P1 dashboard | sebagian |
| M20 | Admin & Governance | FR-152..159 | P1 | ada + test |

"Ada + test" berarti kode snapshot dengan test unit/integrasi lulus — **belum terintegrasi end-to-end**.

### 5.2 M21 — Portal AI · FR-160..FR-184 · P1 (inti P10 = P0ᵉ) · fase P10–P11

Agent pemilik: I42, I43, I44, I45, I46, I47, S52. Delivery: AG-19 (backend), AG-20 (frontend), review AG-13.

| FR | Prioritas | Kebutuhan |
|---|---|---|
| FR-160 | P0ᵉ | Ruang & thread, model tier, label sensitivitas, cari riwayat |
| FR-161 | P0ᵉ | Pesan asinkron 202 + streaming SSE dengan reconnect |
| FR-162 | P0ᵉ | Edit, regenerate, cancel stream yang benar-benar menghentikan provider |
| FR-163 | P0ᵉ | Pra-cek RBAC, kuota capability, redaksi secret sebelum provider |
| FR-164 | P0ᵉ | Label "Tanpa sumber web" + peringatan angka/tanggal tanpa sitasi |
| FR-165 | P0ᵉ | Routing I42 hanya ke capability aktif & dalam kuota; jawaban berupa teks |
| FR-166 | P0ᵉ | Mode Cepat/Mendalam, versi `web_search` dipin, `max_uses` 3/20 |
| FR-167 | P0ᵉ | Riset Mendalam: rencana → pencarian/`web_fetch` → laporan bersumber |
| FR-168 | P0ᵉ | Ekstraksi sitasi ke `message_citations` + tampil di samping klaim |
| FR-169 | P0ᵉ | Allow/block domain + anggaran pencarian per user/bulan |
| FR-170 | P0ᵉ | Konten web = data tak tepercaya; tanpa tool R3/R4 di mode riset |
| FR-171 | P0ᵉ | Dokumen PPTX/DOCX/XLSX/PDF + template sans-serif + thumbnail |
| FR-172 | P0ᵉ | Engine ganda: Skills (non-confidential) vs lokal (wajib untuk confidential) |
| FR-173 | P0ᵉ | Unduhan via URL bertanda tangan TTL 300 detik, attachment + nosniff |
| FR-174 | P0ᵉ | Validasi file hasil dengan parser format sebelum ditandai siap |
| FR-175 | P1 | Upload PDF/DOCX/XLSX/CSV + ekstraksi teks di sandbox |
| FR-176 | P1 | Chunk + ringkasan kontekstual + FTS/`pg_trgm`, tanpa embedding |
| FR-177 | P1 | Jawaban KB via blok `search_result` bersitasi; confidential inline |
| FR-178 | P2 | Memori personal opt-in, dapat dilihat/dihapus |
| FR-179 | P1 | Analisis CSV/XLSX di sandbox/code execution + kode dapat diperiksa |
| FR-180 | P1 | Angka/grafik hanya dari output run tercatat; narasi dipisah |
| FR-181 | P1 | Generate/edit gambar hanya via `ImageProvider`; hasil flagged disembunyikan |
| FR-182 | P1 | Moderasi prompt S52 + moderasi vendor + audit penolakan |
| FR-183 | P1 | Kuota gambar/hari, estimasi biaya, provenance (+C2PA bila tersedia) |
| FR-184 | P1 | Asisten kustom (prompt + capability + koleksi), berbagi per role |

### 5.3 M22 — Integrasi GitHub App · FR-185..FR-196 · P0ᵉ · fase P9

Agent pemilik: J48 (deterministik), E29 (judul/deskripsi PR). Delivery: AG-18, sign-off keamanan AG-13.

| FR | Kebutuhan |
|---|---|
| FR-185 | Install GitHub App dengan `state` nonce terikat sesi + callback |
| FR-186 | Permission minimum; tanpa Workflows/Administration/Secrets |
| FR-187 | Daftar instalasi & repo terpilih (R0, metadata) |
| FR-188 | Token instalasi per operasi, 1 repo, ≤ 1 jam, tidak disimpan |
| FR-189 | Impor repo privat; clone tanpa token di URL/config |
| FR-190 | Pull tanpa menimpa; konflik → branch `kk/sync-*` |
| FR-191 | Push & PR = R3 terikat repo + branch + `head_commit` |
| FR-192 | Secret scan pre-push memblokir push |
| FR-193 | Blok `.github/workflows/**`; force push/merge/hapus branch = R4 |
| FR-194 | Webhook HMAC-SHA256 constant-time + dedupe delivery + rate limit |
| FR-195 | Private key = secret file read-only (hanya `worker-integrations`); redaksi token GitHub |
| FR-196 | Status PR & CI di layar review; merge tetap oleh manusia |

### 5.4 M23 — Portal Portfolio Profesional · FR-197..FR-212 · P1 · fase P12

Agent pemilik: K49 (kurator), K50 (CV & job-fit), K51 (publisher, deterministik). Delivery: AG-21 (+AG-18 impor GitHub, AG-06 hosting situs).

| FR | Prioritas | Kebutuhan |
|---|---|---|
| FR-197 | P1 | Profil; kontak default non-publik |
| FR-198 | P1 | Pengalaman & pendidikan dengan visibility dan sumber |
| FR-199 | P1 | Showcase proyek manual (CRUD) |
| FR-200 | P1 | Impor dari GitHub; repo privat hanya metadata terpilih |
| FR-201 | P1 | Impor dari project KodeKantor; hasil test dari `test_runs` nyata |
| FR-202 | P1 | Matriks skill & sertifikat |
| FR-203 | P1 | AI hanya draft bertanda; tidak dapat public sebelum disetujui |
| FR-204 | P1 | Larangan mengarang; klaim tanpa rujukan ditandai |
| FR-205 | P1 | Snapshot versi yang dirujuk publikasi |
| FR-206 | P1 | CV DOCX/PDF ramah ATS, versi per lamaran |
| FR-207 | P1 | Asisten lamaran: `web.fetch` lowongan → saran draft bersitasi |
| FR-208 | P1 | Build situs statis di sandbox + pratinjau + SEO |
| FR-209 | P1 | Cek privasi + secret scan pra-publish yang memblokir |
| FR-210 | P1 | Publish = R3 terikat hash snapshot |
| FR-211 | P2 | Analitik kunjungan tanpa cookie pihak ketiga |
| FR-212 | P1 | Ekspor & hapus data portfolio |

### 5.5 M24 — Capability Registry & Provider Gateway · FR-213..FR-220 · P0ᵉ · fase P10

Konsumen: I42 dan semua agent v2. Delivery: AG-07 (WP-10.1); kunci kontrak WP-9.0 oleh AG-00.

| FR | Kebutuhan |
|---|---|
| FR-213 | Registry capability tervalidasi (`config/capabilities.yaml` + tabel `capability_settings`) |
| FR-214 | `GET /capabilities`; capability nonaktif tidak pernah dipanggil |
| FR-215 | Routing provider per capability + contract test sebelum vendor aktif |
| FR-216 | Server tool lewat ToolGateway (`executor: provider`) + permission v2 |
| FR-217 | Budget per capability/user/bulan + kill switch admin |
| FR-218 | Usage & biaya per capability dengan `cost_status` jujur |
| FR-219 | Tabel data-flow per capability + pengungkapan ZDR |
| FR-220 | Kunci vendor per antrean; antrean Celery terpisah |

### 5.6 M25 — SDLC Program & Delivery Management · FR-221..FR-234 · P1 (merge queue & traceability P0ᵉ) · fase P13

Agent: A02, A03 (`ProgramPlan`), A05/A06/B12 (desain, P13), E28, F30, F32; merge queue & release train deterministik. Delivery: AG-08, AG-09, AG-10, AG-11, AG-14, AG-18.

| FR | Prioritas | Kebutuhan |
|---|---|---|
| FR-221 | P1 | Hierarki work item Program → Epic → Story → Task; task tetap unit eksekusi agent |
| FR-222 | P1 | Baseline requirement & AC per story; status story/epic/program turunan deterministik |
| FR-223 | P1 | Usulan dekomposisi A03 (`ProgramPlan` + DAG) sebagai diff rencana; task dibuat setelah disetujui user |
| FR-224 | P1 | Penjadwalan task paralel sesuai DAG, kapasitas tier, dan budget program |
| FR-225 | P0ᵉ | Merge queue per repo: rebase → test wajib (+CI) → merge; gagal → branch target tidak berubah |
| FR-226 | P1 | Papan SDLC 8 fase per story dengan bukti per fase |
| FR-227 | P1 | Artefak desain (ADR, diagram, skema) sebagai patch dokumen yang direview |
| FR-228 | P2 | Release train, changelog dari task/commit, release notes direview |
| FR-229 | P2 | Registry environment dev/staging/production; production tetap R3 |
| FR-230 | P1 | Status CI GitHub sebagai bukti & syarat merge queue (WP-13.12, setelah G9) |
| FR-231 | P0ᵉ | Traceability requirement → story → task → commit → test → acceptance → release, ekspor CSV/JSON/PDF |
| FR-232 | P1 | Dashboard program: progres, blocked, lead time, antrean, biaya |
| FR-233 | P2 | Code ownership (CODEOWNERS) & reviewer wajib per path (tier M+) |
| FR-234 | P2 | Insiden (F33/F34) → story perbaikan dengan tautan bukti |

### 5.7 M26 — Developer Workspace v2 · FR-235..FR-244 · P0ᵉ (FR-235..237) · fase P13

Delivery: AG-09, AG-10, AG-02, AG-03; sign-off terminal AG-13 🔒.

| FR | Prioritas | Kebutuhan |
|---|---|---|
| FR-235 | P0ᵉ | Buat/edit/rename/hapus file & folder dengan version check, audit, undo per sesi |
| FR-236 | P0ᵉ | Take-over task: `interrupted/user_takeover`, commit atas nama user, bukti stale, apply diblokir sampai validasi ulang |
| FR-237 | P0ᵉ | Agent tidak pernah menimpa perubahan user; konflik sebagai diff 3-way |
| FR-238 | P1 | Format & lint on save di sandbox sebagai patch yang dapat ditolak |
| FR-239 | P1 | Run project dari IDE (profil `run.*`) + preview terisolasi |
| FR-240 | P1 | Test oleh user (semua/file/satu test), tersimpan sebagai bukti |
| FR-241 | P1 | Terminal interaktif PTY di sandbox untuk manusia; default nonaktif |
| FR-242 | P2 | Language intelligence lewat language server di sandbox |
| FR-243 | P2 | Debugger (DAP) di sandbox untuk Python & Node |
| FR-244 | P1 | Git manual (stage/commit/branch/diff/discard); push tetap R3 |

### 5.8 M27 — Scale & Enterprise Platform · FR-245..FR-258 · P1 tier M / P2 tier L · fase P13–P14

Delivery: AG-06, AG-03, AG-01, AG-02, AG-07; sign-off AG-13 🔒.

| FR | Prioritas | Kebutuhan |
|---|---|---|
| FR-245 | P1 | Profil tier S/M/L tervalidasi tanpa mengubah kode domain |
| FR-246 | P1 | Pool node executor: registrasi, token terotasi, health, penempatan task |
| FR-247 | P1 | Node gagal → task `interrupted`, dilanjutkan di node lain dari checkpoint |
| FR-248 | P1 | Workspace multi-node (volume bersama / git bundle) dengan checksum |
| FR-249 | P2 | Driver sandbox microVM (Firecracker/Kata) untuk tier L |
| FR-250 | P1 | SSO OIDC (tier M) dan SAML (tier L), pemetaan grup → role |
| FR-251 | P2 | SCIM; deprovision mencabut akses ≤ 5 menit [Menebak] |
| FR-252 | P2 | Organisasi/tenant dengan isolasi data, budget, kebijakan, audit |
| FR-253 | P1 | Repo besar: sparse checkout, index simbol inkremental, batas konteks |
| FR-254 | P1 | Project multi-repo: story lintas repo, merge queue per repo |
| FR-255 | P1 | Anggaran per organisasi/program/epic + alert + kill switch |
| FR-256 | P2 | HA & DR tier L (RPO ≤ 15 menit, RTO ≤ 4 jam [Menebak]) |
| FR-257 | P2 | Ekspor audit ke SIEM dengan rantai hash |
| FR-258 | P1 | Kapasitas & antrean transparan (waktu tunggu, utilisasi node) |

### 5.9 Rekap jumlah FR

| Rentang | Modul | Jumlah |
|---|---|---|
| FR-001..FR-159 | M01–M20 | 159 |
| FR-160..FR-184 | M21 | 25 |
| FR-185..FR-196 | M22 | 12 |
| FR-197..FR-212 | M23 | 16 |
| FR-213..FR-220 | M24 | 8 |
| FR-221..FR-234 | M25 | 14 |
| FR-235..FR-244 | M26 | 10 |
| FR-245..FR-258 | M27 | 14 |
| **Total** | **M01–M27** | **258** |

---

## 6. Katalog Agent v2

### 6.1 Prinsip

1. **Agent bukan proses LLM bila fungsi deterministik cukup** (PRD v1 §14.1 #7): J48, K51, dan S52 adalah agent *tool-mode* deterministik; operasi GitHub, publikasi, dan moderasi tidak diputuskan model.
2. Semua agent v2 berstatus `mvp_mode: planned`, `enabled: false` sampai fasenya lulus gate; aktivasi per agent butuh contract test dan eval set (gate G8 berlaku juga untuk v2).
3. Setiap agent hanya memanggil tool di allowlist-nya lewat ToolGateway; server tool Claude (`web_search`, `web_fetch`, code execution) juga melewati gateway (`executor: provider`, ADR-051).
4. Agent bersitasi (I42 untuk jawaban, I43, I47, K50 saat mengutip lowongan) mengakhiri run dengan **teks bersitasi**, bukan `submit_result` (ADR-047).
5. Private key GitHub App tidak pernah berada di konteks agent mana pun; E29 tidak memegang token.

### 6.2 Komposisi registry v2

| Dimensi | Nilai |
|---|---|
| Total entri | 53 = 41 specialist PRD + Orchestrator + 11 agent baru |
| Mode MVP | `llm` 6 · `profile` 7 · `tool` 7 · `planned` 33 |
| Grup baru | `ai_assistant` (I42–I47), `integrations` (J48), `portfolio` (K49–K51); S52 di `quality_security` |
| Promosi | E29 Pull Request & Collaboration Agent: `planned` → target `llm` fase P9 (judul/deskripsi PR) |
| File | `agents/registry.v2.yaml` (schema 2) — `agents/registry.yaml` tetap schema 1 untuk runtime snapshot sampai WP-9.0 |
| Validasi | `python3 scripts/validate_registry.py` → `OK: 53 agents (41 specialists + ORCH + 11 K2) — schema_version 2` [Pasti, dijalankan 21 Okt 2026] |

### 6.3 Tabel induk 53 entri

| ID | Nama | Grup | Mode MVP → target | Status v2 |
|---|---|---|---|---|
| A01 | Request & Intent Classifier | Planning & intent | profile → A02 | v1 |
| A02 | Requirement Analyst | Planning & intent | llm | v1 |
| A03 | Planner & Task Decomposer | Planning & intent | llm | v1 |
| A04 | Codebase Explorer & Context Engineer | Planning & intent | tool | v1 |
| A05 | Software Architect | Planning & intent | planned | v1 |
| A06 | Change Impact & Risk Analyst | Planning & intent | planned | v1 |
| A07 | User Proxy / User Intent & Acceptance | Planning & intent | llm | v1 |
| B08 | UI/UX Designer | Software development | planned | v1 |
| B09 | Frontend Developer | Software development | profile → B14 | v1 |
| B10 | Backend Developer | Software development | profile → B14 | v1 |
| B11 | API & Integration Specialist | Software development | planned | v1 |
| B12 | Database & Data Modeling Specialist | Software development | planned | v1 |
| B13 | Authentication & Identity Specialist | Software development | profile → B14 | v1 |
| B14 | General Code Implementation Agent | Software development | llm | v1 |
| B15 | AI/LLM Application Specialist | Software development | planned | v1 |
| C16 | Test Strategy Agent | Testing & debugging | profile → A03 | v1 |
| C17 | Unit Testing Agent | Testing & debugging | tool | v1 |
| C18 | Integration & API Testing Agent | Testing & debugging | planned | v1 |
| C19 | E2E & Browser QA Agent | Testing & debugging | planned | v1 |
| C20 | Debugger & Root Cause Analyst | Testing & debugging | llm | v1 |
| C21 | Environment & Build Troubleshooter | Testing & debugging | profile → C20 | v1 |
| D22 | Code Review Agent | Quality & security | llm | v1 |
| D23 | Application Security Agent | Quality & security | tool | v1 |
| D24 | Dependency & Supply Chain Agent | Quality & security | planned | v1 |
| D25 | Performance & Scalability Agent | Quality & security | planned | v1 |
| D26 | Accessibility & Compatibility Agent | Quality & security | planned | v1 |
| E27 | Git Workflow Agent | Git & collaboration | tool | v1 |
| E28 | Merge Conflict Resolution Agent | Git & collaboration | planned | v1 |
| E29 | Pull Request & Collaboration Agent | Git & collaboration | planned → llm (P9) | dipromosikan (v2) |
| F30 | Build & CI Agent | DevOps & operations | planned | v1 |
| F31 | Infrastructure as Code Agent | DevOps & operations | planned | v1 |
| F32 | Deployment & Release Agent | DevOps & operations | planned | v1 |
| F33 | Observability & SRE Agent | DevOps & operations | planned | v1 |
| F34 | Incident Response & Recovery Agent | DevOps & operations | planned | v1 |
| G35 | Technical Documentation Agent | Documentation & knowledge | profile → B14 | v1 |
| G36 | API & Developer Documentation Agent | Documentation & knowledge | planned | v1 |
| G37 | Project Knowledge & Memory Agent | Documentation & knowledge | tool | v1 |
| H38 | Data Engineering Agent | Specialized engineering | planned | v1 |
| H39 | Migration Specialist | Specialized engineering | planned | v1 |
| H40 | Legacy Modernization & Refactoring Agent | Specialized engineering | planned | v1 |
| H41 | Language & Framework Specialist | Specialized engineering | tool | v1 |
| I42 | Assistant Router & Concierge | AI assistant (v2) | planned → llm (P10) | baru (v2) |
| I43 | Web Research & Citation Agent | AI assistant (v2) | planned → llm (P10) | baru (v2) |
| I44 | Document Builder Agent | AI assistant (v2) | planned → llm (P10) | baru (v2) |
| I45 | Visual Prompt Agent | AI assistant (v2) | planned → llm (P11) | baru (v2) |
| I46 | Data Analysis Agent | AI assistant (v2) | planned → llm (P11) | baru (v2) |
| I47 | Knowledge Base Agent | AI assistant (v2) | planned → llm (P11) | baru (v2) |
| J48 | GitHub Integration Agent | Integrations (v2) | planned → tool (P9) | baru (v2) |
| K49 | Portfolio Curator Agent | Portfolio (v2) | planned → llm (P12) | baru (v2) |
| K50 | CV & Job-fit Agent | Portfolio (v2) | planned → llm (P12) | baru (v2) |
| K51 | Portfolio Publisher | Portfolio (v2) | planned → tool (P12) | baru (v2) |
| S52 | Content Safety & Moderation | Quality & security | planned → tool (P10) | baru (v2) |
| ORCH | Orchestrator Agent | Orchestration | tool | v1 |

`profile → X` = profil yang dijalankan oleh runtime agent X; `planned → llm (P10)` = belum aktif, target mode dan fase aktivasi.

### 6.4 Kartu agent baru dan yang dipromosikan

| ID | Agent | Tujuan | Tools (allowlist) | Output | Fase |
|---|---|---|---|---|---|
| E29 | Pull Request & Collaboration Agent | prepare PR descriptions, change summaries and review checklists; remote writes require authorization | `review.get_task_diff`, `git.diff`, `git.log`, `acceptance.list_criteria`, `acceptance.list_evidence`, `tests.get_run`, `github.get_pr_status`, `submit_result` | `PRDraft` | P9 |
| I42 | Assistant Router & Concierge | pintu masuk percakapan Portal AI: menjawab langsung bila cukup, atau menghasilkan keputusan routing ke capability/agent yang aktif dan masih dalam kuota (AX-11) | `capabilities.list`, `kb.search`, `submit_result` | `RouteDecision` | P10 |
| I43 | Web Research & Citation Agent | menjawab pertanyaan yang membutuhkan informasi terkini dengan rencana kueri, pencarian, pembacaan sumber, dan sintesis bersitasi; jawaban tanpa sumber diberi label (ADR-038) | `web.search`, `web.fetch` | `CitedAnswer` | P10 |
| I44 | Document Builder Agent | mengubah permintaan dokumen menjadi outline dan konten terstruktur, lalu merender file valid lewat engine yang diizinkan policy (ADR-039) dan menyimpannya sebagai aset dengan provenance | `docs.render_skills`, `docs.render_local`, `assets.store`, `submit_result` | `DocumentResult` | P10 |
| I45 | Visual Prompt Agent | mengubah permintaan visual user menjadi prompt gambar yang lolos moderasi, memanggil ImageProvider (vendor ADR-040a), dan mencatat provenance serta estimasi biaya | `moderation.check_image_prompt`, `image.generate`, `image.edit`, `submit_result` | `VisualResult` | P11 |
| I46 | Data Analysis Agent | menjawab pertanyaan data berbasis bukti eksekusi kode nyata di sandbox tanpa network; angka hanya dilaporkan dari output run, bukan tebakan model | `analysis.run`, `assets.store`, `submit_result` | `AnalysisResult` | P11 |
| I47 | Knowledge Base Agent | tanya jawab dokumen sendiri tanpa embedding (ADR-046): mencari potongan relevan, mengirimnya sebagai blok search_result, dan menjawab dengan sitasi ke potongan (AX-10) | `kb.search`, `kb.get_chunk` | `CitedAnswer` | P11 |
| J48 | GitHub Integration Agent | satu-satunya identitas yang mengeksekusi operasi GitHub; token instalasi per operasi (1 repo, ≤1 jam, tidak disimpan), push/PR hanya dengan approval R3 terikat payload | `github.list_repos`, `github.import_repo`, `github.pull`, `github.push_branch`, `github.open_pr`, `github.get_pr_status` | `GitHubOperationResult` | P9 |
| K49 | Portfolio Curator Agent | mengkurasi bahan portfolio menjadi draft bertanda AI dengan rujukan bukti (README, metadata repo, hasil test); tidak pernah mengarang pencapaian, angka, sertifikat, atau testimoni (AX-09) | `portfolio.get_profile`, `github.get_repo_summary`, `repo.read_file`, `portfolio.draft_item`, `submit_result` | `PortfolioDraft` | P12 |
| K50 | CV & Job-fit Agent | membandingkan persyaratan lowongan (dikutip dengan sitasi) dengan bukti di portfolio user, lalu menghasilkan laporan kecocokan dan draft — tanpa mengarang pengalaman atau skill | `web.fetch`, `portfolio.get_profile`, `docs.render_local`, `portfolio.draft_item`, `submit_result` | `JobFitReport` | P12 |
| K51 | Portfolio Publisher | publikasi portfolio deterministik: snapshot → cek privasi + secret scan → build Jinja2 di sandbox → approval R3 → publish ke portfolio.<domain> atau GitHub Pages | `portfolio.privacy_check`, `portfolio.build_site`, `portfolio.publish` | `PublishResult` | P12 |
| S52 | Content Safety & Moderation | gerbang keselamatan konten sebelum capability berisiko dijalankan: aturan kebijakan deterministik dulu, moderasi vendor sebagai lapisan kedua, verdict dapat diaudit | `moderation.check_text`, `moderation.check_image_prompt` | `ModerationVerdict` | P10 |

Batas risiko per agent baru: I42, I43, I47 hanya R0; I44 R1 (Skills) atau R2 (render lokal); I45 R1 + moderasi wajib; I46 R2 sandbox tanpa network; J48 R0–R1, dan R3 hanya lewat `ApprovalGrant`; K49 R0–R1 (draft); K50 R0–R2 (draft + render CV lokal); K51 R2 (build) + R3 (publish); S52 R0. Detail input/output contract, limit, dan model tier: `AGENT_CATALOG.md` §12 dan `agents/registry.v2.yaml`.

### 6.5 Rencana aktivasi

| Fase | Agent yang diaktifkan | Syarat |
|---|---|---|
| P9 | J48, E29 | WP-9.0 (loader registry v2, ApprovalGrant), G9 |
| P10 | I42, I43, I44, S52 | Capability registry (WP-10.1), eval sitasi WP-10.6, G10 |
| P11 | I45, I46, I47 | Vendor gambar ADR-040a, eval KB Recall@8, G11 |
| P12 | K49, K50, K51 | Model konten portfolio, cek privasi, G12 |
| P13 (SDLC, v2.1) | A05, A06, B12, C18, C19, E28, F30, F32 | WP-13.11; G8 per agent; A03 menambah kontrak `ProgramPlan` |
| P14 (enterprise, v2.1) | B08, B11, B15, D24, D25, D26, F31, F33, F34, G36, H38–H40 | WP-14.8; G8 per agent |

**v2.1 tidak menambah agent.** Kebutuhan SDLC end-to-end dipenuhi dengan mengaktifkan agent `planned` yang sudah ada per gate G8; merge queue, release train, dan penjadwalan bersifat deterministik (tanpa LLM). Registry tetap 53 entri; field `phase` menerima P13/P14 mulai WP-13.0.

---

## 7. Arsitektur Sistem

### 7.1 Tiga portal, satu core

```mermaid
flowchart TB
  subgraph portals["Frontend — satu SPA, tiga ruang kerja"]
    IDE["Portal IDE<br/>project, task, agent, review"]
    AI["Portal AI<br/>chat, riset web, dokumen, gambar, analisis"]
    PF["Portal Portfolio<br/>profil, proyek, CV, situs publik"]
  end
  subgraph core["Core bersama — modular monolith"]
    AUTH["Auth, RBAC, audit"]
    CAP["Capability Registry<br/>fitur, risiko, budget, flag"]
    PGW["Provider Gateway<br/>LLM · Image · DocEngine"]
    TGW["Tool Gateway + Approval R0–R4"]
    USE["Usage & budget per capability"]
    FILES["Asset store<br/>file hasil, upload"]
    INT["Integrations<br/>GitHub App"]
    AUTH ~~~ PGW
    CAP ~~~ TGW
    USE ~~~ INT
  end
  IDE --> core
  AI --> core
  PF --> core
  PGW --> CL[("Claude Platform API<br/>teks, web_search, web_fetch,<br/>code execution + Skills")]
  PGW --> IMG[("Image provider<br/>vendor via ADR-040a")]
  INT --> GH[("GitHub<br/>repo privat")]
  TGW --> SBX["Sandbox Docker<br/>tetap seperti MVP"]
```

KodeKantor tetap *modular monolith* (FastAPI + Celery + PostgreSQL + Redis) dengan modul domain baru `assistant/`, `kb/`, `docengine/`, `capabilities/`, `assets/`, `integrations/github/`, `portfolio/`, `moderation/`. Tidak ada microservice baru; sandbox-manager tetap satu-satunya service terpisah (ADR-036).

### 7.2 Komponen logis

| Komponen | Tanggung jawab | Keputusan |
|---|---|---|
| SPA (React) | Tiga ruang kerja IDE · AI · Portfolio, satu design system | ADR-001, ADR-036 |
| API (FastAPI) | REST, SSE task & thread, webhook ingress GitHub, unduhan aset; tidak punya akses outbound | ARCHITECTURE §2, ADR-053 |
| Capability Registry | Modul capability: provider, tools, risiko, budget, izin, flag, kill switch | ADR-044, FR-213–217 |
| Provider Gateway | `LLMProvider` (Claude), `ImageProvider` (vendor ADR-040a), `DocumentEngine` (Skills / lokal) | ADR-037, ADR-039, ADR-040 |
| Agent runtime | Satu runtime untuk task (IDE) dan thread (Portal AI/Portfolio) lewat `RunScope` | ADR-050 |
| Tool Gateway | Satu pintu side effect: allowlist, permission, kuota, approval, audit; jalur server tool provider | ADR-051 |
| Approval service | Approval terikat payload hash, sekali pakai; `ApprovalGrant` untuk eksekusi R3 multi-langkah | ADR-034, ADR-052, ADR-056 |
| Asset store | File hasil di `ASSETS_ROOT`, metadata + provenance di DB, unduhan URL bertanda tangan | ADR-055 |
| KB | Ekstraksi di sandbox, chunk + ringkasan kontekstual, FTS + `pg_trgm`, blok `search_result` | ADR-046 |
| Integrations | GitHub App: instalasi, token per operasi, clone/pull/push/PR, webhook | ADR-041, ADR-053 |
| Sandbox | Profil tetap, network none; image `sandbox-python`, `sandbox-node`, `sandbox-docs`; workspace task dan `_jobs` | ADR-009, ADR-054 |

### 7.3 Diagram container v2

Diagram disederhanakan; versi lengkap dengan network dan volume ada di `IMPLEMENTATION_DESIGN_V2.md` §3.1.

```mermaid
flowchart TB
  U["Browser pengguna<br/>SPA IDE · AI · Portfolio"] --> caddy
  PV["Pengunjung situs portfolio"] --> caddy
  GHW["Webhook GitHub"] --> caddy
  caddy["Caddy :443<br/>APP_DOMAIN + PORTFOLIO_DOMAIN"] --> api["api (FastAPI)<br/>REST · SSE · webhook ingress<br/>tanpa akses outbound"]
  caddy --> web["web (SPA statis)"]
  api --> data[("PostgreSQL 16 + pg_trgm/FTS<br/>Redis 7 · ASSETS_ROOT")]
  api -->|"enqueue"| Q{{"Antrean Celery<br/>ide · assistant · media · integrations"}}
  Q --> wide["worker-ide<br/>task, test, index, E29"]
  Q --> wasst["worker-assistant<br/>I42 I43 I47 K49 K50"]
  Q --> wmedia["worker-media<br/>I44 I45 I46, KB ingest, CV, situs"]
  Q --> wint["worker-integrations<br/>J48 GitHub, webhook, K51 publish"]
  wide & wmedia --> sm["sandbox-manager"] --> dsp["docker-socket-proxy"] --> sbx[["Sandbox network none<br/>sandbox-python · sandbox-node · sandbox-docs"]]
  wide & wasst & wmedia --> CL[("Claude Platform API")]
  wmedia --> IV[("Vendor gambar<br/>ADR-040a")]
  wint --> GH[("GitHub API + git HTTPS")]
```

### 7.4 Antrean, worker, dan segregasi rahasia

| Container | Antrean | Pekerjaan utama | Rahasia yang dipegang |
|---|---|---|---|
| `worker-ide` | `ide` | Task IDE, test, index, recovery, draft PR (E29) | `ANTHROPIC_API_KEY`, `SANDBOX_MANAGER_TOKEN` |
| `worker-assistant` | `assistant` | Jawaban chat/riset (I42, I43, I47), kurasi portfolio (K49, K50) | `ANTHROPIC_API_KEY` |
| `worker-media` | `media` | Render dokumen, gambar, ingest KB, analisis, render CV, build situs | `ANTHROPIC_API_KEY`, `IMAGE_PROVIDER_API_KEY`, `SANDBOX_MANAGER_TOKEN` |
| `worker-integrations` | `integrations` | Verifikasi instalasi, webhook, impor/pull/push/PR, publish portfolio | Private key GitHub App, `GITHUB_CLIENT_SECRET` |
| `api` | — (produsen) | REST, SSE, webhook ingress | `SECRET_KEY`, `GITHUB_WEBHOOK_SECRET` (+`_PREVIOUS`), `SANDBOX_MANAGER_TOKEN`; `ANTHROPIC_API_KEY` hanya untuk smoke (ADR-004) |

Mode *compact* (satu worker berisi semua antrean dan semua rahasia) hanya untuk dev/CI dan ditolak validator konfigurasi saat `APP_ENV=production` (ADR-053).

### 7.5 Integrasi Claude Platform API

| Fitur | Dipakai untuk | Catatan data & kebijakan |
|---|---|---|
| Messages API (streaming, tool use, prompt caching) | Semua agent LLM, chat | API key hanya di worker; hanya `app/llm/anthropic_provider.py` yang meng-import SDK `anthropic` |
| Server tool `web_search` | Riset web (I43) | Versi dipin lewat `WEB_SEARCH_TOOL_VERSION` (versi yang terdokumentasi: `web_search_20250305`, `web_search_20260209`, `web_search_20260318`; naik hanya lewat ADR + contract test); sitasi selalu aktif; `allowed_domains` atau `blocked_domains` (tidak keduanya); `max_uses` per mode; $10 per 1.000 pencarian + token [Pasti — dokumentasi Web search tool, dibaca 21 Okt 2026] |
| Server tool `web_fetch` | Membaca halaman/lowongan (I43, K50) | Konten = data tidak tepercaya |
| Code execution + Agent Skills `pptx`/`docx`/`xlsx`/`pdf` | Dokumen (I44); analisis remote (I46) hanya setelah ADR + versi registry baru — default analisis lokal | Container tanpa internet; **tidak tercakup ZDR**; dilarang untuk `confidential` [Pasti] |
| Files API | Mengambil file hasil Skills | **Tidak eligible ZDR**; file dihapus setelah diunduh (TTL `PROVIDER_FILES_TTL_MINUTES`) |
| Citations (blok `document` dan `search_result`) | Jawaban KB (I47), riset | Tidak kompatibel dengan structured outputs → jawaban akhir berupa teks bersitasi (ADR-047) [Pasti] |
| Embeddings | — | Anthropic tidak menyediakan model embedding sendiri → tidak dipakai (ADR-046) [Pasti] |
| Generate gambar | — | Claude tidak menghasilkan foto/ilustrasi → `ImageProvider` vendor kedua (ADR-040) [Pasti] |

Readiness gate v1 (ADR-032) diperluas per capability: capability baru hanya dapat diaktifkan setelah contract test terhadap fitur provider yang dipakainya lulus (OI-11).

### 7.6 Capability Registry

| Capability | Portal | Engine / provider | Tool utama | Risiko | Prioritas / fase |
|---|---|---|---|---|---|
| `chat` | AI | Claude Messages API (streaming) | – | R0 | P0ᵉ / P10 |
| `web_research` | AI, Portfolio | Claude `web_search` + `web_fetch` | `web.search`, `web.fetch` | R0 (data keluar ke provider) | P0ᵉ / P10 |
| `doc_generate` | AI, Portfolio | Agent Skills **atau** DocEngine lokal (ADR-039) | `docs.render_skills`, `docs.render_local` | R1 / R2 | P0ᵉ / P10 |
| `data_analysis` | AI | Sandbox lokal (default) atau code execution Claude | `analysis.run`, `code.execute_remote` | R2 | P1 / P11 |
| `kb_qa` | AI | Claude + PostgreSQL FTS/`pg_trgm` + `search_result` (ADR-046) | `kb.search`, `kb.get_chunk` | R0 | P1 / P11 |
| `image_generate` / `image_edit` | AI, Portfolio | `ImageProvider` (vendor ADR-040a) | `image.generate`, `image.edit` | R1 + moderasi | P1 / P11 |
| `github_sync` | IDE, Portfolio | GitHub App | `github.import_repo`, `github.pull` | R1 | P0ᵉ / P9 |
| `github_push_pr` | IDE | GitHub App | `github.push_branch`, `github.open_pr` | **R3** | P0ᵉ / P9 |
| `portfolio_publish` | Portfolio | Situs statis atau GitHub Pages | `portfolio.publish` | **R3** | P1 / P12 |
| `voice` | AI | (belum) | – | – | P2 |

### 7.7 GitHub App — permission dan operasi

| Permission | Level | Alasan |
|---|---|---|
| Metadata | Read | Daftar repo, info dasar |
| Contents | Read & write | Clone/fetch; push branch `kk/*` hanya setelah approval |
| Pull requests | Read & write | Membuat/memperbarui PR dari branch task |
| Checks / Commit statuses | Read | Status CI di layar review |
| Workflows | **Tidak diminta** | Mencegah agent mengubah `.github/workflows` (jalur eksfiltrasi secret CI) |
| Administration, Secrets, Actions write | **Tidak diminta** | Tidak dibutuhkan |

| Operasi | Risiko |
|---|---|
| Daftar instalasi & repo | R0 |
| Impor/clone, fetch/pull | R1 |
| Push branch `kk/task-*` | R3 — approval terikat repo + branch + `head_commit` |
| Buka/update PR, komentar PR | R3 — pratinjau dulu |
| Force push, hapus branch remote, merge PR, ubah workflow | R4 — tidak tersedia |

Token instalasi dibuat per operasi lewat `POST /app/installations/{id}/access_tokens` dengan JWT RS256, dibatasi ke satu repo dan permission minimum, kedaluwarsa ≤ 1 jam, dan tidak disimpan. Git HTTPS memakai `x-access-token`, disuntikkan sebagai header lewat environment proses git saja — tidak pernah di URL atau `.git/config` [Kemungkinan Besar untuk mekanisme `http.extraheader`; dibuktikan di AX-01].

### 7.8 Topologi dan kapasitas pilot

| Aspek | Ketentuan |
|---|---|
| Server | VPS Ubuntu Server LTS, pilot 4 vCPU / 16 GB RAM / 200 GB SSD (ADR-010) |
| Runtime | Docker Compose; Caddy 2 (TLS, header keamanan, SSE, situs portfolio terpisah origin) |
| Anggaran RAM v2 | ±12,25 GB dari 16 GB (±12,5 GB termasuk scheduler) untuk container (worker ±5,75 GB, api 1,5 GB, PostgreSQL 2 GB, Redis 0,5 GB, sandbox-manager 0,5 GB, 2 sandbox × 1 GB) [Menebak] |
| Pemicu scale-out | RAM > 70% selama 1 minggu → pisahkan `worker-media` + sandbox ke node executor |
| Jaringan | Publik hanya 80/443; egress HTTPS dari worker ke Claude, GitHub, vendor gambar; sandbox `network_mode=none` |
| Penyimpanan | `WORKSPACE_ROOT`, `ASSETS_ROOT`, `PORTFOLIO_SITE_ROOT` — semua masuk backup |

---

### 7.9 Tier S/M/L — dari project kecil sampai mega project (v2.1)

| Dimensi | Tier S — project kecil (pilot) | Tier M — tim/produk | Tier L — enterprise / mega project |
|---|---|---|---|
| Pengguna | 1–3 | 5–50 [Menebak] | 50–1.000+ [Menebak] |
| Node | 1 VPS 4 vCPU/16 GB | Node kontrol + 1–4 node executor | Node kontrol HA + pool executor terskala (VM pool / Kubernetes — WP-14.0) |
| Task agent aktif | 2 | 2 per node executor (4–8) [Menebak] | ≥ 50 bersamaan [Menebak] |
| Repo | 1 per project | Multi-repo (≤ 10) [Menebak] | Multi-repo + monorepo ≥ 1 juta LOC [Menebak] |
| Hierarki kerja | Task (+ story opsional) | Story → Task, Epic | Program → Epic → Story → Task |
| Identitas | Login lokal + RBAC | + OIDC, `four_eyes` | + SAML, SCIM, organisasi/tenant |
| Sandbox | Docker profil tetap (+gVisor opsional) | Docker + gVisor wajib | microVM (Firecracker/Kata) |
| Data | PostgreSQL & Redis tunggal | + backup terverifikasi, workspace bersama | PostgreSQL & Redis HA, RPO ≤ 15 mnt, RTO ≤ 4 jam [Menebak] |
| Integrasi | GitHub App (P9) | + status CI, merge queue, environment | + release train, CODEOWNERS, SIEM, budget per organisasi |
| Fase / gate | P0–P12 / G3 | P13 / G13 | P14 / G14 |

```mermaid
flowchart TB
  U["Pengguna · SSO OIDC/SAML"] --> LB["Caddy / load balancer"]
  LB --> API["api ×N (stateless)"]
  API --> PG[("PostgreSQL HA")]
  API --> RD[("Redis HA")]
  API --> SCH["Scheduler + merge queue<br/>penempatan task per node"]
  SCH --> N1["Executor node 1<br/>worker + sandbox-manager<br/>+ docker-socket-proxy"]
  SCH --> N2["Executor node 2..n<br/>gVisor / microVM"]
  N1 & N2 --> WS[("Workspace store<br/>volume bersama atau<br/>git bundle per task")]
  N1 & N2 --> EXT[("Claude API · GitHub · vendor")]
  API --> SIEM[("SIEM / OTLP")]
```

Prinsip: **satu basis kode, tier = konfigurasi (`DEPLOYMENT_TIER`) + topologi**; fitur tier lebih tinggi tidak boleh melemahkan kontrol tier S (sandbox, approval, evidence). Node executor memakai token terotasi dan hanya rahasia antreannya (ADR-053); kegagalan node → task dilanjutkan dari checkpoint. Repo besar memakai sparse checkout + index simbol inkremental di sandbox; embedding tetap dikecualikan (ADR-046, ADR-062).

---

## 8. Tech Stack v2

Tabel ini mengikat (ADR-049, K4). **Snapshot** = sudah ada di kode 21 Okt 2026; **rencana** = belum ada. Versi library dipin saat fase coding dimulai dan diverifikasi ulang (rilis berumur ≥ 2 minggu, audit dependensi bersih).

### 8.1 Stack per layer

| Layer | Teknologi | Kebijakan versi | Status |
|---|---|---|---|
| Frontend inti | React 19, TypeScript strict, Vite, React Router 7, TanStack Query 5, Tailwind CSS 4 + Radix (gaya shadcn/ui), Lucide, Monaco (lokal), xterm.js, React Hook Form + Zod, Zustand selektif, i18next | Exact pin di `package.json` + `package-lock.json` | snapshot |
| Frontend — jawaban AI | `react-markdown` + `remark-gfm` + `rehype-sanitize` (tanpa HTML mentah) | Exact pin, dipilih saat WP-10.5 | rencana |
| Frontend — kode di chat | Shiki (bahasa dimuat lazy, tanpa `dangerouslySetInnerHTML`) | Exact pin; grammar dibundel lokal | rencana |
| Frontend — pratinjau PDF | `pdfjs-dist` dengan worker lokal, eval & scripting PDF dimatikan | Exact pin | rencana |
| Backend inti | Python 3.12 (CI juga 3.13), FastAPI, Pydantic v2, SQLAlchemy 2 async + asyncpg, Alembic, Celery 5 + Redis, structlog, httpx, prometheus-client, PyYAML | Pin penuh di `uv.lock` | snapshot |
| Backend — antrean v2 | Celery `task_routes` → `ide`, `assistant`, `media`, `integrations`; 4 container worker | Konfigurasi kode | rencana |
| AI — teks & agent | SDK resmi `anthropic` (hanya `app/llm/anthropic_provider.py`): Messages streaming, tool use, prompt caching | Model ID dari `CLAUDE_MODEL_*` (ADR-004) | snapshot |
| AI — fitur v2 | Server tools `web_search`, `web_fetch`, code execution + Agent Skills `pptx/docx/xlsx/pdf`, Files API, citations, blok `search_result` & `document` | Versi tool & beta header dipin di konfigurasi; naik hanya lewat ADR + contract test | rencana |
| Gambar | Adapter `ImageProvider`; implementasi vendor lewat httpx REST di `app/llm/image_<vendor>.py` | Endpoint & model di `config/providers.yaml`; harga di `pricing.yaml` | rencana |
| Pengetahuan (KB) | PostgreSQL FTS (`tsvector` konfigurasi `simple`), `pg_trgm` (GIN), ringkasan kontekstual oleh Claude tier `fast` | Ekstensi contrib PostgreSQL 16 | rencana |
| Dokumen lokal | Image sandbox `kodekantor/sandbox-docs:1`: Python 3.12, python-pptx, python-docx, openpyxl, pdfplumber, pypdf, pandas, matplotlib, Pillow, Jinja2, LibreOffice headless, poppler-utils, font sans-serif (Inter, Noto Sans, Liberation Sans) | Base image dipin digest; `requirements.lock` dengan hash; SBOM; rebuild bulanan + Trivy | rencana |
| GitHub | GitHub App (REST, `X-GitHub-Api-Version` dipin, webhooks), `PyJWT[crypto]` (RS256, hanya `app_auth.py`), httpx, git CLI ≥ 2.31 | `uv.lock`; `GITHUB_API_VERSION` | rencana |
| Portfolio | Jinja2 (autoescape) di dalam `sandbox-docs`; Caddy `file_server` di `PORTFOLIO_DOMAIN`; opsi GitHub Pages | Template berversi di repo | rencana |
| Data | PostgreSQL 16, Redis 7, asset store filesystem `ASSETS_ROOT` | Tag image dipin (`postgres:16.15-alpine`, `redis:7.4.11-alpine`) | snapshot / rencana |
| Eksekusi | Docker sandbox profil tetap, sandbox-manager + docker-socket-proxy, gVisor opsional, egress proxy (P1) | Image dipin; tanpa auto-pull (ADR-026) | snapshot |
| Infra | Docker Compose, Caddy 2, Ubuntu Server LTS | Tag image dipin | snapshot |
| Kualitas | pytest, pytest-asyncio, respx, Ruff, Pyright, import-linter, Vitest + RTL, Playwright, gitleaks, Semgrep, Trivy; baru: fixture webhook GitHub, contract test provider, eval harness sitasi & retrieval | Lockfile | snapshot + rencana |
| Observabilitas | structlog JSON + redaksi, `audit_events`, Prometheus `/metrics`, OpenTelemetry opsional; metrik per capability & antrean | – | snapshot + rencana |
| **v2.1** — Editor & terminal | Monaco (mode edit + diff editor 3-way), xterm.js mode interaktif; satu route WebSocket FastAPI khusus terminal (tiket sekali pakai + cek Origin); PTY lewat *attach stream* Docker via socket proxy (tanpa `docker exec`) [Kemungkinan Besar — wajib diuji] | Exact pin | rencana |
| **v2.1** — Run, format, index | Profil sandbox `run.*`, `*.test_select`, `fmt.*`, `lint.*_file`, `index.ctags_incremental` (universal-ctags / tree-sitter) | Image sandbox dipin digest | rencana |
| **v2.1** — Identitas | OIDC (pustaka terawat), SAML (mode strict, verifikasi tanda tangan & audience), SCIM v2 | `uv.lock` | rencana (tier M/L) |
| **v2.1** — Tier M/L | Node executor (worker + sandbox-manager), lease ber-epoch, workspace store (NFS / git bundle / object storage); tier L: PostgreSQL & Redis HA, microVM (Firecracker/Kata), SIEM (syslog/OTLP) | ADR per komponen | rencana (P13–P14) |
| Demo saja (bukan produk) | Pyodide 0.26.4 (Python WebAssembly) untuk menjalankan test hasil editan di live demo | Exact pin, lazy-load | demo |

### 8.2 Yang sengaja tidak dipakai

| Tidak dipakai | Alasan | Dipertimbangkan ulang bila |
|---|---|---|
| Vector DB / pgvector / provider embedding | ADR-046: Anthropic tidak menyediakan embedding; PO meminta semua AI lewat Claude | Recall@8 KB < ambang → ADR `EmbeddingProvider` (mis. Voyage AI atau `voyage-4-nano` self-hosted) dengan persetujuan PO |
| Kubernetes, message bus (Kafka/RabbitMQ/NATS), microservice baru | Single VPS, modular monolith (ADR-010, ADR-036) | Scale-out terbukti tidak cukup |
| WebSocket selain terminal | SSE + `Last-Event-ID` cukup (ADR-014); pengecualian tunggal: terminal interaktif (ADR-059) | Voice (P2) |
| Framework agent pihak ketiga (LangChain, LlamaIndex, dll.) | Runtime sendiri sudah ada; menyembunyikan tool loop dari ToolGateway | – |
| Elasticsearch/OpenSearch | FTS PostgreSQL cukup untuk pilot | Volume KB sangat besar [Menebak ±5 juta chunk] |
| Model gambar self-hosted | Butuh GPU | Node GPU tersedia |
| CDN pihak ketiga (Monaco, pdf.js, font) | CSP `default-src 'self'` | – |
| Cookie/pelacak pihak ketiga di situs portfolio | Privasi | Analitik P2 tanpa cookie |
| S3/object storage | Filesystem + backup cukup untuk pilot | Multi-node |

### 8.3 Konfigurasi kunci v2

| Variabel | Default | Dipakai oleh |
|---|---|---|
| `WEB_SEARCH_TOOL_VERSION` / `WEB_FETCH_TOOL_VERSION` | versi dipin | worker-assistant |
| `CODE_EXECUTION_TOOL_VERSION`, `DOC_SKILLS_VERSION` | versi dipin | worker-media |
| `IMAGE_PROVIDER`, `IMAGE_PROVIDER_API_KEY`, `IMAGE_DAILY_QUOTA_PER_USER` | – / – / `20` | kuota: api + worker-media; kunci hanya worker-media |
| `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH`, `GITHUB_CLIENT_SECRET` | – / `/run/secrets/github_app.pem` / – | **worker-integrations saja** |
| `GITHUB_WEBHOOK_SECRET` | – | api |
| `KK_PROCESS_ROLE`, `WORKER_QUEUES` | – / `ide,assistant,media,integrations` | semua proses backend |
| `ASSETS_ROOT`, `ASSET_SIGNED_URL_TTL_SECONDS` | `/srv/kodekantor/assets` / `300` | api, worker-media, worker-integrations |
| `SANDBOX_IMAGE_DOCS` | `kodekantor/sandbox-docs:1` | sandbox-manager |
| `PORTFOLIO_PUBLIC_BASE_URL`, `PORTFOLIO_SITE_ROOT` | `https://portfolio.<domain>` / `/srv/kodekantor/portfolio` | api, worker-integrations |
| `DEPLOYMENT_TIER` | `S` | semua (v2.1) |
| `MERGE_QUEUE_REQUIRED_PROFILES` | profil test stack | merge queue (v2.1) |
| `TERMINAL_ENABLED`, `TERMINAL_SESSION_MAX_MINUTES`, `TERMINAL_IDLE_MINUTES` | `false` / `30` / `10` | api, sandbox-manager (v2.1) |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | – | api (client id), worker-integrations (secret) — v2.1 |
| `SAML_IDP_METADATA_URL`, `SCIM_TOKEN`, `EXECUTOR_NODE_TOKEN_TTL_HOURS`, `SPARSE_CHECKOUT_ENABLED` | – | tier M/L (v2.1) |

Daftar lengkap dan catatan: `CONFIGURATION.md`.

---

## 9. Data Model

Konvensi v1 berlaku: PK UUID, `created_at`/`updated_at`, enum `VARCHAR + CHECK`, isi file tidak di DB (hanya `storage_ref`). **Semua tabel v2 ber-`owner_id` + scope**, dan **tidak ada token GitHub, private key, atau API key vendor di tabel mana pun**.

### 9.1 Entitas v1 (ringkas)

Identitas & akses (`users`, `user_roles`, `sessions`, `policy_settings`), project & repository (`projects`, `repositories`, `repo_index_entries`), requirement & acceptance (`requirements`, `acceptance_criteria`, `acceptance_reports`), percakapan (`conversations`, `messages`), task (`tasks`, `task_dependencies`, `task_events`), agent & tool (`agent_profiles`, `agent_runs`, `tool_calls`), evidence (`artifacts`, `test_runs`, `findings`), `approvals`, provider & usage (`provider_configs`, `usage_records`), `audit_events` (append-only), `project_memory`, `deployments`.

### 9.2 Entitas v2

| Tabel | Isi kunci | Modul |
|---|---|---|
| `assistant_spaces`, `assistant_threads`, `assistant_messages` | Ruang dengan label sensitivitas, thread, pesan (diredaksi), mode, status, usage | M21 |
| `message_citations` | url, judul, `cited_text`, waktu pencarian, query; jenis lokasi + rujukan potongan KB | M21 |
| `assistant_files`, `kb_collections`, `kb_documents`, `kb_chunks` | File unggahan; koleksi; dokumen KB; potongan dengan offset, ringkasan kontekstual, kata kunci, `tsvector` + indeks trigram — **tanpa kolom embedding** | M21 |
| `assets` | Registry objek biner (satu-satunya sumber `storage_ref`): kind, mime, sha256, sensitivitas, provenance, pin, retensi | M21–M23 |
| `generated_assets` | `asset_id`, jenis (`image`/`pptx`/`docx`/`xlsx`/`pdf`/`chart`), engine, provider, model, prompt_hash, validasi | M21, M23 |
| `assistant_events`, `moderation_events` | Event SSE persisten per thread; verdict moderasi yang dapat diaudit | M21 |
| `custom_assistants` | System prompt, capability terpilih, koleksi, pembagian per role | M21 |
| `capability_settings`, `capability_usage` | Provider, flag, budget, kill switch; unit, kuantitas, estimasi biaya, `cost_status` | M24 |
| `github_installations`, `github_installation_grants`, `github_user_links`, `github_link_requests`, `github_repositories`, `github_repo_snapshots`, `webhook_deliveries`, `github_pull_requests` | Instalasi & grant per user, tautan identitas, repo terpilih, snapshot metadata untuk portfolio, delivery id unik (dedupe), PR yang dibuka | M22 |
| `portfolio_profiles`, `portfolio_items`, `portfolio_versions`, `portfolio_publications` | Profil (kontak + flag publik), item (`draft`/`public`, sumber `manual`/`github`/`kodekantor`/`ai_draft`), snapshot versi, publikasi (target, url, approval) | M23 |

### 9.3 Perubahan pada tabel v1

| Tabel | Perubahan | Keputusan |
|---|---|---|
| `agent_runs`, `tool_calls` | `thread_id`; `agent_runs`: tepat satu dari `task_id`/`thread_id`; `tool_calls`: paling banyak satu (job tool-mode tanpa scope); `tool_calls.executor` (`backend`/`sandbox`/`worker`/`provider`) | ADR-050, ADR-051 |
| `approvals` | `project_id` nullable, `scope_type` (`project`/`portfolio`/`integration`), `owner_id`; `action_type` + `github_push_pr`, `portfolio_publish` | ADR-056 |
| `repositories.provider` | + `github_app` | ADR-041 |
| `agent_profiles` | `target_mode`, `phase`, grup v2; permission `network: provider_web`, `portfolio`, `data_sensitivity_max` | ADR-048 |
| `policy_settings` | Allow/block domain riset, anggaran pencarian, retensi per jenis aset | ADR-038, ADR-045 |

Migrasi linear `0002` → `0006`: 0002 penyelarasan v1.1 (ADR-021..035), 0003 assistant + scope approval (di-land *schema-first* di WP-9.0), 0004 GitHub, 0005 portfolio, 0006 KB. **v2.1:** 0007_sdlc (`work_items`, `work_item_links`, `tasks.work_item_id`, `merge_queue_entries`, `releases`, `release_items`, `environments`, `ci_check_results`, `terminal_sessions`, `test_runs.initiated_by` + `suite='run'`) dan 0008_scale (`organizations`, `org_memberships`, `sso_connections`, `scim_tokens`, `executor_nodes`, `node_leases`, `project_repositories`, `cost_budgets`), di-land *schema-first* di WP-13.0; tabel tier L inert sampai WP-14.1. Retensi v1 tetap (task events 90 hari, artefak 30 hari, audit 365 hari); retensi per jenis aset v2 menunggu keputusan PO (§15.3 #11).

---

## 10. API Surface

Prinsip v1 berlaku untuk semua endpoint v2: base `/api/v1`, envelope `{request_id, status, data, error, meta}`, CSRF untuk request mutating, `Idempotency-Key`, pagination, operasi panjang → 202 + SSE. Kode error baru antara lain `CAPABILITY_DISABLED`, `QUOTA_EXCEEDED` (429), `SENSITIVITY_BLOCKED` (403), `HEAD_MOVED`. Kontrak mengikat: `API_CONTRACTS.md` §12.

| Domain | Endpoint utama | Izin |
|---|---|---|
| Capability | `GET /capabilities` (modul aktif + kuota tersisa) | auth |
| Ruang & thread | `GET/POST/PATCH /assistant/spaces`, `GET/POST/PATCH /assistant/threads` | `assistant:use` |
| Pesan | `POST /assistant/threads/{id}/messages` (202) · SSE `GET /assistant/threads/{id}/events` · regenerate · cancel | `assistant:use` |
| File & KB | `POST /assistant/files` · koleksi & pertanyaan KB | `assistant:use` |
| Dokumen | `POST /assistant/documents` `{kind, outline, template_id, engine?}` | `assistant:docs` |
| Gambar | `POST /assistant/images` `{prompt, size, n, style, references[]}` | `assistant:image` |
| Aset | `GET /assets/{id}/download` (URL bertanda tangan, `attachment`, `nosniff`) | pemilik aset |
| GitHub | `GET /integrations/github/install-url` · `GET …/callback` · `POST …/webhook` (publik, HMAC) · `GET …/installations` · `POST …/installations/{iid}/sync` · `GET …/repos` | install/sync: `integrations:github:manage`; callback: sesi; webhook: HMAC; repos: auth + grant instalasi |
| GitHub ↔ project | `POST /projects` `source:{type:"github_app"}` · `POST /projects/{id}/git/pull` · `POST /tasks/{id}/github/pr-draft` · `POST /tasks/{id}/github/push-pr` → approval · `GET /tasks/{id}/github/pr` | create: auth + grant repo; pull: `workspace:write` + `git:read`; draft: `task:control`; push-pr: `github:push`; status PR: `task:read` |
| Portfolio | profil, item, impor GitHub/project, `POST /portfolio/cv`, `POST /portfolio/preview`, `POST /portfolio/publish` → approval, publikasi, ekspor/hapus | `portfolio:edit` / `portfolio:publish` |
| Admin capability | budget, kill switch, routing provider | admin |

Event SSE Portal AI: `assistant.delta` (ephemeral), `citation.added`, `asset.created`, `usage.updated`, `message.completed`; reconnect lewat `Last-Event-ID` atau `?after_seq=` (ADR-014).

**Endpoint v2.1** (kontrak: `API_CONTRACTS.md` §13):

| Domain | Endpoint utama | Izin |
|---|---|---|
| Work item & SDLC | `GET/POST /projects/{id}/work-items` · `GET/PATCH /work-items/{wid}` · `PUT /work-items/{wid}/requirements` · `POST /work-items/{id}/plan` → `POST …/plan/accept` · `GET /work-items/{wid}/board` · `GET /work-items/{wid}/traceability?format=json\|csv\|pdf` · `GET /projects/{id}/program-dashboard` | `project:read`, `workitem:write`, `workitem:plan` |
| Merge queue & release | `GET/POST /merge-queue/{repo_id}…` · `PUT /merge-queue/{repo_id}/policy` · `GET /tasks/{tid}/ci-checks` · `GET/POST /projects/{id}/releases` · `POST /releases/{rid}/promote` · `GET/POST /projects/{id}/environments` | `merge_queue:manage`, `release:manage`; entri `user_branch`: `workspace:write` + `git:branch`; promosi production: `deploy:production` (R3) |
| Developer workspace | `POST/PATCH/DELETE /projects/{id}/files` · `PUT /projects/{id}/files/{path}` · `POST /projects/{id}/files/merge3` · `POST /projects/{id}/workspace-ops/undo` · `POST /projects/{id}/format` · `POST /tasks/{id}/takeover` · `POST /tasks/{id}/takeover/release` · `POST /projects/{id}/runs` · `POST /projects/{id}/test-runs` · `POST /projects/{id}/git/{stage\|commit\|checkout\|discard}` | `workspace:write`, `task:control`, `test:run` (run & test), `git:branch` (commit/checkout) |
| Terminal | `POST /projects/{id}/terminal-sessions` · `POST /terminal-sessions/{sid}/ticket` · WebSocket `GET /api/v1/terminal-sessions/{id}/ws` (satu-satunya WS) · `DELETE /terminal-sessions/{sid}` | `terminal:use` (bila diaktifkan admin) |
| Node & kapasitas | `GET/POST /admin/nodes` · `POST /admin/nodes/{nid}/drain` · `GET /capacity` · `POST /internal/nodes/register` (internal, token node) | `admin:nodes` |
| Identitas enterprise | SSO OIDC/SAML · `/scim/v2/*` · `/orgs` | `admin:sso`, `admin:orgs` |

Kode error baru antara lain `TAKEOVER_ACTIVE`, `MERGE_QUEUE_FAILED` (dengan `stage`), `TERMINAL_DISABLED`, `NODE_UNAVAILABLE`, `ORG_SCOPE`.

---

## 11. Keamanan, Isolasi, dan Approval

### 11.1 Tingkat risiko

| Risk | Kebijakan | Contoh v2 |
|---|---|---|
| R0 Read | Diizinkan dalam scope | `capabilities.list`, `web.search`, `web.fetch`, `kb.search`, `moderation.*`, `github.list_repos`, `portfolio.privacy_check` |
| R1 Reversible scoped write | Hanya di area terisolasi milik user | `docs.render_skills`, `assets.store`, `image.generate/edit`, `github.import_repo`, `github.pull`, `portfolio.draft_item` |
| R2 Execute in sandbox | Sandbox profil tetap, network none, limit | `docs.render_local`, `analysis.run`, `code.execute_remote` (provider), `portfolio.build_site` |
| R3 External / destructive | Approval eksplisit, sekali pakai, terikat payload hash, audit | `github.push_branch`, `github.open_pr`, `portfolio.publish` |
| R4 Disallowed | Tidak didaftarkan | Force push, hapus branch remote, merge PR, ubah `.github/workflows/**` |

### 11.2 Ancaman tambahan v2 (T11–T18)

| ID | Ancaman | Kontrol utama |
|---|---|---|
| T11 | Prompt injection dari halaman web riset | Konten web = data; tanpa tool R3/R4 di mode riset; sitasi tampil |
| T12 | Kebocoran kunci/token GitHub App | Private key hanya di `worker-integrations` (secret file); token per operasi ≤ 1 jam, tidak disimpan; redaksi `ghs_`/`ghu_`/`github_pat_` |
| T13 | Agent mengubah workflow CI untuk mencuri secret | Permission Workflows tidak diminta + blok path `.github/workflows/**` |
| T14 | Webhook palsu/replay | HMAC SHA-256 atas body mentah, constant-time, dedupe delivery id, batas ukuran body |
| T15 | Data sensitif ke vendor (Skills tidak ZDR, vendor gambar) | Label sensitivitas; engine lokal untuk `confidential`; tabel data-flow |
| T16 | Penyalahgunaan generate gambar | Kebijakan prompt (S52) + moderasi vendor + audit + kuota |
| T17 | Konten portfolio palsu/berlebihan | AI hanya draft; review wajib per kalimat; penanda AI |
| T18 | Publikasi data pribadi tidak sengaja | Publish = R3 dengan pratinjau, cek privasi, dan secret scan |

T1–T10 v1 (prompt injection repo, workspace escape, host compromise, resource exhaustion, secret leakage, cross-project, dangerous action, supply chain, data loss, audit gaps) tetap berlaku: `SECURITY_MODEL.md`.

### 11.2b Ancaman tambahan v2.1 (T19–T23)

| ID | Ancaman | Kontrol utama |
|---|---|---|
| T19 | Terminal interaktif disalahgunakan (eksfiltrasi, mining, escape) | Sandbox sama dengan run (network off, limit), default nonaktif, durasi & idle timeout, audit teredaksi, rate limit sesi |
| T20 | Kebocoran lintas tenant | Scope organisasi di setiap query, path workspace, cache; test 404 lintas organisasi |
| T21 | Node executor dikompromikan | Identitas node + token terotasi, mTLS, rahasia antrean saja, drain & revoke node |
| T22 | Salah konfigurasi SSO/SCIM | Validasi tanda tangan & audience, JIT nonaktif default, pemetaan grup eksplisit |
| T23 | Bypass merge queue | Branch target dilindungi; merge hanya oleh worker merge queue; audit |

### 11.3 Izin RBAC v2

| Izin | owner | admin | developer | reviewer | read-only |
|---|---|---|---|---|---|
| `assistant:use` | ✓ | ✓ | ✓ | ✓ | – |
| `assistant:docs` | ✓ | ✓ | ✓ | – | – |
| `assistant:image` | ✓ | ✓ | ✓ | – | – |
| `integrations:github:manage` | ✓ | ✓ | – | – | – |
| `github:push` (minta push + PR) | ✓ | ✓ | ✓ | – | – |
| `portfolio:edit` | ✓ | ✓ | ✓ | ✓ | – |
| `portfolio:publish` | ✓ | ✓ | ✓ | ✓ | – |
| `workitem:write` / `workitem:plan` *(v2.1)* | ✓ | ✓ | ✓ | – | – |
| `merge_queue:manage` *(v2.1)* | ✓ | ✓ | – | – | – |
| `release:manage` *(v2.1)* | ✓ | ✓ | ✓ | – | – |
| `terminal:use` *(v2.1, bila diaktifkan)* | ✓ | ✓ | ✓ | – | – |
| `admin:nodes`, `admin:sso` *(v2.1)* | ✓ | ✓ | – | – | – |
| `admin:orgs` *(v2.1)* | ✓ | – | – | – | – |

Pemetaan ini usulan (`API_CONTRACTS.md` §12 dan §13.9) dan menunggu persetujuan PO; budget per capability per user per bulan dengan kill switch admin.

### 11.4 Matriks approval v2

| action_type | Payload terikat | Pemutus | Self-approval |
|---|---|---|---|
| `github_push_pr` | repo, installation, base/head branch, `base_commit`, `head_commit`, `diff_sha256`, file berubah, `workflow_paths_touched: false`, hasil secret scan, judul & `body_sha256` PR | `approval:decide` + `github:push` | Boleh di pilot single-owner; dilarang bila policy `four_eyes` aktif (usulan: otomatis bila user > 1) |
| `portfolio_publish` | profile, version, `snapshot_sha256`, `bundle_sha256`, target, url, `privacy_report_sha256`, kontak publik, item repo privat, `ai_unapproved_count: 0` | `portfolio:publish` + pemilik profil | Boleh — hanya pemilik yang dapat memutuskan publikasi portfolionya |

Keduanya kedaluwarsa 24 jam dan dieksekusi lewat `ApprovalGrant` (ADR-052): approval dikonsumsi sekali oleh job server, lalu langkah yang field-nya terikat payload dijalankan dalam ≤ 15 menit. Head berubah → `HEAD_MOVED`, approval lama `superseded`. LLM tidak dapat membuat, memperluas, atau memperpanjang grant. `git_push` (ADR-003) tetap dicadangkan untuk remote generik non-GitHub.

---

## 12. Non-Functional Requirements

NFR-01..NFR-12 v1 tetap berlaku (availability, performance, scalability, security, reliability, observability, maintainability, provider portability, accessibility, data durability, cost control, localization) dan diterapkan pada layar & job v2. NFR baru:

| ID | Area | Target (pilot) | Verifikasi |
|---|---|---|---|
| NFR-13 | Citation fidelity | 100% jawaban riset berklaim web punya ≥ 1 sitasi tampil; 100% jawaban tanpa `web_search` berlabel; ≥ 90% sitasi mendukung klaim [Menebak] | Eval harness sitasi WP-10.6 |
| NFR-14 | Latency Portal AI | Ack 202 p95 ≤ 300 ms; TTFT chat p95 ≤ 3 dtk; riset Cepat selesai p95 ≤ 60 dtk; riset Mendalam progres tiap ≤ 15 dtk [Menebak] | Histogram Prometheus per capability |
| NFR-15 | Transparansi biaya | 100% panggilan provider tercatat; estimasi tampil sebelum generate gambar; `cost_status` selalu terisi | Test akuntansi usage (mock respx) |
| NFR-16 | Data-flow & ZDR | Setiap capability aktif punya entri data-flow; 0 panggilan Skills/Files API untuk `confidential` | Validasi registry + test routing AX-06 |
| NFR-17 | Portabilitas provider per capability | Ganti vendor = konfigurasi + adapter + contract test; 0 perubahan di `services/` domain | Contract test + import-linter |
| NFR-18 | Keamanan konten | 100% prompt gambar dimoderasi; 0 hasil flagged tampil; 0 tool R3/R4 di mode riset; 0 konten AI belum disetujui terpublikasi | Fixture moderasi & prompt injection |
| NFR-19 | Rahasia GitHub | 0 token/private key di DB, log, event, artefak, `.git/config`, env sandbox; umur token ≤ 1 jam | Test AX-01 (grep), test redaksi |
| NFR-20 | Degradasi terkendali | Capability terdampak berstatus *degraded* ≤ 1 menit; antrean `ide` tetap jalan saat `media` penuh; jawaban tak lengkap tidak pernah berstatus selesai | Test fault injection |
| NFR-21 | Aksesibilitas & i18n layar baru | 0 pelanggaran a11y *serious/critical*; sitasi & badge AI dapat dijangkau keyboard; semua teks lewat i18n | axe via Playwright + checklist |
| NFR-22 | Auditabilitas aksi keluar | 100% aksi keluar (query riset, unggah ke Skills/Files, generate gambar, token & operasi GitHub, webhook, publikasi) menulis `audit_events` tanpa secret | Test audit trace |

**NFR v2.1:**

| ID | Area | Target (rancangan) | Verifikasi |
|---|---|---|---|
| NFR-23 | Editor & workspace | Buka file ≤ 1 MB p95 ≤ 1 dtk; ack simpan p95 ≤ 300 ms; 100% konflik versi terdeteksi [Menebak] | Test beban kecil + test konflik |
| NFR-24 | Kapasitas per tier | S: 2 task aktif; M: ≥ 2 per node, antre p95 ≤ 5 menit; L: ≥ 50 bersamaan [Menebak] | Benchmark G13/G14 |
| NFR-25 | Ketahanan node | Task dilanjutkan ≤ 5 menit setelah node mati; 0 side effect ganda [Menebak] | Test chaos |
| NFR-26 | Isolasi tenant & identitas | 0 akses lintas organisasi; deprovision SCIM ≤ 5 menit [Menebak] | Test cross-tenant + SCIM |
| NFR-27 | Integritas branch target | Repo dengan merge queue aktif: 100% commit lewat test wajib | Audit merge queue |

---

## 13. Acceptance Criteria dan Strategi Uji

### 13.1 AC-01..AC-20 (v1, tetap)

Login & isolasi project (AC-01), project & impor aman (AC-02), IDE (AC-03), prompt → task (AC-04), streaming & reconnect (AC-05), allowlist tool (AC-06), sandbox (AC-07), runner test (AC-08), diff & reject (AC-09), review findings (AC-10), User Proxy per kriteria (AC-11), requirement gagal tidak diterima (AC-12), approval R3 (AC-13), cancel (AC-14), secret redaction (AC-15), usage/biaya jujur (AC-16), recovery (AC-17), remediasi gap (AC-18), audit (AC-19), deployment terdokumentasi (AC-20). **Status snapshot: test komponen ada untuk sebagian besar AC, tetapi belum ada AC yang terbukti E2E di CI/staging** — syarat MVP DoD.

### 13.2 AX-01..AX-11 (v2)

| AX | Kriteria | Bukti | WP → gate |
|---|---|---|---|
| AX-01 | Repo privat dapat diimpor lewat GitHub App tanpa token tersimpan di DB/log/`.git/config` | Test integrasi App uji + grep token | WP-9.1, WP-9.3 → G9 |
| AX-02 | Push & PR hanya setelah approval R3 terikat `head_commit`; workflow CI tidak dapat diubah | Test approval + path policy | WP-9.4 → G9 |
| AX-03 | Webhook dengan tanda tangan salah ditolak; delivery ganda diabaikan | Test HMAC & dedupe | WP-9.2 → G9 |
| AX-04 | Setiap klaim web disertai sitasi + tanggal pencarian; jawaban tanpa sumber berlabel | Eval harness sitasi | WP-10.3, WP-10.5 → WP-10.6 |
| AX-05 | Dokumen PPT/DOCX/XLSX/PDF valid dapat diunduh lewat URL bertanda tangan | Test buka file dengan parser format | WP-10.4 → WP-10.6 |
| AX-06 | Data `confidential` memakai engine lokal; tidak ada panggilan Skills | Test routing + audit | WP-10.4 → G10 |
| AX-07 | Generate gambar mematuhi moderasi, kuota, dan menyimpan provenance | Contract test provider + test kuota | WP-11.2 → G11 |
| AX-08 | Publikasi portfolio butuh approval R3 + cek privasi; repo privat hanya metadata terpilih | Test approval + snapshot | WP-12.2, WP-12.4 → G12 |
| AX-09 | AI di portfolio hanya membuat draft bertanda; tidak mengarang | Review + test label | WP-12.1, WP-12.3 → G12 |
| AX-10 | Jawaban KB menampilkan sitasi ke potongan dokumen sumber | Test `search_result_location` → `kb_chunks` benar; eval Recall@8 | WP-11.3 → G11 |
| AX-11 | Router hanya memanggil capability yang aktif & dalam kuota | Test router/kuota/kill switch: 0 panggilan provider | WP-9.0, WP-10.1 → G10 |

### 13.2b AX-12..AX-18 (v2.1)

| AX | Kriteria | Bukti | WP → gate |
|---|---|---|---|
| AX-12 | Buat/edit/rename/hapus file dengan version check; tidak ada overwrite diam-diam; take-over menandai bukti stale & memblokir apply sampai validasi ulang | Test integrasi workspace + E2E edit/konflik/take-over | WP-13.1 → G13 |
| AX-13 | Run, test, dan terminal dari IDE hanya di sandbox dan tersimpan sebagai bukti | E2E run/test + test isolasi terminal (T19) | WP-13.2, WP-13.3 → G13 |
| AX-14 | Story ≥ 5 task [Menebak] paralel; merge queue menguji ulang tiap entri; branch target tanpa commit gagal | E2E program kecil + test merge queue | WP-13.4, 13.5, 13.9, 13.12 → G13 |
| AX-15 | Release dapat ditelusuri requirement → story → task → commit → test → acceptance dan diekspor | Test ekspor traceability | WP-13.4, 13.6, 13.10 → G13 |
| AX-16 | Tier M: node ditambah tanpa ubah kode domain; node gagal → task dilanjutkan dari checkpoint | Test chaos + penempatan | WP-13.7 → G13 |
| AX-17 | OIDC (G13); SAML, SCIM ≤ 5 menit, isolasi organisasi, DR drill, SIEM berantai hash (G14) | Test SSO/SCIM, cross-tenant, DR drill | WP-13.8 → G13; WP-14.1..14.5 → G14 |
| AX-18 | Repo ≥ 1 juta LOC [Menebak]: index & pemilihan konteks dalam batas waktu; agent hanya menerima file relevan | Benchmark repo besar | WP-13.9, WP-14.7 → G14 |

### 13.3 Lapisan uji v2

| Lapisan | Tambahan v2 |
|---|---|
| Unit | Router capability, kuota, kill switch, redaksi pola GitHub, verifikasi HMAC, builder payload approval |
| Integrasi | Service + DB untuk assistant, KB, GitHub (mock respx), portfolio; migrasi 0002–0006 |
| Contract | `LLMProvider` (server tools, citations, Skills, Files), `ImageProvider`, `DocumentEngine`, GitHub REST — wajib lulus sebelum capability diaktifkan |
| Security | AX-01/02/03/06/08, T11–T18, prompt injection dari web, cross-user isolation untuk aset & thread |
| Eval | Sitasi (WP-10.6), retrieval KB (Recall@8 ≥ 0,85 [Menebak]), biaya per jawaban |
| E2E (Playwright) | Repo privat → task → approval → PR; riset bersitasi; dokumen; publikasi portfolio dengan approval |

---

## 14. Roadmap Implementasi dan Delivery Agentik

### 14.1 Fase

```mermaid
flowchart TB
  P0["P0–P2<br/>kontrak, fondasi, IDE"] --> P3["P3–P5<br/>agent loop, sandbox,<br/>review & User Proxy"]
  P3 --> P67["P6–P7<br/>kelengkapan + staging"]
  P67 --> G3{"G3<br/>MVP DoD"}
  G3 --> P8["P8 rollout specialist<br/>(per gate G8)"]
  G3 --> P9["P9 GitHub App<br/>(WP-9.0 kunci kontrak v2)"]
  P9 --> G9{"G9"}
  G9 --> P10["P10 Portal AI inti<br/>capability, riset, dokumen"]
  P10 --> G10{"G10"}
  G10 --> P11["P11 gambar, KB, analisis"]
  G10 --> P12["P12 Portfolio"]
  P11 --> G11{"G11"}
  P12 --> G12{"G12"}
  G3 --> P13["P13 SDLC end-to-end & tier M<br/>(status CI setelah G9)"]
  P13 --> G13{"G13"}
  G13 --> P14["P14 Enterprise tier L"]
  P14 --> G14{"G14"}
```

| Fase | Scope | Exit / gate | Estimasi |
|---|---|---|---|
| P0–P8 | Baseline PRD v1 (kontrak → fondasi → IDE → agent loop → sandbox & test → review & User Proxy → kelengkapan → staging → rollout specialist) | G0–G8; MVP DoD = G3 | Lihat WBS |
| P9 GitHub App | WP-9.0 kunci kontrak v2 (loader registry v2, RunScope, ApprovalGrant, migrasi 0002–0003), registrasi App, instalasi & webhook, impor/pull, push & PR R3, UI | **G9**: repo privat impor → task → approval → PR; token tidak tersimpan; webhook HMAC teruji; sign-off keamanan | WBS 8,5 jam agent (PE §8: ±10–14) [Menebak] |
| P10 Portal AI inti | Capability registry & provider gateway, assistant backend, riset bersitasi, engine dokumen + asset store, UI, evaluasi | **G10**: eval sitasi & biaya lulus ambang PO; data-flow terdokumentasi; capability nonaktif tidak dipanggil | WBS 11,5 jam agent (PE §8: ±18–24) [Menebak] |
| P11 Gambar, KB, analisis | ADR-040a vendor gambar, ImageProvider + moderasi, KB tanpa embedding, analisis data, UI | **G11**: moderasi & kuota teruji; vendor disetujui; KB Recall@8 ≥ 0,85 | WBS 8,5 jam agent (PE §8: ±12–16) [Menebak] |
| P12 Portfolio | Model konten, impor GitHub/KodeKantor, CV & asisten lamaran, situs + publish R3, UI | **G12**: CV & situs publik dari data nyata pengguna, publish via R3 | WBS 7,5 jam agent (PE §8: ±10–14) [Menebak] |
| P13 SDLC end-to-end & tier M (v2.1) | Kunci kontrak v2.1 (WP-13.0), developer workspace v2, run/test/terminal, work item & papan SDLC, merge queue, traceability, node executor, OIDC, repo besar/multi-repo, release train, aktivasi agent SDLC, status CI (setelah G9) | **G13**: program kecil end-to-end di tier M dengan merge queue & traceability; edit/take-over/run/test/terminal; failover node; OIDC | WBS 20,5 jam agent [Menebak] |
| P14 Enterprise tier L (v2.1) | ADR orkestrator, organisasi/tenant, SAML + SCIM, microVM, HA & DR, SIEM & budget organisasi, LSP & debugger, benchmark repo besar, aktivasi agent tier L | **G14**: SAML/SCIM, isolasi tenant, microVM, DR drill, SIEM, benchmark; sign-off keamanan | WBS 14,0 jam agent [Menebak] |

Estimasi = jumlah kolom *Est. durasi agent* di WBS (lebih rendah dari rentang awal PE §8 karena WP sudah dirinci); keduanya tebakan dan belum termasuk waktu review PO dan operator.

### 14.2 Tim delivery agentik

| Agent delivery | Peran | Model | Fase v2 |
|---|---|---|---|
| AG-00 | Lead Architect / Orchestrator — kontrak, ADR, integrasi, satu-satunya yang commit | Opus | Semua (WP-9.0) |
| AG-07 | Agentic Core — capability registry, provider gateway, server tool path | Opus | P10 |
| AG-12 | Integration & E2E QA — E2E, eval harness | Opus | P10–P12 |
| AG-13 | Security Reviewer — sign-off 🔒 GitHub, publikasi, data-flow, terminal, node, SSO/tenant | Opus | P9–P14 |
| AG-18 | Integrations Engineer (GitHub) | Opus | P9, P12 |
| AG-19 | AI Portal Backend | Opus | P10–P12 (WP-12.3) |
| AG-20 | AI Portal Frontend | Sonnet | P10–P11 |
| AG-21 | Portfolio Full-stack | Sonnet | P12 |
| AG-06, AG-03, AG-01, AG-02 | Infra, sandbox, backend core, workspace & Git — tier M/L, terminal, take-over, repo besar *(v2.1)* | Sonnet/Opus | P13–P14 |
| AG-15 | Specialist Rollout — aktivasi agent SDLC per G8 *(v2.1)* | Sonnet/Opus | P13–P14 |
| AG-03, AG-06, AG-08, AG-09, AG-10, AG-11 | Sandbox, infra, workflow, platform services, frontend workspace/admin (dukungan) | Sonnet/Opus | sesuai WP |
| PO / OPS | Product Owner (keputusan, gate) / operator VPS (secret, DNS, App GitHub) | Manusia | Semua |

Aturan kerja: path milik tegas per agent (anti konflik paralel), kontrak berubah → ADR, tidak ada klaim test lulus tanpa output, gate manusia tidak dapat dilewati. WBS lengkap (112 work package, gate G0–G14, wave W0–W12): `docs/wbs/` dan workbook Excel.

### 14.3 Definition of done per fase v2/v2.1

1. Semua AX fase terkait lulus dengan bukti tersimpan (output test aktual, bukan klaim).
2. Contract test provider untuk capability yang diaktifkan lulus; capability lain tetap nonaktif.
3. Tidak ada temuan keamanan kritis; sign-off AG-13 untuk P9, P12, P13 (terminal, node, OIDC), dan P14.
4. Data-flow, biaya, dan batasan capability tampil jujur di UI dan dokumen.
5. `IMPLEMENTATION_TRACKER.md` diperbarui; ADR terkait berubah dari *Proposed* ke *Accepted*.

---

## 15. Risiko, Asumsi, dan Keputusan Terbuka

### 15.1 Risiko utama v2

| ID | Risiko | Dampak | Mitigasi |
|---|---|---|---|
| RN-09 | Scope melebar tiga portal sebelum IDE stabil | High | Urutan P9–P12 hanya setelah G3; gate per fase |
| RN-10 | Kunci GitHub App bocor → akses repo privat | Critical | Secret file read-only hanya di `worker-integrations`, token per operasi ≤ 1 jam, redaksi, rotasi kunci di RUNBOOK |
| RN-11 | Agent Skills tidak tercakup ZDR | High | Engine lokal untuk `confidential`; tabel data-flow di Settings |
| RN-12 | Ekspektasi "akurat" melebihi kemampuan model | Medium | Sitasi wajib, label tanpa sumber, evaluasi WP-10.6 |
| RN-13 | Harga & ketersediaan vendor gambar berubah | Medium | Adapter netral, ADR-040a, kuota & estimasi biaya |
| RV-03 | Retrieval leksikal lemah untuk Bahasa Indonesia (tanpa stemming) | Medium–High | Kata kunci kata dasar + sinonim, multi-query I47, eval WP-11.3; ADR `EmbeddingProvider` bila gagal ambang |
| RN-06 | Integrasi IDE belum diuji E2E | High | Wave 3–4 (AG-12) sebelum klaim MVP |
| R-01 | Arbitrary code execution di VPS | Critical | Sandbox profil tetap, socket-proxy, `.git` mask, limit; gVisor di production |
| RS-01 | Scope melebar lagi (P13–P14) *(v2.1)* | High | Gate per fase; tier L hanya bila ada kebutuhan nyata |
| RS-02 | Merge queue menjadi bottleneck program besar *(v2.1)* | Medium | Batching entri yang tidak bersinggungan path; ukur di G13 |
| RS-03 | Terminal interaktif membuka jalur eksfiltrasi *(v2.1)* | High | ADR-059, default off, T19 |
| RS-04 | Biaya model naik dengan task paralel *(v2.1)* | Medium | Budget per program/epic, kill switch |
| RS-05 | Repo besar melampaui konteks model *(v2.1)* | Medium | Sparse checkout + index simbol + A04; tanpa janji "seluruh repo dalam konteks" |

Risiko v1 lainnya (R-02..R-10, RN-01..RN-08): `docs/wbs/` dan `IMPLEMENTATION_ROADMAP.md` §5.

### 15.2 Asumsi dan dependensi

| ID | Asumsi | Bila tidak terpenuhi |
|---|---|---|
| D-01 | Akun Claude Platform operator dapat memakai streaming, tool use, prompt caching, citations, `web_search`/`web_fetch`, code execution + Skills, Files API | Capability terkait dinonaktifkan di registry |
| D-03 | GitHub App dapat dipasang pada akun PO dan repo terpilih | P9 tidak dapat dimulai |
| D-04 | Vendor gambar dengan API generate/edit dan kebijakan data yang diterima PO tersedia (kandidat dari sumber pihak ketiga, perlu verifikasi) | Capability gambar tetap nonaktif |
| D-05 | Domain/DNS untuk `portfolio.<domain>` atau GitHub Pages | Publikasi tidak tersedia |
| D-06 | Versi git di image mendukung kredensial via `GIT_CONFIG_COUNT`/`http.extraheader` [Kemungkinan Besar] | Risiko token di config; diuji di AX-01 |
| D-08 | `sandbox-docs` (LibreOffice headless) berjalan dalam limit sandbox pilot [Menebak] | Timeout/OOM render; profil dengan limit sendiri |
| D-09 | Portal IDE lulus G3 sebelum P9 | Risiko R-06 berulang |
| D-10 | Retrieval leksikal + ringkasan kontekstual cukup akurat | ADR `EmbeddingProvider` |

### 15.3 Keputusan yang dibutuhkan dari Product Owner

| # | Keputusan | Rekomendasi default |
|---|---|---|
| 1 | Vendor gambar pertama (ADR-040a) dan anggaran gambar/bulan | Uji teks dalam gambar, edit, biaya, kebijakan data — putuskan di WP-11.1 |
| 2 | Engine dokumen default | Skills untuk non-sensitif, lokal untuk `confidential` |
| 3 | Target situs portfolio | Subdomain sendiri (`static`); GitHub Pages opsional |
| 4 | Akun GitHub yang dipasangi App dan repo awal | Akun pribadi PO, repo terpilih saja |
| 5 | Portal AI multi-user | Personal-first; berbagi asisten kustom per role |
| 6 | `web_research` di thread `confidential` | Nonaktif |
| 7 | Engine default analisis data | Lokal (`sandbox-docs`); remote hanya setelah registry I46 versi baru (`network: provider_web`, sensitivitas ≤ internal) + ADR |
| 8 | Mode worker di pilot | Split per antrean (ADR-053); compact hanya dev |
| 9 | `four_eyes` untuk `github_push_pr` | Aktif otomatis bila user > 1 |
| 10 | Batas biaya per pesan riset Mendalam | $1,00 per pesan, konfirmasi bila estimasi melebihi [Menebak] |
| 11 | Nilai retensi default per jenis aset | Setujui tabel desain v2 §7.6 |
| 12 | Ambang evaluasi sitasi, KB, dan biaya | Target pilot di §1.3, ditinjau setelah 1 bulan |
| 13 | Status ADR-036..056 | Putuskan sebelum WP-9.0 |
| 14 | Orkestrator tier L (VM pool vs Kubernetes) *(v2.1)* | Putuskan di WP-14.0 dari data beban tier M |
| 15 | Branch kerja user: `kk/user-*` + merge queue vs commit langsung ke default branch *(v2.1)* | `kk/user-*` + merge queue; commit langsung hanya tier S dengan merge queue nonaktif |
| 16 | Ambang G13/G14 (task paralel, LOC benchmark, RPO/RTO) *(v2.1)* | Gunakan target [Menebak] di §12 sebagai awal |
| 17 | Status ADR-057..062 *(v2.1)* | Putuskan sebelum WP-13.0 |

Isu terbuka yang belum terselesaikan (OI-09..OI-11, OI-13..OI-18; v2.1: OI-25, OI-26, OI-32, OI-34): `SYSTEM_SPEC.md` §11.3 dan §12.

---

## 16. Ringkasan ADR

### 16.1 ADR v2 & v2.1 (perluasan)

| ADR | Judul | Status |
|---|---|---|
| ADR-036 | KodeKantor Platform: tiga portal di atas satu core (modular monolith) | Proposed |
| ADR-037 | Provider gateway multi-model per capability (Claude utama) | Proposed |
| ADR-038 | Riset web memakai `web_search`/`web_fetch` Claude; sitasi wajib | Proposed |
| ADR-039 | Engine dokumen ganda: Agent Skills vs sandbox lokal | Proposed |
| ADR-040 | `ImageProvider` netral; vendor dipilih lewat ADR-040a | Proposed |
| ADR-041 | Integrasi GitHub lewat GitHub App (repo privat) | Proposed |
| ADR-042 | Portal Portfolio Profesional | Proposed |
| ADR-043 | ~~pgvector untuk tanya jawab dokumen~~ — **Superseded oleh ADR-046** | Superseded |
| ADR-044 | Capability Module Registry | Proposed |
| ADR-045 | Tata kelola data & ancaman T11–T18 | Proposed |
| ADR-046 | Tanya jawab dokumen tanpa embedding: retrieval leksikal + sitasi Claude (menggantikan ADR-043) | Proposed |
| ADR-047 | Agent bersitasi mengakhiri jawaban dengan teks bersitasi, bukan `submit_result` | Proposed |
| ADR-048 | Katalog agent v2: 11 agent baru + promosi E29 | Proposed |
| ADR-049 | Tech stack v2 | Proposed |
| ADR-050 | RunScope: run & tool record tanpa task (Portal AI/Portfolio) | Proposed |
| ADR-051 | Server tool Claude tetap lewat ToolGateway (`executor: provider`) | Proposed |
| ADR-052 | ApprovalGrant untuk eksekusi R3 multi-langkah | Proposed |
| ADR-053 | Worker per antrean & secret per role (pem hanya `worker-integrations`) | Proposed |
| ADR-054 | Workspace job `_jobs/<uuid>` & image `sandbox-docs` | Proposed |
| ADR-055 | Asset store & URL bertanda tangan | Proposed |
| ADR-056 | Scope approval v2 & action type `github_push_pr` / `portfolio_publish` | Proposed |
| ADR-057 | Hierarki kerja Program/Epic/Story/Task + merge queue + release train | Proposed |
| ADR-058 | Edit manual & take-over worktree task | Proposed |
| ADR-059 | Terminal interaktif sandbox untuk user (pengecualian WebSocket) | Proposed |
| ADR-060 | Tier deployment S/M/L & pool executor | Proposed |
| ADR-061 | Identitas enterprise & tenancy (OIDC, SAML, SCIM, organisasi) | Proposed |
| ADR-062 | Strategi repo besar & multi-repo | Proposed |

### 16.2 ADR v1 (baseline IDE)

| ADR | Judul | Status |
|---|---|---|
| ADR-001 | Technology baseline MVP | Accepted |
| ADR-002 | Identity: single-owner pilot, skema multi-user + RBAC sejak awal | Proposed |
| ADR-003 | Git: lokal dulu, remote write = P2 | Proposed |
| ADR-004 | Claude API key dikelola admin (server-side), model & budget dari konfigurasi | Proposed |
| ADR-005 | Workspace storage, kuota & retensi | Proposed |
| ADR-006 | Deviasi state machine untuk cancel/clarify/reopen | Accepted |
| ADR-007 | Sandbox egress default OFF + proxy allowlist untuk install dependency | Proposed |
| ADR-008 | Preview di subdomain terpisah | Proposed |
| ADR-009 | Sandbox-manager mengakses Docker hanya via socket proxy; gVisor opsional | Accepted |
| ADR-010 | Ukuran VPS awal & batas concurrency | Proposed |
| ADR-011 | Kebijakan pengiriman source code ke provider | Proposed |
| ADR-012 | 41 agent → 6 runtime LLM + tool deterministik | Accepted |
| ADR-013 | Output agent terstruktur via tool `submit_result` | Accepted |
| ADR-014 | Event streaming: PostgreSQL + Redis pub/sub + SSE | Accepted |
| ADR-015 | ScriptedProvider untuk test & demo, dilarang di production | Accepted |
| ADR-016 | Review-first via git worktree per task | Accepted |
| ADR-017 | Type checker: Pyright | Accepted |
| ADR-018 | Enum sebagai VARCHAR + CHECK | Accepted |
| ADR-019 | Ingress: Caddy | Accepted |
| ADR-020 | Katalog FR diturunkan karena FR-001..159 tidak didefinisikan di PRD | Proposed |
| ADR-021 | Tiga transisi state tambahan (apply conflict, R3 ditolak, approval saat planning) | Accepted (desain) |
| ADR-022 | Workflow ↔ services lewat `WorkflowPorts` (DI), tanpa import dinamis | Accepted (desain) |
| ADR-023 | Encoding nama tool untuk Claude API (`.` → `__`) | Accepted |
| ADR-024 | Semantik transaksi approval handler | Accepted |
| ADR-025 | `cost_status` menambah nilai `partial`; `agent_runs.status` menambah `approval_required` | Accepted (desain) |
| ADR-026 | Sandbox menyembunyikan `.git`, image tidak pernah auto-pull, flag socket-proxy minimum | Accepted |
| ADR-027 | Strategi remediasi: tambah langkah perbaikan gap; re-plan hanya bila requirement berubah | Accepted |
| ADR-028 | Dispatcher abstraction; Celery eager mode dilarang dari kode async | Accepted |
| ADR-029 | Penyelarasan kontrak API v1.1 (field yang dibutuhkan UI) | Accepted (desain) |
| ADR-030 | Redaksi `key=value` huruf kecil & pola tambahan | Accepted (desain) |
| ADR-031 | Pause task = `interrupted` (`paused_by_user`) + batas durasi task | Accepted (desain) |
| ADR-032 | Hasil readiness gate disimpan & memblokir agent LLM | Accepted (desain) |
| ADR-033 | Run sandbox read-only untuk `main` + batas pertumbuhan disk | Accepted (desain) |
| ADR-034 | Matriks approval per action_type, izin & self-approval | Accepted (desain) |
| ADR-035 | `test_runs.failure_kind` | Accepted (desain) |

Isi lengkap (konteks → keputusan → konsekuensi): `DECISIONS.md`.

---

## 17. Glosarium (tambahan v2 & v2.1)

| Istilah | Arti |
|---|---|
| Portal | Ruang kerja dalam satu SPA: IDE, AI, atau Portfolio |
| Capability | Modul kemampuan terdaftar (mis. `web_research`, `doc_generate`) dengan provider, tools, risiko, budget, izin, dan flag |
| Provider Gateway | Lapisan adapter per capability: `LLMProvider`, `ImageProvider`, `DocumentEngine` |
| Server tool | Tool yang dieksekusi di sisi Anthropic (`web_search`, `web_fetch`, code execution); tetap dicatat ToolGateway dengan `executor: provider` |
| Agent Skills | Paket kemampuan Anthropic (`pptx`, `docx`, `xlsx`, `pdf`) yang berjalan di container code execution |
| ZDR | Zero Data Retention — Skills dan Files API tidak tercakup |
| Sitasi | Rujukan sumber (url, judul, kutipan, lokasi) yang dikembalikan API dan ditampilkan di samping klaim |
| Blok `search_result` | Cara mengirim potongan dokumen ke Claude agar jawabannya dapat bersitasi ke potongan tersebut |
| Retrieval leksikal | Pencarian berbasis kata (FTS + trigram), bukan embedding |
| GitHub App | Aplikasi GitHub dengan permission granular per repo; token instalasi berumur ≤ 1 jam |
| ApprovalGrant | Izin dalam memori hasil konsumsi approval sekali pakai untuk eksekusi R3 multi-langkah |
| RunScope | Lingkup run agent: task (IDE) atau thread (Portal AI/Portfolio) |
| Label sensitivitas | `public`, `internal`, `confidential` per Ruang/project; menentukan engine dan vendor yang boleh dipakai |
| Draft bertanda AI | Konten portfolio buatan AI yang belum disetujui pemilik; tidak dapat dipublikasikan |
| P0ᵉ | Prioritas P0 di dalam perluasan (wajib untuk fasenya), bukan P0 MVP |
| SDLC 8 fase | Requirement, desain, implementasi, test, run/preview, review & acceptance, release, operate — masing-masing dengan bukti wajib |
| Work item | Program, epic, atau story; task tetap unit eksekusi agent |
| `ProgramPlan` | Usulan dekomposisi A03 (epic/story/task + DAG) yang wajib disetujui user |
| Merge queue | Antrean per repo yang me-rebase dan menguji ulang setiap perubahan sebelum merge ke branch target |
| Take-over | User mengambil alih worktree task yang dijeda; bukti lama menjadi stale dan wajib divalidasi ulang |
| Tier S/M/L | Profil deployment: pilot 1 VPS / tim dengan node executor / enterprise dengan HA, SSO, tenant |
| Node executor | Mesin yang menjalankan worker + sandbox-manager untuk task; dapat ditambah tanpa mengubah kode domain |
| OIDC / SAML / SCIM | Standar SSO dan provisioning identitas enterprise |
| Sparse checkout | Checkout sebagian file repo besar yang relevan untuk satu task |
| Pyodide | Python yang berjalan di browser (WebAssembly); dipakai hanya di live demo, bukan sandbox produk |

---

## 18. Referensi

Dokumentasi eksternal diakses 21 Oktober 2026; versi dan harga wajib diverifikasi ulang saat fase coding dimulai.

| Sumber | URL | Relevansi |
|---|---|---|
| Claude Platform — Web search tool | https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool | Versi tool, sitasi, filter domain, `max_uses`, harga |
| Claude Platform — Web fetch tool | https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool | Membaca halaman sebagai sumber |
| Claude Platform — Using Agent Skills with the API | https://platform.claude.com/docs/en/build-with-claude/skills-guide | Skills `pptx/docx/xlsx/pdf`, container tanpa network, ZDR |
| Claude Platform — Files API | https://platform.claude.com/docs/en/build-with-claude/files | Unduh file hasil; status ZDR |
| Claude Platform — Citations | https://platform.claude.com/docs/en/build-with-claude/citations | Sitasi dokumen; tidak kompatibel structured outputs |
| Claude Platform — Search results | https://platform.claude.com/docs/en/build-with-claude/search-results | Blok `search_result` untuk RAG bersitasi |
| Claude Platform — Embeddings | https://platform.claude.com/docs/en/build-with-claude/embeddings | Anthropic tidak menyediakan model embedding sendiri |
| Claude Help Center — Can Claude produce images? | https://support.claude.com/en/articles/9002504-can-claude-produce-images | Claude tidak menghasilkan foto/ilustrasi |
| Claude Platform — Messages API, Tool use, Prompt caching, Models overview | https://platform.claude.com/docs/en/build-with-claude/working-with-messages | Runtime LLM (baseline v1) |
| GitHub Docs — Authenticating as a GitHub App installation | https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation | Token instalasi 1 jam, scope repo, git `x-access-token` |
| GitHub Docs — Choosing permissions for a GitHub App | https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app | Permission minimum; Workflows |
| GitHub Docs — Validating webhook deliveries | https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries | `X-Hub-Signature-256`, HMAC-SHA256 |
| OWASP ASVS | https://asvs.owasp.org/ | Baseline verifikasi keamanan |
| Docker Engine Security | https://docs.docker.com/engine/security/ | Hardening sandbox |
| Perbandingan API gambar (pihak ketiga) | https://www.aifreeapi.com/en/posts/gemini-vs-openai-image-generation | Kandidat vendor — **perlu verifikasi di halaman resmi vendor** |

---

## Lampiran A — Traceability Ringkas

| Modul | FR | Agent produk | Fase | Work package | Kriteria |
|---|---|---|---|---|---|
| M22 GitHub App | FR-185..196 | J48, E29 | P9 | WP-9.0..9.5 | AX-01..AX-03 |
| M24 Capability & Provider Gateway | FR-213..220 | — (core) | P10 | WP-9.0, WP-10.1 | AX-11, AX-06 |
| M21 Portal AI — chat & riset | FR-160..170 | I42, I43, S52 | P10 | WP-10.2, WP-10.3, WP-10.5, WP-10.6 | AX-04 |
| M21 Portal AI — dokumen | FR-171..174 | I44 | P10 | WP-10.4 | AX-05, AX-06 |
| M21 Portal AI — KB | FR-175..177 | I47 | P11 | WP-11.3 | AX-10 |
| M21 Portal AI — analisis | FR-179..180 | I46 | P11 | WP-11.4 | — |
| M21 Portal AI — gambar | FR-181..183 | I45, S52 | P11 | WP-11.1, WP-11.2 | AX-07 |
| M23 Portfolio | FR-197..212 | K49, K50, K51 | P12 | WP-12.1..12.5 | AX-08, AX-09 |
| M21 Portal AI — memori personal (P2) & asisten kustom | FR-178, FR-184 | I42 | — | belum ada WP | AX-11 |
| M26 Developer Workspace v2 | FR-235..244 | — (manusia); C17 untuk test terpilih | P13 (LSP/DAP P14) | WP-13.1, 13.2, 13.3, 14.6 | AX-12, AX-13 |
| M25 SDLC Program & Delivery | FR-221..234 | A02, A03, A05, A06, B12, E28, F30, F32 (P13); F33, F34 (P14) | P13 (agent operasi P14) | WP-13.4, 13.5, 13.6, 13.10, 13.11, 13.12, 14.8 | AX-14, AX-15 |
| M27 Scale & Enterprise | FR-245..258 | — (core) | P13 (tier M), P14 (tier L) | WP-13.7, 13.8, 13.9, 14.0..14.5, 14.7 | AX-16, AX-17, AX-18 |

Matriks per FR (FR → UC → AC/AX → agent → fase → WP): `SYSTEM_SPEC.md` §9.2.

## Lampiran B — Galeri Mockup

Semua layar diambil pada viewport 1440×900, tema terang, skala 2×. Layar 01–11 = data eksekusi nyata; layar 12–24 = prototipe dengan data contoh; layar 25–30 = file & run pembanding dari eksekusi nyata, test hasil editan dijalankan lewat Pyodide di browser; layar 31/31b = rancangan tier.


### B.1 Portal IDE (data eksekusi nyata)

![Layar 01. Login: panel brand dengan alur agent dan form masuk.](mockups/screen-01-login.png)

![Layar 02. Dashboard: metrik setelah 3 eksekusi terekam, task terbaru, penggunaan model (biaya tidak tersedia), aktivitas agent, kartu Platform.](mockups/screen-02-dashboard.png)

![Layar 03. Project Baru: template *API Service* dan pilihan eksekusi terekam (tab "Dari template").](mockups/screen-03-project-baru.png)

![Layar 04. Workspace/IDE saat replay berjalan (skenario *Alur normal*, test sedang dijalankan): explorer worktree, editor, chat agent dengan bukti tool, terminal sandbox.](mockups/screen-04-workspace.png)

![Layar 05. Katalog 42 AI Agents dengan filter mode dan status.](mockups/screen-05-ai-agents.png)

![Layar 06. Detail task — Rencana: requirement, kriteria penerimaan, langkah rencana, rencana test.](mockups/screen-06-task-rencana.png)

![Layar 07. Detail task — Ringkasan & workflow: status akhir, riwayat status, pipeline. Menggantikan layar *Preview & Result* PRD v1 karena replay tidak merekam preview aplikasi.](mockups/screen-07-task-ringkasan.png)

![Layar 08. Testing & debugging (skenario *Debug loop*): 9 run, 3 gagal; drill-down kasus gagal `assert 7 == 12`, lalu run berikutnya lulus.](mockups/screen-08-testing.png)

![Layar 09. Review & diff: temuan review (0), diff branch per file dengan nomor baris lama/baru.](mockups/screen-09-review-diff.png)

![Layar 10. Validasi User Proxy (skenario *Remediasi gap*): iterasi 1 *Perlu perbaikan* dengan AC-2 gagal dan gap, iterasi 2 *Diterima*. Berlabel "Rekomendasi User Proxy bukan persetujuan Anda".](mockups/screen-10-user-proxy.png)

![Layar 11. Halaman Persetujuan: kartu `apply_changes` R3 tertunda dengan branch, commit, diff, kriteria, hash payload.](mockups/screen-11-approval.png)


### B.2 Portal Asisten AI (prototipe, data contoh)

![Layar 12. Riset web bersitasi: catatan "Bukan output model", jawaban contoh dengan nomor sitasi, panel sumber terbuka (judul, URL, tanggal baca, ringkasan sumber).](mockups/screen-12-ai-riset-sumber.png)

![Layar 13. Buat dokumen: outline contoh, pilihan engine, pratinjau 4 slide HTML ("file .pptx belum dihasilkan").](mockups/screen-13-ai-buat-dokumen.png)

![Layar 14. Buat gambar: form, checklist moderasi, dan keadaan kosong "Provider gambar belum dikonfigurasi (ADR-040a) — Claude tidak menghasilkan gambar". Tidak ada gambar hasil.](mockups/screen-14-ai-buat-gambar.png)

![Layar 15. Tanya jawab: thread kosong dengan composer dan label kebijakan "Tanpa sumber web".](mockups/screen-15-ai-tanya-jawab.png)


### B.3 Integrasi GitHub (prototipe, data contoh)

![Layar 16. Langkah 1 · pasang GitHub App: tabel izin minimum (Workflows: tidak diminta) dan dua kutipan verbatim GitHub Docs.](mockups/screen-16-github-izin.png)

![Layar 17. Langkah 2 · pilih repo contoh (privat/publik).](mockups/screen-17-github-pilih-repo.png)

![Layar 18. Langkah 3 · import: token 1 jam khusus repo, clone tanpa token di URL, deteksi stack, index — semua bertanda "simulasi".](mockups/screen-18-github-import.png)

![Layar 19. Kartu persetujuan prototipe "Push & buka PR" (R3) dengan branch dan head commit dari rekaman, tabel risiko operasi, fakta webhook HMAC.](mockups/screen-19-github-push-pr.png)


### B.4 Portal Portfolio (prototipe, data contoh)

![Layar 20. Profil: pengalaman dengan kolom pencapaian kosong ("AI hanya membantu menyusun draf, tidak mengarang"), pendidikan, keahlian.](mockups/screen-20-portfolio-profil.png)

![Layar 21. Proyek: KodeKantor (data nyata, tautan ke replay IDE) dan dua slot contoh.](mockups/screen-21-portfolio-proyek.png)

![Layar 22. CV ramah ATS satu kolom sans-serif; tombol unduh belum diimplementasikan.](mockups/screen-22-portfolio-cv.png)

![Layar 23. Situs publik: pratinjau dan kartu persetujuan publikasi R3 dengan cek privasi; "Prototipe: tidak ada yang dipublikasikan".](mockups/screen-23-portfolio-situs-publik.png)


### B.5 Peta Platform (rancangan)

![Layar 24. Tiga portal di atas satu core: diagram arsitektur (garis putus-putus = belum ada/rancangan).](mockups/screen-24-peta-platform.png)

![Layar 24b. Detail: empat kebenaran yang tidak nyaman, capability registry (ADR-044), awal fase P9–P12.](mockups/screen-24b-peta-platform-detail.png)


### B.6 Developer Workspace v2, SDLC, dan skala (v2.1)

![Layar 25. Editor kode: file project direkonstruksi dari patch commit rekaman (sha256 cocok 3/3) + 7 file template dari repo rekaman; tombol Jalankan test/project, Simulasikan perubahan agent.](mockups/screen-25-editor-kode.png)

![Layar 26. Diff vs versi rekaman setelah editan manual.](mockups/screen-26-editor-diff.png)

![Layar 27. Dialog 409 VERSION_CONFLICT dengan kolom Milikmu / Server / Basis dan opsi gabungkan 3-way (simulasi di memori).](mockups/screen-27-editor-konflik.png)

![Layar 28. Test dijalankan di browser lewat Pyodide (label "bukan sandbox Docker KodeKantor"): 11/11 lulus, cocok dengan run nyata b8a1fe83.](mockups/screen-28-test-pyodide-lulus.png)

![Layar 29. Editan `a + b` → `test_multiply` FAILED `assert 7 == 12`, ditandai "berbeda dengan rekaman".](mockups/screen-29-test-pyodide-gagal.png)

![Layar 30. Papan SDLC 8 fase untuk tiga eksekusi terekam; fase tanpa bukti ditandai "Sebagian"/"Tidak ada di rekaman".](mockups/screen-30-siklus-sdlc.png)

![Layar 31. Tier S/M/L (rancangan, belum diimplementasikan).](mockups/screen-31-skala-sml.png)

![Layar 31b. Arsitektur tier M/L (rancangan, ADR-060).](mockups/screen-31b-skala-arsitektur.png)

## Lampiran C — Peta Dokumen Repo

| Dokumen | Isi |
|---|---|
| `docs/PRD.md` | PRD v1.0 (baseline M01–M20, tidak diubah) |
| `docs/PRD_v2.md` | PRD v2.0 (tetap utuh) |
| `docs/PRD_v2.1.md` | Dokumen ini |
| `docs/SDLC_SCALE_DESIGN.md` | KONTRAK v2.1: SDLC end-to-end, Developer Workspace v2, tier S/M/L |
| `docs/SYSTEM_SPEC.md` | SRS v2 + §12 v2.1: kebutuhan lengkap, UC-01..UC-24, NFR-01..NFR-27, traceability, isu terbuka |
| `docs/FR_CATALOG.md` | FR-001..FR-258 |
| `docs/PLATFORM_EXPANSION.md` | Analisis & desain perluasan, KONTRAK v2 (§9b K1–K4) |
| `docs/IMPLEMENTATION_DESIGN_V2.md` | Desain implementasi v2 (+§13 v2.1): paket, interface kode, alur, migrasi, test, rencana WP |
| `docs/AGENT_CATALOG.md`, `agents/registry.v2.yaml` | Katalog & registry agent v2 (53 entri) + aktivasi SDLC v2.1 (§13) |
| `docs/TOOL_CATALOG.md` | Tool v1 + K3 v2 + K10 v2.1 |
| `docs/API_CONTRACTS.md` §12–§13 | Kontrak API v2 & v2.1, RBAC, matriks approval, protokol WebSocket terminal |
| `docs/DATA_MODEL.md` | Model data + Ekstensi v2/v2.1 (migrasi 0002–0008) |
| `docs/SECURITY_MODEL.md` | Ancaman T1–T23, kontrol, redaksi |
| `docs/CONFIGURATION.md` | Variabel lingkungan v1 + v2 + v2.1 |
| `docs/DECISIONS.md` | ADR-001..062 |
| `docs/mockups/` | 33 layar + 7 gambar gabungan |
| `docs/wbs/` | Sumber WBS agentik (112 WP, gate G0–G14) |
| `docs/IMPLEMENTATION_TRACKER.md` | Status jujur implementasi dan rancangan |
