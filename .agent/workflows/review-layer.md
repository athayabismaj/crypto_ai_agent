# Workflow: review-layer

Layer yang di-review: [ISI NAMA LAYER]
Dokumen referensi: [ISI NAMA DOKUMEN DI docs/]

Bandingkan implementasi di runtime/agent/[layer]/
dengan interface contract di dokumen.

Cari dan laporkan:
1. Fungsi yang ada di docs tapi belum diimplementasi
2. Signature yang berbeda dari kontrak
3. Aturan yang dilanggar (cek .agent/rules.md)
4. Test yang belum ada atau coverage kurang
5. Bug yang bisa menyebabkan masalah di production

Format laporan:
✅ Sesuai: [list yang sudah benar]
⚠️ Kurang: [list yang perlu ditambah]
❌ Salah:  [list yang perlu diperbaiki]
