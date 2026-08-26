# Migration
Untuk menggunakan konsep migration disini digunakan library bernama alembic
- Untuk menginisialisasi migrationnya atau membuat folder migration serta file alembic.ini:
    `alembic init migrations`
- Untuk menghubungkan alembic dengan database, buka file alembic.ini dan cari `sqlalchemy.url` kemudian sesuaikan dengan database yang digunakan.
- Untuk membuat tabel migration diperlukan sebuah model terlebih dahulu, kemudian model itu didaftarkan di `migrations\env.py`. Setelah itu jalankan perintah berikut untuk eksekusi:
    - Generate = `alembic revision --autogenerate -m "message"`
    - Migrate = `alembic upgrade head`
    - Perintah darurat untuk menjalankan migrasi tanpa membuat index = `alembic stamp head`
