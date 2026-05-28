"""
wsgi.py — CamWatch
Entry point para o Gunicorn.

DispatcherMiddleware monta a aplicação em /camwatch, fazendo com que
url_for(), redirects e HTMX gerem URLs corretas em produção.
"""

from werkzeug.middleware.dispatcher import DispatcherMiddleware

from app import create_app

_app = create_app()

# Em produção (via Nginx/Gunicorn): acesso em /camwatch
app = DispatcherMiddleware(None, {'/camwatch': _app})

if __name__ == "__main__":
    # Desenvolvimento local: acesso direto em http://localhost:5000
    _app.run(debug=True)
