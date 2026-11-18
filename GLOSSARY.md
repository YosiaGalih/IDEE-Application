# KodeKantor

Platform kerja self-hosted dengan tiga portal (IDE, AI, Portfolio) di atas satu core. Agent AI membantu membangun software, tetapi setiap hasil harus bisa ditelusuri ke bukti, dan setiap aksi keluar hanya terjadi atas persetujuan manusia. Sumber istilah: PRD v2.2 §17, `docs/SYSTEM_SPEC.md`, `docs/USER_PROXY_ACCEPTANCE.md`.

## Dua jenis agent (jangan tertukar)

**Runtime agent**:
Agent di dalam produk KodeKantor (A01–H41, I42–S52, ORCH; 53 entri `agents/registry.v2.yaml`) yang bekerja untuk pengguna KodeKantor.
_Avoid_: "agent" tanpa kualifikasi saat konteksnya bisa ganda

**Delivery agent**:
Agent Claude Code yang membangun repo ini (AG-00..AG-21 di WBS). Tidak pernah memakai ID runtime agent.
_Avoid_: builder agent, dev agent

**Lead Architect (AG-00)**:
Sesi utama yang memegang kontrak, ADR, integrasi, dan satu-satunya yang menggabungkan pekerjaan ke branch integrasi.

**Product Owner (PO)**:
Manusia pemegang keputusan akhir: ADR berstatus Proposed, review FR turunan, dan setiap gate.

**Operator (OPS)**:
Manusia yang memegang VPS, secret, model ID, budget, dan drill backup.

## Portal dan core

**Portal**:
Ruang kerja dalam satu SPA: Portal IDE, Portal AI, atau Portal Portfolio.

**Core**:
Bagian bersama ketiga portal: autentikasi/RBAC/audit, capability registry, provider gateway, tool gateway + approval, usage & budget, asset store, integrasi.

**Capability**:
Kemampuan terdaftar (mis. `chat`, `web_research`, `doc_generate`) dengan provider, tools, risiko, budget, izin, flag, dan kill switch.

**Kill switch**:
Saklar admin yang mematikan capability atau panggilan provider tanpa mengubah kode.

**Label sensitivitas**:
`public`, `internal`, atau `confidential` pada project atau Ruang; menentukan engine dan vendor yang boleh menerima datanya.

**Ruang (space)**:
Wadah thread Portal AI yang membawa label sensitivitas.

## Kerja dan bukti

**Task**:
Unit eksekusi runtime agent: satu worktree di satu repo, berjalan lewat state machine.

**Work item**:
Program, Epic, atau Story di atas task; statusnya diturunkan secara deterministik, tidak pernah diketik oleh model.

**ProgramPlan**:
Usulan dekomposisi work item dari A03 yang baru berlaku setelah disetujui pengguna.

**Requirement baseline**:
Versi requirement dan kriteria penerimaan yang disepakati; perubahan scope membuat versi baru.

**Kriteria penerimaan (acceptance criterion)**:
Syarat yang dinilai per task dengan status `pending/passed/failed/unverified/blocked/waived_by_user`.
_Avoid_: menyebutnya "AC" tanpa konteks; AC-01..AC-20 adalah kriteria produk, bukan kriteria per task

**Bukti (evidence)**:
Hasil tool nyata yang tersimpan (test run, artefak, finding, diff). Klaim model tanpa bukti tidak mengubah status apa pun.
_Avoid_: "test lulus" tanpa output

**Bukti stale**:
Bukti yang tidak lagi berlaku karena kode berubah setelahnya (mis. setelah take-over); wajib divalidasi ulang.

**User Proxy (A07)**:
Runtime agent yang menilai hasil task per kriteria berdasarkan bukti. Rekomendasinya bukan keputusan pengguna.

**Take-over**:
Pengguna mengambil alih worktree task yang dijeda; task menjadi `interrupted` dan buktinya menjadi stale.

**Tahap (S1–S10)**:
Langkah workflow engine untuk satu task (ARCHITECTURE §5.2): S1–S3 intake, konteks, dan plan di status `draft`; S4–S6 implementasi, test, dan debug di `running`; S7–S10 review, User Proxy, gate, dan apply di `validating` hingga keputusan.

**Checkpoint**:
Isi `tasks.checkpoint` (JSONB) yang menyimpan posisi engine dan isian awal user (`user_set`), sehingga task yang terputus dilanjutkan dari tahap terakhir, bukan dari awal.

**Lease**:
Hak sementara satu worker atas satu task (`lease_owner`, `lease_expires_at`), diperpanjang selama bekerja; lease yang kedaluwarsa membuat sweep recovery mengambil alih task itu, dan jumlah lease hidup membatasi `MAX_ACTIVE_TASKS`.

**Sweep dispatch**:
Job beat (`dispatch_sweep`, tiap 30 dtk) yang mengirim ulang task `draft|queued` yang pernah di-dispatch (`checkpoint.dispatch.at`) tetapi lease-nya mati dan sudah lewat masa tunggu 60 dtk, karena job `run_task`-nya hilang; berbeda dari sweep recovery, yang memulihkan task `running|validating` yang lease-nya kedaluwarsa.

**Iterasi**:
Putaran ke-n pengerjaan satu task (`tasks.iteration`, mulai 1), naik pada `request-changes` atau remediation; berbeda dari iterasi tool di dalam satu agent run.
_Avoid_: mencampurnya dengan `max_tool_iterations`

