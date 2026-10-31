# KodeKantor — Runbook Operasional (VPS single-node)

> Untuk operator/admin. Kontrak teknis: `docs/ARCHITECTURE.md` §2/§12, topologi: `docs/DEPLOYMENT.md`, konfigurasi: `docs/CONFIGURATION.md`, keamanan: `docs/SECURITY_MODEL.md`. Keputusan terkait: ADR-005 (workspace & backup), ADR-007 (egress), ADR-009 (Docker access, gVisor), ADR-010 (ukuran VPS), ADR-019 (Caddy).
>
> Konvensi: repo di-clone ke `/opt/kodekantor`; semua perintah dijalankan dari direktori itu sebagai `root` (atau user di grup `docker` + `sudo` untuk langkah host). `make` selalu memakai `--env-file .env`. **Jangan pernah** menjalankan `docker compose` tanpa `--env-file .env` (lebih baik lewat `make`).

---

## 1. Prasyarat

### 1.1 Server

| Item | Nilai pilot (ADR-010) | Catatan |
|---|---|---|
| OS | Ubuntu Server 24.04 LTS (22.04 LTS juga didukung) | patch otomatis via `unattended-upgrades` |
| CPU / RAM / disk | 4 vCPU / 16 GB / 200 GB SSD | pindah worker+sandbox ke node lain bila p95 antrean > 5 menit atau RAM control plane > 70% selama 1 minggu |
| DNS | record `A` (dan `AAAA` bila IPv6) `kode.<domain>` → IP VPS | wajib sebelum start: Caddy menerbitkan sertifikat via ACME HTTP-01/TLS-ALPN |
| Docker | Docker Engine ≥ 27 + Compose plugin ≥ 2.24 (dari repo resmi Docker) | fitur yang dipakai: `!override`, `create_host_path: false`, `deploy.resources.limits.pids` |
| Port masuk | 22/tcp (SSH), 80/tcp, 443/tcp, 443/udp (HTTP/3) | tidak ada port lain |

Alokasi memori di compose (batas keras, `deploy.resources.limits`): postgres 2 GB, worker 2 GB, api 1,5 GB, sandbox-manager/migrate/redis 0,5 GB, sisanya ≤ 256 MB → ±7,5 GB control plane + maksimal `SANDBOX_MAX_CONCURRENT_RUNS` × `SANDBOX_MEMORY_LIMIT` (4 × 1 GB) untuk container task. Sisa RAM untuk OS dan page cache.

### 1.2 Persiapan host (sekali)

```bash
# Paket dasar & patch otomatis
apt-get update && apt-get -y upgrade
apt-get install -y ca-certificates curl git make ufw unattended-upgrades chrony
dpkg-reconfigure -plow unattended-upgrades

# Docker Engine + compose plugin (repo resmi): https://docs.docker.com/engine/install/ubuntu/
# lalu verifikasi:
docker version && docker compose version

# Firewall: hanya 22/80/443
ufw default deny incoming
ufw default allow outgoing
ufw limit 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw enable && ufw status verbose
```

> **Penting — Docker vs ufw:** port yang di-*publish* Docker melewati aturan ufw (chain iptables `DOCKER`). Karena itu **hanya `caddy`** yang boleh mem-publish port (`infra/compose.yml`). `infra/compose.dev.yml` mem-publish api/postgres/redis ke `127.0.0.1` dan **tidak boleh** dipakai di server publik.

SSH: login hanya dengan key (`PasswordAuthentication no`, `PermitRootLogin prohibit-password`), pertimbangkan `fail2ban`.

### 1.3 Konfigurasi Docker daemon

Container task sandbox dibuat oleh sandbox-manager (bukan compose), sehingga rotasi log harus diset di level daemon. `/etc/docker/daemon.json`:

```json
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3", "compress": "true" },
  "live-restore": true,
  "no-new-privileges": true,
  "userland-proxy": false
}
```

```bash
systemctl restart docker
docker info --format '{{.LoggingDriver}} live-restore={{.LiveRestoreEnabled}}'
```

(Bila gVisor dipasang, `runsc install` menambah blok `runtimes` ke file ini — lihat §11.)

---

## 2. Instalasi

