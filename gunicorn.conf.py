# gunicorn.conf.py
timeout = 120      # 2 minutes avant de tuer le process
workers = 1        # 1 worker pour 512MB RAM
keepalive = 5
