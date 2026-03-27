# Workflow: fix-and-test

Jalankan: pytest tests/ -v --tb=short

Untuk setiap test yang gagal:
1. Baca error message
2. Baca interface contract di docs/ jika ada
   ketidaksesuaian
3. Perbaiki kode
4. Jalankan ulang pytest
5. Pastikan test lain tidak ikut rusak

Ulangi sampai semua test lulus.
Lapor final: berapa test, coverage berapa.
