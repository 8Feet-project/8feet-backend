"""
WSGI config for 8Feet project.
"""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eightfeet.settings')
application = get_wsgi_application()
