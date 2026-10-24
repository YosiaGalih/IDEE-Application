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

Untuk repo ini, WP yang menunggu keputusan PO, operator (OPS), atau sign-off keamanan 🔒 selalu memakai `ready-for-human`, bukan `ready-for-agent`.
