"""
app/routes/cadastro.py — CamWatch
Blueprint de cadastros: empresas, grupos de câmeras e câmeras.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort
from app.models import db, Empresa, GrupoCamera, Camera, StatusCamera

cadastro_bp = Blueprint("cadastro", __name__, url_prefix="/cadastro")


def _empresa_restrita() -> int | None:
    """Retorna empresa_id se a sessão estiver restrita a uma empresa, None se acesso total."""
    acesso = session.get("empresa_acesso")
    if acesso == "*":
        return None
    return acesso  # int quando restrito


@cadastro_bp.before_request
def verificar_acesso():
    if "empresa_acesso" not in session:
        return redirect(url_for("monitor.acesso"))


# ==================================================================
# EMPRESAS — apenas acesso master
# ==================================================================

@cadastro_bp.route("/empresas")
def empresas():
    if _empresa_restrita() is not None:
        abort(403)
    lista = Empresa.query.order_by(Empresa.nome).all()
    return render_template("cadastro/empresas.html", empresas=lista)


@cadastro_bp.route("/empresas/nova", methods=["GET", "POST"])
def empresa_nova():
    if _empresa_restrita() is not None:
        abort(403)
    if request.method == "POST":
        nome  = request.form.get("nome",  "").strip()
        cnpj  = request.form.get("cnpj",  "").strip() or None
        senha = request.form.get("senha", "").strip() or None
        if not nome:
            flash("Nome é obrigatório.", "erro")
            return render_template("cadastro/empresa_form.html", empresa=None)
        if senha and (len(senha) != 6 or not senha.isdigit()):
            flash("Senha deve ter exatamente 6 dígitos numéricos.", "erro")
            return render_template("cadastro/empresa_form.html", empresa=None)
        db.session.add(Empresa(nome=nome, cnpj=cnpj, senha=senha))
        db.session.commit()
        flash("Empresa cadastrada com sucesso.", "ok")
        return redirect(url_for("cadastro.empresas"))
    return render_template("cadastro/empresa_form.html", empresa=None)


@cadastro_bp.route("/empresas/<int:id>/editar", methods=["GET", "POST"])
def empresa_editar(id):
    if _empresa_restrita() is not None:
        abort(403)
    empresa = Empresa.query.get_or_404(id)
    if request.method == "POST":
        nome  = request.form.get("nome",  "").strip()
        senha = request.form.get("senha", "").strip() or None
        if not nome:
            flash("Nome é obrigatório.", "erro")
            return render_template("cadastro/empresa_form.html", empresa=empresa)
        if senha and (len(senha) != 6 or not senha.isdigit()):
            flash("Senha deve ter exatamente 6 dígitos numéricos.", "erro")
            return render_template("cadastro/empresa_form.html", empresa=empresa)
        empresa.nome  = nome
        empresa.cnpj  = request.form.get("cnpj", "").strip() or None
        empresa.ativo = "ativo" in request.form
        empresa.senha = senha
        db.session.commit()
        flash("Empresa atualizada.", "ok")
        return redirect(url_for("cadastro.empresas"))
    return render_template("cadastro/empresa_form.html", empresa=empresa)


@cadastro_bp.route("/empresas/<int:id>/excluir", methods=["POST"])
def empresa_excluir(id):
    if _empresa_restrita() is not None:
        abort(403)
    empresa = Empresa.query.get_or_404(id)
    try:
        db.session.delete(empresa)
        db.session.commit()
        flash("Empresa excluída.", "ok")
    except Exception:
        db.session.rollback()
        flash("Não é possível excluir: existem grupos ou câmeras vinculados.", "erro")
    return redirect(url_for("cadastro.empresas"))


# ==================================================================
# GRUPOS DE CÂMERAS
# ==================================================================

@cadastro_bp.route("/grupos")
def grupos():
    empresa_id = _empresa_restrita()
    q = GrupoCamera.query.join(GrupoCamera.empresa)
    if empresa_id:
        q = q.filter(GrupoCamera.empresa_id == empresa_id)
    lista = q.order_by(Empresa.nome, GrupoCamera.nome).all()
    return render_template("cadastro/grupos.html", grupos=lista)


@cadastro_bp.route("/grupos/novo", methods=["GET", "POST"])
def grupo_novo():
    empresa_id = _empresa_restrita()
    if empresa_id:
        empresas = Empresa.query.filter_by(id=empresa_id, ativo=True).all()
    else:
        empresas = Empresa.query.filter_by(ativo=True).order_by(Empresa.nome).all()

    if request.method == "POST":
        nome      = request.form.get("nome", "").strip()
        eid       = empresa_id or request.form.get("empresa_id", type=int)
        descricao = request.form.get("descricao", "").strip() or None
        telegram  = request.form.get("telegram",  "").strip() or None
        if not nome or not eid:
            flash("Nome e empresa são obrigatórios.", "erro")
            return render_template("cadastro/grupo_form.html",
                                   grupo=None, empresas=empresas, empresa_restrita=empresa_id)
        db.session.add(GrupoCamera(nome=nome, empresa_id=eid, descricao=descricao, telegram=telegram))
        db.session.commit()
        flash("Grupo cadastrado com sucesso.", "ok")
        return redirect(url_for("cadastro.grupos"))
    return render_template("cadastro/grupo_form.html",
                           grupo=None, empresas=empresas, empresa_restrita=empresa_id)


@cadastro_bp.route("/grupos/<int:id>/editar", methods=["GET", "POST"])
def grupo_editar(id):
    grupo      = GrupoCamera.query.get_or_404(id)
    empresa_id = _empresa_restrita()
    if empresa_id and grupo.empresa_id != empresa_id:
        abort(403)

    if empresa_id:
        empresas = Empresa.query.filter_by(id=empresa_id, ativo=True).all()
    else:
        empresas = Empresa.query.filter_by(ativo=True).order_by(Empresa.nome).all()

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        eid  = empresa_id or request.form.get("empresa_id", type=int)
        if not nome or not eid:
            flash("Nome e empresa são obrigatórios.", "erro")
            return render_template("cadastro/grupo_form.html",
                                   grupo=grupo, empresas=empresas, empresa_restrita=empresa_id)
        grupo.nome       = nome
        grupo.empresa_id = eid
        grupo.descricao  = request.form.get("descricao", "").strip() or None
        grupo.telegram   = request.form.get("telegram",  "").strip() or None
        db.session.commit()
        flash("Grupo atualizado.", "ok")
        return redirect(url_for("cadastro.grupos"))
    return render_template("cadastro/grupo_form.html",
                           grupo=grupo, empresas=empresas, empresa_restrita=empresa_id)


@cadastro_bp.route("/grupos/<int:id>/excluir", methods=["POST"])
def grupo_excluir(id):
    grupo      = GrupoCamera.query.get_or_404(id)
    empresa_id = _empresa_restrita()
    if empresa_id and grupo.empresa_id != empresa_id:
        abort(403)
    try:
        db.session.delete(grupo)
        db.session.commit()
        flash("Grupo excluído.", "ok")
    except Exception:
        db.session.rollback()
        flash("Não é possível excluir: existem câmeras vinculadas.", "erro")
    return redirect(url_for("cadastro.grupos"))


# ==================================================================
# CÂMERAS
# ==================================================================

@cadastro_bp.route("/cameras")
def cameras():
    empresa_id = _empresa_restrita()
    filtro_grupo = request.args.get("grupo_id", type=int)

    q = Camera.query.join(Camera.empresa)
    if empresa_id:
        q = q.filter(Camera.empresa_id == empresa_id)
    else:
        filtro_empresa = request.args.get("empresa_id", type=int)
        if filtro_empresa:
            q = q.filter(Camera.empresa_id == filtro_empresa)
    if filtro_grupo:
        q = q.filter(Camera.grupo_id == filtro_grupo)
    q = q.order_by(Empresa.nome, Camera.nome)

    if empresa_id:
        empresas = Empresa.query.filter_by(id=empresa_id, ativo=True).all()
        grupos   = GrupoCamera.query.filter_by(empresa_id=empresa_id).order_by(GrupoCamera.nome).all()
        filtro_empresa = empresa_id
    else:
        filtro_empresa = request.args.get("empresa_id", type=int)
        empresas = Empresa.query.filter_by(ativo=True).order_by(Empresa.nome).all()
        grupos   = GrupoCamera.query.order_by(GrupoCamera.nome).all()

    return render_template(
        "cadastro/cameras.html",
        cameras=q.all(),
        empresas=empresas,
        grupos=grupos,
        empresa_id=filtro_empresa,
        grupo_id=filtro_grupo,
        empresa_restrita=empresa_id,
    )


@cadastro_bp.route("/cameras/nova", methods=["GET", "POST"])
def camera_nova():
    empresa_id = _empresa_restrita()
    if empresa_id:
        empresas = Empresa.query.filter_by(id=empresa_id, ativo=True).all()
        grupos   = GrupoCamera.query.filter_by(empresa_id=empresa_id).order_by(GrupoCamera.nome).all()
    else:
        empresas = Empresa.query.filter_by(ativo=True).order_by(Empresa.nome).all()
        grupos   = GrupoCamera.query.order_by(GrupoCamera.nome).all()

    if request.method == "POST":
        nome     = request.form.get("nome", "").strip()
        url_rtsp = request.form.get("url_rtsp", "").strip()
        eid      = empresa_id or request.form.get("empresa_id", type=int)
        grupo_id = request.form.get("grupo_id", type=int) or None
        intervalo = request.form.get("intervalo_segundos", 60, type=int)
        if not nome or not url_rtsp or not eid:
            flash("Nome, URL RTSP e empresa são obrigatórios.", "erro")
            return render_template("cadastro/camera_form.html",
                                   camera=None, empresas=empresas, grupos=grupos,
                                   empresa_restrita=empresa_id)
        db.session.add(Camera(
            nome=nome,
            url_rtsp=url_rtsp,
            empresa_id=eid,
            grupo_id=grupo_id,
            intervalo_segundos=intervalo,
        ))
        db.session.commit()
        flash("Câmera cadastrada com sucesso.", "ok")
        return redirect(url_for("cadastro.cameras"))
    return render_template("cadastro/camera_form.html",
                           camera=None, empresas=empresas, grupos=grupos,
                           empresa_restrita=empresa_id)


@cadastro_bp.route("/cameras/<int:id>/editar", methods=["GET", "POST"])
def camera_editar(id):
    camera     = Camera.query.get_or_404(id)
    empresa_id = _empresa_restrita()
    if empresa_id and camera.empresa_id != empresa_id:
        abort(403)

    if empresa_id:
        empresas = Empresa.query.filter_by(id=empresa_id, ativo=True).all()
        grupos   = GrupoCamera.query.filter_by(empresa_id=empresa_id).order_by(GrupoCamera.nome).all()
    else:
        empresas = Empresa.query.filter_by(ativo=True).order_by(Empresa.nome).all()
        grupos   = GrupoCamera.query.order_by(GrupoCamera.nome).all()

    if request.method == "POST":
        nome     = request.form.get("nome", "").strip()
        url_rtsp = request.form.get("url_rtsp", "").strip()
        eid      = empresa_id or request.form.get("empresa_id", type=int)
        if not nome or not url_rtsp or not eid:
            flash("Nome, URL RTSP e empresa são obrigatórios.", "erro")
            return render_template("cadastro/camera_form.html",
                                   camera=camera, empresas=empresas, grupos=grupos,
                                   empresa_restrita=empresa_id)
        camera.nome               = nome
        camera.url_rtsp           = url_rtsp
        camera.empresa_id         = eid
        camera.grupo_id           = request.form.get("grupo_id", type=int) or None
        camera.intervalo_segundos = request.form.get("intervalo_segundos", 60, type=int)
        camera.ativo              = "ativo" in request.form
        db.session.commit()
        flash("Câmera atualizada.", "ok")
        return redirect(url_for("cadastro.cameras"))
    return render_template("cadastro/camera_form.html",
                           camera=camera, empresas=empresas, grupos=grupos,
                           empresa_restrita=empresa_id)


@cadastro_bp.route("/cameras/<int:id>/excluir", methods=["POST"])
def camera_excluir(id):
    camera     = Camera.query.get_or_404(id)
    empresa_id = _empresa_restrita()
    if empresa_id and camera.empresa_id != empresa_id:
        abort(403)
    db.session.delete(camera)
    db.session.commit()
    flash("Câmera excluída.", "ok")
    return redirect(url_for("cadastro.cameras"))


@cadastro_bp.route("/cameras/<int:id>/toggle", methods=["POST"])
def camera_toggle(id):
    """Ativa/desativa câmera via botão rápido na listagem."""
    camera     = Camera.query.get_or_404(id)
    empresa_id = _empresa_restrita()
    if empresa_id and camera.empresa_id != empresa_id:
        abort(403)
    camera.ativo = not camera.ativo
    db.session.commit()
    return redirect(url_for("cadastro.cameras"))
