# Triage Labels

Skill Pocock memakai lima peran triage kanonik. File ini memetakan peran itu ke string label di issue tracker repo ini.

| Label di mattpocock/skills | Label di tracker kita | Arti                                        |
| -------------------------- | --------------------- | ------------------------------------------- |
| `needs-triage`             | `needs-triage`        | Maintainer perlu mengevaluasi issue ini     |
| `needs-info`               | `needs-info`          | Menunggu informasi tambahan dari pelapor    |
| `ready-for-agent`          | `ready-for-agent`     | Spesifikasi lengkap, siap dikerjakan agent  |
| `ready-for-human`          | `ready-for-human`     | Butuh implementasi atau keputusan manusia   |
| `wontfix`                  | `wontfix`             | Tidak akan dikerjakan                       |

Saat skill menyebut sebuah peran (mis. "apply the AFK-ready triage label"), pakai string label yang sesuai dari tabel ini.

Untuk repo ini:

- WP yang **hasilnya** keputusan atau aksi manusia memakai `ready-for-human`. Contohnya gate G0–G16, WP-0.9, WP-INT-07 (readiness gate dengan key nyata), WP-6.6, WP-9.1 (registrasi GitHub App), WP-11.1, WP-14.0.
- WP bertanda 🔒 tetap dibangun agent: `ready-for-agent` + label `security-signoff`. Sign-off formal AG-13 dilakukan di sesi terpisah sebelum gate (spec sistem pengembangan §6 #10).
