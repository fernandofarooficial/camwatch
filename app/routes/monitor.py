"""
app/routes/monitor.py — CamWatch
Blueprint da tela de monitoramento de eventos.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request
from sqlalchemy import desc, func, text
from sqlalchemy.orm import joinedload

from app.models import db, EventoCamera, Camera, GrupoCamera, Empresa, StatusCamera, StatusEvento

_SP = ZoneInfo("America/Sao_Paulo")

monitor_bp = Blueprint("monitor", __name__)

PAGE_SIZE = 50


def _query_eventos(empresa_id=None, grupo_id=None, camera_id=None, page=1):
    """
    Retorna (eventos, total, paginas) com filtros opcionais.
    """
    q = (
        db.session.query(EventoCamera)
        .join(EventoCamera.camera)
        .join(Camera.empresa)
        .order_by(desc(EventoCamera.timestamp))
    )

    if empresa_id:
        q = q.filter(Camera.empresa_id == empresa_id)
    if grupo_id:
        q = q.filter(Camera.grupo_id == grupo_id)
    if camera_id:
        q = q.filter(EventoCamera.camera_id == camera_id)

    total   = q.count()
    eventos = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    paginas = (total + PAGE_SIZE - 1) // PAGE_SIZE

    return eventos, total, paginas


@monitor_bp.route("/")
def index():
    """Tela principal — carrega filtros e primeira página."""
    empresas = Empresa.query.filter_by(ativo=True).order_by(Empresa.nome).all()
    grupos   = GrupoCamera.query.order_by(GrupoCamera.nome).all()
    cameras  = Camera.query.filter_by(ativo=True).order_by(Camera.nome).all()

    empresa_id = request.args.get("empresa_id", type=int)
    grupo_id   = request.args.get("grupo_id",   type=int)
    camera_id  = request.args.get("camera_id",  type=int)
    page       = request.args.get("page", 1,    type=int)

    eventos, total, paginas = _query_eventos(empresa_id, grupo_id, camera_id, page)

    # Resumo de status atual (para os cards do topo)
    resumo = db.session.execute(text("""
        SELECT
            SUM(ativo = TRUE  AND ultimo_status = 'online')      AS online,
            SUM(ativo = TRUE  AND ultimo_status = 'offline')     AS offline,
            SUM(ativo = TRUE  AND ultimo_status = 'desconhecido') AS desconhecido,
            SUM(ativo = TRUE)                                     AS total
        FROM camera
    """)).mappings().fetchone()

    return render_template(
        "monitor/index.html",
        empresas=empresas,
        grupos=grupos,
        cameras=cameras,
        eventos=eventos,
        total=total,
        paginas=paginas,
        page=page,
        empresa_id=empresa_id,
        grupo_id=grupo_id,
        camera_id=camera_id,
        resumo=resumo,
    )


@monitor_bp.route("/eventos/parcial")
def eventos_parcial():
    """
    Endpoint HTMX — retorna só a tabela de eventos (sem layout completo).
    Usado para filtros e paginação sem reload.
    """
    empresa_id = request.args.get("empresa_id", type=int)
    grupo_id   = request.args.get("grupo_id",   type=int)
    camera_id  = request.args.get("camera_id",  type=int)
    page       = request.args.get("page", 1,    type=int)

    eventos, total, paginas = _query_eventos(empresa_id, grupo_id, camera_id, page)

    return render_template(
        "partials/tabela_eventos.html",
        eventos=eventos,
        total=total,
        paginas=paginas,
        page=page,
        empresa_id=empresa_id,
        grupo_id=grupo_id,
        camera_id=camera_id,
    )


@monitor_bp.route("/resumo/parcial")
def resumo_parcial():
    """
    Endpoint HTMX — atualiza os cards de resumo periodicamente.
    """
    resumo = db.session.execute(text("""
        SELECT
            SUM(ativo = TRUE AND ultimo_status = 'online')       AS online,
            SUM(ativo = TRUE AND ultimo_status = 'offline')      AS offline,
            SUM(ativo = TRUE AND ultimo_status = 'desconhecido') AS desconhecido,
            SUM(ativo = TRUE)                                    AS total
        FROM camera
    """)).mappings().fetchone()

    return render_template("partials/cards_resumo.html", resumo=resumo)


# ==================================================================
# POLAROID — grade com status atual de cada câmera
# ==================================================================

def _query_cameras_polaroid(empresa_id=None, grupo_id=None, status=None):
    """Retorna (cameras, offline_desde) com filtros opcionais."""
    q = (Camera.query
         .filter_by(ativo=True)
         .join(Camera.empresa)
         .options(joinedload(Camera.grupo)))
    if empresa_id:
        q = q.filter(Camera.empresa_id == empresa_id)
    if grupo_id:
        q = q.filter(Camera.grupo_id == grupo_id)
    if status in ("online", "offline"):
        q = q.filter(Camera.ultimo_status == StatusCamera[status])
    cameras = q.order_by(Empresa.nome, Camera.nome).all()

    # Timestamp do último evento offline para câmeras atualmente offline
    offline_ids = [c.id for c in cameras if c.ultimo_status == StatusCamera.offline]
    offline_desde = {}
    if offline_ids:
        rows = (
            db.session.query(
                EventoCamera.camera_id,
                func.max(EventoCamera.timestamp).label("ts"),
            )
            .filter(
                EventoCamera.camera_id.in_(offline_ids),
                EventoCamera.status == StatusEvento.offline,
            )
            .group_by(EventoCamera.camera_id)
            .all()
        )
        offline_desde = {r.camera_id: r.ts for r in rows}

    return cameras, offline_desde


def _resumo_sql():
    return db.session.execute(text("""
        SELECT
            SUM(ativo = TRUE AND ultimo_status = 'online')       AS online,
            SUM(ativo = TRUE AND ultimo_status = 'offline')      AS offline,
            SUM(ativo = TRUE AND ultimo_status = 'desconhecido') AS desconhecido,
            SUM(ativo = TRUE)                                    AS total
        FROM camera
    """)).mappings().fetchone()


@monitor_bp.route("/polaroid")
def polaroid():
    """Tela Polaroid — situação atual de cada câmera em cards."""
    empresas = Empresa.query.filter_by(ativo=True).order_by(Empresa.nome).all()
    grupos   = GrupoCamera.query.order_by(GrupoCamera.nome).all()

    empresa_id = request.args.get("empresa_id", type=int)
    grupo_id   = request.args.get("grupo_id",   type=int)
    status     = request.args.get("status",     "")

    cameras, offline_desde = _query_cameras_polaroid(empresa_id, grupo_id, status)
    agora  = datetime.now(_SP).replace(tzinfo=None)
    resumo = _resumo_sql()

    return render_template(
        "monitor/polaroid.html",
        empresas=empresas,
        grupos=grupos,
        cameras=cameras,
        offline_desde=offline_desde,
        empresa_id=empresa_id,
        grupo_id=grupo_id,
        status=status,
        agora=agora,
        resumo=resumo,
    )


@monitor_bp.route("/polaroid/parcial")
def polaroid_parcial():
    """Endpoint HTMX — atualiza o grid de câmeras."""
    empresa_id = request.args.get("empresa_id", type=int)
    grupo_id   = request.args.get("grupo_id",   type=int)
    status     = request.args.get("status",     "")

    cameras, offline_desde = _query_cameras_polaroid(empresa_id, grupo_id, status)
    agora = datetime.now(_SP).replace(tzinfo=None)

    return render_template(
        "partials/grid_cameras.html",
        cameras=cameras,
        offline_desde=offline_desde,
        empresa_id=empresa_id,
        grupo_id=grupo_id,
        status=status,
        agora=agora,
    )
