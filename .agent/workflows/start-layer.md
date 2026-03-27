# Workflow: start-layer
# Cara pakai:
#   Claude Code  → paste langsung ke terminal claude
#   Antigravity  → /start-layer di Agent Manager
#   Codex        → paste ke chat

## Instruksi
Layer yang akan diimplementasi: [ISI NAMA LAYER]

Langkah yang harus dilakukan:
1. Baca .agent/rules.md untuk memahami konteks proyek
2. Baca dokumen yang relevan di docs/ (lihat bagian
   "Dokumen Referensi per Layer" di rules)
3. Buat semua file sesuai interface contract di dokumen
4. Tulis unit test bersamaan di tests/unit/
5. Jalankan pytest dan perbaiki sampai semua lulus
6. Lapor hasilnya dengan coverage report
7. Update status di .agent/rules.md
