# Issue tracker: GitHub

Issues, spec, dan tiket repo ini ada di GitHub Issues `YosiaGalih/IDEE-Application`.

**Akses.** Di mesin lokal, pakai `gh` CLI. Di sesi Claude Code cloud, `gh` tidak tersedia; pakai tool MCP GitHub yang setara (`mcp__github__issue_write`, `issue_read`, `list_issues`, `search_issues`, `add_issue_comment`, `sub_issue_write`).

## Konvensi

- **Buat issue**: `gh issue create --title "..." --body "..."` (body multi-baris lewat heredoc).
- **Baca issue**: `gh issue view <number> --json number,title,body,labels,comments`.
- **Daftar issue**: `gh issue list --state open --json number,title,body,labels,comments` dengan filter `--label` / `--state`.
- **Sub-issue**: `gh issue create --parent <parent> ...`, atau `gh issue edit <parent> --add-sub-issue <child>`. Bila sub-issue tidak tersedia, tulis `Part of #<parent>` di baris pertama body child.
- **Komentar**: `gh issue comment <number> --body "..."`.
- **Label**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`.
- **Tutup**: `gh issue close <number> --comment "..."`.

Repo diambil dari `git remote -v`.

## Judul dan label untuk pekerjaan WBS

- Judul diawali ID WBS: `WP-9.0: Kunci kontrak v2`. ID WBS tidak pernah diganti.
- Label tambahan di luar label triage:
  - `phase:P<n>`: fase roadmap, mis. `phase:P1`.
  - `gate:G<n>`: gate yang menunggu WP ini, mis. `gate:G3`.
  - `prio:P0`, `prio:P0e`, `prio:P1`, `prio:P2`. Pakai `P0e` sebagai ASCII untuk P0ᵉ.
  - `owner:AG-<nn>`: delivery agent pemilik, mis. `owner:AG-07`.
  - `security-signoff`: WP bertanda 🔒.
- Milestone GitHub = gate (G0..G16). Hanya PO yang menutup milestone.
- Body issue merujuk FR/AC/AX dan path dokumen kontrak. Isi requirement tidak disalin; dokumennya tetap satu-satunya sumber kebenaran.

## Pull request sebagai permukaan triage

**PR sebagai permukaan request: tidak.** _(Ubah ke `ya` bila PR eksternal diperlakukan sebagai feature request; `/triage` membaca flag ini.)_

## Saat skill berkata "publish to the issue tracker"

Buat GitHub issue.

## Saat skill berkata "fetch the relevant ticket"

Baca issue seperti di **Baca issue** di atas.

## Operasi wayfinding

Dipakai oleh `/wayfinder`. **Map** adalah satu issue; **child** issue adalah tiket.

- **Map**: satu issue berlabel `wayfinder:map` yang berisi Notes / Decisions-so-far / Fog. Untuk roadmap ini, satu map per fase atau per gate.
- **Child ticket**: sub-issue dari map. Label `wayfinder:<type>` (`research`, `prototype`, `grilling`, `task`). Setelah diklaim, issue di-assign ke pengemudinya.
- **Blocking**: dependensi issue native GitHub (`POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, memakai database id, bukan `#number`). Fallback: baris `Blocked by: #<n>, #<n>` di atas body. Dependensi WBS (kolom *Dependensi*) dipetakan ke edge ini.
- **Frontier**: child yang masih open, tanpa blocker open dan tanpa assignee; urutan map yang paling atas menang.
- **Claim**: assign ke diri sendiri sebagai tulisan pertama sesi.
- **Resolve**: komentar jawaban, tutup issue, lalu tambahkan pointer konteks (ringkasan + tautan) ke Decisions-so-far di map.
