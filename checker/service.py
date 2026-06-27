"""
checker/service.py — CamWatch
Daemon de verificação RTSP.

Fluxo:
  1. Busca câmeras cujo intervalo individual já venceu
  2. Verifica cada uma em paralelo via FFprobe (sem decodificar vídeo)
  3. Se o status mudou → grava evento_camera + atualiza camera
  4. Se voltou online   → preenche duracao_offline_segundos no último evento offline
  5. Se não mudou      → apenas atualiza ultima_verificacao
  6. Após commit → envia notificações Telegram para grupos configurados
  7. Dorme CHECKER_LOOP_SLEEP segundos e repete
"""

import json
import logging
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

_SP = ZoneInfo("America/Sao_Paulo")

# Debounce: exige N falhas consecutivas antes de marcar câmera como offline.
_OFFLINE_DEBOUNCE = 3

_falhas:             dict[int, int]      = {}
_offline_desde:      dict[int, datetime] = {}  # camera_id → quando confirmou offline
_offline_notificado: dict[int, bool]     = {}  # camera_id → se já enviou o alerta

# Permite rodar como script autônomo ou importado pelo Flask
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from app.models import db, EventoCamera, StatusEvento
from config import Config

# Notificação Telegram só após câmera ficar offline por este tempo.
_NOTIF_THRESHOLD_SEC = Config.NOTIF_THRESHOLD_SEC

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
# Telegram
# ------------------------------------------------------------------

def _formatar_duracao(segundos: int | None) -> str:
    if not segundos:
        return "desconhecido"
    if segundos >= 3600:
        return f"{segundos // 3600}h {(segundos % 3600) // 60}min"
    if segundos >= 60:
        return f"{segundos // 60}min {segundos % 60}s"
    return f"{segundos}s"