```bash
git clone https://<git-host>/<org>/kodekantor.git /opt/kodekantor
cd /opt/kodekantor
git checkout <tag-rilis>            # mis. v1.0.0 — jangan deploy branch yang bergerak

make setup
#  -> scripts/gen-secrets.sh: .env baru (mode 600) dengan SECRET_KEY, SANDBOX_MANAGER_TOKEN,
#     POSTGRES_PASSWORD, REDIS_PASSWORD acak (menolak menimpa .env yang sudah ada)
#  -> membuat /srv/kodekantor/workspaces (owner 10001:10001, mode 0750)
#  -> membangun image sandbox kodekantor/sandbox-python:1 & kodekantor/sandbox-node:1 (lokal, tidak di-pull)
```

Edit `.env` (nilai bertanda `>>> SET <<<`):

| Variabel | Contoh | Wajib |
|---|---|---|
| `APP_DOMAIN` | `kode.perusahaan.co.id` | ya — nama host sertifikat |
| `APP_URL` | `https://kode.perusahaan.co.id` | ya |
| `CADDY_EMAIL` | `ops@perusahaan.co.id` | ya — akun ACME (alamat nyata) |
| `ANTHROPIC_API_KEY` | (dari Claude Console) | ya bila `LLM_PROVIDER=anthropic` |
| `CLAUDE_MODEL_DEFAULT` | model ID dari katalog resmi | ya bila `LLM_PROVIDER=anthropic` |

```bash
chmod 600 .env && chown root:root .env
make config        # validasi compose (prod + dev) terhadap .env
make up            # build image backend/web/sandbox-manager, migrate, start semua service
make ps            # semua service "healthy", migrate "Exited (0)"
make healthcheck   # health container + GET https://$APP_DOMAIN/api/v1/health/{live,ready} dan /
```

Urutan start otomatis: `postgres`/`redis` healthy → `migrate` (`alembic upgrade head`) selesai sukses → `api`/`worker`/`scheduler`; `docker-socket-proxy` healthy → `sandbox-manager`. Bila `migrate` gagal, api/worker **tidak** start — lihat `make logs SERVICE=migrate`.

Proxy TLS korporat (build gagal "certificate verify failed"): salin CA PEM ke `infra/build/ca.pem` (gitignored), set `KK_BUILD_CA_FILE=./build/ca.pem` di `.env`. CA hanya dipakai saat build (BuildKit secret `build_ca`), tidak tersimpan di image.

---

## 3. Owner pertama & registry agent

```bash
make create-owner EMAIL=admin@perusahaan.co.id NAME="Admin KodeKantor"   # password diminta (tersembunyi)
make seed-agents                                                          # sinkron agents/registry.yaml -> DB
```

Registrasi publik nonaktif (ADR-002); user lain ditambahkan admin dari UI.

---

## 4. Claude API readiness gate (wajib sebelum coding agent diaktifkan)

```bash
make smoke      # = docker compose exec worker python -m app.cli claude-smoke
```

Dijalankan di container **worker** (satu-satunya pemegang `ANTHROPIC_API_KEY` yang dimaksud, ARCHITECTURE §2). Smoke test membuktikan (PRD §7.3.6): API key valid, model ID dapat diakses, Messages API berhasil, streaming + usage diproses, round-trip tool-use bekerja, timeout/cancel/error ditangani, dan secret tidak muncul di log. Memakai fixture sintetis, bukan repo pengguna.

- Exit code ≠ 0 → **jangan** aktifkan agent. Periksa: key (401/403), model ID salah (404), kuota/rate limit (429), egress ke `api.anthropic.com:443` (worker ada di network `outbound`).
- Catat output smoke (tanpa secret) di catatan perubahan deployment.
- Ulangi `make smoke` setiap ganti key, model, atau versi rilis.

---

## 5. Konfigurasi provider & budget

| Setting | Tempat | Catatan |
|---|---|---|
| Provider | `LLM_PROVIDER=anthropic` | `scripted` ditolak saat `APP_ENV=production` (ADR-015) |
| Model | `CLAUDE_MODEL_DEFAULT` / `_FAST` / `_COMPLEX` | tidak di-set = pakai default |
| Endpoint | `ANTHROPIC_BASE_URL` | biarkan **ter-comment** untuk endpoint resmi (nilai kosong ≠ tidak di-set) |
| Batas | `CLAUDE_MAX_OUTPUT_TOKENS`, `CLAUDE_REQUEST_TIMEOUT_SECONDS`, `CLAUDE_MAX_TOOL_ITERATIONS` | ADR-004 |
| Budget task | `TASK_MAX_COST_USD=2.0`, `TASK_MAX_TOKENS=400000`, `TASK_MAX_DEBUG_ATTEMPTS=5` | terlampaui → approval `budget_increase` |
| Harga | `backend/config/pricing.yaml` (berversi) | verifikasi tarif terhadap halaman harga resmi saat deployment |
| Kill switch | setting project `provider_calls_enabled=false` | task di project tsb tidak memanggil LLM (ADR-011) |

