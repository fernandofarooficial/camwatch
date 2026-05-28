"""
app/models.py — CamWatch
Mapeamento SQLAlchemy dos modelos do banco de dados.
"""

from datetime import datetime
from enum import Enum as PyEnum
from zoneinfo import ZoneInfo

from flask_sqlalchemy import SQLAlchemy

_SP = ZoneInfo("America/Sao_Paulo")


def _agora():
    """Retorna a hora atual em São Paulo como datetime sem fuso (para MySQL DATETIME)."""
    return datetime.now(_SP).replace(tzinfo=None)

db = SQLAlchemy()


# ------------------------------------------------------------------
# Enumerações
# ------------------------------------------------------------------

class StatusCamera(PyEnum):
    online       = "online"
    offline      = "offline"
    desconhecido = "desconhecido"


class StatusEvento(PyEnum):
    online  = "online"
    offline = "offline"


# ------------------------------------------------------------------
# Empresa
# ------------------------------------------------------------------

class Empresa(db.Model):
    __tablename__ = "empresa"

    id        = db.Column(db.Integer,     primary_key=True)
    nome      = db.Column(db.String(100), nullable=False)
    cnpj      = db.Column(db.String(18),  unique=True)
    ativo     = db.Column(db.Boolean,     nullable=False, default=True)
    criado_em = db.Column(db.DateTime,    nullable=False, default=_agora)

    # relacionamentos
    grupos  = db.relationship("GrupoCamera", back_populates="empresa",
                              lazy="dynamic", cascade="all, delete-orphan")
    cameras = db.relationship("Camera",      back_populates="empresa",
                              lazy="dynamic")

    def __repr__(self):
        return f"<Empresa id={self.id} nome={self.nome!r}>"

    def to_dict(self):
        return {
            "id":        self.id,
            "nome":      self.nome,
            "cnpj":      self.cnpj,
            "ativo":     self.ativo,
            "criado_em": self.criado_em.isoformat() if self.criado_em else None,
        }


# ------------------------------------------------------------------
# GrupoCamera
# ------------------------------------------------------------------

class GrupoCamera(db.Model):
    __tablename__ = "grupo_camera"

    id         = db.Column(db.Integer,     primary_key=True)
    nome       = db.Column(db.String(100), nullable=False)
    empresa_id = db.Column(db.Integer,     db.ForeignKey("empresa.id"), nullable=False)
    descricao  = db.Column(db.String(255))

    # relacionamentos
    empresa = db.relationship("Empresa", back_populates="grupos")
    cameras = db.relationship("Camera",  back_populates="grupo", lazy="dynamic")

    def __repr__(self):
        return f"<GrupoCamera id={self.id} nome={self.nome!r}>"

    def to_dict(self):
        return {
            "id":         self.id,
            "nome":       self.nome,
            "empresa_id": self.empresa_id,
            "descricao":  self.descricao,
        }


# ------------------------------------------------------------------
# Camera
# ------------------------------------------------------------------

class Camera(db.Model):
    __tablename__ = "camera"

    id                 = db.Column(db.Integer,     primary_key=True)
    nome               = db.Column(db.String(100), nullable=False)
    url_rtsp           = db.Column(db.String(255), nullable=False)
    empresa_id         = db.Column(db.Integer,     db.ForeignKey("empresa.id"),      nullable=False)
    grupo_id           = db.Column(db.Integer,     db.ForeignKey("grupo_camera.id"), nullable=True)
    ativo              = db.Column(db.Boolean,     nullable=False, default=True)
    intervalo_segundos = db.Column(db.Integer,     nullable=False, default=60)
    ultimo_status      = db.Column(
        db.Enum(StatusCamera),
        nullable=False,
        default=StatusCamera.desconhecido,
    )
    ultima_verificacao = db.Column(db.DateTime, nullable=True)

    # relacionamentos
    empresa = db.relationship("Empresa",      back_populates="cameras")
    grupo   = db.relationship("GrupoCamera",  back_populates="cameras")
    eventos = db.relationship("EventoCamera", back_populates="camera",
                              lazy="dynamic",  cascade="all, delete-orphan",
                              order_by="EventoCamera.timestamp.desc()")

    def __repr__(self):
        return f"<Camera id={self.id} nome={self.nome!r} status={self.ultimo_status}>"

    @property
    def precisa_verificar(self):
        """True se o intervalo individual já expirou."""
        if self.ultima_verificacao is None:
            return True
        delta = (datetime.utcnow() - self.ultima_verificacao).total_seconds()
        return delta >= self.intervalo_segundos

    def to_dict(self):
        return {
            "id":                  self.id,
            "nome":                self.nome,
            "url_rtsp":            self.url_rtsp,
            "empresa_id":          self.empresa_id,
            "empresa_nome":        self.empresa.nome if self.empresa else None,
            "grupo_id":            self.grupo_id,
            "grupo_nome":          self.grupo.nome if self.grupo else None,
            "ativo":               self.ativo,
            "intervalo_segundos":  self.intervalo_segundos,
            "ultimo_status":       self.ultimo_status.value,
            "ultima_verificacao":  (
                self.ultima_verificacao.isoformat()
                if self.ultima_verificacao else None
            ),
        }


# ------------------------------------------------------------------
# EventoCamera  (só grava mudanças de estado)
# ------------------------------------------------------------------

class EventoCamera(db.Model):
    __tablename__ = "evento_camera"

    id                       = db.Column(db.BigInteger,  primary_key=True)
    camera_id                = db.Column(db.Integer,     db.ForeignKey("camera.id"), nullable=False)
    status                   = db.Column(db.Enum(StatusEvento), nullable=False)
    timestamp                = db.Column(db.DateTime, nullable=False, default=_agora)
    duracao_offline_segundos = db.Column(db.Integer, nullable=True)

    # relacionamento
    camera = db.relationship("Camera", back_populates="eventos")

    def __repr__(self):
        return f"<EventoCamera camera_id={self.camera_id} status={self.status} ts={self.timestamp}>"

    def to_dict(self):
        return {
            "id":                       self.id,
            "camera_id":                self.camera_id,
            "camera_nome":              self.camera.nome if self.camera else None,
            "empresa_nome":             self.camera.empresa.nome if self.camera and self.camera.empresa else None,
            "grupo_nome":               self.camera.grupo.nome if self.camera and self.camera.grupo else None,
            "status":                   self.status.value,
            "timestamp":                self.timestamp.isoformat(),
            "duracao_offline_segundos": self.duracao_offline_segundos,
        }