def enviar_telegram(chat_id: str, mensagem: str):
    token = Config.TELEGRAM_BOT_TOKEN
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    dados = json.dumps({"chat_id": chat_id, "text": mensagem}).encode()
    req = urllib.request.Request(
        url, data=dados, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        log.debug(f"Telegram enviado para {chat_id}")
    except Exception as e:
        log.warning(f"Telegram [{chat_id}]: {e}")


# ------------------------------------------------------------------
# Lógica de persistência
# ------------------------------------------------------------------

def get_cameras_due(session) -> list:
    """Retorna câmeras ativas cujo intervalo individual já venceu."""
    sql = text("""
        SELECT
            c.id, c.nome, c.url_rtsp, c.ultimo_status, c.intervalo_segundos,
            e.nome  AS empresa_nome,
            g.telegram AS grupo_telegram
        FROM camera c
        JOIN empresa e ON e.id = c.empresa_id
        LEFT JOIN grupo_camera g ON g.id = c.grupo_id
        WHERE c.ativo = TRUE
          AND (
              c.ultima_verificacao IS NULL
              OR DATE_ADD(c.ultima_verificacao, INTERVAL c.intervalo_segundos SECOND) <= NOW()
          )
    """)
    rows = session.execute(sql).mappings().all()
    return [dict(r) for r in rows]


def processar_resultado(session, cam: dict, novo_status: str, agora: datetime) -> dict | None:
    """
    Compara novo status com o atual e age conforme necessário.
    Retorna dict de notificação pendente ou None.
    """
    status_atual = cam["ultimo_status"]
    camera_id    = cam["id"]

    session.execute(
        text("UPDATE camera SET ultima_verificacao = :ts WHERE id = :id"),
        {"ts": agora, "id": camera_id},
    )

    if novo_status == "online":
        _falhas[camera_id] = 0
    elif novo_status == "offline" and status_atual != "offline":
        _falhas[camera_id] = _falhas.get(camera_id, 0) + 1
        if _falhas[camera_id] < _OFFLINE_DEBOUNCE:
            log.debug(f"Câmera {cam['nome']} (id={camera_id}): "
                      f"falha {_falhas[camera_id]}/{_OFFLINE_DEBOUNCE}, aguardando confirmação.")
            return None

    # Sem mudança de estado — verifica se notificação de 10 min está pendente
    if status_atual == novo_status:
        if novo_status == "offline":
            return _verificar_notif_offline_pendente(cam, agora)
        return None

    log.info(f"[MUDANÇA] Câmera {cam['nome']} (id={camera_id}): {status_atual} → {novo_status}")

    session.execute(
        text("UPDATE camera SET ultimo_status = :s WHERE id = :id"),
        {"s": novo_status, "id": camera_id},
    )

    duracao = None
    if novo_status == "online" and status_atual == "offline":
        duracao = calcular_duracao_offline(session, camera_id, agora)

    session.add(EventoCamera(
        camera_id=camera_id,
        status=StatusEvento[novo_status],
        timestamp=agora,
        duracao_offline_segundos=duracao,
    ))

    telegram = cam.get("grupo_telegram")

    if novo_status == "offline":
        # Registra início do offline; notificação só após _NOTIF_THRESHOLD_SEC
        _offline_desde[camera_id]      = agora
        _offline_notificado[camera_id] = False
        return None

    if novo_status == "online":
        notificado = _offline_notificado.pop(camera_id, False)
        _offline_desde.pop(camera_id, None)
        # Envia recuperação apenas se o alerta de offline foi enviado
        if telegram and notificado:
            hora = agora.strftime("%d/%m/%Y %H:%M:%S")
            mensagem = (
                f"🟢 CÂMERA ONLINE\n\n"
                f"📷 {cam['nome']}\n"
                f"🏢 {cam['empresa_nome']}\n"
                f"⏱ Offline por: {_formatar_duracao(duracao)}\n"
                f"🕒 {hora}"
            )
            return {"chat_id": telegram, "mensagem": mensagem}

    return None


def _verificar_notif_offline_pendente(cam: dict, agora: datetime) -> dict | None:
    """Envia alerta de offline se a câmera atingiu _NOTIF_THRESHOLD_SEC sem notificação."""
    camera_id = cam["id"]
    telegram  = cam.get("grupo_telegram")
    if not telegram:
        return None
    if _offline_notificado.get(camera_id, False):
        return None
    inicio = _offline_desde.get(camera_id)
    if inicio is None:
        # Câmera estava offline antes do checker iniciar; usa agora como referência
        _offline_desde[camera_id]      = agora
        _offline_notificado[camera_id] = False
        return None
    if (agora - inicio).total_seconds() < _NOTIF_THRESHOLD_SEC:
        return None
    _offline_notificado[camera_id] = True
    hora_inicio = inicio.strftime("%d/%m/%Y %H:%M:%S")
    return {
        "chat_id": telegram,
        "mensagem": (
            f"🔴 CÂMERA OFFLINE\n\n"
            f"📷 {cam['nome']}\n"
            f"🏢 {cam['empresa_nome']}\n"
            f"🕒 Offline desde: {hora_inicio}"
        ),
    }


def calcular_duracao_offline(session, camera_id: int, agora: datetime) -> int | None:
    sql = text("""
        SELECT timestamp FROM evento_camera
        WHERE camera_id = :id AND status = 'offline'
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    row = session.execute(sql, {"id": camera_id}).fetchone()
    if row:
        return int((agora - row[0]).total_seconds())
    return None


# ------------------------------------------------------------------
# Inicialização do estado offline (sobrevive ao restart do processo)
# ------------------------------------------------------------------

def _init_offline_desde(app):
    """Reconstrói _offline_desde/_offline_notificado a partir do banco ao iniciar."""
    with app.app_context():
        rows = db.session.execute(text("""
            SELECT camera_id, MAX(timestamp) AS ts
            FROM evento_camera
            WHERE status = 'offline'
              AND camera_id IN (
                  SELECT id FROM camera WHERE ativo = TRUE AND ultimo_status = 'offline'
              )
            GROUP BY camera_id
        """)).fetchall()
        for row in rows:
            _offline_desde[row[0]]      = row[1]
            _offline_notificado[row[0]] = False
        if rows:
            log.info(f"Estado offline restaurado para {len(rows)} câmeras.")


# ------------------------------------------------------------------
# Loop principal
# ------------------------------------------------------------------

def run_checker():
    app = create_app()
    _init_offline_desde(app)

    log.info("CamWatch Checker iniciado.")
    log.info(f"Workers: {Config.CHECKER_WORKERS} | "
             f"Loop sleep: {Config.CHECKER_LOOP_SLEEP}s | "
             f"Timeout por câmera: {Config.CHECKER_TIMEOUT_SEC}s")

    while True:
        agora = datetime.now(_SP).replace(tzinfo=None)

        with app.app_context():
            with db.engine.begin() as conn_raw:
                session = db.session
                cameras = get_cameras_due(session)

                if not cameras:
                    log.debug("Nenhuma câmera para verificar neste ciclo.")
                    time.sleep(Config.CHECKER_LOOP_SLEEP)
                    continue

                log.info(f"Verificando {len(cameras)} câmeras...")

                notificacoes = []
                with ThreadPoolExecutor(max_workers=Config.CHECKER_WORKERS) as executor:
                    futures = {
                        executor.submit(check_rtsp, cam["url_rtsp"]): cam
                        for cam in cameras
                    }
                    for future in as_completed(futures):
                        cam         = futures[future]
                        novo_status = "online" if future.result() else "offline"
                        try:
                            notif = processar_resultado(session, cam, novo_status, agora)
                            if notif:
                                notificacoes.append(notif)
                        except Exception as e:
                            log.error(f"Erro ao processar câmera {cam['id']}: {e}")

                try:
                    session.commit()
                    log.info("Ciclo concluído e commit realizado.")
                    for notif in notificacoes:
                        enviar_telegram(notif["chat_id"], notif["mensagem"])
                except Exception as e:
                    session.rollback()
                    log.error(f"Erro no commit: {e}")

        time.sleep(Config.CHECKER_LOOP_SLEEP)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    run_checker()
