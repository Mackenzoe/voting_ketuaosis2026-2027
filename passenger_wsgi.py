import sys
import os

# Menambahkan direktori aplikasi saat ini ke dalam sys.path
sys.path.insert(0, os.path.dirname(__file__))

# Mengimpor instance Flask 'app' dari file app.py Anda
from app import app as application