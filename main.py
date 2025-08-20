
import os
import sys
import subprocess

# Change to backend directory
os.chdir('backend')

# Start Django development server
subprocess.run([
    sys.executable, 'manage.py', 'runserver', '0.0.0.0:8000'
])
