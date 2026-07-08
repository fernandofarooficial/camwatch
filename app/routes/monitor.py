"""
app/routes/monitor.py — CamWatch
Blueprint da tela de monitoramento de eventos.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request, session, redirect, url_for
from sqlalchemy import desc, func, text
from sqlalchemy.orm import joinedload

from app.models import db, EventoCamera, Camera, GrupoCamera, Empresa, StatusCamera, StatusEvento
from config import Config

_NOTIF_THRESHOLD_SEC = Config.NOTIF_THRESHOLD_SEC

_SP = ZoneInfo("America/Sao_Paulo")

monitor_bp = Blueprint("monitor", __name__)

PAGE_SIZE = 50


# ------------------------------------------------------------------
# Controle de acesso por sessão
# ------------------------------------------------------------------

def _empresa_restrita() -> int | None:
    """Retorna empresa_id se a sessão estiver restrita a uma empresa, None se acesso total."""
    acesso = session.get("empresa_acesso")
    if acesso == "*":
        return None
    return acesso  # int quando restrito


@monitor_bp.before_request
def verificar_acesso():
    if request.endpoint in ("monitor.acesso", "monitor.sair"):
        return
    if "empresa_acesso" not in session:
        return redirect(url_for("monitor.acesso"))


@monitor_bp.route("/acesso", methods=["GET", "POST"])
def acesso():
    if "empresa_acesso" in session:
        return redirect(url_for("monitor.index"))

    erro = None
    if request.method == "POST":
        pin = request.form.get("pin", "").strip()

        # Senha master tem prioridade
        master = Config.MASTER_PASSWORD
        if master and pin == master:
            session["empresa_acesso"] = "*"
            session["empresa_nome"]   = "Acesso total"
            return redirect(url_for("monitor.index"))

        # PIN de empresa: exatamente 6 dígitos
        if len(pin) == 6 and pin.isdigit():
            empresa = Empresa.query.filter_by(senha=pin, ativo=True).first()
            if empresa:
                session["empresa_acesso"] = empresa.id
                session["empresa_nome"]   = empresa.nome
                return redirect(url_for("monitor.index"))

        erro = "Senha incorreta."

    return render_template("monitor/acesso.html", erro=erro)


@monitor_bp.route("/sair")
def sair():
    session.pop("empresa_acesso", None)
    session.pop("empresa_nome",   None)
    return redirect(url_for("monitor.acesso"))


# ------------------------------------------------------------------
# Helpers de query
# ------------------------------------------------------------------

def _resumo_sql(empresa_id=None):
    where  = "AND c.empresa_id = :eid" if empresa_id else ""
    params = {"eid": empresa_id} if empresa_id else {}
    return db.session.execute(text(f"""
        SELECT
            SUM(ativo = TRUE AND ultimo_status = 'online')       AS online,
            SUM(ativo = TRUE AND ultimo_status = 'offline')      AS offline,
            SUM(ativo = TRUE AND ultimo_status = 'desconhecido') AS desconhecido,
            SUM(ativo = TRUE)                                    AS total
        FROM camera c
        WHERE 1=1 {where}
    """), params).mappings().fetchone()


def _query_eventos(empresa_id=None, grupo_id=None, camera_id=None, page=1):
    """
    Retorna (eventos, total, paginas) com filtros opcionais.
    Count e dados em queries separadas para não degradar o COUNT com joinedload.
    """
    def _base(with_load=False):
        q = (
            db.session.query(EventoCamera)
            .join(EventoCamera.camera)
            .join(Camera.empresa)
            .order_by(desc(EventoCamera.timestamp))
        )
        if with_load:
            q = q.options(
                joinedload(EventoCamera.camera).joinedload(Camera.empresa),
                joinedload(EventoCamera.camera).joinedload(Camera.grupo),
            )
        if empresa_id:
            q = q.filter(Camera.empresa_id == empresa_id)
        if grupo_id:
            q = q.filter(Camera.grupo_id == grupo_id)
        if camera_id:
            q = q.filter(EventoCamera.camera_id == camera_id)
        return q

    total   = _base().count()
    eventos = _base(with_load=True).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    paginas = (total + PAGE_SIZE - 1) // PAGE_SIZE

    return eventos, total, paginas


# ------------------------------------------------------------------
# Monitor — log de eventos
# ------------------------------------------------------------------

@monitor_bp.route("/")
def index():
    """Tela principal — carrega filtros e primeira página."""
    empresas = Empresa.query.filter_by(ativo=True).order_by(Empresa.nome).all()
    grupos   = GrupoCamera.query.order_by(GrupoCamera.nome).all()
    cameras  = Camera.query.filter_by(ativo=True).order_by(Camera.nome).all()

    restrito   = _empresa_restrita()
    empresa_id = restrito if restrito is not None else request.args.get("empresa_id", type=int)
    grupo_id   = request.args.get("grupo_id",  type=int)
    camera_id  = request.args.get("camera_id", type=int)
    page       = request.args.get("page", 1,   type=int)

    eventos, total, paginas = _query_eventos(empresa_id, grupo_id, camera_id, page)
    resumo = _resumo_sql(restrito)

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
        empresa_restrita=restrito,
    )


@monitor_bp.route("/eventos/parcial")
def eventos_parcial():
    """Endpoint HTMX — retorna só a tabela de eventos (sem layout completo)."""
    restrito   = _empresa_restrita()
    empresa_id = restrito if restrito is not None else request.args.get("empresa_id", type=int)
    grupo_id   = request.args.get("grupo_id",  type=int)
    camera_id  = request.args.get("camera_id", type=int)
    page       = request.args.get("page", 1,   type=int)

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
    """Endpoint HTMX — atualiza os cards de resumo periodicamente."""
    resumo = _resumo_sql(_empresa_restrita())
    return render_template("partials/cards_resumo.html", resumo=resumo)


# ------------------------------------------------------------------
# Polaroid — grade com status atual de cada câmera
# ------------------------------------------------------------------

def _query_cameras_polaroid(empresa_id=None, grupo_id=None, status=None):
    """Retorna (cameras, offline_desde) com filtros opcionais."""
    q = (Camera.query
         .filter_by(ativo=True)
         .join(Camera.empresa)
         .options(
             joinedload(Camera.empresa),
             joinedload(Camera.grupo),
         ))
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


@monitor_bp.route("/polaroid")
def polaroid():
    """Tela Polaroid — situação atual de cada câmera em cards."""
    empresas = Empresa.query.filter_by(ativo=True).order_by(Empresa.nome).all()
    grupos   = GrupoCamera.query.order_by(GrupoCamera.nome).all()

    restrito   = _empresa_restrita()
    empresa_id = restrito if restrito is not None else request.args.get("empresa_id", type=int)
    grupo_id   = request.args.get("grupo_id", type=int)
    status     = request.args.get("status",   "")

    cameras, offline_desde = _query_cameras_polaroid(empresa_id, grupo_id, status)
    agora  = datetime.now(_SP).replace(tzinfo=None)
    resumo = _resumo_sql(restrito)

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
        empresa_restrita=restrito,
    )


@monitor_bp.route("/polaroid/parcial")
def polaroid_parcial():
    """Endpoint HTMX — atualiza o grid de câmeras."""
    restrito   = _empresa_restrita()
    empresa_id = restrito if restrito is not None else request.args.get("empresa_id", type=int)
    grupo_id   = request.args.get("grupo_id", type=int)
    status     = request.args.get("status",   "")

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


# ------------------------------------------------------------------
# Números — estatísticas de offline por empresa
# ------------------------------------------------------------------

@monitor_bp.route("/numeros")
def numeros():
    """Tela Números — estatísticas de offline por empresa nas últimas 120h."""
    restrito = _empresa_restrita()
    limite   = datetime.now(_SP).replace(tzinfo=None) - timedelta(hours=120)

    empresa_where = "AND e.id = :empresa_id" if restrito else ""
    params = {"limite": limite}
    if restrito:
        params["empresa_id"] = restrito

    stats = db.session.execute(text(f"""
        WITH cam_events AS (
            SELECT
                ev.id,
                ev.camera_id,
                c.empresa_id,
                ev.status,
                LEAD(ev.duracao_offline_segundos)
                    OVER (PARTITION BY ev.camera_id ORDER BY ev.timestamp)
                    AS duracao_seg
            FROM evento_camera ev
            JOIN camera c ON c.id = ev.camera_id
            WHERE ev.timestamp >= :limite
        )
        SELECT
            e.id   AS empresa_id,
            e.nome AS empresa_nome,

            (SELECT COUNT(*)
             FROM camera c
             WHERE c.empresa_id = e.id AND c.ativo = TRUE)
                AS total_cameras,

            (SELECT COUNT(*)
             FROM camera c
             WHERE c.empresa_id = e.id
               AND c.ativo = TRUE
               AND c.ultimo_status = 'offline')
                AS cameras_offline_agora,

            COUNT(DISTINCT ce.camera_id)  AS cameras_com_offline,
            COUNT(ce.id)                  AS total_vezes_offline,
            AVG(ce.duracao_seg)           AS tempo_medio_offline_seg,

            SUM(ce.duracao_seg < 180)                                  AS offline_menos_3min,
            SUM(ce.duracao_seg >= 180 AND ce.duracao_seg < 600)        AS offline_menos_10min,
            SUM(ce.duracao_seg >= 600)                                 AS offline_mais_10min

        FROM empresa e
        LEFT JOIN cam_events ce ON ce.empresa_id = e.id AND ce.status = 'offline'
        WHERE e.ativo = TRUE {empresa_where}
        GROUP BY e.id, e.nome
        ORDER BY e.nome
    """), params).mappings().fetchall()

    return render_template("monitor/numeros.html", stats=stats, limite=limite)


# ------------------------------------------------------------------
# Fora do ar — câmeras offline há mais de _NOTIF_THRESHOLD_SEC
# ------------------------------------------------------------------

def _query_fora_do_ar(empresa_id=None, grupo_id=None):
    """Retorna (cameras, offline_desde, agora) — só câmeras offline acima do limiar."""
    agora  = datetime.now(_SP).replace(tzinfo=None)
    limite = agora - timedelta(seconds=_NOTIF_THRESHOLD_SEC)

    sub = (
        db.session.query(
            EventoCamera.camera_id,
            func.max(EventoCamera.timestamp).label("ts"),
        )
        .filter(EventoCamera.status == StatusEvento.offline)
        .group_by(EventoCamera.camera_id)
        .subquery()
    )

    q = (
        db.session.query(Camera, sub.c.ts)
        .filter(Camera.ativo.is_(True))
        .filter(Camera.ultimo_status == StatusCamera.offline)
        .join(sub, Camera.id == sub.c.camera_id)
        .filter(sub.c.ts <= limite)
        .join(Camera.empresa)
        .options(
            joinedload(Camera.empresa),
            joinedload(Camera.grupo),
        )
    )

    if empresa_id:
        q = q.filter(Camera.empresa_id == empresa_id)
    if grupo_id:
        q = q.filter(Camera.grupo_id == grupo_id)

    rows = q.order_by(sub.c.ts.desc()).all()
    cameras      = [r[0] for r in rows]
    offline_desde = {r[0].id: r[1] for r in rows}

    return cameras, offline_desde, agora


@monitor_bp.route("/fora-do-ar")
def fora_do_ar():
    """Tela Fora do ar — câmeras offline acima do limiar de notificação."""
    restrito   = _empresa_restrita()
    empresa_id = restrito if restrito is not None else request.args.get("empresa_id", type=int)
    grupo_id   = request.args.get("grupo_id", type=int)
    refresh    = request.args.get("refresh", 60, type=int)

    empresas = Empresa.query.filter_by(ativo=True).order_by(Empresa.nome).all()
    grupos   = GrupoCamera.query.order_by(GrupoCamera.nome).all()
    if restrito:
        grupos = [g for g in grupos if g.empresa_id == restrito]

    cameras, offline_desde, agora = _query_fora_do_ar(empresa_id, grupo_id)
    resumo = _resumo_sql(restrito)

    return render_template(
        "monitor/fora_do_ar.html",
        empresas=empresas,
        grupos=grupos,
        cameras=cameras,
        offline_desde=offline_desde,
        empresa_id=empresa_id,
        grupo_id=grupo_id,
        agora=agora,
        resumo=resumo,
        empresa_restrita=restrito,
        refresh=refresh,
        threshold_sec=_NOTIF_THRESHOLD_SEC,
        threshold_min=_NOTIF_THRESHOLD_SEC // 60,
    )


@monitor_bp.route("/fora-do-ar/parcial")
def fora_do_ar_parcial():
    """Endpoint HTMX — atualiza a tabela de câmeras fora do ar."""
    restrito   = _empresa_restrita()
    empresa_id = restrito if restrito is not None else request.args.get("empresa_id", type=int)
    grupo_id   = request.args.get("grupo_id", type=int)

    cameras, offline_desde, agora = _query_fora_do_ar(empresa_id, grupo_id)

    return render_template(
        "partials/tabela_fora_do_ar.html",
        cameras=cameras,
        offline_desde=offline_desde,
        empresa_id=empresa_id,
        grupo_id=grupo_id,
        agora=agora,
        threshold_sec=_NOTIF_THRESHOLD_SEC,
    )


@monitor_bp.route("/numeros/detalhe/<int:empresa_id>")
def numeros_detalhe(empresa_id):
    """Endpoint HTMX — breakdown por câmera para auditar os números."""
    restrito = _empresa_restrita()
    if restrito is not None and restrito != empresa_id:
        return "", 403

    limite = datetime.now(_SP).replace(tzinfo=None) - timedelta(hours=120)

    cameras = db.session.execute(text("""
        WITH cam_events AS (
            SELECT
                ev.id,
                ev.camera_id,
                ev.status,
                LEAD(ev.duracao_offline_segundos)
                    OVER (PARTITION BY ev.camera_id ORDER BY ev.timestamp)
                    AS duracao_seg
            FROM evento_camera ev
            JOIN camera c ON c.id = ev.camera_id
            WHERE ev.timestamp >= :limite
              AND c.empresa_id  = :empresa_id
        )
        SELECT
            c.id            AS camera_id,
            c.nome          AS camera_nome,
            g.nome          AS grupo_nome,
            c.ultimo_status AS status_atual,
            COUNT(ce.id)              AS total_vezes_offline,
            AVG(ce.duracao_seg)       AS tempo_medio_seg,
            SUM(ce.duracao_seg < 180)                           AS menos_3min,
            SUM(ce.duracao_seg >= 180 AND ce.duracao_seg < 600) AS menos_10min,
            SUM(ce.duracao_seg >= 600)                          AS mais_10min
        FROM camera c
        LEFT JOIN grupo_camera g  ON g.id = c.grupo_id
        LEFT JOIN cam_events   ce ON ce.camera_id = c.id AND ce.status = 'offline'
        WHERE c.empresa_id = :empresa_id AND c.ativo = TRUE
        GROUP BY c.id, c.nome, g.nome, c.ultimo_status
        ORDER BY total_vezes_offline DESC, c.nome
    """), {"limite": limite, "empresa_id": empresa_id}).mappings().fetchall()

    return render_template("partials/numeros_detalhe.html", cameras=cameras)
