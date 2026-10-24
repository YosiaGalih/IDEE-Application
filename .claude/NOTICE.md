# Pihak ketiga di `.claude/`

- `.claude/agents/kk-*.md` dan `.claude/rules/*.md` diadaptasi dari struktur agent reviewer dan rules bahasa **Everything Claude Code (ECC)**, https://github.com/affaan-m/ECC, MIT License, Copyright (c) Affaan Mustafa. Isinya ditulis ulang mengikuti kontrak KodeKantor. Aturan ECC yang bertentangan dengan dokumen proyek tidak diambil, misalnya black/isort/mypy/bandit, format respons `{success,data,error}`, dan `process.env` untuk secret.
- Plugin **Superpowers** (https://github.com/obra/superpowers, MIT) dan **mattpocock/skills** (https://github.com/mattpocock/skills, MIT) dipasang lewat marketplace di `.claude/settings.json`, bukan disalin ke repo.
