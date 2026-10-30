# Rencana lanjutan bila kredit cloud habis: kerja di bawah limit sesi dan limit mingguan

Dokumen ini dipakai setiap kali sesi baru dimulai. Status terakhir ada di `HANDOFF.md`; dokumen ini hanya berubah bila aturan atau antrian berubah.

## 0. Yang perlu diketahui dulu

- **Kontainer cloud dibuang saat sesi berakhir atau kredit habis.** Hanya yang sudah di-push yang selamat. Aturan nomor satu: setiap sesi berakhir dengan push.
- **Review adalah biaya terbesar.** Data nyata WP-1.3: satu subagent reviewer memakai 170–210 ribu token (backend 207k, silent-failure 171k), sehingga tiga reviewer paralel ≈ 550k token per WP. Di bawah limit mingguan, review harus dijatah (aturan §2.4).
- **PR #3 sudah besar** (ADR-026 (4) + WP-1.3 + perbaikan review). Mulai WP-1.5, satu PR = satu sub-WP.
- Angka limit sesi dan mingguan tergantung paket langganan dan tidak saya ketahui. Satuan kerja di bawah memakai ukuran relatif, bukan angka token. [Menebak] untuk semua perkiraan jumlah sesi.

## 1. Status terakhir

Status terkini dan langkah berikutnya ada di **`HANDOFF.md`** (root repo). Perbarui file itu di akhir setiap sesi.

## 2. Aturan kerja di bawah limit

1. **Satu sesi = satu unit dari §3**, dan berakhir dengan push yang CI-nya hijau. Unit yang tidak muat dipecah, jangan dipaksa.
2. **Commit kecil dan sering.** Setiap langkah yang lint, type, dan test-nya hijau langsung di-commit. Di cloud, push juga setiap kali hijau.
3. **Bila limit hampir habis dan kode belum hijau:** commit dengan judul `wip: <apa yang belum selesai>`, lalu push ke branch PR draft. CI merah di PR draft lebih murah daripada pekerjaan hilang. Sesi berikutnya wajib mulai dengan memperbaikinya. [Kemungkinan Besar] ini pilihan terbaik di cloud; di mesin lokal cukup commit tanpa push.
4. **Jatah review per WP:**
   - WP 🔒 (sandbox, path, git, auth, infra): `kk-security-reviewer` + satu reviewer bidang (`kk-backend-reviewer` atau `kk-frontend-reviewer`).
   - `kk-silent-failure-hunter` hanya untuk kode workflow, tool gateway, klien sandbox, usage/biaya, approval.
   - Review dijalankan sekali pada diff final WP, bukan per sub-langkah. Putaran kedua hanya pada diff perbaikan.
5. **Hemat konteks:**
   - Mulai sesi baru per unit; jangan meneruskan satu sesi raksasa.
   - Pakai `/compact` bila konteks memanjang.
   - Baca hanya bagian dokumen kontrak yang dirujuk spec WP, bukan seluruh dokumen.
   - Test Docker dijalankan terarah (`-k`) selama bekerja, penuh sekali sebelum push.
6. **Cek sisa kuota di awal sesi** (`/usage` atau `/status` di Claude Code [Kemungkinan Besar]). Bila sisa mingguan tinggal sedikit, pilih unit S atau pekerjaan dokumen.
7. **Gate dan keputusan PO bukan pekerjaan agent.** Agent menyiapkan bukti, mencatat di tracker, lalu berhenti.

Pembagian jatah mingguan yang saya sarankan [Menebak]:
- sekitar 70% implementasi + test;
- 20% review;
- 10% cadangan untuk CI merah dan perbaikan review.

## 3. Antrian unit kerja (urut)

Ukuran: S ≈ setengah sesi, M ≈ satu sesi, L ≈ dua sesi [Menebak].

