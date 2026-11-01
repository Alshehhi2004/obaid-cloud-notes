"""Cloud Notes: Flask + PostgreSQL, with shared-session support across app nodes."""
import os
import re
import socket
from datetime import timedelta
from functools import wraps

import click
from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import (BigInteger, Column, DateTime, ForeignKey, Integer, MetaData,
                        String, Table, Text, create_engine, delete, func, insert, select, update)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

metadata = MetaData()
users = Table('users', metadata,
    Column('id', Integer, primary_key=True),
    Column('username', String(30), nullable=False, unique=True),
    Column('password_hash', String(255), nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False, server_default=func.now()))
notes = Table('notes', metadata,
    Column('id', Integer, primary_key=True),
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
    Column('title', String(120), nullable=False),
    Column('body', Text, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column('updated_at', DateTime(timezone=True), nullable=False, server_default=func.now()))


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get('SECRET_KEY'),
        DATABASE_URL=os.environ.get('DATABASE_URL'),
        INSTANCE_NAME=os.environ.get('INSTANCE_NAME', socket.gethostname()),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', 'false').lower() == 'true',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=1),
        MAX_CONTENT_LENGTH=64 * 1024,
        WTF_CSRF_TIME_LIMIT=timedelta(hours=1),
    )
    if test_config:
        app.config.update(test_config)
    if not app.config['SECRET_KEY'] or not app.config['DATABASE_URL']:
        raise RuntimeError('SECRET_KEY and DATABASE_URL must be configured outside the repository.')
    # Opt in only behind one trusted proxy. Leave disabled for the initial HTTP test
    # and for Quick Tunnel access: this app uses relative URLs, with no proxy dependency.
    if os.environ.get('TRUST_PROXY', 'false').lower() == 'true':
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0)
    CSRFProtect(app)
    engine = create_engine(app.config['DATABASE_URL'], pool_pre_ping=True)
    app.extensions['database'] = engine

    @app.cli.command('init-db')
    def init_db():
        """Create tables once before starting the application."""
        metadata.create_all(engine)
        click.echo('Cloud Notes database tables are ready.')

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return redirect(url_for('login'))
            return view(*args, **kwargs)
        return wrapped

    @app.before_request
    def load_user():
        g.user = None
        user_id = session.get('user_id')
        if user_id is not None:
            with engine.connect() as db:
                g.user = db.execute(select(users.c.id, users.c.username).where(users.c.id == user_id)).mappings().first()
            if g.user is None:
                session.clear()

    @app.context_processor
    def page_context():
        return {'instance_name': app.config['INSTANCE_NAME']}

    @app.after_request
    def response_headers(response):
        response.headers['X-Served-By'] = app.config['INSTANCE_NAME']
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'none'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        if not request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/health')
    def health():
        try:
            with engine.connect() as db:
                db.execute(select(1))
            return jsonify(status='ok', database='connected', served_by=app.config['INSTANCE_NAME'])
        except SQLAlchemyError:
            return jsonify(status='unavailable', served_by=app.config['INSTANCE_NAME']), 503

    @app.get('/instance')
    def instance():
        return jsonify(served_by=app.config['INSTANCE_NAME'])

    @app.get('/')
    def home():
        if g.user:
            return redirect(url_for('dashboard'))
        return render_template('home.html')

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if g.user:
            return redirect(url_for('dashboard'))
        if request.method == 'POST':
            username = request.form.get('username', '').strip().lower()
            password = request.form.get('password', '')
            if not re.fullmatch(r'[a-z0-9_]{3,30}', username):
                flash('Use 3–30 lowercase letters, numbers or underscores for your username.', 'error')
            elif len(password) < 12 or len(password) > 128:
                flash('Choose a password between 12 and 128 characters.', 'error')
            elif password != request.form.get('confirm_password', ''):
                flash('The passwords do not match.', 'error')
            else:
                try:
                    with engine.begin() as db:
                        user_id = db.execute(insert(users).values(username=username,
                            password_hash=generate_password_hash(password)).returning(users.c.id)).scalar_one()
                    session.clear()
                    session['user_id'] = user_id
                    session.permanent = True
                    flash('Your account is ready. Add your first note.', 'success')
                    return redirect(url_for('dashboard'))
                except IntegrityError:
                    flash('That username is already taken.', 'error')
        return render_template('auth.html', registering=True)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if g.user:
            return redirect(url_for('dashboard'))
        if request.method == 'POST':
            username = request.form.get('username', '').strip().lower()
            password = request.form.get('password', '')
            with engine.connect() as db:
                user = db.execute(select(users).where(users.c.username == username)).mappings().first()
            if user and check_password_hash(user['password_hash'], password):
                session.clear()
                session['user_id'] = user['id']
                session.permanent = True
                flash('Welcome back.', 'success')
                return redirect(url_for('dashboard'))
            flash('Incorrect username or password.', 'error')
        return render_template('auth.html', registering=False)

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('home'))

    @app.get('/notes')
    @login_required
    def dashboard():
        with engine.connect() as db:
            records = db.execute(select(notes).where(notes.c.user_id == g.user['id'])
                .order_by(notes.c.updated_at.desc(), notes.c.id.desc())).mappings().all()
        return render_template('notes.html', notes=records)

    def owned_note(note_id):
        with engine.connect() as db:
            record = db.execute(select(notes).where(notes.c.id == note_id,
                notes.c.user_id == g.user['id'])).mappings().first()
        if record is None:
            abort(404)
        return record

    @app.route('/notes/new', methods=['GET', 'POST'])
    @app.route('/notes/<int:note_id>/edit', methods=['GET', 'POST'])
    @login_required
    def edit_note(note_id=None):
        record = owned_note(note_id) if note_id else None
        if request.method == 'POST':
            title = request.form.get('title', '').strip()
            body = request.form.get('body', '').strip()
            if not title or len(title) > 120 or not body or len(body) > 10000:
                flash('Enter a title (up to 120 characters) and a note (up to 10,000 characters).', 'error')
                record = {'title': title, 'body': body}
            else:
                with engine.begin() as db:
                    if note_id:
                        db.execute(update(notes).where(notes.c.id == note_id,
                            notes.c.user_id == g.user['id']).values(title=title, body=body, updated_at=func.now()))
                    else:
                        db.execute(insert(notes).values(user_id=g.user['id'], title=title, body=body))
                flash('Note saved to your database.', 'success')
                return redirect(url_for('dashboard'))
        return render_template('edit.html', note=record, editing=note_id is not None)

    @app.post('/notes/<int:note_id>/delete')
    @login_required
    def delete_note(note_id):
        owned_note(note_id)
        with engine.begin() as db:
            db.execute(delete(notes).where(notes.c.id == note_id, notes.c.user_id == g.user['id']))
        flash('Note deleted.', 'success')
        return redirect(url_for('dashboard'))

    @app.errorhandler(CSRFError)
    def bad_csrf(error):
        return render_template('error.html', heading='Please reload the form',
            message='Your form token expired or is missing. Reload the page and try again.'), 400

    @app.errorhandler(404)
    def not_found(error):
        return render_template('error.html', heading='Page not found',
            message='This page or note is not available to your account.'), 404

    return app
