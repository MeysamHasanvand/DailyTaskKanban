\
    """
    Flask Daily Kanban
    ------------------
    Small Flask app using SQLite (SQLAlchemy) to provide a daily kanban with:
      - configurable up to 5 columns (Settings page)
      - tasks only editable for "today"; at first request after date change tasks are archived (read-only)
      - browse archive by day / week / month / year
    Run:
      pip install flask flask_sqlalchemy
      python app.py
    Open http://127.0.0.1:5000/
    """
    from datetime import datetime, date, timedelta
    import os
    from flask import Flask, request, redirect, url_for, render_template, jsonify, abort
    from flask_sqlalchemy import SQLAlchemy

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, 'kanban.db')

    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DB_PATH
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db = SQLAlchemy(app)

    # -------------------- Models --------------------
    class Setting(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        key = db.Column(db.String(64), unique=True, nullable=False)
        value = db.Column(db.String(1024), nullable=True)

        @staticmethod
        def get(key, default=None):
            s = Setting.query.filter_by(key=key).first()
            return s.value if s else default

        @staticmethod
        def set(key, value):
            s = Setting.query.filter_by(key=key).first()
            if not s:
                s = Setting(key=key, value=value)
                db.session.add(s)
            else:
                s.value = value
            db.session.commit()

    class Task(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        title = db.Column(db.String(255), nullable=False)
        description = db.Column(db.Text, nullable=True)
        column_index = db.Column(db.Integer, nullable=False, default=0)  # 0..4
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        task_date = db.Column(db.Date, nullable=False)  # date the task belongs to
        archived = db.Column(db.Boolean, default=False)
        archived_at = db.Column(db.DateTime, nullable=True)

        def to_dict(self):
            return {
                'id': self.id,
                'title': self.title,
                'description': self.description or '',
                'column_index': self.column_index,
                'created_at': self.created_at.isoformat(),
                'task_date': self.task_date.isoformat(),
                'archived': self.archived
            }

    # -------------------- DB init --------------------
    @app.before_first_request
    def init_db():
        db.create_all()
        # default column names if not set
        cols = Setting.get('columns')
        if not cols:
            default_cols = ['Backlog', 'To Do', 'In Progress', 'Review', 'Done']
            Setting.set('columns', ','.join(default_cols))
        # last_active_date
        if not Setting.get('last_active_date'):
            Setting.set('last_active_date', date.today().isoformat())

    # -------------------- Utility: rollover/archiving --------------------
    def check_rollover_and_archive():
        \"\"\"Archive tasks for days older than the stored last_active_date up to yesterday.\"\"\"
        last = Setting.get('last_active_date')
        if not last:
            Setting.set('last_active_date', date.today().isoformat())
            return
        try:
            last_date = date.fromisoformat(last)
        except Exception:
            Setting.set('last_active_date', date.today().isoformat())
            return
        today = date.today()
        if last_date >= today:
            return
        # Archive each day from last_date (exclusive) up to yesterday (inclusive)
        day = last_date
        while day < today:
            tasks = Task.query.filter_by(task_date=day, archived=False).all()
            for t in tasks:
                t.archived = True
                t.archived_at = datetime.utcnow()
            if tasks:
                db.session.commit()
            day = day + timedelta(days=1)
        # update last_active_date
        Setting.set('last_active_date', today.isoformat())

    # -------------------- Routes --------------------
    @app.route('/')
    def index():
        check_rollover_and_archive()
        cols_raw = Setting.get('columns') or ''
        cols = [c for c in cols_raw.split(',') if c]
        if len(cols) > 5:
            cols = cols[:5]
        while len(cols) < 5:
            cols.append(f'Column {len(cols)+1}')
        today = date.today()
        tasks = Task.query.filter_by(task_date=today).order_by(Task.id).all()
        tasks_by_col = {i: [] for i in range(len(cols))}
        for t in tasks:
            tasks_by_col.setdefault(t.column_index, []).append(t)
        return render_template('index.html', columns=cols, tasks_by_col=tasks_by_col, today=today.isoformat())

    @app.route('/add_task', methods=['POST'])
    def add_task():
        check_rollover_and_archive()
        data = request.json or request.form
        title = data.get('title')
        description = data.get('description', '')
        col = int(data.get('column_index', 0))
        if not title:
            return jsonify({'error': 'title required'}), 400
        t = Task(title=title, description=description, column_index=col, task_date=date.today())
        db.session.add(t)
        db.session.commit()
        return jsonify({'task': t.to_dict()})

    @app.route('/move_task', methods=['POST'])
    def move_task():
        check_rollover_and_archive()
        data = request.json
        task_id = data.get('id')
        new_col = int(data.get('column_index'))
        t = Task.query.get(task_id)
        if not t:
            return jsonify({'error': 'task not found'}), 404
        if t.task_date != date.today() or t.archived:
            return jsonify({'error': 'task is archived or not editable'}), 400
        t.column_index = new_col
        db.session.commit()
        return jsonify({'ok': True})

    @app.route('/edit_task', methods=['POST'])
    def edit_task():
        check_rollover_and_archive()
        data = request.json or request.form
        tid = data.get('id')
        t = Task.query.get(tid)
        if not t:
            return jsonify({'error': 'task not found'}), 404
        if t.task_date != date.today() or t.archived:
            return jsonify({'error': 'not editable'}), 400
        t.title = data.get('title', t.title)
        t.description = data.get('description', t.description)
        db.session.commit()
        return jsonify({'task': t.to_dict()})

    @app.route('/delete_task', methods=['POST'])
    def delete_task():
        check_rollover_and_archive()
        data = request.json or request.form
        tid = data.get('id')
        t = Task.query.get(tid)
        if not t:
            return jsonify({'error': 'task not found'}), 404
        if t.task_date != date.today() or t.archived:
            return jsonify({'error': 'not deletable'}), 400
        db.session.delete(t)
        db.session.commit()
        return jsonify({'ok': True})

    @app.route('/settings', methods=['GET', 'POST'])
    def settings():
        check_rollover_and_archive()
        if request.method == 'POST':
            cols = request.form.getlist('column')
            cols = [c.strip() for c in cols if c.strip()][:5]
            Setting.set('columns', ','.join(cols))
            return redirect(url_for('index'))
        cols_raw = Setting.get('columns') or ''
        cols = cols_raw.split(',')
        while len(cols) < 5:
            cols.append('')
        return render_template('settings.html', columns=cols)

    @app.route('/archive')
    def archive_index():
        check_rollover_and_archive()
        dates = db.session.query(Task.task_date).filter(Task.archived==True).distinct().order_by(Task.task_date.desc()).all()
        dates = [d[0].isoformat() for d in dates]
        return render_template('archive_index.html', dates=dates)

    @app.route('/archive/day/<day>')
    def archive_day(day):
        check_rollover_and_archive()
        try:
            day_date = date.fromisoformat(day)
        except Exception:
            abort(404)
        tasks = Task.query.filter_by(task_date=day_date).order_by(Task.id).all()
        return render_template('archive_day.html', tasks=tasks, day=day)

    @app.route('/archive/week/<year>/<week>')
    def archive_week(year, week):
        check_rollover_and_archive()
        try:
            y = int(year)
            w = int(week)
        except:
            abort(404)
        tasks = []
        for t in Task.query.filter(Task.archived==True).all():
            if t.task_date.isocalendar()[0] == y and t.task_date.isocalendar()[1] == w:
                tasks.append(t)
        return render_template('archive_list.html', tasks=tasks, title=f'Week {w}, {y}')

    @app.route('/archive/month/<year>/<month>')
    def archive_month(year, month):
        check_rollover_and_archive()
        try:
            y = int(year); m = int(month)
        except:
            abort(404)
        tasks = Task.query.filter(Task.archived==True).all()
        tasks = [t for t in tasks if t.task_date.year==y and t.task_date.month==m]
        return render_template('archive_list.html', tasks=tasks, title=f'{m}/{y}')

    @app.route('/archive/year/<year>')
    def archive_year(year):
        check_rollover_and_archive()
        try:
            y = int(year)
        except:
            abort(404)
        tasks = Task.query.filter(Task.archived==True).all()
        tasks = [t for t in tasks if t.task_date.year==y]
        return render_template('archive_list.html', tasks=tasks, title=str(y))

    if __name__ == '__main__':
        print('Starting Flask Daily Kanban...')\n        app.run(host='0.0.0.0', port=5000, debug=True)\n