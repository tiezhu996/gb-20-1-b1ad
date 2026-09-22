"""本地/CI 测试用配置：使用 SQLite 内存库，无需启动 PostgreSQL。

用法：python manage.py test --settings=timetable.settings_test
"""
from .settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}
