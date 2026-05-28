"""
checker/service.py — CamWatch
Daemon de verificação RTSP.

Fluxo:
  1. Busca câmeras cujo intervalo individual já venceu
  2. Verifica cada uma em paralelo via FFprobe (sem decodificar vídeo)
  3. Se o status mudou → grava evento_camera + atualiza camera
  4. Se voltou online   → preenche duracao_offline_segundos no último evento offline
  5. Se não mudou      → apenas atualiza ultima_verificacao
  6. Dorme CHECKER_LOOP_SLEEP segundos e repete
"""

import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

_SP = ZoneInfo("America/Sao_Paulo")

from sqlalchemy import text

# Permite rodar como script autônomo ou importado pelo Flask
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from app.models import db, Camera, EventoCamera, StatusCamera, StatusEvento
from config import Config

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/log/camwatch_checker.log"),
    ],
)
log = logging.getLogger("camwatch.checker")


# ------------------------------------------------------------------
# Verificação RTSP via FFprobe
# ------------------------------------------------------------------

def check_rtsp(url: str, timeout: int = Config.CHECKER_TIMEOUT_SEC) -> bool:
    """
    Retorna True se o stream RTSP está acessível.
    Usa FFprobe sem decodificar vídeo — apenas testa conectividade.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-rtsp_transport", "tcp",
                "-i", url,
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=noprint_wrappers=1",
            ],
            timeout=timeout,
            capture_output=True,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except FileNotFoundError:
        log.error("FFprobe não encontrado. Instale com: apt install ffmpeg")
        return False
    except Exception as e:
        log.warning(f"Erro ao verificar {url}: {e}")
        return False


# ------------------------------------------------------------------
# Lógica de persistência
# ------------------------------------------------------------------

def get_cameras_due(session) -> list:
    """
    Retorna câmeras ativas cujo intervalo individual já venceu.
    Usa query SQL direta para performance com 500+ câmeras.
    """
    sql = text("""
        SELECT id, nome, url_rtsp, ultimo_status, intervalo_segundos
        FROM camera
        WHERE ativo = TRUE
          AND (
              ultima_verificacao IS NULL
              OR DATE_ADD(ultima_verificacao, INTERVAL intervalo_segundos SECOND) <= NOW()
          )
    """)
    rows = session.execute(sql).mappings().all()
    return [dict(r) for r in rows]


def processar_resultado(session, cam: dict, novo_status: str, agora: datetime):
    """
    Compara novo status com o atual e age conforme necessário.
    """
    status_atual = cam["ultimo_status"]
    camera_id    = cam["id"]

    # Sempre atualiza ultima_verificacao
    session.execute(
        text("UPDATE camera SET ultima_verificacao = :ts WHERE id = :id"),
        {"ts": agora, "id": camera_id},
    )

    # Sem mudança de estado — só atualiza timestamp
    if status_atual == novo_status:
        return

    # --- Mudança de estado detectada ---
    log.info(f"[MUDANÇA] Câmera {cam['nome']} (id={camera_id}): {status_atual} → {novo_status}")

    # Atualiza ultimo_status na camera
    session.execute(
        text("UPDATE camera SET ultimo_status = :s WHERE id = :id"),
        {"s": novo_status, "id": camera_id},
    )

    # Calcula duração offline se câmera voltou online
    duracao = None
    if novo_status == "online" and status_atual == "offline":
        duracao = calcular_duracao_offline(session, camera_id, agora)

    # Grava evento
    evento = EventoCamera(
        camera_id=camera_id,
        status=StatusEvento[novo_status],
        timestamp=agora,
        duracao_offline_segundos=duracao,
    )
    session.add(evento)


def calcular_duracao_offline(session, camera_id: int, agora: datetime) -> int | None:
    """
    Busca o último evento offline desta câmera e calcula
    quantos segundos ela ficou fora.
    """
    sql = text("""
        SELECT timestamp FROM evento_camera
        WHERE camera_id = :id AND status = 'offline'
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    row = session.execute(sql, {"id": camera_id}).fetchone()
    if row:
        delta = (agora - row[0]).total_seconds()
        return int(delta)
    return None


# ------------------------------------------------------------------
# Loop principal
# ------------------------------------------------------------------

def run_checker():
    app = create_app()

    log.info("CamWatch Checker iniciado.")
    log.info(f"Workers: {Config.CHECKER_WORKERS} | "
             f"Loop sleep: {Config.CHECKER_LOOP_SLEEP}s | "
             f"Timeout por câmera: {Config.CHECKER_TIMEOUT_SEC}s")

    while True:
        agora = datetime.now(_SP).replace(tzinfo=None)

        with app.app_context():
            with db.engine.begin() as conn_raw:
                # Usamos uma session manual para controle fino
                session = db.session

                cameras = get_cameras_due(session)

                if not cameras:
                    log.debug("Nenhuma câmera para verificar neste ciclo.")
                    time.sleep(Config.CHECKER_LOOP_SLEEP)
                    continue

                log.info(f"Verificando {len(cameras)} câmeras...")

                with ThreadPoolExecutor(max_workers=Config.CHECKER_WORKERS) as executor:
                    futures = {
                        executor.submit(check_rtsp, cam["url_rtsp"]): cam
                        for cam in cameras
                    }
                    for future in as_completed(futures):
                        cam    = futures[future]
                        online = future.result()
                        novo_status = "online" if online else "offline"
                        try:
                            processar_resultado(session, cam, novo_status, agora)
                        except Exception as e:
                            log.error(f"Erro ao processar câmera {cam['id']}: {e}")

                try:
                    session.commit()
                    log.info("Ciclo concluído e commit realizado.")
                except Exception as e:
                    session.rollback()
                    log.error(f"Erro no commit: {e}")

        time.sleep(Config.CHECKER_LOOP_SLEEP)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    run_checker()