**Conversation**:
Riwayat chat satu user di satu project (`conversations`, `messages`); hanya pembuatnya yang melihatnya, sedangkan task yang lahir dari chat terlihat oleh semua pemegang `task:read`.

**Chat→task**:
Pesan chat dengan `create_task=true` yang membuat task `draft` di transaksi yang sama dengan pesannya, dengan `source="chat"` dan `conversation_id` terisi.

**`interrupted`**:
Status task yang berhenti di tengah jalan dan dapat dilanjutkan (`POST /resume`): lease habis, worker mati, jeda user, take-over, provider belum siap, atau titik berhenti `validation_unavailable`; berbeda dari `failed` (gagal) dan `cancelled` (dibatalkan user).

**`paused`**:
Bukan status: jeda atas permintaan user (ADR-031, `pause_requested`) yang berhenti di batas langkah dan tampil sebagai task `interrupted` dengan `status_reason=paused_by_user`; `resume` mengembalikannya ke `queued`.

**Validasi belum tersedia (`validation_unavailable`)**:
Titik berhenti jujur setelah S4 selama S5 belum ada: perubahan sudah dikomit di branch `kk/task-…`, task menjadi `interrupted` dengan `status_reason=validation_unavailable`, tidak pernah `validating` atau `completed`, dan `POST /resume` menjawab 409 sampai S5 dibangun.

## Risiko dan persetujuan

**Tingkat risiko (R0–R4)**:
R0 baca, R1 tulis terbatas yang dapat dibalik, R2 eksekusi di sandbox, R3 aksi eksternal/destruktif yang wajib approval, R4 tidak pernah tersedia.

**Approval**:
Keputusan manusia atas aksi R3, terikat hash payload, sekali pakai, dan gugur bila payload berubah.
_Avoid_: menganggap teks model yang "menyetujui" sebagai approval

**ApprovalGrant**:
Izin sementara hasil konsumsi satu approval untuk menjalankan aksi R3 multi-langkah; model tidak dapat membuat atau memperpanjangnya.

**Waiver**:
Keputusan pengguna menerima kriteria wajib yang gagal; hanya pengguna yang dapat memberikannya.

**Tool Gateway**:
Satu-satunya pintu side effect runtime agent: allowlist, skema, scope, risiko/approval, kuota, audit.

**Sandbox**:
Container sekali pakai dengan profil tetap tempat seluruh kode project dijalankan; network mati kecuali profil instal dependensi.

## Biaya dan pemakaian

**Ledger usage**:
Catatan resmi pemakaian per panggilan (token LLM dan unit non-token); tampilan realtime adalah proyeksinya, bukan ledger kedua.

**Status angka biaya**:
`aktual`, `estimasi`, `resmi`, atau `tidak tersedia`. Biaya yang tidak diketahui tidak pernah ditampilkan sebagai 0.

**Rekonsiliasi**:
Pencocokan usage internal dengan angka resmi Usage & Cost Admin API dan penandaan selisihnya.

**Readiness gate**:
Pemeriksaan provider dengan kunci nyata yang wajib lulus sebelum runtime agent LLM boleh dipanggil.

## Skala, bahasa, integrasi

**Tier S/M/L**:
Profil deployment: pilot satu VPS, tim dengan node executor, atau enterprise dengan HA/SSO/tenant. Satu basis kode; tier adalah konfigurasi dan topologi.

**Node executor**:
Mesin yang menjalankan worker dan sandbox untuk task, dapat ditambah tanpa mengubah kode domain.

**Merge queue**:
Antrean per repo yang me-rebase dan menguji ulang setiap perubahan sebelum masuk ke branch target.

**Language pack**:
Paket berversi per ekosistem bahasa (image toolchain, deteksi stack, profil, registry dependensi, template, petunjuk dan set evaluasi agent), tingkat L1 (penuh) atau L2 (build & test).

**Proxy dependensi**:
Satu-satunya jalur instal dependensi dari sandbox, per ekosistem, dengan allowlist dan lockfile wajib.

**Lingkungan integrasi**:
Sekumpulan container sementara (service + DB + broker) di network terisolasi tanpa egress untuk contract test dan E2E lintas service.

**Kontrak antar-service**:
Spesifikasi interface (OpenAPI, AsyncAPI, Protobuf/gRPC, GraphQL) di repo pengguna sebagai sumber kebenaran; breaking change memblokir merge queue.

## Delivery

**Fase (P0–P16)**:
Tahap roadmap produk. Berbeda dari prioritas P0/P1/P2.
_Avoid_: menulis "P1" tanpa menyebut fase atau prioritas

**Prioritas P0 / P0ᵉ / P1 / P2**:
P0 wajib MVP; P0ᵉ wajib untuk gate fase perluasannya (bukan MVP); P1 produk lebih lengkap; P2 lanjutan.

**Gate (G0–G16)**:
Titik keputusan manusia di akhir fase. Nomor gate bukan nomor fase (G3 = MVP DoD, G4 = egress, G8 = aktivasi agent).

**Work package (WP)**:
Unit pekerjaan WBS (`WP-<fase>.<n>`, `WP-INT-*`, `WP-E2E-*`, `WP-SEC-*`) dengan pemilik, path milik, dependensi, dan DoD.

**Sign-off keamanan 🔒**:
Persetujuan tertulis Security Reviewer (AG-13) yang wajib untuk WP bertanda 🔒.

**Label keyakinan**:
`[Pasti]` bukti kuat, `[Kemungkinan Besar]` inferensi kuat, `[Menebak]` tebakan yang wajib divalidasi.