Setelah mengubah `.env`: `make up` (compose me-*recreate* hanya service yang env-nya berubah), lalu `make smoke`.

> Variabel yang di-export di shell operator **mengalahkan** `.env` (semantik compose). Jalankan `make` dari shell bersih (`env | grep -E 'ANTHROPIC|CLAUDE|APP_'` harus kosong).

---

## 6. Upgrade

1. **Baca** catatan rilis/ADR baru; cek apakah ada migrasi yang tidak bisa di-*downgrade*.
2. **Backup** dulu: `make backup` → catat direktori backup.
3. Ambil versi baru:
   ```bash
   git fetch --tags && git checkout <tag-baru>
   make config
   make build                         # build dengan --pull (base image terbaru utk patch keamanan)
   make build-sandbox-images          # bila sandbox-images/ berubah
   ```
4. **Terapkan**: `make up` — compose me-recreate container yang berubah; `migrate` berjalan sebelum api/worker start.
   - Task yang sedang `running` saat worker di-recreate menjadi `interrupted` (lease habis, sweep 30 dtk) dan dapat di-*resume* dari checkpoint (ARCHITECTURE §5.3). Untuk menghindarinya, umumkan jendela maintenance dan tunggu `running_tasks = 0` di dashboard.
5. **Verifikasi**: `make healthcheck`, `make smoke`, login + buka satu project.

**Rollback**

- Tanpa perubahan skema: `git checkout <tag-lama> && make up`.
- Dengan migrasi: bila migrasi mendukung downgrade, `docker compose -f infra/compose.yml --env-file .env run --rm migrate alembic downgrade <revisi>` **sebelum** checkout tag lama. Bila tidak: `git checkout <tag-lama>` lalu restore DB dari backup langkah 2 (`scripts/restore.sh --db-only <dir>`). Data yang ditulis setelah backup hilang (RPO = waktu backup).

---

## 7. Backup, restore & restore drill

### 7.1 Backup harian

`scripts/backup.sh [--quiesce]` (via `make backup`, sebagai root) menghasilkan `$BACKUP_DIR/kodekantor-<UTC>/`:

| File | Isi |
|---|---|
| `postgres.dump` | `pg_dump --format=custom` di container postgres; harus memuat data `alembic_version` menurut `pg_restore --list` |
| `workspaces.tar.gz` | isi `WORKSPACE_HOST_PATH` (format pax: owner numerik, mode, mtime presisi penuh) berisi repo git, worktree task, dan artefak. Tanpa `node_modules`, `.venv`, `__pycache__`, serta `uploads/` dan `.kk-runs/` di level project |
| `workspaces.manifest` | daftar isi arsip itu sendiri: mode, uid/gid, ukuran, mtime, path |
| `manifest.json` | `created_at`, `git_commit`, `image_tag`, `alembic_revision`, `postgres_version`, `counts` (users, projects, tasks, audit_events), ukuran, `quiesced` |
| `SHA256SUMS` | checksum keempat file di atas |