| # | Unit | Isi | Ukuran | Selesai bila |
|---|---|---|---|---|
| U1 | Tutup temuan review WP-1.3, bagian sandbox_manager | registry (kill antre, kill per run, eviction, tail, `/logs` berhalaman, idempotensi), driver (LogConfig, ukur `.kk` + workspace gagal-tertutup, `remove_tree`, OOM, `CONTAINER_NOT_REMOVED`, artefak wajib per profil, `log_incomplete`), config (batas > 0, `DOCKER_HOST` proxy di production, `SANDBOX_TIMEOUT_SECONDS` dipakai), test unit + Docker diperbarui | L | `uv run pytest -q` dan `-m docker` hijau, push, CI hijau |
| U2 | Tutup temuan review WP-1.3, bagian backend + dokumen | klien (deadline dari `running`, kill saat batal, cek koherensi record, `kill_task` gagal → error, health mencatat penyebab, cek images/driver), `safexml` encoding, JUnit status terburuk + `cases_truncated` + ketidaksesuaian klaim, coverage `lines-valid=0`, `FakeSandboxClient` default eksplisit; konflik #38/#39; spec, tracker, body PR #3 | M | 1205+ test backend hijau, PR #3 keluar dari draft |
| U3 | Hasil review keamanan WP-1.3 | tutup temuan AG-13 pra-review yang belum tertutup U1/U2 | S–M | temuan ditutup atau dijawab |
| U4 | Sign-off formal AG-13 WP-1.2 + WP-1.3 | **sesi terpisah**, read-only, `kk-security-reviewer` | M | laporan sign-off di PR dan tracker |
| U5 | Keputusan PO | konflik #36–#39, merge PR #3 (manusia) | – | PR #3 di `main` |
| U6 | WP-1.5a | `infra/compose.yml` 11 service, jaringan privat (ARCHITECTURE §2), docker-socket-proxy dengan flag ADR-026 (3), service sandbox-manager (uid 10001, path identik ADR-005), `.env.example` | L | `docker compose config` valid, hanya `caddy` mempublikasi port |
| U7 | WP-1.5b | Caddy, egress proxy, `scripts/healthcheck.sh`, CI (hadolint, shellcheck, actionlint, `caddy validate`) | M | job CI hijau |
| U8 | WP-1.5c | backup/restore + RUNBOOK + drill restore | M | drill restore lulus |
| U9 | WP-1.4a | scaffold frontend (Vite, React 19, TS), design tokens, UI kit, API client + SSE resume, i18n id/en, CI frontend | L | build + test hijau |
| U10 | WP-1.4b | layar login, dashboard, projects, agents, approvals | L | test komponen + build hijau |
| U11 | WP-1.6 | bukti G1: `make up` dari nol, semua service healthy, alembic head, smoke API | M | bukti di tracker; **berhenti di gate G1** |

U1 dan U2 sedang berjalan di sesi 24 Okt; sebagian perubahan U1 mungkin sudah ter-push. Cek `git log` di branch kerja sebelum mulai.

## 4. Pindah ke mesin lokal (kredit cloud habis, langganan masih aktif)

Prasyarat:
- git;
- `uv` 0.13.x;
- Python 3.12;
- Docker Engine;
- PostgreSQL 16 dan Redis 7 (atau container);
- Node.js (mulai WP-1.4).

Langkah:
1. `git clone` repo, `git checkout claude/gracious-goodall-3hjh19` (atau branch WP berikutnya), lalu `git pull`.
2. Perintah ada di `CLAUDE.md` bagian "Perintah": backend (`uv sync --locked`, ruff, pyright, lint-imports, pytest) dan sandbox manager (unit + `-m docker`).
3. Bangun image sandbox sekali: `docker build -t kodekantor/sandbox-python:1 sandbox-images/python` dan `docker build -t kodekantor/sandbox-node:1 sandbox-images/node`. Di mesin lokal tanpa proxy TLS, build arg mirror dan secret CA tidak perlu.
4. Jalankan Claude Code di folder repo. Konfigurasi `.claude/` (rules, agent reviewer, hook) ikut ter-clone.

## 5. Prompt siap pakai

Awal setiap sesi (salin, ganti `<Ux>`):

```text
Baca CLAUDE.md dan docs/superpowers/plans/2026-10-24-continuation-under-usage-limits.md.
Kerjakan unit <Ux> dari §3 saja. Cek dulu `git log` dan status CI PR yang terbuka.
Ikuti aturan §2: commit setiap langkah hijau, push sebelum sesi berakhir, jatah review sesuai §2.4.
Di akhir sesi perbarui §1 dokumen ini (status, commit hijau terakhir, unit berikutnya) dan push.
```

Untuk sign-off AG-13 (U4), di sesi terpisah:

```text
Peranmu AG-13 Security Reviewer (agent kk-security-reviewer). Review read-only diff main..<branch>
untuk WP-1.2 dan WP-1.3 terhadap SECURITY_MODEL.md §4 dan ADR-009/026/033/007/005.
Tulis temuan per severity dengan file:line dan sign-off atau penolakan eksplisit.
Jangan mengubah kode. Simpan laporan di docs/IMPLEMENTATION_TRACKER.md §12 lewat satu commit docs.
```

Bila sesi sebelumnya berhenti di tengah (`wip:` commit atau CI merah):

```text
Sesi sebelumnya berhenti di tengah unit <Ux>. Baca commit terakhir dan log CI yang gagal.
Perbaiki sampai hijau dulu, baru lanjutkan sisa unit.
```
