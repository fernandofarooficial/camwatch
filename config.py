"""
config.py — CamWatch
Configurações via variáveis de ambiente (.env ou export).
Credenciais de banco NUNCA devem ter valores padrão aqui — use o .env.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Banco de dados — lidos exclusivamente do .env
    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST', '127.0.0.1')}:{os.getenv('DB_PORT', '3306')}"
        f"/{os.getenv('DB_NAME', 'camwatch')}?charset=utf8mb4"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_POOL_RECYCLE        = 280
    SQLALCHEMY_POOL_PRE_PING       = True   # revalida conexão antes de usar

    SECRET_KEY = os.getenv("SECRET_KEY", "dev-inseguro-troque-em-producao")

    # Fuso horário do sistema
    TIMEZONE = "America/Sao_Paulo"

    # Checker
    CHECKER_WORKERS     = int(os.getenv("CHECKER_WORKERS",     "80"))   # threads simultâneas
    CHECKER_LOOP_SLEEP  = int(os.getenv("CHECKER_LOOP_SLEEP",  "10"))   # segundos entre varreduras
    CHECKER_TIMEOUT_SEC = int(os.getenv("CHECKER_TIMEOUT_SEC", "4"))    # timeout por câmera