Aturan backup:
- **Folder dan file:** folder backup 0700, file 0600, milik root.
- **Atomik:** backup disusun di `kodekantor-<UTC>.partial/` dan di-rename hanya bila semua langkah lulus. Backup yang gagal dihapus.
- **Kunci:** `$BACKUP_DIR/.lock` mencegah backup dan restore berjalan bersamaan.
- **Retensi:** `BACKUP_RETENTION_DAYS` (default 14). Retensi hanya menghapus folder bernama persis `kodekantor-YYYYMMDDTHHMMSSZ` yang lebih tua dari batas itu; folder lain di `BACKUP_DIR` tidak disentuh.
- **Konsistensi:** tanpa `--quiesce`, dump DB konsisten tetapi tar workspace diambil saat stack berjalan. File yang berubah saat dibaca disimpan apa adanya dengan peringatan, dan `manifest.json` mencatat `"workspace_consistent": false`. `--quiesce` menghentikan api/worker/scheduler/sandbox-manager selama penyalinan; pakai saat upgrade (§6).
- **Pantau:** backup gagal hanya tercatat di log cron. Pasang alert bila backup terbaru di `$BACKUP_DIR` berumur lebih dari 26 jam, mis. `find /var/backups/kodekantor -maxdepth 1 -name 'kodekantor-*' -mmin -1560 | grep -q .`.
- **Volume:** mount `/srv/kodekantor` dengan `nosuid,nodev`.
- **Secret:** `.env` **tidak** ikut. Simpan salinannya di password manager / secret store.
- **Yang tidak di-backup:** Redis (dapat dibuang) dan sertifikat Caddy (diterbitkan ulang, perhatikan rate limit Let's Encrypt saat pindah VM).

Jadwal (systemd timer atau cron root), mis. `/etc/cron.d/kodekantor-backup`:

```cron
30 2 * * * root cd /opt/kodekantor && scripts/backup.sh >> /var/log/kodekantor-backup.log 2>&1
```

Off-site (wajib untuk DR): salin `$BACKUP_DIR` ke storage lain **terenkripsi**, mis. `restic -r <repo> backup /var/backups/kodekantor` atau `rclone` dengan remote `crypt`. Backup lokal di disk yang sama tidak melindungi dari kehilangan VPS.

### 7.2 Restore

```bash
make restore BACKUP=/var/backups/kodekantor/kodekantor-20261021T023000Z           # minta konfirmasi "RESTORE"
scripts/restore.sh --yes --db-only  <dir>                                         # non-interaktif, DB saja
scripts/restore.sh --workspace-only <dir>
```

Langkah skrip (sebagai root):
1. **Validasi, sebelum apa pun disentuh:**
   - kunci `$BACKUP_DIR/.lock`;
   - folder `kodekantor-<UTC>` milik root, tidak bisa ditulis pihak lain, dan berisi file biasa (bukan symlink);
   - `SHA256SUMS` memuat tepat keempat file dan lulus `sha256sum --check --strict`;
   - `pg_restore --list` bisa membaca dump;
   - untuk workspace: folder asli milik 10001:10001, folder induknya bisa dijangkau uid 10001 (0755), dan ruang cukup untuk salinan kedua.

   Lalu konfirmasi `RESTORE`.

   `SHA256SUMS` hanya membuktikan salinan utuh, **bukan** asal-usulnya: `pg_restore` menjalankan SQL dari dump sebagai owner (superuser). Keaslian datang dari alat off-site yang terautentikasi (restic, rclone crypt); jangan me-restore folder yang bisa diubah pihak lain.
2. **Hentikan semua penulis:** caddy, web, api, worker, scheduler, sandbox-manager, egress-proxy, dan container task sandbox (`kk.managed=1`). postgres tetap jalan.
3. **DB.**
   - Role `kodekantor_app` harus ada; bila belum, jalankan `make up` sekali agar postgres menginisialisasinya.
   - DB lama **disimpan** sebagai `kodekantor_pre_restore_<utc>` (rename, CONNECT dicabut).
   - DB baru dibuat dengan ACL `REVOKE ALL … FROM PUBLIC` dan `GRANT CONNECT … TO kodekantor_app`.
   - `pg_restore --exit-on-error --single-transaction`.
   - Setelah load, tidak boleh muncul role baru atau superuser selain `kodekantor`.
   - Event audit `ops.restore` (actor `restore.sh`, metadata `backup`, `backup_time`, `restored_at`) dicatat di DB hasil restore, karena restore memutar balik jejak audit.
4. **Workspace.**
   - Pohon lama dipindah ke `<path>.pre-restore-<UTC>`. Bila `<path>` adalah mount point (volume terpisah, §9), pohon lama masuk ke `<path>/.pre-restore-<UTC>` (0700 root); folder itu tidak ikut backup berikutnya.
   - Arsip diekstrak **sebagai uid 10001**, jadi arsip yang diubah tidak bisa menulis di luar wilayah 10001.
   - Setelah ekstraksi, folder root dikembalikan ke 0750 dan bit setuid/setgid dibuang.
   - Jumlah entri harus sama dengan `workspaces.manifest`.
5. **Nyalakan:** `docker compose up -d --wait` (migrate ke head + `grant-runtime-role`) → `healthcheck.sh --wait 180`. Skrip lalu menampilkan RTO dan waktu backup (RPO).

Bila gagal setelah langkah 2, service tetap mati dan skrip mencetak apa yang disimpan serta perintah untuk kembali (rename DB lama, folder pre-restore, `make up`). Redis **tidak** di-restore (hanya broker/pub-sub); task yang terputus menjadi `interrupted` dan bisa di-resume. Setelah verifikasi, hapus `kodekantor_pre_restore_*` (`DROP DATABASE`) dan folder `pre-restore`; perintah persisnya dicetak skrip.

### 7.3 Restore drill (bulanan, wajib PRD §7.5)

**Drill otomatis Fase 1** (`infra/tests/restore_drill.sh`, job CI `clean-install` pada setiap PR dan nightly; stack uji saja). Urutannya:
1. seed penanda audit dan project git milik 10001;
2. `backup.sh`;
3. rusak DB dan workspace;
4. `restore.sh --yes`;
5. DB dan setiap file yang di-backup harus sama dengan saat backup: mode, owner, mtime, sha256, `git fsck`, `ops.restore` tercatat;
6. backup yang diubah satu byte harus ditolak sebelum service mana pun berhenti.

Drill otomatis ini **tidak** menggantikan drill operasional di bawah.

**Drill operasional** di VM staging (bukan produksi):

1. Provision VM sesuai §1, clone repo pada tag yang sama dengan produksi, salin `.env` produksi lalu ganti `APP_DOMAIN`/`APP_URL` ke domain staging.
2. Salin backup terbaru dari off-site, jalankan `scripts/restore.sh --yes <dir>`.
3. Verifikasi dan catat: `make healthcheck` lulus; login owner; jumlah project/task sama dengan `manifest.json`/dashboard produksi; buka satu repo, `git log` pada worktree; lihat satu laporan acceptance & artefak; `alembic_revision` di manifest = head setelah migrate.
4. Catat durasi restore (RTO) dan umur backup (RPO) di log drill; perbaiki runbook bila ada langkah yang gagal.

---

## 8. Log & rotasi

- Service compose: driver `json-file`, `max-size=10m`, `max-file=5`, terkompresi (anchor `x-logging` di `infra/compose.yml`). Container task sandbox: `daemon.json` (§1.3).
- Format: JSON ke stdout (structlog di backend, access log JSON di Caddy). Secret diredaksi oleh `core/redaction.py`.
- Membaca: `make logs SERVICE=worker`, `docker compose -f infra/compose.yml --env-file .env logs --since 1h api | grep '"level":"error"'`.
- Journald host: batasi `SystemMaxUse=1G` di `/etc/systemd/journald.conf`.
- Verifikasi tidak ada kebocoran key: `make logs 2>&1 | grep -c 'sk-ant-'` harus `0` (jalankan dengan `--no-log-prefix --since 24h` untuk periode tertentu).

---

## 9. Disk, kuota & alert

| Lapisan | Mekanisme |
|---|---|
| Per project | `PROJECT_QUOTA_MB` (2048) ditegakkan aplikasi saat import/tulis; upload ≤ `UPLOAD_MAX_MB` (200) dan Caddy membatasi body 210 MB **hanya** di route import, 10 MB di route lain |
| Retensi aplikasi | artefak 30 hari, task events 90 hari, audit 365 hari, project `deleted` dihapus fisik 7 hari (ADR-005, job harian scheduler) |
| Image Docker | `docker image prune -f --filter "until=168h"` mingguan. **Jangan** `docker system prune --volumes` (menghapus `pgdata`!) |
| Host | alert bila partisi `/`, `/var/lib/docker`, `/srv/kodekantor` > 80% |

Contoh alert sederhana (`/etc/cron.hourly/kodekantor-disk`, ganti `ALERT_URL` dengan webhook Slack/Teams/ntfy):

```bash
#!/bin/sh
for p in / /var/lib/docker /srv/kodekantor; do
  use=$(df --output=pcent "$p" | tail -1 | tr -dc 0-9)
  [ "$use" -ge 80 ] && curl -fsS -m 10 -d "KodeKantor: disk $p ${use}% on $(hostname)" "$ALERT_URL"
done
exit 0
```

Opsional: letakkan `/srv/kodekantor/workspaces` di partisi/volume terpisah (XFS dengan project quota) agar workspace penuh tidak mematikan PostgreSQL.

---

## 10. Playbook insiden

Selalu mulai dengan: `make ps`, `make healthcheck`, `make logs SERVICE=<svc>`. Catat waktu, gejala, tindakan di log insiden.

### 10.1 Worker mati / task macet di `running`

Gejala: worker `unhealthy`/restart loop, task tidak bergerak, event SSE berhenti.

1. `make logs SERVICE=worker` — cari OOM (`docker inspect --format '{{.State.OOMKilled}}' kodekantor-worker-1`), error koneksi Redis/PostgreSQL, atau traceback.
2. `docker compose -f infra/compose.yml --env-file .env restart worker` (atau `up -d worker` bila konfigurasi berubah).
3. Recovery otomatis: `scheduler` menjalankan sweep tiap 30 dtk — task `running|validating` dengan lease kedaluwarsa → `interrupted` (atau `cancelled` bila `cancel_requested`); agent run yang menggantung → `failed(interrupted)`.
4. Bila scheduler juga mati: jalankan manual `docker compose -f infra/compose.yml --env-file .env exec worker python -m app.cli recover`.
5. Resume task dari UI (atau `POST /api/v1/tasks/{id}/resume`) — lanjut dari checkpoint; langkah yang sudah selesai tidak diulang.
6. OOM berulang → turunkan `CELERY_WORKER_CONCURRENCY`/`MAX_ACTIVE_TASKS` atau naikkan limit memori worker (ADR-010).

### 10.2 Container sandbox tersangkut

Gejala: run test tidak selesai, `SANDBOX_MAX_CONCURRENT_RUNS` penuh, CPU host tinggi.

```bash
docker ps --filter label=kk.task_id --format 'table {{.ID}}\t{{.Label "kk.task_id"}}\t{{.Label "kk.run_id"}}\t{{.RunningFor}}\t{{.Status}}'
```

1. Batalkan task dari UI (`POST /tasks/{id}/cancel` → sandbox-manager `kill_task`).
2. Bila tetap hidup melewati `SANDBOX_TIMEOUT_SECONDS` + 60 dtk (reaper sandbox-manager tiap 60 dtk): `docker rm -f <id>` untuk run tsb, atau semua run satu task: `docker rm -f $(docker ps -q --filter label=kk.task_id=<task_id>)`.
3. Periksa `make logs SERVICE=sandbox-manager` dan `docker-socket-proxy` (403 = endpoint yang tidak di-allowlist, berarti bug/serangan — eskalasi).
4. Jangan pernah mount `/var/run/docker.sock` ke service lain sebagai "perbaikan cepat" (ADR-009).

### 10.3 Redis hilang / korup

Redis hanya broker + pub/sub + rate-limit; PostgreSQL adalah source of truth.

1. Gejala: api/worker error koneksi Redis, redis restart loop (`Bad file format reading the append only file`).
2. AOF korup: `docker compose -f infra/compose.yml --env-file .env run --rm --no-deps --entrypoint redis-check-aof redis --fix /data/appendonlydir/appendonly.aof.manifest` (konfirmasi `y`), lalu `docker compose ... up -d redis`.
3. Bila tidak bisa diperbaiki: `docker compose ... stop redis && docker volume rm kodekantor_redisdata && make up`.
4. `restart worker scheduler api`. Job Celery yang hilang: task terkait menjadi `interrupted` lewat sweep → resume. Klien SSE reconnect dan me-*replay* event dari PostgreSQL (`Last-Event-ID`). Sesi login tidak terpengaruh (disimpan di DB).

### 10.4 Restore database

Saat: korupsi data, migrasi buruk, penghapusan tak sengaja.

1. Hentikan kerusakan: `docker compose ... stop api worker scheduler`.
2. Pilih backup terakhir yang baik (cek `manifest.json`), jalankan `scripts/restore.sh --db-only <dir>` (§7.2). Workspace dibiarkan.
3. Rekonsiliasi: task yang dibuat setelah backup tidak ada di DB tetapi worktree-nya masih ada di workspace (`<project>/tasks/<task_id>`) — arsipkan manual bila perlu.
4. RPO saat ini = interval backup (24 jam). Bila tidak dapat diterima, aktifkan WAL archiving/PITR (di luar MVP).

### 10.5 Kebocoran API key / rotasi secret

**ANTHROPIC_API_KEY bocor** (terlihat di log, commit, tiket, layar):

1. **Segera** revoke key di Claude Console; buat key baru.
2. Ganti `ANTHROPIC_API_KEY` di `.env`, lalu `docker compose -f infra/compose.yml --env-file .env up -d worker api` (recreate = env baru).
3. `make smoke` harus lulus.
4. Tinjau usage/billing di Console untuk penggunaan tak sah sejak waktu bocor; catat di audit insiden.
5. Cari sumber: `git log -p -S 'sk-ant-'`, log container, tiket. Bila masuk git: hapus dari history + rotasi tetap wajib (anggap sudah bocor). Pastikan gitleaks CI hijau.

**Secret lain:**

| Secret | Langkah | Dampak |
|---|---|---|
| `SECRET_KEY` | ganti di `.env` (`openssl rand -hex 32`), `up -d api worker scheduler` | semua sesi/token turunan tidak valid → user login ulang |
| `SANDBOX_MANAGER_TOKEN` | ganti, `up -d sandbox-manager api worker` | run sandbox yang sedang berjalan bisa gagal → task `interrupted`/resume |
| `POSTGRES_PASSWORD` | `docker compose ... exec postgres psql -U kodekantor -d kodekantor -c "ALTER USER kodekantor PASSWORD '<baru>'"`, ganti `.env`, `up -d` | restart singkat api/worker |
| `REDIS_PASSWORD` | ganti `.env`, `up -d redis api worker scheduler` | restart singkat; job antre bisa hilang (lihat §10.3) |

### 10.6 Sertifikat TLS gagal terbit

`make logs SERVICE=caddy` → cari `obtaining certificate` / `challenge failed`. Periksa: DNS `APP_DOMAIN` mengarah ke VPS, port 80 & 443 terbuka (ufw + firewall provider), `CADDY_EMAIL` alamat valid, rate limit Let's Encrypt (tunggu / pakai staging). Data sertifikat ada di volume `caddy_data` — jangan dihapus.

---

## 11. gVisor (opsional, direkomendasikan production — ADR-009)

gVisor (`runsc`) menambah kernel user-space antara kode project dan kernel host.

```bash
# Repo resmi gVisor: https://gvisor.dev/docs/user_guide/install/
curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
  > /etc/apt/sources.list.d/gvisor.list
apt-get update && apt-get install -y runsc
runsc install                 # menambah runtime "runsc" ke /etc/docker/daemon.json
systemctl restart docker      # live-restore menjaga container tetap hidup
docker run --rm --runtime=runsc hello-world
```

Aktifkan: set `SANDBOX_RUNTIME=runsc` di `.env`, `make up` (recreate sandbox-manager). Hanya container **task** yang memakai runsc; service control plane tetap runc.

Validasi sebelum production (SECURITY_MODEL §4): jalankan test isolasi `cd sandbox_manager && uv run pytest -q -m docker` di host dengan runtime runsc, plus satu task end-to-end (test python & node). Catat versi runsc.

---

## 12. Checklist hardening keamanan

Host & jaringan
- [ ] Ubuntu LTS ter-patch, `unattended-upgrades` aktif; reboot terjadwal untuk update kernel
- [ ] ufw: hanya 22/80/443 (+443/udp); SSH key-only; tidak ada port lain yang di-publish Docker (`docker ps --format '{{.Ports}}'` hanya caddy)
- [ ] `/etc/docker/daemon.json`: rotasi log, `live-restore`, `no-new-privileges`
- [ ] gVisor terpasang dan `SANDBOX_RUNTIME=runsc` (atau risiko diterima tertulis)

Secret & konfigurasi
- [ ] `.env` mode 600 milik root, dibuat oleh `scripts/gen-secrets.sh`, tidak ada nilai `change-me`
- [ ] `APP_ENV=production`, `COOKIE_SECURE=true`, `LLM_PROVIDER=anthropic`, `CORS_ORIGINS` kosong
- [ ] `ANTHROPIC_API_KEY` hanya di worker (+api, ADR-004); migrate/scheduler menerima placeholder
- [ ] `make smoke` lulus dan dicatat

Container (diverifikasi oleh `infra/compose.yml`)
- [ ] Semua service `no-new-privileges`, `cap_drop: ALL` (caddy hanya `NET_BIND_SERVICE`), `read_only` + tmpfs, limit CPU/RAM/PID
- [ ] api/worker/scheduler/migrate uid 10001; postgres uid 70; redis uid 999; web uid 101
- [ ] `docker.sock` **hanya** di `docker-socket-proxy` (ro); sandbox-manager memakai `DOCKER_HOST=tcp://docker-socket-proxy:2375`
- [ ] socket proxy: `/volumes`, `/networks`, `/exec/*/start`, `/build`, `/system`, `/events` → 403 (cek: `docker compose ... exec sandbox-manager python -c "import urllib.request as u; u.urlopen('http://docker-socket-proxy:2375/volumes')"` harus HTTP 403)
- [ ] postgres & redis tanpa port publik; redis dengan `requirepass`
- [ ] Image dipin (tag versi), image lokal `pull_policy: never` (tidak pernah di-pull); image sandbox dibangun lokal
- [ ] `SANDBOX_EGRESS=off` kecuali ada kebutuhan P1 yang disetujui (ADR-007)

Aplikasi & operasional
- [ ] Header keamanan aktif: `curl -sI https://$APP_DOMAIN | grep -Ei 'strict-transport|content-security|x-frame'`
- [ ] Backup harian + off-site terenkripsi; restore drill bulan ini tercatat
- [ ] Alert disk > 80% terpasang
- [ ] CI hijau untuk rilis: gitleaks, pip-audit, npm audit, semgrep, trivy image (gate `infra/security/scan_gate.py`, §12.1), SBOM terlampir pada tag

### 12.1 Pengecualian scan keamanan (bulanan, dan setiap kali `security-scan` merah)

`security-scan` (`.github/workflows/security.yml`) berjalan di setiap PR, push ke `main`, dan nightly. Job ini merah bila:
- ada temuan baru (CRITICAL, atau HIGH yang punya perbaikan);
- ada exception yang kedaluwarsa;
- ada exception yang tidak lagi cocok dengan temuan apa pun.

Exception ada di `infra/security/scan-exceptions.toml` (Trivy) atau di komentar `# nosemgrep` (Semgrep), masing-masing dengan baris `EX-nn` di `docs/DECISIONS.md` bagian "Pengecualian keamanan".

1. Baca log langkah "Image scan (Trivy) and gate" atau "SAST (Semgrep)". Setiap baris `BLOCKING:` menyebut image, CVE, paket, dan versi perbaikan.
2. **Temuan punya perbaikan dan image dibangun sendiri** (`kodekantor/*`): naikkan digest base di Dockerfile, atau kunci ulang dependensinya. Lalu build dan scan ulang (`make sandbox-images`, `docker compose ... build`, `infra/security/scan-images.sh <folder>`).
3. **Image pihak ketiga** (caddy, nginx-unprivileged, postgres, redis, docker-socket-proxy):
   - cek apakah upstream sudah merilis digest dengan perbaikan, lalu naikkan pin di `infra/compose.yml`;
   - bila belum, tambahkan CVE-nya ke entri EX-03 image itu, dengan alasan mengapa tidak tereksploitasi di stack ini.
4. **Exception tidak lagi cocok**: hapus CVE atau entrinya. Ini tanda upstream sudah memperbaiki.
5. **Memperpanjang exception**: perbarui tanggal di TOML **dan** di DECISIONS (maksimal 92 hari ke depan), dengan persetujuan PO. Gate menolak tanggal yang berbeda di dua tempat itu.
6. **HIGH tanpa perbaikan** tidak memblokir selama EX-02 berlaku. Daftarnya tercetak di setiap run. Begitu distro merilis perbaikan, temuan itu memblokir sehingga langkah 2 berlaku.
7. **Evaluasi ulang EX-03** bila:
   - profil sandbox node dengan network (deps_install, ADR-007) ditambahkan. Entri npm (`ip-address`, SSRF) tidak lagi "tanpa network";
   - stack akan dideploy publik. CVE-2026-78669 di caddy (HTTP/2) harus ditutup lebih dulu, dengan caddy hasil build ulang atau `protocols h1 h3` sementara.
8. `infra/security/scan-images.sh` hanya untuk CI dan mesin pengembang, **tidak pernah di host produksi**: container Trivy memegang socket Docker (setara root) dan cache yang persisten.

---

## 13. Referensi cepat

| Tujuan | Perintah |
|---|---|
| Status / health | `make ps` · `make healthcheck` |
| Log | `make logs SERVICE=api` |
| Start / stop | `make up` · `make down` (volume & workspace tetap) |
| Migrasi manual | `make migrate` |
| Shell DB | `docker compose -f infra/compose.yml --env-file .env exec postgres psql -U kodekantor -d kodekantor` |
| Backup / restore | `make backup` · `make restore BACKUP=<dir>` |
| Recovery task | `docker compose -f infra/compose.yml --env-file .env exec worker python -m app.cli recover` |
| Validasi infra | `make lint-infra` |
